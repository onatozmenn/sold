"""UYAP Evidence Ingestion Pipeline V1 — provenans-farkında veri-edinim alt sistemi.

Aşamalar: discovery → collection (tarayıcı-destekli / elle içe aktarma) → extraction →
same-asset reconciliation → rule-based completed-sale audit → human review → explicit
admission → mevcut UYAP kanıt şeması (``sold.structural``). Yapısal ekonometrik çekirdeği
DEĞİŞTİRMEZ; admisyon mevcut ``normalize_auction`` şemasına yazar ve UYAP P/Q moment tanımını
KORUR (pay = açık İhale Bedeli; ASLA Ödenmesi Gereken Bedel/depozito/hisse/mahsup/KDV).
"""

from __future__ import annotations

from . import admit, audit, collect, discovery, extract, models, pilot, pipeline, reconcile, review, store
from .admit import INGESTION_BATCH, admit as admit_candidate, build_genuine_record, record_exclusion
from .audit import audit_candidate
from .collect import (
    BROWSER_PREREQUISITES,
    BrowserCollector,
    card_document_list_control,
    classify_access_pattern,
    classify_document_entry_path,
    classify_document_label,
    classify_document_list_container,
    classify_page_state,
    classify_view_access_pattern,
    classify_viewer_representation,
    classify_viewer_url,
    discover_document_links,
    extract_panel_document_rows,
    file_identity_matches,
    find_target_record_card,
    has_document_list_control,
    import_artifact,
    normalize_file_identity,
    page_state_evidence,
    panel_has_documents,
    select_row_document_actions,
    select_target_page_index,
    viewer_mime_hint,
)
from .discovery import discover
from .extract import asset_descriptors, extract_evidence
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
from .pilot import (
    KNOWN_TRUTH,
    MODE_LIVE,
    MODE_OFFLINE,
    PILOT_NAME,
    compare_to_truth,
    genuine_fingerprint,
    run_pilot,
    verify_pilot,
)
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
    "discover_document_links",
    "classify_document_label",
    "has_document_list_control",
    "select_row_document_actions",
    "classify_access_pattern",
    "panel_has_documents",
    "extract_panel_document_rows",
    "classify_document_list_container",
    "classify_viewer_url",
    "viewer_mime_hint",
    "classify_viewer_representation",
    "classify_view_access_pattern",
    "classify_page_state",
    "page_state_evidence",
    "select_target_page_index",
    "normalize_file_identity",
    "file_identity_matches",
    "find_target_record_card",
    "card_document_list_control",
    "classify_document_entry_path",
    "asset_descriptors",
    "INGESTION_BATCH",
    # pilot (live browser verification)
    "pilot",
    "run_pilot",
    "verify_pilot",
    "compare_to_truth",
    "genuine_fingerprint",
    "KNOWN_TRUTH",
    "PILOT_NAME",
    "MODE_LIVE",
    "MODE_OFFLINE",
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
