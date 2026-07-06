"""Tüketici satışı için ANLIK non-ML analitik + anonim segment benchmark.

Katkı sağlayan kişiye ANINDA değer döner (flywheel teşviki): initial-ask→close ve
final-ask→close farkı, kapanış süresi, fiyat-kesinti sayısı. Segment benchmark ancak
YETERLİ gerçek gözlem varsa gösterilir; yoksa dürüstçe "yeterli gözlem yok" denir —
ASLA uydurma bir benchmark üretilmez. Benchmark yalnızca AGREGAT istatistiklerdir
(satır düzeyi kayıt sızdırılmaz).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..labels.registry import NON_PRODUCTION_ORIGINS
from .collector import DEFAULT_PROPERTY_TYPE, load_consumer_sales

# Bir segmentin benchmark gösterebilmesi için gereken en az gerçek gözlem sayısı
MIN_SEGMENT_OBSERVATIONS = 5


def _f(v: object) -> float | None:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _gap_pct(reference: object, realized: object) -> float | None:
    """(1 − realized / reference) × 100 — pozitif = referansın altında kapandı."""
    ref = _f(reference)
    real = _f(realized)
    if not ref or real is None:
        return None
    return round((1.0 - real / ref) * 100.0, 2)


def sale_analytics(sale: dict) -> dict:
    """Tek bir satıştan anlık müzakere metrikleri (katkı sağlayana hemen döner)."""
    days = sale.get("days_to_close")
    if days is None:
        listing_date, closing_date = sale.get("listing_date"), sale.get("closing_date")
        if listing_date and closing_date:
            delta = (closing_date - listing_date).days
            days = delta if delta >= 0 else None
    return {
        "initial_ask_to_close_gap_pct": _gap_pct(
            sale.get("initial_asking_price"), sale.get("closing_price")
        ),
        "final_ask_to_close_gap_pct": _gap_pct(
            sale.get("final_asking_price"), sale.get("closing_price")
        ),
        "days_to_close": int(days) if days is not None else None,
        "price_cut_count": sale.get("price_cut_count"),
    }


def segment_key(sale: dict) -> tuple[str, str]:
    """Karşılaştırma segmenti: (il, taşınmaz türü). Kaba tutulur ki segment dolsun."""
    return (
        str(sale.get("province") or "?"),
        str(sale.get("property_type") or DEFAULT_PROPERTY_TYPE),
    )


def _segment_frame(df: pd.DataFrame, province: str, property_type: str) -> pd.DataFrame:
    if df.empty:
        return df
    prov = df.get("province")
    ptype = df.get("property_type")
    if prov is None or ptype is None:
        return df.iloc[0:0]
    mask = (prov.astype(str) == province) & (ptype.astype(str) == property_type)
    return df[mask]


def _median(values: np.ndarray) -> float | None:
    v = values[~np.isnan(values)]
    return round(float(np.median(v)), 2) if v.size else None


def _mean(values: np.ndarray) -> float | None:
    v = values[~np.isnan(values)]
    return round(float(np.mean(v)), 2) if v.size else None


def segment_benchmark(
    session: Session,
    sale: dict,
    min_observations: int = MIN_SEGMENT_OBSERVATIONS,
) -> dict:
    """Aynı segmentteki (il × taşınmaz türü) gerçek satışlardan anonim benchmark.

    Yeterli gözlem YOKSA ``enough_observations=False`` ve açık bir mesaj döner
    (uydurma benchmark ASLA üretilmez). Yeterliyse yalnızca AGREGAT istatistik
    (medyan/ortalama gap, kapanış süresi, fiyat-kesinti) döner. Test/demo kökenli
    kayıtlar benchmark'a DAHİL EDİLMEZ (benchmark yalnızca üretim gözlemlerinden).
    """
    province, property_type = segment_key(sale)
    df = load_consumer_sales(session)
    if not df.empty and "origin" in df.columns:
        df = df[~df["origin"].astype(str).isin(NON_PRODUCTION_ORIGINS)]
    seg = _segment_frame(df, province, property_type)
    n = int(len(seg))
    segment_info = {"province": province, "property_type": property_type}

    if n < min_observations:
        return {
            "enough_observations": False,
            "observations": n,
            "min_required": int(min_observations),
            "segment": segment_info,
            "message": (
                f"'{province} · {property_type}' segmentinde henüz yeterli gözlem yok "
                f"({n}/{min_observations}). Benchmark gösterilmiyor (uydurma yok)."
            ),
        }

    closing = pd.to_numeric(seg["closing_price"], errors="coerce").to_numpy(float)
    final_ask = pd.to_numeric(seg["final_asking_price"], errors="coerce").to_numpy(float)
    initial_ask = pd.to_numeric(seg["initial_asking_price"], errors="coerce").to_numpy(float)
    days = pd.to_numeric(seg["days_to_close"], errors="coerce").to_numpy(float)
    cuts = pd.to_numeric(seg["price_cut_count"], errors="coerce").to_numpy(float)

    with np.errstate(divide="ignore", invalid="ignore"):
        final_gap = np.where(final_ask > 0, (1.0 - closing / final_ask) * 100.0, np.nan)
        initial_gap = np.where(initial_ask > 0, (1.0 - closing / initial_ask) * 100.0, np.nan)

    return {
        "enough_observations": True,
        "observations": n,
        "min_required": int(min_observations),
        "segment": segment_info,
        "median_final_ask_to_close_gap_pct": _median(final_gap),
        "mean_final_ask_to_close_gap_pct": _mean(final_gap),
        "median_initial_ask_to_close_gap_pct": _median(initial_gap),
        "median_days_to_close": _median(days),
        "median_price_cut_count": _median(cuts),
    }
