"""Longitudinal ilan verisinden model özelliklerini (feature) üretir.

Her ilan için hem hedonik nitelikler (m², oda, yaş, kat, konum) hem de
davranışsal/longitudinal sinyaller (ilk/son fiyat, time-on-market, fiyat düşüş
sayısı, toplam düşüş yüzdesi, delisted mi) tek satırda toplanır.
"""

from __future__ import annotations

import math
import re

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Listing, PriceChange

FEATURE_COLUMNS = [
    "source",
    "source_listing_id",
    "listing_type",
    "province",
    "district",
    "neighborhood",
    "lat",
    "lon",
    "gross_m2",
    "net_m2",
    "room_count_num",
    "building_age",
    "floor",
    "total_floors",
    "heating",
    "initial_price",
    "last_price",
    "num_snapshots",
    "num_price_changes",
    "days_on_market",
    "total_drop_pct",
    "is_delisted",
]


def parse_room_count(text: str | None) -> float:
    """'2+1' -> 3.0, '3+1' -> 4.0, 'Stüdyo' -> 1.0, boş -> NaN."""
    if not text:
        return math.nan
    numbers = re.findall(r"\d+", str(text))
    if numbers:
        return float(sum(int(n) for n in numbers))
    return 1.0 if "stüd" in str(text).lower() else math.nan


def _f(value: object) -> float | None:
    return float(value) if value is not None else None


def build_feature_frame(session: Session) -> pd.DataFrame:
    """Tüm ilanlar için tek-satır-özellik tablosu döndürür."""
    rows: list[dict] = []
    for listing in session.scalars(select(Listing)).all():
        snapshots = sorted(listing.snapshots, key=lambda s: s.captured_at)
        if not snapshots:
            continue
        initial = float(snapshots[0].price)
        last = float(snapshots[-1].price)
        num_changes = (
            session.scalar(
                select(func.count())
                .select_from(PriceChange)
                .where(PriceChange.listing_id == listing.id)
            )
            or 0
        )
        drop_pct = (last - initial) / initial * 100.0 if initial else 0.0
        rows.append(
            {
                "source": listing.source,
                "source_listing_id": listing.source_listing_id,
                "listing_type": listing.listing_type,
                "province": listing.province,
                "district": listing.district,
                "neighborhood": listing.neighborhood,
                "lat": _f(listing.lat),
                "lon": _f(listing.lon),
                "gross_m2": _f(listing.gross_m2),
                "net_m2": _f(listing.net_m2),
                "room_count_num": parse_room_count(listing.room_count),
                "building_age": listing.building_age,
                "floor": listing.floor,
                "total_floors": listing.total_floors,
                "heating": listing.heating,
                "initial_price": initial,
                "last_price": last,
                "num_snapshots": len(snapshots),
                "num_price_changes": int(num_changes),
                "days_on_market": snapshots[-1].days_on_market,
                "total_drop_pct": drop_pct,
                "is_delisted": listing.status == "delisted",
            }
        )
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)
