"""UYAP Evidence Ingestion Pipeline V1 — provenans-farkında veri-edinim alt sistemi.

Aşamalar: discovery → collection (tarayıcı-destekli / elle içe aktarma) → extraction →
same-asset reconciliation → rule-based completed-sale audit → human review → explicit
admission → mevcut UYAP kanıt şeması (``sold.structural``). Yapısal ekonometrik çekirdeği
DEĞİŞTİRMEZ; admisyon mevcut ``normalize_auction`` şemasına yazar ve UYAP P/Q moment tanımını
KORUR (pay = açık İhale Bedeli; ASLA Ödenmesi Gereken Bedel/depozito/hisse/mahsup/KDV).
"""

from __future__ import annotations

from . import admit, audit, collect, discovery, extract, models, pipeline, reconcile, review, store
from .admit import INGESTION_BATCH, admit as admit_candidate, build_genuine_record, record_exclusion
from .audit import audit_candidate
from .collect import BROWSER_PREREQUISITES, BrowserCollector, import_artifact
from .discovery import discover
from .extract import extract_evidence
from .models import (
    ADMISSIBLE_COMPLETED_SALE,
    AUDIT_DECISIONS,
    DUPLICATE,
    EXCLUDED_NON_TERMINAL,
    MISSING_APPRAISAL,
    MISSING_AUCTION_PRICE,
    MISSING_TERMINAL_EVIDENCE,
    PENDING_REVIEW,
    RECONCILIATION_FAILED,
    AuditResult,
    ExtractedEvidence,
    ReconciliationResult,
    SourceArtifact,
    parse_tl_amount,
)
from .pipeline import run_audit, run_extract, status_summary
from .reconcile import reconcile
from .review import needs_review, review_item, review_queue

__all__ = [
    "admit",
    "audit",
    "collect",
    "discovery",
    "extract",
    "models",
    "pipeline",
    "reconcile",
    "review",
    "store",
    # işlevler
    "discover",
    "import_artifact",
    "extract_evidence",
    "run_extract",
    "reconcile",
    "audit_candidate",
    "run_audit",
    "review_queue",
    "review_item",
    "needs_review",
    "admit_candidate",
    "build_genuine_record",
    "record_exclusion",
    "status_summary",
    "parse_tl_amount",
    "BrowserCollector",
    "BROWSER_PREREQUISITES",
    "INGESTION_BATCH",
    # modeller / sözlük
    "SourceArtifact",
    "ExtractedEvidence",
    "ReconciliationResult",
    "AuditResult",
    "AUDIT_DECISIONS",
    "ADMISSIBLE_COMPLETED_SALE",
    "EXCLUDED_NON_TERMINAL",
    "PENDING_REVIEW",
    "DUPLICATE",
    "RECONCILIATION_FAILED",
    "MISSING_APPRAISAL",
    "MISSING_AUCTION_PRICE",
    "MISSING_TERMINAL_EVIDENCE",
]
