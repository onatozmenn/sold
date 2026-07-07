"""Boru hattı orkestrasyonu — aşamaları çalışma deposundaki aday üzerinde uygular.

Çıkarım ve denetimi bir arada tutar; denetim ADMİSYON DEĞİLdİr. Aynı-varlık mutabakatı ve
DUPLICATE tespiti burada (dosya sistemi erişimi olan orkestratör) yapılır; ``audit_candidate``
saf kalır. Yapısal çekirdek çağrılmaz/değiştirilmez.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import store
from .audit import audit_candidate
from .extract import extract_evidence
from .models import (
    ADMISSIBLE_COMPLETED_SALE,
    DUPLICATE,
    EXCLUDED_NON_TERMINAL,
    PENDING_REVIEW,
    STATE_AUDITED,
    STATE_EXTRACTED,
    STATE_PENDING_REVIEW,
    ExtractedEvidence,
    ReconciliationResult,
)
from .reconcile import reconcile
from .review import REVIEW_DECISIONS


def run_extract(candidate: dict, store_dir: Path | str | None = None) -> dict:
    """Aday artifact'larından deterministik çıkarım yapar; ``extracted`` alanını doldurur."""
    ev = extract_evidence(
        candidate.get("artifacts", []),
        institution=candidate.get("institution"),
        file_id=candidate.get("file_id"),
    )
    candidate["extracted"] = ev.to_dict()
    candidate["state"] = STATE_EXTRACTED
    store.log_event(candidate, "extracted", f"status={ev.extraction_status}")
    return store.upsert(candidate, store_dir)


def _genuine_ids(genuine_path: Path | str | None) -> set[str]:
    from ...structural.datasets import GENUINE_DIR

    path = Path(genuine_path) if genuine_path else GENUINE_DIR / "uyap.json"
    if not path.exists():
        return set()
    return {str(r.get("public_record_id")) for r in json.loads(path.read_text(encoding="utf-8"))}


def run_audit(
    candidate: dict,
    store_dir: Path | str | None = None,
    genuine_path: Path | str | None = None,
) -> dict:
    """Aynı-varlık mutabakatı + kural-tabanlı denetim; ADMİSYON DEĞİL, ``audit`` doldurur."""
    rec = reconcile(candidate.get("artifacts", []), candidate.get("institution"), candidate.get("file_id"))
    ev_dict = candidate.get("extracted")
    if ev_dict is None:
        candidate = run_extract(candidate, store_dir)
        ev_dict = candidate["extracted"]
    ev = ExtractedEvidence(**ev_dict)
    audit = audit_candidate(ev, rec)

    # DUPLICATE: dosya zaten genuine uyap.json'da admitte edilmişse (idempotent, hata değil).
    if str(candidate.get("file_id")) in _genuine_ids(genuine_path):
        audit.decision = DUPLICATE
        audit.blocking_reasons.append("public_record_id already admitted as a genuine UYAP observation")

    candidate["reconciliation"] = rec.to_dict()
    candidate["audit"] = audit.to_dict()
    candidate["state"] = (
        STATE_PENDING_REVIEW if audit.decision in REVIEW_DECISIONS else STATE_AUDITED
    )
    store.log_event(candidate, "audited", audit.decision)
    return store.upsert(candidate, store_dir)


def status_summary(store_dir: Path | str | None = None) -> dict:
    """Operatör durum özeti: aşama sayıları + inceleme/admissible/admitted/excluded."""
    candidates = store.load_candidates(store_dir)
    by_state: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    for c in candidates:
        by_state[c.get("state", "unknown")] = by_state.get(c.get("state", "unknown"), 0) + 1
        dec = (c.get("audit") or {}).get("decision")
        if dec:
            by_decision[dec] = by_decision.get(dec, 0) + 1
    return {
        "total_candidates": len(candidates),
        "by_state": by_state,
        "by_audit_decision": by_decision,
        "review_blockers": sum(
            1 for c in candidates if (c.get("audit") or {}).get("decision") in REVIEW_DECISIONS
        ),
        "admissible": by_decision.get(ADMISSIBLE_COMPLETED_SALE, 0),
        "excluded_non_terminal": by_decision.get(EXCLUDED_NON_TERMINAL, 0),
        "admitted": sum(1 for c in candidates if c.get("admitted_public_record_id")),
    }
