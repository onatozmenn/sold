"""Birleşik, provenance-aware gerçekleşen-fiyat etiket registry'si.

Çok-domainli etiketleri tek şemada toplar ama domain'leri KARIŞTIRMAZ. En kritik
ilke: ``asking → closing`` ML head'i YALNIZCA doğrudan closing gözleyen kaynakları
(reference='asking', mekanizma=arm_length, ilişkili taraf değil) görür. Kamu
domain'leri (UYAP icra, KAP kurumsal, TOKİ birincil) yalnızca ``FairValue →
realized`` kalibrasyonuna gider.

Zorunlu provenance alanları: domain, label_source, sale_mechanism,
reference_price_type. Böylece hangi etiketin nereden ve hangi mekanizmayla
geldiği modele açıkça bildirilir.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import RealizedLabel

# Domainler — her biri AYRI selection mekanizması
DOMAINS = ("public_auction", "corporate", "primary_market", "consumer", "broker")
SALE_MECHANISMS = (
    "auction",  # UYAP icra
    "corporate_arm_length",  # KAP kurumsal satış
    "public_auction",  # TOKİ / GYO açık artırma
    "primary_market",  # TOKİ / GYO birincil satış
    "arm_length",  # sıradan ikinci el (broker/seller)
)
REFERENCE_PRICE_TYPES = ("appraisal", "reserve", "asking", "offered_avg", "none")

# Doğrudan closing gözleyen kaynaklar (asking→closing head'ine YALNIZCA bunlar girer)
DIRECT_CLOSING_SOURCES = frozenset(
    {"broker_closing", "seller_self_reported", "bank_transfer_observed", "manual"}
)
# Gözlenmiş kamu işlem kaynakları (gerçek bedel gözlendi → güven A)
OBSERVED_PUBLIC_SOURCES = frozenset({"uyap", "kap", "toki"})


class LabelError(ValueError):
    """Geçersiz gerçekleşen-fiyat etiketi."""


def confidence_for(label_source: str | None, related_party: bool = False) -> str:
    """Etiket güveni (A/B/C).

    - Gözlenmiş kamu işlemi (uyap/kap/toki) → 'A' (gerçek bedel gözlendi).
    - Tapu beyanı (deed_declared) → 'C' (düşük beyanlı).
    - Diğer (broker/seller öz-beyanı) → 'B'.
    """
    if label_source == "deed_declared":
        return "C"
    if label_source in OBSERVED_PUBLIC_SOURCES:
        return "A"
    return "B"


def _f(v: object) -> float | None:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(v: object) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def normalize_label(raw: dict) -> dict:
    """Ham etiketi doğrular/normalize eder. Zorunlu provenance alanlarını dayatır."""
    domain = str(raw.get("domain") or "").strip()
    mechanism = str(raw.get("sale_mechanism") or "").strip()
    ref_type = str(raw.get("reference_price_type") or "none").strip()
    source = str(raw.get("label_source") or "").strip() or None
    if domain not in DOMAINS:
        raise LabelError(f"Geçersiz domain: {domain!r}. Geçerli: {', '.join(DOMAINS)}")
    if mechanism not in SALE_MECHANISMS:
        raise LabelError(f"Geçersiz sale_mechanism: {mechanism!r}.")
    if ref_type not in REFERENCE_PRICE_TYPES:
        raise LabelError(f"Geçersiz reference_price_type: {ref_type!r}.")
    realized = _f(raw.get("realized_price"))
    if realized is None or realized <= 0:
        raise LabelError("realized_price (gerçekleşen bedel) zorunlu ve > 0 olmalı.")

    related = bool(raw.get("related_party"))
    return {
        "domain": domain,
        "label_source": source,
        "sale_mechanism": mechanism,
        "reference_price_type": ref_type,
        "reference_price": _f(raw.get("reference_price")),
        "realized_price": realized,
        "related_party": related,
        "province": _s(raw.get("province")),
        "district": _s(raw.get("district")),
        "property_type": _s(raw.get("property_type")),
        "gross_m2": _f(raw.get("gross_m2")),
        "transaction_date": _date(raw.get("transaction_date")),
        "label_confidence": _s(raw.get("label_confidence"))
        or confidence_for(source, related),
        "external_ref": _s(raw.get("external_ref")),
    }


def _s(v: object) -> str | None:
    if v is None or (isinstance(v, float) and v != v) or v == "":
        return None
    return str(v)


# --------------------------------------------------------------------------- #
# Kalıcılık
# --------------------------------------------------------------------------- #
def persist_labels(session: Session, labels: list[dict]) -> int:
    """Normalize edilmiş etiketleri realized_labels tablosuna ekler."""
    count = 0
    for raw in labels:
        v = normalize_label(raw)
        session.add(RealizedLabel(**v))
        count += 1
    session.flush()
    return count


_COLUMNS = [
    "domain",
    "label_source",
    "sale_mechanism",
    "reference_price_type",
    "reference_price",
    "realized_price",
    "related_party",
    "province",
    "district",
    "property_type",
    "gross_m2",
    "transaction_date",
    "label_confidence",
    "external_ref",
]


def load_labels(session: Session, domain: str | None = None) -> pd.DataFrame:
    """Tüm gerçekleşen-fiyat etiketlerini DataFrame olarak yükler."""
    stmt = select(RealizedLabel)
    if domain:
        stmt = stmt.where(RealizedLabel.domain == domain)
    rows = session.scalars(stmt).all()
    if not rows:
        return pd.DataFrame(columns=_COLUMNS)
    return pd.DataFrame(
        [
            {
                "domain": r.domain,
                "label_source": r.label_source,
                "sale_mechanism": r.sale_mechanism,
                "reference_price_type": r.reference_price_type,
                "reference_price": _f(r.reference_price),
                "realized_price": _f(r.realized_price),
                "related_party": bool(r.related_party),
                "province": r.province,
                "district": r.district,
                "property_type": r.property_type,
                "gross_m2": _f(r.gross_m2),
                "transaction_date": r.transaction_date,
                "label_confidence": r.label_confidence,
                "external_ref": r.external_ref,
            }
            for r in rows
        ]
    )


# --------------------------------------------------------------------------- #
# DOMAIN AYRIMI — projenin metodolojik çekirdeği
# --------------------------------------------------------------------------- #
def asking_to_closing_labels(df: pd.DataFrame) -> pd.DataFrame:
    """asking→closing ML head'i için UYGUN etiketler.

    YALNIZCA: reference='asking' + mekanizma=arm_length + ilişkili taraf değil +
    doğrudan closing gözleyen kaynak. UYAP/KAP/TOKİ buraya ASLA girmez.
    """
    if df.empty:
        return df
    src = df["label_source"].fillna("").astype(str)
    mask = (
        (df["reference_price_type"] == "asking")
        & (df["sale_mechanism"] == "arm_length")
        & (~df["related_party"].fillna(False).astype(bool))
        & (src.isin(DIRECT_CLOSING_SOURCES))
    )
    return df[mask].reset_index(drop=True)


def fair_value_labels(df: pd.DataFrame) -> pd.DataFrame:
    """FairValue→realized için ADAY etiketleri döndürür (appraisal/reserve/offered_avg).

    UYARI: Bu bir REGISTRY SORGUSUDUR — eğitim kümesi DEĞİL. Dönen küme farklı
    domain/mekanizma/referans türlerini içerir ve TEK bir hedefe HAVUZLANMAMALIDIR
    (appraisal→kurumsal satış, appraisal→ihale, reserve→ihale, offered_avg→birincil
    piyasa AYRI ilişkilerdir). Stratifikasyon için ``fair_value_strata`` kullanın; her
    stratum ayrı kalibre edilir. ``sale_mechanism`` ve ``reference_price_type`` korunur.
    """
    if df.empty:
        return df
    return df[
        df["reference_price_type"].isin(["appraisal", "reserve", "offered_avg"])
    ].reset_index(drop=True)


def fair_value_strata(df: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    """FairValue etiketlerini AYRI stratalara böler: (sale_mechanism, reference_price_type).

    KRİTİK: farklı referans→realized ilişkileri (appraisal→kurumsal, appraisal→ihale,
    reserve→ihale, offered_avg→birincil) tek bir hedefe HAVUZLANMAZ; her stratum kendi
    kalibratörüyle modellenmelidir. Boşsa {} döner.
    """
    fv = fair_value_labels(df)
    if fv.empty:
        return {}
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for (mech, ref), group in fv.groupby(["sale_mechanism", "reference_price_type"]):
        out[(str(mech), str(ref))] = group.reset_index(drop=True)
    return out
