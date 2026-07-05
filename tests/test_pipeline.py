"""Scraper -> pipeline uçtan uca testleri (SQLite bellek içi)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from sold.db.models import Base, Listing, ListingSnapshot, PriceChange
from sold.scraper.base import ListingRecord
from sold.scraper.example_parser import ExampleParser
from sold.scraper.pipeline import ingest_records, mark_delisted


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_example_parser_reads_fixture():
    html = Path("samples/example_listing.html").read_text(encoding="utf-8")
    with ExampleParser() as parser:
        record = parser.parse(html, url="local")

    assert record is not None
    assert record.price == 2_500_000.0
    assert record.province == "İstanbul"
    assert record.room_count == "2+1"
    assert record.gross_m2 == 120.0
    assert record.building_age == 5


def test_price_change_and_delisting():
    session = _make_session()

    ingest_records(
        session,
        [ListingRecord(source="t", source_listing_id="1", price=1_000_000)],
        captured_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
    )
    session.commit()

    # ikinci turda fiyat düşüşü
    ingest_records(
        session,
        [ListingRecord(source="t", source_listing_id="1", price=900_000)],
        captured_at=dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc),
    )
    session.commit()

    changes = session.scalars(select(PriceChange)).all()
    assert len(changes) == 1
    assert float(changes[0].old_price) == 1_000_000
    assert float(changes[0].new_price) == 900_000
    assert round(changes[0].pct_change, 1) == -10.0

    snapshots = session.scalars(
        select(ListingSnapshot).order_by(ListingSnapshot.captured_at)
    ).all()
    assert len(snapshots) == 2
    assert snapshots[-1].days_on_market == 14  # 1 Ocak -> 15 Ocak

    # bu turda görülmeyen ilan 'delisted' olur
    delisted = mark_delisted(session, "t", seen_listing_ids=[])
    session.commit()
    assert delisted == 1

    listing = session.scalars(select(Listing)).one()
    assert listing.status == "delisted"
    assert listing.delisted_at is not None


def test_relisting_reactivates():
    session = _make_session()
    rec = ListingRecord(source="t", source_listing_id="9", price=500_000)

    ingest_records(session, [rec])
    session.commit()
    mark_delisted(session, "t", seen_listing_ids=[])
    session.commit()
    assert session.scalars(select(Listing)).one().status == "delisted"

    # yeniden görülürse tekrar aktif
    ingest_records(session, [rec])
    session.commit()
    listing = session.scalars(select(Listing)).one()
    assert listing.status == "active"
    assert listing.delisted_at is None
