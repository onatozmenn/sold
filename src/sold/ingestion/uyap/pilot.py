"""UYAP LIVE BROWSER PILOT 1 — kullanıcı-kontrollü tarayıcı oturumu için DOĞRULAMA iş akışı.

Tek soruyu yanıtlar: uygulanan UYAP Evidence Ingestion Pipeline V1, GERÇEK kullanıcı-kontrollü
bir tarayıcı oturumuna bağlanıp bilinen bir tamamlanmış-satış kaydını (2026/263 Esas) toplayıp
çıkarıp denetleyerek, GENUINE veri kümesini DEĞİŞTİRMEDEN elle-denetlenmiş gerçeği üretebilir mi?

KRİTİK: Bu bir DOĞRULAMA pilotudur, kanıt-genişletme değil. 2026/263 zaten genuine ``uyap.json``'da
admitte edilmiştir → pilot 8. gözlem OLUŞTURMAZ; genuine UYAP sayısı 7 KALIR. Bilinen gerçek
değerler yalnızca DOĞRULAMA HEDEFİdİr; çıkarıcıya ENJEKTE EDİLMEZ (``if file_id == ...`` YASAK).
Yapısal çekirdek DEĞİŞTİRİLMEZ.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from .audit import audit_candidate
from .extract import extract_evidence
from .models import (
    ADMISSIBLE_COMPLETED_SALE,
    TERMINAL_SALE_TOKENS,
    ExtractedEvidence,
    _ascii_lower,
)
from .reconcile import reconcile

PILOT_NAME = "UYAP Live Browser Pilot 1"

# Pilot sonuç sınıflandırması (dürüst semantik).
PASS = "PASS"
PARTIAL = "PARTIAL"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"

MODE_LIVE = "live"
MODE_OFFLINE = "offline_simulated"

# Bilinen pilot kaydının ELLE-DENETLENMİŞ gerçeği — yalnızca DOĞRULAMA HEDEFİ (enjeksiyon DEĞİL).
KNOWN_TRUTH = {
    "official_file_id": "2026/263 Esas",
    "official_file_id_aliases": ["2026/263 esas", "2026/263 icra"],
    "institution": "Ankara Gayrimenkul Satış İcra Dairesi",
    "appraisal_value_tl": 6_800_000,
    "official_auction_price_tl": 5_715_000,
    "p_over_q": 5_715_000 / 6_800_000,  # 0.8404411764705882
    "terminal_completed_sale": True,
    "alacaga_mahsuben": True,   # opsiyonel korroborasyon
    "kdv_rate": 20.0,           # opsiyonel korroborasyon
    "audit_decision": ADMISSIBLE_COMPLETED_SALE,
    "asset": {"ada": "50984", "parsel": "1", "section_no": "60", "floor": "12", "property_type": "konut"},
}


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _num_eq(actual, expected) -> bool:
    if actual is None:
        return False
    return int(round(float(actual))) == int(round(float(expected)))


def _ratio_eq(actual, expected, tol: float = 1e-9) -> bool:
    if actual is None:
        return False
    return abs(float(actual) - float(expected)) <= tol


def _field(expected, actual, match: bool) -> dict:
    return {"expected": expected, "actual": actual, "match": bool(match),
            "missing": actual is None, "wrong": (actual is not None and not match)}


def _final_viewer_state(collection_diagnostics: dict | None, key: str):
    """Fix 10: SON (kararlı) görüntüleyici durumunu attempt tanılarından okur (auction_result öncelikli).

    İlk (kararlılık-öncesi) anlık gözlem yerine, kaynağın gerçekten toplandığı KARARLI durumu döndürür.
    """
    attempts = (collection_diagnostics or {}).get("document_collection_attempts") or []
    ranked = sorted(
        (a for a in attempts if isinstance(a, dict) and key in a),
        key=lambda a: (0 if a.get("artifact_type") == "auction_result" else 1,
                       0 if a.get("document_source_artifact_collected") else 1),
    )
    return ranked[0].get(key) if ranked else None


def compare_to_truth(extracted: ExtractedEvidence, audit, file_id: str, truth: dict = None) -> dict:
    """Canlı çıkarım/denetim çıktısını elle-denetlenmiş gerçekle karşılaştırır (mutasyon YOK).

    ZORUNLU doğrulamalar (formal admisyon koşulları) ile OPSİYONEL korroborasyon (ALACAĞA
    MAHSUBEN / KDV) açıkça ayrılır: eksik opsiyonel kanıt tam BAŞARISIZLIK değildir.
    """
    truth = truth or KNOWN_TRUTH
    terminal = extracted.terminal_status_text in TERMINAL_SALE_TOKENS
    fid_fold = _ascii_lower(file_id or "")
    fid_ok = any(a in fid_fold or fid_fold in a for a in truth["official_file_id_aliases"])

    required = {
        "file_id": _field(truth["official_file_id"], file_id, fid_ok),
        "appraisal_value_tl": _field(truth["appraisal_value_tl"], extracted.appraisal_value,
                                     _num_eq(extracted.appraisal_value, truth["appraisal_value_tl"])),
        "official_auction_price_tl": _field(truth["official_auction_price_tl"], audit.auction_price,
                                            _num_eq(audit.auction_price, truth["official_auction_price_tl"])),
        "p_over_q": _field(truth["p_over_q"], audit.win_over_appraisal,
                           _ratio_eq(audit.win_over_appraisal, truth["p_over_q"])),
        "terminal_completed_sale": _field(True, terminal, terminal is True),
        "audit_decision": _field(truth["audit_decision"], audit.decision, audit.decision == truth["audit_decision"]),
    }
    optional = {
        "alacaga_mahsuben": _field(True, extracted.alacaga_mahsuben, extracted.alacaga_mahsuben is True),
        "kdv_rate": _field(truth["kdv_rate"], extracted.kdv_rate, extracted.kdv_rate == truth["kdv_rate"]),
    }
    return {
        "required": required,
        "optional": optional,
        "required_all_passed": all(f["match"] for f in required.values()),
        "optional_all_passed": all(f["match"] for f in optional.values()),
        "required_wrong": [k for k, f in required.items() if f["wrong"]],
        "required_missing": [k for k, f in required.items() if f["missing"]],
    }


def _classify(mode: str, live_page_reached: bool, comparison: dict, reconciliation_status: str) -> tuple[str, list]:
    """Pilot sonucunu sınıflandırır (dürüst). OFFLINE fixture ASLA canlı PASS olmaz."""
    reasons: list[str] = []
    if mode != MODE_LIVE or not live_page_reached:
        reasons.append("no real user-controlled live UYAP browser session was reached in this environment")
        return NOT_RUN, reasons
    if comparison["required_wrong"]:
        reasons.append("incorrect required evidence/decision: " + ", ".join(comparison["required_wrong"]))
        return FAIL, reasons
    if comparison["required_missing"]:
        reasons.append("required evidence not collected: " + ", ".join(comparison["required_missing"]))
        return PARTIAL, reasons
    if reconciliation_status != "reconciled":
        reasons.append(f"same-asset reconciliation {reconciliation_status}; human review required")
        return PARTIAL, reasons
    return PASS, reasons


def verify_pilot(
    artifacts: list[dict],
    file_id: str = "2026/263 Esas",
    mode: str = MODE_OFFLINE,
    live_page_reached: bool = False,
    browser_status: str = "offline",
    source_refs: list | None = None,
    document_access_patterns: list | None = None,
    collection_diagnostics: dict | None = None,
    institution: str | None = None,
) -> dict:
    """SAF doğrulama katmanı: çıkarım → mutabakat → denetim → gerçek-karşılaştırma → sınıflandırma.

    Mutasyon YOK (genuine yazılmaz). OFFLINE fixture için ``mode=offline_simulated`` ve
    ``verification_layer_result`` verilir; ``pilot_outcome`` canlı oturum olmadıkça NOT_RUN kalır.
    Bilinen gerçek çıkarıcıya ENJEKTE EDİLMEZ — değerler artifact metninden çıkarılır.
    """
    ev = extract_evidence(artifacts, institution=institution or KNOWN_TRUTH["institution"], file_id=file_id)
    recon = reconcile(artifacts, institution, file_id)
    audit = audit_candidate(ev, recon)
    comparison = compare_to_truth(ev, audit, file_id)
    outcome, reasons = _classify(mode, live_page_reached, comparison, recon.status)
    verification_layer_result = PASS if (comparison["required_all_passed"] and recon.status == "reconciled"
                                         and audit.decision == ADMISSIBLE_COMPLETED_SALE) else (
        FAIL if comparison["required_wrong"] else PARTIAL)
    return {
        "pilot_name": PILOT_NAME,
        "run_timestamp": _utcnow_iso(),
        "official_file_id": file_id,
        "mode": mode,
        "browser_connection_status": browser_status,
        "live_page_reached": bool(live_page_reached),
        "source_refs": source_refs or [],
        "artifact_types_collected": sorted({a.get("artifact_type") for a in artifacts}),
        "document_access_patterns": document_access_patterns or [],
        "collection_diagnostics": collection_diagnostics or {},
        "extraction_status": ev.extraction_status,
        "extracted_appraisal": ev.appraisal_value,
        "extracted_auction_price": audit.auction_price,
        "terminal_evidence": ev.terminal_status_text,
        "field_extraction": {
            "final_viewer_representation": _final_viewer_state(collection_diagnostics, "final_viewer_representation"),
            "final_viewer_text_available": _final_viewer_state(collection_diagnostics, "final_viewer_text_available"),
            "final_viewer_outcome": _final_viewer_state(collection_diagnostics, "final_viewer_outcome"),
            "auction_price_field_label_found": ev.auction_price_field_label_found,
            "auction_price_candidate_count": ev.auction_price_candidate_count,
            "auction_price_value_relation_strategy": ev.auction_price_value_relation_strategy,
            "appraisal_field_label_found": ev.appraisal_field_label_found,
            "appraisal_candidate_count": ev.appraisal_candidate_count,
            "appraisal_candidates": ev.appraisal_candidates,
            "appraisal_value_relation_strategies": ev.appraisal_value_relation_strategies,
            "settlement_field_label_found": ev.settlement_field_label_found,
            "alacaga_mahsuben_detected": ev.alacaga_mahsuben_detected,
            "settlement_value_relation_strategy": ev.settlement_value_relation_strategy,
            "field_neighborhood": ev.field_neighborhood,
            "source_text_persisted": _final_viewer_state(collection_diagnostics, "source_text_persisted"),
            "source_text_artifact_sha256": _final_viewer_state(collection_diagnostics, "source_text_artifact_sha256"),
            "source_text_artifact_size": _final_viewer_state(collection_diagnostics, "source_text_artifact_size"),
        },
        "native_udf": {
            "native_download_attempted": _final_viewer_state(collection_diagnostics, "native_download_attempted"),
            "native_download_action_resolved": _final_viewer_state(collection_diagnostics, "native_download_action_resolved"),
            "native_download_event_detected": _final_viewer_state(collection_diagnostics, "native_download_event_detected"),
            "native_requested_artifact_type": _final_viewer_state(collection_diagnostics, "native_requested_artifact_type"),
            "native_requested_normalized_label": _final_viewer_state(collection_diagnostics, "native_requested_normalized_label"),
            "native_row_reacquired": _final_viewer_state(collection_diagnostics, "native_row_reacquired"),
            "native_row_reacquired_artifact_type": _final_viewer_state(collection_diagnostics, "native_row_reacquired_artifact_type"),
            "native_row_reacquired_label_match": _final_viewer_state(collection_diagnostics, "native_row_reacquired_label_match"),
            "native_action_owner_same_row": _final_viewer_state(collection_diagnostics, "native_action_owner_same_row"),
            "native_action_owner_semantic_revalidated": _final_viewer_state(collection_diagnostics, "native_action_owner_semantic_revalidated"),
            "native_action_owner_fingerprint_match": _final_viewer_state(collection_diagnostics, "native_action_owner_fingerprint_match"),
            "native_artifact_collected": _final_viewer_state(collection_diagnostics, "native_artifact_collected"),
            "native_artifact_extension": _final_viewer_state(collection_diagnostics, "native_artifact_extension"),
            "native_artifact_size": _final_viewer_state(collection_diagnostics, "native_artifact_size"),
            "native_artifact_sha256": _final_viewer_state(collection_diagnostics, "native_artifact_sha256"),
            "native_container_kind": _final_viewer_state(collection_diagnostics, "native_container_kind"),
            "native_udf_zip_valid": _final_viewer_state(collection_diagnostics, "native_udf_zip_valid"),
            "native_udf_member_names_safe_summary": _final_viewer_state(collection_diagnostics, "native_udf_member_names_safe_summary"),
            "native_udf_content_xml_found": _final_viewer_state(collection_diagnostics, "native_udf_content_xml_found"),
            "native_udf_content_xml_size": _final_viewer_state(collection_diagnostics, "native_udf_content_xml_size"),
            "native_udf_xml_parse_succeeded": _final_viewer_state(collection_diagnostics, "native_udf_xml_parse_succeeded"),
            "native_udf_content_element_found": _final_viewer_state(collection_diagnostics, "native_udf_content_element_found"),
            "native_udf_source_text_available": _final_viewer_state(collection_diagnostics, "native_udf_source_text_available"),
            "native_udf_text_extraction_supported": _final_viewer_state(collection_diagnostics, "native_udf_text_extraction_supported"),
            "native_detected_document_type": _final_viewer_state(collection_diagnostics, "native_detected_document_type"),
            "native_document_type_corroborated": _final_viewer_state(collection_diagnostics, "native_document_type_corroborated"),
            "native_document_type_mismatch": _final_viewer_state(collection_diagnostics, "native_document_type_mismatch"),
            "native_document_type_corroboration_reason": _final_viewer_state(collection_diagnostics, "native_document_type_corroboration_reason"),
            "native_udf_source_relation": _final_viewer_state(collection_diagnostics, "native_udf_source_relation"),
            "native_udf_blocking_reason": _final_viewer_state(collection_diagnostics, "native_udf_blocking_reason"),
        },
        "reconciliation_status": recon.status,
        "reconciliation_matched_on": recon.matched_on,
        "audit_decision": audit.decision,
        "audit_win_over_appraisal": audit.win_over_appraisal,
        "known_truth_comparison": comparison,
        "verification_layer_result": verification_layer_result,
        "pilot_outcome": outcome,
        "blocking_reasons": reasons + (audit.blocking_reasons or []),
    }


def genuine_fingerprint(genuine_path: Path | str | None = None) -> dict:
    """Mutasyon-korumu parmak izi: genuine uyap.json sha256 + sayı + 4-moment SMM vektörü."""
    from ..uyap.admit import _genuine_dir

    path = Path(genuine_path) if genuine_path else _genuine_dir() / "uyap.json"
    data = path.read_bytes() if path.exists() else b""
    records = json.loads(data.decode("utf-8")) if data else []
    smm: dict = {}
    try:
        from ...structural import build_observed_moments, load_genuine_datasets

        g = load_genuine_datasets(directory=path.parent)
        built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
        smm = {k: round(float(v), 9) for k, v in built["moments"].items() if k.startswith(("uyap_win", "kap_log"))}
    except Exception:
        smm = {}
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "genuine_uyap_count": len(records),
        "smm_moments": smm,
    }


def run_pilot(
    cdp_endpoint: str | None = None,
    url: str | None = None,
    genuine_path: Path | str | None = None,
    store_dir: Path | str | None = None,
    report_path: Path | str | None = None,
    offline_artifacts: list[dict] | None = None,
    file_id: str = "2026/263 Esas",
) -> dict:
    """Non-mutating pilot: canlı toplama (ya da offline fixture) → doğrulama → mutasyon-korumu → rapor.

    ASLA admit çağırmaz (8. gözlem oluşmaz). Canlı yol başarısız olursa (Playwright yok / CDP yok /
    sayfa yok) UYDURMA YAPMAZ → NOT_RUN + bloklayıcı neden. Rapor gitignored data yoluna yazılır.
    """
    genuine_file = Path(genuine_path).resolve() if genuine_path else (_genuine_dir() / "uyap.json").resolve()
    output_file = (
        Path(report_path).resolve()
        if report_path
        else (Path(store_dir or "data/ingestion/uyap") / "pilot_report.json").resolve()
    )
    if genuine_file == output_file:
        raise ValueError("report_path must not point to the genuine UYAP evidence file")
    before = genuine_fingerprint(genuine_path)
    artifacts: list[dict] = []
    source_refs: list = []
    doc_patterns: list = []
    coll_diag: dict | None = None
    if offline_artifacts is not None:
        artifacts = offline_artifacts
        mode, live_reached, browser_status = MODE_OFFLINE, False, "offline_fixture"
    else:
        mode, live_reached, browser_status = MODE_LIVE, False, "not_attempted"
        try:
            from .collect import BrowserCollector

            coll = BrowserCollector(cdp_endpoint=cdp_endpoint).collect_record(
                url=url, target_file_id=file_id, target_institution=KNOWN_TRUTH["institution"]
            )
            live_reached, browser_status = True, "connected"
            source_refs = [coll.get("url")]
            doc_patterns = coll.get("document_access_patterns", [])
            coll_diag = coll.get("collection_diagnostics")
            artifacts = [{"artifact_type": "status_card", "text": coll.get("html", ""), "source_ref": coll.get("url")}]
            artifacts += coll.get("documents", [])
        except Exception as exc:  # pragma: no cover - canlı ortam-bağımlı
            browser_status = _classify_browser_error(str(exc))

    report = verify_pilot(
        artifacts, file_id=file_id, mode=mode, live_page_reached=live_reached,
        browser_status=browser_status, source_refs=source_refs, document_access_patterns=doc_patterns,
        collection_diagnostics=coll_diag,
    )
    after = genuine_fingerprint(genuine_path)
    report["mutation_guard"] = {
        "before": before,
        "after": after,
        "genuine_uyap_count_unchanged": before["genuine_uyap_count"] == after["genuine_uyap_count"],
        "uyap_json_unchanged": before["sha256"] == after["sha256"],
        "smm_moments_unchanged": before["smm_moments"] == after["smm_moments"],
        "uyap_sale_prob_absent": "uyap_sale_prob" not in after["smm_moments"],
    }
    path = output_file
    from .io import atomic_write_json, locked

    with locked(path):
        atomic_write_json(path, report)
    report["report_path"] = str(path)
    return report


def _classify_browser_error(msg: str) -> str:
    low = msg.lower()
    if "playwright" in low or "pip install" in low:
        return "playwright_missing"
    if "no_usable_browser_context" in low:
        return "no_usable_browser_context"
    if "no_matching_uyap_page" in low:
        return "no_matching_uyap_page"
    if "no_user_controlled_session" in low:
        return "no_user_controlled_session"
    if "connect" in low or "refused" in low or "econnrefused" in low or "cdp" in low or "9222" in low:
        return "cdp_unavailable"
    return "browser_error"
