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

# Domainler — KAYNAK/domain ekseni (kaynak-domain yanlılığı ölçümü için);
# ekonomik mekanizma ayrıca ``sale_mechanism``'de tutulur.
DOMAINS = ("kap", "uyap", "toki", "broker", "consumer")
SALE_MECHANISMS = (
    "auction",  # UYAP icra
    "corporate_arm_length",  # KAP: yalnızca GÜÇLÜ arm's-length kanıtı varsa
    "corporate_negotiated_non_related",  # KAP: ilişkisiz, pazarlıkla (arm's-length İDDİA EDİLMEZ)
    "corporate_related_party",  # KAP: ilişkili taraf
    "public_auction",  # TOKİ / GYO açık artırma
    "primary_market",  # TOKİ / GYO birincil satış
    "arm_length",  # sıradan ikinci el (broker/seller aracılı)
    "ordinary_resale",  # tüketicinin kendi beyanı (seller_self_reported): sıradan konut yeniden satışı
)
# appraisal = O İŞLEM İÇİN hazırlanmış GÜNCEL yapısal değerleme; prior_appraisal =
# açıklama metninde atıf yapılan ÖNCEKİ/emsal ekspertiz (yapısal alan boş).
REFERENCE_PRICE_TYPES = (
    "appraisal",
    "prior_appraisal",
    "reserve",
    "asking",
    "offered_avg",
    "none",
)

# Parser sürümü — gerçek-kayıt doğrulama manifestleri buna referans verir; parser
# davranışı değişince beklenen çıktılar yeniden denetlenmelidir.
PARSER_VERSION = "1.1.0"

# Doğrudan closing gözleyen kaynaklar (asking→closing head'ine YALNIZCA bunlar girer)
DIRECT_CLOSING_SOURCES = frozenset(
    {"broker_closing", "seller_self_reported", "bank_transfer_observed", "manual"}
)
# Sıradan konut yeniden-satış mekanizmaları (doğrudan asking→closing). Kamu mekanizmaları
# (auction/corporate_*/public_auction/primary_market) BURADA DEĞİL → asla head'e girmez.
DIRECT_RESALE_MECHANISMS = frozenset({"arm_length", "ordinary_resale"})

# Kayıt kökeni (origin) — GERÇEK tüketici gönderimini test/demo/manuel-import'tan AYIRIR.
# asking→closing head'i test/demo kayıtlarını VARSAYILAN olarak DIŞLAR; fixture verisi
# 'genuine' (gerçek) doğrudan-etiket sayısını ASLA şişirmemelidir. Köken belirtilmeyen ham
# etiket 'manual_import' sayılır (ne gerçek tüketici gönderimi ne test/demo).
ORIGIN_CONSUMER_SUBMISSION = "consumer_submission"  # TEK gerçek üretim kökeni (genuine)
ORIGIN_BROKER_SUBMISSION = "broker_submission"
ORIGIN_TEST_FIXTURE = "test_fixture"
ORIGIN_DEMO_SEED = "demo_seed"
ORIGIN_MANUAL_IMPORT = "manual_import"
ORIGINS = (
    ORIGIN_CONSUMER_SUBMISSION,
    ORIGIN_BROKER_SUBMISSION,
    ORIGIN_TEST_FIXTURE,
    ORIGIN_DEMO_SEED,
    ORIGIN_MANUAL_IMPORT,
)
DEFAULT_ORIGIN = ORIGIN_MANUAL_IMPORT
GENUINE_ORIGIN = ORIGIN_CONSUMER_SUBMISSION
GENUINE_ORIGINS = frozenset({ORIGIN_CONSUMER_SUBMISSION, ORIGIN_BROKER_SUBMISSION})
NON_PRODUCTION_ORIGINS = frozenset({ORIGIN_TEST_FIXTURE, ORIGIN_DEMO_SEED})
PUBLIC_DOMAINS = frozenset({"uyap", "kap", "toki"})

