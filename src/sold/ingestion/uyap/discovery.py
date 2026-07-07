"""Keşif (discovery) — bir UYAP açık artırma/sonuç adayını kişisel-olmayan metaveriyle temsil eder.

Yalnızca adayı UYAP kaydına yeniden bağlamak için gereken kaynak metaverisi yakalanır:
kurum, resmî dosya kimliği, liste/sonuç referansı, durum metni, keşif zaman damgası, kaynak
sayfa referansı. P/Q'nun büyüklüğü/yönü keşif ya da admisyon için ASLA kullanılmaz. Başarısız/
terminal-olmayan açık artırmalar sale-probability NEGATİFİ olarak SINIFLANDIRILMAZ.
"""

from __future__ import annotations

from pathlib import Path

from . import store
from .models import STATE_DISCOVERED, deterministic_candidate_id


def discover(
    institution: str,
    file_id: str,
    listing_ref: str | None = None,
    status_text: str | None = None,
    source_page_ref: str | None = None,
    store_dir: Path | str | None = None,
) -> dict:
    """Bir adayı keşfeder ve çalışma deposuna IDEMPOTENT kaydeder.

    Aynı kurum+dosya için tekrar çağrılırsa aynı ``candidate_id`` güncellenir (kopya YOK).
    Durum metni ham olarak korunur; admisyon kararı burada VERİLMEZ.
    """
    cid = deterministic_candidate_id(institution, file_id)
    existing = store.get_candidate(cid, store_dir)
    if existing is not None:
        # yalnızca keşif metaverisini tazele; iş akışı durumunu geri ALMA
        existing["listing_ref"] = listing_ref or existing.get("listing_ref")
        existing["status_text"] = (
            status_text if status_text is not None else existing.get("status_text")
        )
        existing["source_page_ref"] = source_page_ref or existing.get("source_page_ref")
        store.log_event(existing, "rediscovered", status_text or "")
        return store.upsert(existing, store_dir)

    cand = store.new_candidate(
        institution=institution,
        file_id=file_id,
        listing_ref=listing_ref,
        status_text=status_text,
        source_page_ref=source_page_ref,
    )
    cand["state"] = STATE_DISCOVERED
    return store.upsert(cand, store_dir)
