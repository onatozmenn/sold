"""Crawler orkestratörü (Faz 1).

Bir ``SiteAdapter`` alıp tek bir tarama turunu yürütür: liste sayfalarını gez,
ilan detaylarını getir, ``ListingRecord``'a ayrıştır, longitudinal pipeline'a
işle (snapshot + fiyat değişimi), turda görülmeyenleri delisted işaretle ve
turun künyesini (``CrawlRun``) kaydet.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import CrawlRun, Listing, PriceChange
from .adapters.base import SiteAdapter
from .archive import save_html
from .pipeline import mark_delisted, record_snapshot, upsert_listing

logger = logging.getLogger(__name__)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def crawl_once(
    session: Session,
    adapter: SiteAdapter,
    captured_at: dt.datetime | None = None,
    archive: bool = False,
) -> CrawlRun:
    """Tek bir tarama turu yürütür ve künyesini döndürür."""
    captured_at = captured_at or _now()
    started = _now()

    price_changes_before = session.scalar(select(func.count()).select_from(PriceChange)) or 0
    listings_before = session.scalar(select(func.count()).select_from(Listing)) or 0

    seen_ids: list[int] = []
    errors = 0

    for page_url in adapter.list_page_urls():
        try:
            page_html = adapter.fetch(page_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Liste sayfası alınamadı %s: %s", page_url, exc)
            errors += 1
            continue
        if not page_html:
            continue

        for listing_url in adapter.extract_listing_urls(page_html, page_url):
            try:
                detail_html = adapter.fetch(listing_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("İlan alınamadı %s: %s", listing_url, exc)
                errors += 1
                continue
            if not detail_html:
                continue

            if archive:
                try:
                    save_html(adapter.source_name, listing_url, detail_html, captured_at)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Arşivleme hatası %s: %s", listing_url, exc)

            try:
                record = adapter.parse(detail_html, listing_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ayrıştırma hatası %s: %s", listing_url, exc)
                errors += 1
                continue
            if record is None:
                continue

            listing = upsert_listing(session, record, now=captured_at)
            record_snapshot(session, listing, record, captured_at=captured_at)
            seen_ids.append(listing.id)

        session.flush()

    delisted = mark_delisted(session, adapter.source_name, seen_ids, now=captured_at)
    session.flush()

    price_changes_after = session.scalar(select(func.count()).select_from(PriceChange)) or 0
    listings_after = session.scalar(select(func.count()).select_from(Listing)) or 0

    run = CrawlRun(
        source=adapter.source_name,
        started_at=started,
        finished_at=_now(),
        listings_seen=len(seen_ids),
        new_listings=listings_after - listings_before,
        price_changes=price_changes_after - price_changes_before,
        delisted=delisted,
        status="ok" if errors == 0 else "partial",
        note=f"errors={errors}" if errors else None,
    )
    session.add(run)
    session.flush()

    logger.info(
        "[%s] görülen=%d yeni=%d fiyat_değişim=%d delisted=%d (%s)",
        run.source,
        run.listings_seen,
        run.new_listings,
        run.price_changes,
        run.delisted,
        run.status,
    )
    return run