# Kalite durumu — tüketici gönderimlerinde kalite kapısı. Yalnızca 'accepted' model
# eğitimine girer; 'flagged' provenance'ıyla SAKLANIR ama a2c'den varsayılan dışlanır;
# 'rejected' yalnızca YAPISAL olarak imkânsız kayıtlar içindir (sınırda reddedilir).
QUALITY_ACCEPTED = "accepted"
QUALITY_FLAGGED = "flagged"
QUALITY_REJECTED = "rejected"
QUALITY_STATUSES = (QUALITY_ACCEPTED, QUALITY_FLAGGED, QUALITY_REJECTED)
DEFAULT_QUALITY_STATUS = QUALITY_ACCEPTED
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
    if domain in PUBLIC_DOMAINS and (
        ref_type == "asking"
        or mechanism in DIRECT_RESALE_MECHANISMS
        or source in DIRECT_CLOSING_SOURCES
    ):
        raise LabelError("Public evidence domains cannot be normalized as direct asking-to-closing labels.")
    if source is None:
        raise LabelError("label_source zorunludur.")
    inferred_origin = ORIGIN_BROKER_SUBMISSION if domain == "broker" else DEFAULT_ORIGIN
    origin = str(raw.get("origin") or inferred_origin).strip()
    if origin not in ORIGINS:
        raise LabelError(f"Geçersiz origin: {origin!r}. Geçerli: {', '.join(ORIGINS)}")
    quality_status = str(raw.get("quality_status") or DEFAULT_QUALITY_STATUS).strip()
    if quality_status not in QUALITY_STATUSES:
        raise LabelError(
            f"Geçersiz quality_status: {quality_status!r}. Geçerli: {', '.join(QUALITY_STATUSES)}"
        )
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
        "value_method": _s(raw.get("value_method")),
        "province": _s(raw.get("province")),
        "district": _s(raw.get("district")),
        "property_type": _s(raw.get("property_type")),
        "gross_m2": _f(raw.get("gross_m2")),
        "transaction_date": _date(raw.get("transaction_date")),
        "label_confidence": _s(raw.get("label_confidence"))
        or confidence_for(source, related),
        "origin": origin,
        "quality_status": quality_status,
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
    """Normalize edilmiş etiketleri realized_labels tablosuna idempotent ekler.

    Resmî kaynak kimliği bulunan etiketlerde ``domain + label_source + external_ref``
    aynı gözlemin yeniden içe aktarılmasını engeller. Kimliği olmayan eski/manuel
    etiketlerin mevcut ekleme davranışı korunur.
    """
    normalized = [normalize_label(raw) for raw in labels]
    existing_keys = {
        tuple(row)
        for row in session.execute(
            select(
                RealizedLabel.domain,
                RealizedLabel.label_source,
                RealizedLabel.external_ref,
            ).where(RealizedLabel.external_ref.is_not(None))
        ).all()
    }
    count = 0
    for v in normalized:
        external_ref = v.get("external_ref")
        key = (v["domain"], v["label_source"], external_ref)
        if external_ref is not None and key in existing_keys:
            continue
        session.add(RealizedLabel(**v))
        if external_ref is not None:
            existing_keys.add(key)
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
    "value_method",
    "province",
    "district",
    "property_type",
    "gross_m2",
    "transaction_date",
    "label_confidence",
    "origin",
    "quality_status",
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
                "value_method": r.value_method,
                "province": r.province,
                "district": r.district,
                "property_type": r.property_type,
                "gross_m2": _f(r.gross_m2),
                "transaction_date": r.transaction_date,
                "label_confidence": r.label_confidence,
                "origin": r.origin,
                "quality_status": r.quality_status,
                "external_ref": r.external_ref,
            }
            for r in rows
        ]
    )


# --------------------------------------------------------------------------- #
# DOMAIN AYRIMI — projenin metodolojik çekirdeği
# --------------------------------------------------------------------------- #
def asking_to_closing_labels(
    df: pd.DataFrame, include_non_production: bool = False
) -> pd.DataFrame:
    """asking→closing ML head'i için UYGUN etiketler.

    YALNIZCA: reference='asking' + sıradan resale mekanizması (arm_length broker/seller
    aracılı VEYA ordinary_resale tüketici öz-beyanı) + ilişkili taraf değil + doğrudan
    closing gözleyen kaynak. UYAP/KAP/TOKİ (auction/corporate_*/public_auction/
    primary_market) buraya ASLA girmez.

    KALİTE KAPISI: yalnızca ``quality_status='accepted'`` kayıtlar girer; 'flagged'/
    'rejected' model eğitiminden dışlanır (flagged, provenance'ıyla saklanır — inceleme
    sonrası accepted olursa girer).
    KÖKEN KAPISI: ``test_fixture``/``demo_seed`` kökenli kayıtlar VARSAYILAN olarak
    DIŞLANIR (``include_non_production=True`` ile test amaçlı dahil edilebilir). Böylece
    fixture verisi gerçek doğrudan-etiket sayısını ASLA şişirmez.
    """
    if df.empty:
        return df
    required = {
        "domain",
        "label_source",
        "sale_mechanism",
        "reference_price_type",
        "related_party",
    }
    if not required.issubset(df.columns):
        return df.iloc[0:0].copy()
    src = df["label_source"].fillna("").astype(str)
    mask = (
        (df["reference_price_type"] == "asking")
        & (df["sale_mechanism"].isin(DIRECT_RESALE_MECHANISMS))
        & (df["domain"].isin({"broker", "consumer"}))
        & (~df["related_party"].fillna(False).astype(bool))
        & (src.isin(DIRECT_CLOSING_SOURCES))
    )
    if "quality_status" in df.columns:
        mask &= (
            df["quality_status"].fillna(DEFAULT_QUALITY_STATUS).astype(str)
            == QUALITY_ACCEPTED
        )
    if not include_non_production and "origin" in df.columns:
        mask &= df["origin"].fillna(DEFAULT_ORIGIN).astype(str).isin(GENUINE_ORIGINS)
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
        df["reference_price_type"].isin(
            ["appraisal", "prior_appraisal", "reserve", "offered_avg"]
        )
    ].reset_index(drop=True)


def fair_value_strata(df: pd.DataFrame) -> dict[tuple[str, str, str], pd.DataFrame]:
    """FairValue etiketlerini AYRI stratalara böler: (domain, sale_mechanism, reference_price_type).

    KRİTİK: farklı referans→realized ilişkileri (appraisal→kurumsal, appraisal→ihale,
    reserve→ihale, offered_avg→birincil) tek bir hedefe HAVUZLANMAZ; her stratum kendi
    kalibratörüyle modellenmelidir. ``domain`` anahtarda KORUNUR ki kaynak-domain
    yanlılığı ileride ölçülebilsin. Boşsa {} döner.
    """
    fv = fair_value_labels(df)
    if fv.empty:
        return {}
    out: dict[tuple[str, str, str], pd.DataFrame] = {}
    for (dom, mech, ref), group in fv.groupby(
        ["domain", "sale_mechanism", "reference_price_type"]
    ):
        out[(str(dom), str(mech), str(ref))] = group.reset_index(drop=True)
    return out
