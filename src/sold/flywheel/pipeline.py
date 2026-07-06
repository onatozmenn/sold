"""Broker veri flywheel — ilan yaşam döngüsü sonucu toplama ve türetme.

Tek bir akış hem **ClosingDiscount** (yalnızca 'sold' + arm_length alt kümesi)
hem de ileride **SaleProbability** (tüm sonuçlar) için etiket üretir. Bu yüzden
form "kapanış fiyatı" değil, "ilan sonucu" toplar: Sold / Withdrawn / Expired /
Active / Lost to another broker / Unknown.

Güven (label_confidence): broker'ın kendi beyanı OTOMATİK 'A' ALMAZ — öz-beyan
varsayılan 'B'dir; ancak ``evidence_verified=True`` (bağımsız doğrulama) ile 'A'
olur. Tapu beyan bedeli ('deed_declared') düşük beyanlı olduğundan 'C'dir.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ListingOutcome
from ..groundtruth.loader import ARM_LENGTH, arm_length_only, to_feature_frame

# İlan yaşam döngüsü sonuçları
OUTCOMES = ("sold", "withdrawn", "expired", "active", "lost_to_other", "unknown")
SALE_OUTCOME = "sold"
# Yalnızca 'sold' sonucunda anlamlı olan kapanış alanları
CLOSING_FIELDS = ("sold_price", "sale_date", "days_to_close", "sale_mode")
EVIDENCE_TYPES = ("none", "screenshot", "contract", "bank_receipt", "deed", "other")


class OutcomeError(ValueError):
    """Geçersiz ilan sonucu kaydı."""


# --------------------------------------------------------------------------- #
# Küçük dönüştürücüler
# --------------------------------------------------------------------------- #
def _s(v: object) -> str | None:
    if v is None or (isinstance(v, float) and v != v) or v == "":
        return None
    return str(v)


def _f(v: object) -> float | None:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        f = float(v)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None


def _i(v: object) -> int | None:
    f = _f(v)
    return int(f) if f is not None else None


def _d(v: object) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Güven ataması + doğrulama
# --------------------------------------------------------------------------- #
def assign_confidence(label_source: str | None, evidence_verified: bool = False) -> str:
    """Etiket güven düzeyi (A/B/C).

    - ``deed_declared`` → 'C' (tapu beyanı düşük beyanlı).
    - Bağımsız doğrulanmış (``evidence_verified=True``) → 'A'.
    - Aksi halde (broker öz-beyanı dahil) → 'B'. broker_closing OTOMATİK A ALMAZ.
    """
    if label_source == "deed_declared":
        return "C"
    if evidence_verified:
        return "A"
    return "B"


def validate_outcome(rec: dict) -> dict:
    """Sonuç kaydını doğrular/normalize eder.

    Kapanış alanları (sold_price vb.) YALNIZCA 'sold' sonucunda tutulur; diğer
    sonuçlarda temizlenir. Güven verilmemişse kaynağa + doğrulamaya göre atanır.
    """
    outcome = str(rec.get("outcome") or "").strip().lower()
    if outcome not in OUTCOMES:
        raise OutcomeError(
            f"Geçersiz outcome: {outcome!r}. Geçerli değerler: {', '.join(OUTCOMES)}"
        )
    out = dict(rec)
    out["outcome"] = outcome

    if outcome != SALE_OUTCOME:
        for k in CLOSING_FIELDS:  # kapanış alanları yalnızca satışta anlamlı
            out[k] = None
    else:
        if not _f(out.get("sold_price")):
            raise OutcomeError("outcome='sold' için sold_price zorunludur.")
        out.setdefault("sale_mode", ARM_LENGTH)
        out["sale_mode"] = out.get("sale_mode") or ARM_LENGTH

    out["evidence_verified"] = bool(out.get("evidence_verified"))
    out.setdefault("evidence_type", "none")
    out["evidence_type"] = out.get("evidence_type") or "none"
    if not out.get("label_source"):
        out["label_source"] = "broker_closing" if outcome == SALE_OUTCOME else "broker_report"
    if not out.get("label_confidence"):
        out["label_confidence"] = assign_confidence(
            out.get("label_source"), out["evidence_verified"]
        )
    return out


def record_outcome(session: Session, rec: dict) -> ListingOutcome:
    """Tek bir ilan sonucunu (doğrulanmış) kaydeder."""
    v = validate_outcome(rec)
    row = ListingOutcome(
        source=_s(v.get("source")),
        listing_ref=_s(v.get("listing_ref")),
        listing_type=_s(v.get("listing_type")) or "sale",
        province=_s(v.get("province")),
        district=_s(v.get("district")),
        neighborhood=_s(v.get("neighborhood")),
        gross_m2=_f(v.get("gross_m2")),
        net_m2=_f(v.get("net_m2")),
        room_count=_s(v.get("room_count")),
        building_age=_i(v.get("building_age")),
        floor=_i(v.get("floor")),
        total_floors=_i(v.get("total_floors")),
        heating=_s(v.get("heating")),
        initial_asking_price=_f(v.get("initial_asking_price")),
        last_asking_price=_f(v.get("last_asking_price")),
        price_cut_count=_i(v.get("price_cut_count")),
        listing_date=_d(v.get("listing_date")),
        days_on_market=_i(v.get("days_on_market")),
        outcome=v["outcome"],
        sold_price=_f(v.get("sold_price")),
        sale_date=_d(v.get("sale_date")),
        days_to_close=_i(v.get("days_to_close")),
        sale_mode=_s(v.get("sale_mode")),
        label_source=_s(v.get("label_source")),
        label_confidence=_s(v.get("label_confidence")),
        evidence_type=_s(v.get("evidence_type")),
        evidence_verified=bool(v.get("evidence_verified")),
    )
    session.add(row)
    session.flush()
    return row


_OUTCOME_COLUMNS = [
    "source",
    "listing_ref",
    "listing_type",
    "province",
    "district",
    "neighborhood",
    "gross_m2",
    "net_m2",
    "room_count",
    "building_age",
    "floor",
    "total_floors",
    "heating",
    "initial_asking_price",
    "last_asking_price",
    "price_cut_count",
    "days_on_market",
    "outcome",
    "sold_price",
    "sale_date",
    "days_to_close",
    "sale_mode",
    "label_source",
    "label_confidence",
    "evidence_type",
    "evidence_verified",
]


def _row_dict(r: ListingOutcome) -> dict:
    return {
        "source": r.source,
        "listing_ref": r.listing_ref,
        "listing_type": r.listing_type,
        "province": r.province,
        "district": r.district,
        "neighborhood": r.neighborhood,
        "gross_m2": _f(r.gross_m2),
        "net_m2": _f(r.net_m2),
        "room_count": r.room_count,
        "building_age": r.building_age,
        "floor": r.floor,
        "total_floors": r.total_floors,
        "heating": r.heating,
        "initial_asking_price": _f(r.initial_asking_price),
        "last_asking_price": _f(r.last_asking_price),
        "price_cut_count": r.price_cut_count,
        "days_on_market": r.days_on_market,
        "outcome": r.outcome,
        "sold_price": _f(r.sold_price),
        "sale_date": r.sale_date,
        "days_to_close": r.days_to_close,
        "sale_mode": r.sale_mode,
        "label_source": r.label_source,
        "label_confidence": r.label_confidence,
        "evidence_type": r.evidence_type,
        "evidence_verified": r.evidence_verified,
    }


def load_outcomes(session: Session, source: str | None = None) -> pd.DataFrame:
    """Tüm ilan sonuçlarını DataFrame olarak yükler (analytics + SaleProbability)."""
    stmt = select(ListingOutcome)
    if source:
        stmt = stmt.where(ListingOutcome.source == source)
    rows = session.scalars(stmt).all()
    if not rows:
        return pd.DataFrame(columns=_OUTCOME_COLUMNS)
    return pd.DataFrame([_row_dict(r) for r in rows])


def closing_discount_frame(session: Session, source: str | None = None) -> pd.DataFrame:
    """ClosingDiscount eğitim çerçevesi: yalnızca 'sold' + arm_length.

    Broker şemasına map'leyip ``groundtruth.to_feature_frame`` ile ortak boru
    hattına verir (asking = son ilan fiyatı; label = sold_price).
    """
    empty = pd.DataFrame(columns=["asking_price", "sold_price", "district"])
    df = load_outcomes(session, source)
    if df.empty:
        return to_feature_frame(empty)
    sold = df[(df["outcome"] == SALE_OUTCOME) & df["sold_price"].notna()].copy()
    if sold.empty:
        return to_feature_frame(empty)

    days = pd.to_numeric(sold["days_to_close"], errors="coerce")
    if "days_on_market" in sold.columns:
        days = days.fillna(pd.to_numeric(sold["days_on_market"], errors="coerce"))
    broker = pd.DataFrame(
        {
            "source": sold["source"],
            "listing_type": sold["listing_type"],
            "province": sold["province"],
            "district": sold["district"],
            "neighborhood": sold["neighborhood"],
            "gross_m2": sold["gross_m2"],
            "net_m2": sold["net_m2"],
            "room_count": sold["room_count"],
            "building_age": sold["building_age"],
            "floor": sold["floor"],
            "total_floors": sold["total_floors"],
            "heating": sold["heating"],
            "asking_price": sold["last_asking_price"],
            "sold_price": sold["sold_price"],
            "days_on_market": days,
            "sale_date": sold["sale_date"],
            "sale_mode": sold["sale_mode"],
            "label_source": sold["label_source"],
            "label_confidence": sold["label_confidence"],
        }
    )
    return arm_length_only(to_feature_frame(broker))
