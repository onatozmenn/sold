"""İnsan inceleme kuyruğu (human review queue).

Belirsiz/bloklanan adaylar AÇIKÇA insan incelemesine sunulur. Boru hattı belirsiz bir adayı
genuine kanıta ASLA sessizce terfi ettirmez. İnceleyiciye şunlar gösterilir: aday kimliği,
önerilen ekspertiz, önerilen açık ihale fiyatı, gözlenen durum, kullanılan kaynak artifact'lar,
tam bloklayıcı denetim nedeni ve doğrulanması gereken alanlar.
"""

from __future__ import annotations

from pathlib import Path

from . import store
from .models import (
    DUPLICATE,
    MISSING_APPRAISAL,
    MISSING_AUCTION_PRICE,
    MISSING_TERMINAL_EVIDENCE,
    PENDING_REVIEW,
    RECONCILIATION_FAILED,
)

# İnsan incelemesi GEREKTİREN kararlar (admissible/excluded terminal kararlar HARİÇ).
REVIEW_DECISIONS = (
    PENDING_REVIEW,
    MISSING_APPRAISAL,
    MISSING_AUCTION_PRICE,
    MISSING_TERMINAL_EVIDENCE,
    RECONCILIATION_FAILED,
    DUPLICATE,
)


def needs_review(candidate: dict) -> bool:
    audit = candidate.get("audit") or {}
    return audit.get("decision") in REVIEW_DECISIONS


def review_item(candidate: dict) -> dict:
    """Bir aday için inceleyiciye sunulacak yapısal inceleme öğesi."""
    audit = candidate.get("audit") or {}
    ev = candidate.get("extracted") or {}
    return {
        "candidate_id": candidate.get("candidate_id"),
        "state": candidate.get("state"),
        "institution": candidate.get("institution"),
        "file_id": candidate.get("file_id"),
        "observed_status": ev.get("terminal_status_text") or candidate.get("status_text"),
        "proposed_appraisal": audit.get("appraisal_value"),
        "proposed_auction_price": audit.get("auction_price"),
        "artifacts_used": sorted({a.get("artifact_type") for a in candidate.get("artifacts", [])}),
        "audit_decision": audit.get("decision"),
        "blocking_reason": "; ".join(audit.get("blocking_reasons", []) or []),
        "fields_to_confirm": audit.get("fields_to_confirm", []) or [],
    }


def review_queue(store_dir: Path | str | None = None) -> list[dict]:
    """İnceleme gerektiren tüm adayların inceleme öğelerini döndürür."""
    return [review_item(c) for c in store.load_candidates(store_dir) if needs_review(c)]
