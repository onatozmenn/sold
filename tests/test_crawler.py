"""Crawler uçtan uca testi: iki günlük yerel "site" (SQLite bellek içi).

Gün 1 -> 3 yeni ilan. Gün 2 -> DEMO-1 fiyatı düşer (1 fiyat değişimi),
DEMO-3 listeden kalkar (1 delisted). Bu, longitudinal 'sold data' proxy
çekirdeğinin tam olarak çalıştığını gösterir.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from sold.db.models import Base, Listing, ListingSnapshot
from sold.scraper.adapters import get_adapter
from sold.scraper.adapters.local_example import LocalExampleAdapter
from sold.scraper.crawler import crawl_once


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_registry_returns_local_example():
    adapter = get_adapter("local-example", path="samples/site/day1")
    assert isinstance(adapter, LocalExampleAdapter)
    adapter.close()


def test_two_day_crawl_longitudinal():
    session = _make_session()

    with LocalExampleAdapter(path="samples/site/day1") as day1:
        run1 = crawl_once(
            session, day1, captured_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        )
    session.commit()

    assert run1.listings_seen == 3
    assert run1.new_listings == 3
    assert run1.price_changes == 0
    assert run1.delisted == 0

    with LocalExampleAdapter(path="samples/site/day2") as day2:
        run2 = crawl_once(
            session, day2, captured_at=dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc)
        )
    session.commit()

    assert run2.listings_seen == 2
    assert run2.new_listings == 0
    assert run2.price_changes == 1  # DEMO-1 düştü
    assert run2.delisted == 1  # DEMO-3 kayboldu

    listings = {
        listing.source_listing_id: listing
        for listing in session.scalars(select(Listing)).all()
    }
    assert listings["DEMO-1"].status == "active"
    assert listings["DEMO-2"].status == "active"
    assert listings["DEMO-3"].status == "delisted"
    assert listings["DEMO-3"].delisted_at is not None

    # DEMO-1 iki snapshot'a sahip (gün1 + gün2); time-on-market = 14 gün
    demo1_snaps = session.scalars(
        select(ListingSnapshot)
        .where(ListingSnapshot.listing_id == listings["DEMO-1"].id)
        .order_by(ListingSnapshot.captured_at)
    ).all()
    assert len(demo1_snaps) == 2
    assert float(demo1_snaps[0].price) == 3_200_000
    assert float(demo1_snaps[1].price) == 2_950_000
    assert demo1_snaps[1].days_on_market == 14


def test_partial_crawl_does_not_delist_existing_inventory():
    class BrokenAdapter:
        source_name = "local-example"

        def list_page_urls(self):
            return ["https://example.invalid/list"]

        def fetch(self, url):
            raise RuntimeError("temporary outage")

    session = _make_session()
    session.add(Listing(source="local-example", source_listing_id="ACTIVE", status="active"))
    session.commit()

    run = crawl_once(
        session,
        BrokenAdapter(),
        captured_at=dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
    )
    listing = session.scalar(select(Listing).where(Listing.source_listing_id == "ACTIVE"))

    assert run.status == "partial"
    assert run.delisted == 0
    assert listing.status == "active"
    assert listing.delisted_at is None
