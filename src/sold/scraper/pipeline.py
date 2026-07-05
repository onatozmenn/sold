"""Longitudinal çekirdek: snapshot yazımı, fiyat değişimi ve delisting tespiti.

Bu modül, ABD'deki 'sold data'nın Türkiye'deki proxy'sini üretmenin kalbidir:
ilan zamanla izlenir, fiyat düşüşleri (asking-side indirim) kaydedilir ve ilan
kaybolduğunda 'delisted' işaretlenir (gerçekleşen fiyata en yakın gözlem).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Listing, ListingSnapshot, PriceChange
from .base import ListingRecord

logger = logging.getLogger(__name__)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime) -> dt.datetime:
    """Naive datetime'ı UTC farz eder (SQLite'tan okurken tz kaybolabilir)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)


def upsert_listing(
    session: Session, rec: ListingRecord, now: dt.datetime | None = None
) -> Listing:
    """İlanı ekler ya da mevcut olanı günceller (last_seen / yeniden yayın)."""
    now = now or _now()
    listing = session.scalars(
        select(Listing).where(
            Listing.source == rec.source,
            Listing.source_listing_id == rec.source_listing_id,
        )
    ).one_or_none()

    if listing is None:
        listing = Listing(
            source=rec.source,
            source_listing_id=rec.source_listing_id,
            url=rec.url,
            listing_type=rec.listing_type,
            first_seen_at=now,
            last_seen_at=now,
            status="active",
            province=rec.province,
            district=rec.district,
            neighborhood=rec.neighborhood,
            lat=rec.lat,
            lon=rec.lon,
            gross_m2=rec.gross_m2,
            net_m2=rec.net_m2,
            room_count=rec.room_count,
            building_age=rec.building_age,
            floor=rec.floor,
            total_floors=rec.total_floors,
            heating=rec.heating,
        )
        session.add(listing)
        session.flush()
    else:
        listing.last_seen_at = now
        if listing.status == "delisted":
            # yeniden yayına alınmış
            listing.status = "active"
            listing.delisted_at = None
    return listing


def record_snapshot(
    session: Session,
    listing: Listing,
    rec: ListingRecord,
    captured_at: dt.datetime | None = None,
) -> ListingSnapshot:
    """Anlık görüntü ekler; fiyat değiştiyse PriceChange kaydı üretir."""
    captured_at = captured_at or _now()

    days_on_market: int | None = None
    if listing.first_seen_at is not None:
        days_on_market = (captured_at - _aware(listing.first_seen_at)).days

    previous = session.scalars(
        select(ListingSnapshot)
        .where(ListingSnapshot.listing_id == listing.id)
        .order_by(ListingSnapshot.captured_at.desc())
    ).first()

    snapshot = ListingSnapshot(
        listing_id=listing.id,
        captured_at=captured_at,
        price=rec.price,
        currency=rec.currency,
        is_active=True,
        days_on_market=days_on_market,
    )
    session.add(snapshot)

    if previous is not None and float(previous.price) != float(rec.price):
        old_price = float(previous.price)
        new_price = float(rec.price)
        pct = (new_price - old_price) / old_price * 100.0 if old_price else 0.0
        session.add(
            PriceChange(
                listing_id=listing.id,
                changed_at=captured_at,
                old_price=old_price,
                new_price=new_price,
                pct_change=pct,
            )
        )
    return snapshot


def ingest_records(
    session: Session,
    records: Iterable[ListingRecord],
    captured_at: dt.datetime | None = None,
) -> list[int]:
    """Bir tarama turundaki kayıtları işler; görülen ilan id'lerini döndürür."""
    captured_at = captured_at or _now()
    seen: list[int] = []
    for rec in records:
        listing = upsert_listing(session, rec, now=captured_at)
        record_snapshot(session, listing, rec, captured_at=captured_at)
        seen.append(listing.id)
    session.flush()
    return seen


def mark_delisted(
    session: Session,
    source: str,
    seen_listing_ids: Iterable[int],
    now: dt.datetime | None = None,
) -> int:
    """Bu turda görülmeyen aktif ilanları 'delisted' işaretler; sayıyı döndürür."""
    now = now or _now()
    seen = set(seen_listing_ids)
    count = 0
    for listing in session.scalars(
        select(Listing).where(Listing.source == source, Listing.status == "active")
    ):
        if listing.id not in seen:
            listing.status = "delisted"
            listing.delisted_at = now
            count += 1
    return count
