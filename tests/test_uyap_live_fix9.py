"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 9 testleri (OFFLINE; ağ/canlı YOK).

Dokuzuncu gerçek-canlı FAIL: Fix 8 KESİN kaynak-bayt yakalamayı CANLI kanıtladı — auction_result ve
sale_notice görüntüleyicileri image_only → image_backed → same_origin_page_fetch ile .png yakalandı.
AMA HER İKİ GÖRÜNTÜLEYİCİ BYTE-IDENTICAL PNG üretti (aynı size + aynı sha256) — mantıksal olarak AYRI
iki resmî belge (Artırma Sonuç / Uzatma Tutanağı vs Satış İlanı) için aynı bayt → YÜKSEK-GÜVEN
generic/shared görüntüleyici-asset yanlış-pozitifi. Fix 8 ikisini de artifact_types_collected'e (unsafe)
promote etti.

Fix 9: (1) SINIRLI görüntüleyici hazır-durum gözlemi (ilk uygun görüntüyü anında yakalama; bounded poll,
unbounded sleep YOK); (2) görüntüleyici-asset ≠ belge-kaynak ayrımı; (3) belge-render kimlik durumu;
(4) cross-document TAM-SHA duplicate guard; (5) promosyon kısıtı (yalnız document_specific promote edilir).
OCR YOK; known-truth ENJEKTE EDİLMEZ; Fix 8 bayt yakalama KORUNUR. CANLI PASS DEĞİL.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import shutil

from sold.ingestion.uyap import (
    audit_candidate,
    classify_viewer_image_document_identity,
    classify_viewer_outcome,
    classify_viewer_ready_state,
    decode_data_url,
    detect_cross_document_image_duplicates,
    extract_evidence,
    extraction_supported_for,
    genuine_fingerprint,
    image_source_capture_supported,
    reconcile,
    resolve_viewer_image_identities,
    run_pilot,
    viewer_download_instruction_detected,
    viewer_image_fingerprint,
    viewer_observation_signature,
)
from sold.ingestion.uyap import collect as _collect
from sold.ingestion.uyap.collect import (
    VIEWER_STABILIZATION_MAX_OBSERVATIONS,
    VIEWER_STABILIZATION_MIN_OBSERVATIONS,
    VIEWER_STABILIZATION_POLL_MS,
    BrowserCollector,
)

TARGET = "2026/263 Esas"
PNG_BYTES = b"\x89PNG\r\n\x1a\nRUN9_298x298_IDENTICAL_VIEWER_ASSET"
PNG_SHA = hashlib.sha256(PNG_BYTES).hexdigest()

# Run-9 ölçülen materyal, görünür, görüntüleyici-kapsamlı görüntü (298x298 doğal).
_RUN9_IMG = {"visible": True, "natural_width": 298, "natural_height": 298,
             "rendered_width": 100, "rendered_height": 120, "viewer_content_scoped": True,
             "same_origin": True}


# ======================================================================================= #
# A. Cross-document TAM-SHA duplicate guard (Run-9'un ölçülen çekirdeği)
# ======================================================================================= #
def test_same_bytes_two_artifact_types_triggers_cross_document_duplicate():
    caps = [{"artifact_type": "auction_result", "sha256": PNG_SHA, "extension": ".png"},
            {"artifact_type": "sale_notice", "sha256": PNG_SHA, "extension": ".png"}]
    dup = detect_cross_document_image_duplicates(caps)
    assert dup["duplicate"] is True
    assert dup["duplicate_artifact_types"] == ["auction_result", "sale_notice"]
    assert PNG_SHA in dup["duplicate_shas"]


def test_exact_sha_equality_used_not_short_prefix():
    # Aynı 16-karakter prefix ama FARKLI tam sha → cross-document DEĞİL (tam-SHA karşılaştırması).
    sha_a = "074e28d977997b50" + "a" * 48
    sha_b = "074e28d977997b50" + "b" * 48
    assert sha_a[:16] == sha_b[:16] and sha_a != sha_b
    dup = detect_cross_document_image_duplicates(
        [{"artifact_type": "auction_result", "sha256": sha_a},
         {"artifact_type": "sale_notice", "sha256": sha_b}])
    assert dup["duplicate"] is False


def test_same_artifact_type_repeat_is_not_cross_document():
    # Aynı türün kendini tekrarı (ayrık tür yok) → cross-document DEĞİL.
    dup = detect_cross_document_image_duplicates(
        [{"artifact_type": "auction_result", "sha256": PNG_SHA},
         {"artifact_type": "auction_result", "sha256": PNG_SHA}])
    assert dup["duplicate"] is False


def test_distinct_bytes_no_duplicate():
    dup = detect_cross_document_image_duplicates(
        [{"artifact_type": "auction_result", "sha256": "a" * 64},
         {"artifact_type": "sale_notice", "sha256": "b" * 64}])
    assert dup["duplicate"] is False and dup["duplicate_artifact_types"] == []


# ======================================================================================= #
# B. Belge-render kimlik durumu (deterministik; ML YOK)
# ======================================================================================= #
def test_shared_cross_document_asset_identity():
    dup = detect_cross_document_image_duplicates(
        [{"artifact_type": "auction_result", "sha256": PNG_SHA},
         {"artifact_type": "sale_notice", "sha256": PNG_SHA}])
    cap = {"artifact_type": "auction_result", "sha256": PNG_SHA, "extension": ".png"}
    assert classify_viewer_image_document_identity(cap, dup) == "shared_cross_document_asset"


def test_lone_capture_is_renderer_asset_unresolved():
    # Tek yakalama, cross-document yok, pozitif belge-render ilişkisi yok → çözülmemiş (promote DEĞİL).
    cap = {"artifact_type": "auction_result", "sha256": "c" * 64, "extension": ".png"}
    dup = detect_cross_document_image_duplicates([cap])
    assert classify_viewer_image_document_identity(cap, dup) == "renderer_asset_unresolved"


def test_capture_without_sha_is_not_document_candidate():
    assert classify_viewer_image_document_identity({"artifact_type": "auction_result"}, {}) == "not_document_candidate"


def test_visible_material_scoped_alone_insufficient_for_document_specific():
    # Görünür + materyal + kapsamlı + bayt-yakalandı YETMEZ — tek başına belge-özgü kimlik vermez.
    cap = {"artifact_type": "auction_result", "sha256": "d" * 64, "extension": ".png",
           "visible": True, "natural_width": 298, "natural_height": 298, "viewer_content_scoped": True}
    assert classify_viewer_image_document_identity(cap, {}) == "renderer_asset_unresolved"


def test_positive_association_marks_document_specific():
    cap = {"artifact_type": "auction_result", "sha256": "e" * 64, "extension": ".png"}
    assert classify_viewer_image_document_identity(cap, {}, association_supported=True) == "document_specific"


def test_generic_flag_marks_generic_viewer_asset():
    cap = {"artifact_type": "auction_result", "sha256": "f" * 64, "generic_viewer_asset": True}
    assert classify_viewer_image_document_identity(cap, {}) == "generic_viewer_asset"


# ======================================================================================= #
# C. resolve_viewer_image_identities + promosyon kısıtı + artifact_types_collected
# ======================================================================================= #
def _promoted_documents(captures):
    """collect.py döngü-sonrası promosyon mantığını (public çözücü üzerinden) sadık biçimde yansıtır."""
    rez = resolve_viewer_image_identities(captures)
    return [{"artifact_type": c["artifact_type"], "source_ref": f"viewer_image:{c['extension']}",
             "extraction_supported": False}
            for c, r in zip(captures, rez) if r["promote_as_document_source"]], rez


def test_shared_asset_not_promoted_as_both_document_sources():
    caps = [{"artifact_type": "auction_result", "sha256": PNG_SHA, "extension": ".png"},
            {"artifact_type": "sale_notice", "sha256": PNG_SHA, "extension": ".png"}]
    docs, rez = _promoted_documents(caps)
    assert docs == []
    assert all(r["viewer_image_document_identity"] == "shared_cross_document_asset" for r in rez)
    assert all(r["promote_as_document_source"] is False for r in rez)
    assert all(r["cross_document_duplicate"] is True for r in rez)


def test_shared_asset_absent_from_artifact_types_collected_but_status_card_remains():
    from sold.ingestion.uyap import verify_pilot

    caps = [{"artifact_type": "auction_result", "sha256": PNG_SHA, "extension": ".png"},
            {"artifact_type": "sale_notice", "sha256": PNG_SHA, "extension": ".png"}]
    docs, _ = _promoted_documents(caps)
    artifacts = [{"artifact_type": "status_card", "text": "Satış Durumu Satıldı", "source_ref": "live://index"}] + docs
    rep = verify_pilot(artifacts, file_id=TARGET, institution="Ankara")
    assert "status_card" in rep["artifact_types_collected"]          # status_card KORUNUR
    assert "auction_result" not in rep["artifact_types_collected"]   # shared asset PROMOTE EDİLMEZ
    assert "sale_notice" not in rep["artifact_types_collected"]


def test_document_specific_capture_is_promoted():
    caps = [{"artifact_type": "auction_result", "sha256": "1" * 64, "extension": ".png",
             "document_render_association_supported": True}]
    docs, rez = _promoted_documents(caps)
    assert len(docs) == 1 and docs[0]["artifact_type"] == "auction_result"
    assert rez[0]["viewer_image_document_identity"] == "document_specific"


# ======================================================================================= #
# D. Fix 8 bayt yakalama KORUNUR ama TEK yakalama PROMOTE ETMEZ (görüntüleyici-asset ayrımı)
# ======================================================================================= #
class _FakeViewerPage:
    def __init__(self, images, fetch=None):
        self._images = images
        self._fetch = fetch or {}

    def eval_on_selector_all(self, selector, js):
        return list(self._images)

    def evaluate(self, js, u):
        return self._fetch.get(u, {"ok": False})


def _collect_image(fake, artifact_type, tmp_path, monkeypatch):
    monkeypatch.setattr(_collect.store, "DEFAULT_STORE_DIR", tmp_path)
    attempt = {"artifact_collected": False}
    diag = {"document_collection_failures": 0}
    capture = BrowserCollector()._collect_viewer_image(fake, attempt, {"artifact_type": artifact_type}, diag)
    return attempt, capture, diag


def test_lone_image_capture_preserved_but_not_promoted(tmp_path, monkeypatch):
    b64 = base64.b64encode(PNG_BYTES).decode()
    fake = _FakeViewerPage([{**_RUN9_IMG, "src": f"data:image/png;base64,{b64}"}])
    attempt, capture, diag = _collect_image(fake, "auction_result", tmp_path, monkeypatch)
    # Fix 8 bayt yakalama KORUNUR
    assert attempt["viewer_image_source_bytes_captured"] is True
    assert attempt["viewer_asset_captured"] is True
    stored = list((tmp_path / "artifacts" / "viewer_images").glob("*.png"))
    assert len(stored) == 1 and stored[0].read_bytes() == PNG_BYTES
    # Fix 9: TEK yakalama TEK BAŞINA belge-kaynak DEĞİL (promosyon post-loop kimliğe bağlı)
    assert attempt["document_source_artifact_collected"] is False
    assert capture is not None and capture["sha256"] == PNG_SHA and len(capture["sha256"]) == 64


def test_image_source_bytes_unchanged_by_identity_guard(tmp_path, monkeypatch):
    b64 = base64.b64encode(PNG_BYTES).decode()
    fake = _FakeViewerPage([{**_RUN9_IMG, "src": f"data:image/png;base64,{b64}"}])
    attempt, capture, diag = _collect_image(fake, "sale_notice", tmp_path, monkeypatch)
    stored = list((tmp_path / "artifacts" / "viewer_images").glob("*.png"))[0]
    assert stored.read_bytes() == PNG_BYTES                 # kimlik guard baytları DEĞİŞTİRMEZ
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == PNG_SHA == capture["sha256"]


def test_full_run9_reproduction_two_viewers_same_bytes(tmp_path, monkeypatch):
    # İki ayrı görüntüleyiciden AYNI baytlar → capture kayıtları → shared_cross_document_asset → promote YOK.
    b64 = base64.b64encode(PNG_BYTES).decode()
    fake_a = _FakeViewerPage([{**_RUN9_IMG, "src": f"data:image/png;base64,{b64}"}])
    fake_b = _FakeViewerPage([{**_RUN9_IMG, "src": f"data:image/png;base64,{b64}"}])
    att_a, cap_a, _ = _collect_image(fake_a, "auction_result", tmp_path, monkeypatch)
    att_b, cap_b, _ = _collect_image(fake_b, "sale_notice", tmp_path, monkeypatch)
    assert cap_a["sha256"] == cap_b["sha256"]              # Run-9 ölçülen bayt-özdeşliği
    docs, rez = _promoted_documents([cap_a, cap_b])
    assert docs == []                                      # hiçbiri belge kaynağı olarak promote edilmez
    assert {r["viewer_image_document_identity"] for r in rez} == {"shared_cross_document_asset"}


# ======================================================================================= #
# E. SINIRLI görüntüleyici hazır-durum gözlemi (SAF karar; ilk görüntü anında promote DEĞİL)
# ======================================================================================= #
def _img_obs(fp, rep="image_only", cnt=1, dim="298x298", kind="http_resource", **kw):
    return {"representation": rep, "candidate_count": cnt, "selected_dimension": dim,
            "selected_src_kind": kind, "selected_fingerprint": fp,
            "download_required": kw.get("download_required", False),
            "viewer_error": kw.get("viewer_error", False)}


def test_stable_image_representation_across_bounded_observations():
    ready = classify_viewer_ready_state([_img_obs("A"), _img_obs("A")])
    assert ready["ready_state"] == "stable_image_representation"
    assert ready["transition_detected"] is False


def test_stable_text_representation():
    txt = {"representation": "dom_text", "candidate_count": 0, "selected_dimension": None,
           "selected_src_kind": None, "selected_fingerprint": None}
    ready = classify_viewer_ready_state([txt, txt])
    assert ready["ready_state"] == "stable_text_representation"


def test_placeholder_then_changed_final_image_stabilizes():
    # T1 placeholder (fp A) → T2/T3 belge-render (fp B) → SON kararlı aday (B) yakalanır.
    ready = classify_viewer_ready_state([_img_obs("A"), _img_obs("B"), _img_obs("B")])
    assert ready["ready_state"] == "stable_image_representation"
    assert ready["transition_detected"] is True            # placeholder → doc geçişi ölçüldü


def test_unstable_changing_fingerprint_times_out():
    ready = classify_viewer_ready_state([_img_obs("A"), _img_obs("B"), _img_obs("C")])
    assert ready["ready_state"] == "timeout_unstable"
    assert ready["transition_detected"] is True


def test_first_qualifying_image_not_immediately_stable():
    # Tek gözlem YETMEZ (ilk uygun görüntü anında kararlı/promote sayılmaz).
    ready = classify_viewer_ready_state([_img_obs("A")])
    assert ready["ready_state"] == "timeout_unstable"
    assert ready["blocking_reason"]


def test_no_observations_times_out():
    ready = classify_viewer_ready_state([])
    assert ready["ready_state"] == "timeout_unstable" and ready["blocking_reason"] == "no_viewer_observation"


def test_download_required_interrupts_stabilization_precedence():
    # İndirme-gerekli, görüntü mevcut olsa bile kararlılığı KESER (Fix 6.1 önceliği).
    ready = classify_viewer_ready_state([_img_obs("A"), _img_obs("A", download_required=True)])
    assert ready["ready_state"] == "download_required"


def test_viewer_error_interrupts_stabilization():
    ready = classify_viewer_ready_state([_img_obs("A", viewer_error=True), _img_obs("A")])
    assert ready["ready_state"] == "viewer_error"


def test_stable_but_unsupported_representation_is_timeout():
    iframe = {"representation": "iframe", "candidate_count": 0, "selected_dimension": None,
              "selected_src_kind": None, "selected_fingerprint": None}
    ready = classify_viewer_ready_state([iframe, iframe])
    assert ready["ready_state"] == "timeout_unstable"


def test_stabilization_polling_is_bounded_no_unbounded_sleep():
    assert 2 <= VIEWER_STABILIZATION_MIN_OBSERVATIONS <= VIEWER_STABILIZATION_MAX_OBSERVATIONS <= 6
    assert 0 < VIEWER_STABILIZATION_POLL_MS <= 1000       # kısa sınırlı bekleme
    src = inspect.getsource(BrowserCollector._observe_viewer_stabilization)
    assert "wait_for_timeout" in src                       # bounded Playwright bekleme
    assert "while True" not in src                         # sonsuz döngü YOK
    for banned in ("time.sleep", "start-sleep", "asyncio.sleep"):
        assert banned not in src.lower()                   # unbounded/keyfi sleep YOK


def test_viewer_observation_signature_privacy_safe():
    sig = viewer_observation_signature(_img_obs("A"))
    assert sig == ("image_only", 1, "298x298", "http_resource", "A")
    # src_kind ("http_resource") güvenli sınıf etiketidir; ham URL/sorgu/evrakId SIZMAZ.
    for x in sig:
        s = str(x or "")
        assert "://" not in s and "evrakId" not in s and "?" not in s


# ======================================================================================= #
# F. DOM parmak izi güvenli (ham URL/bayt SIZMAZ)
# ======================================================================================= #
def test_viewer_image_fingerprint_is_safe_and_deterministic():
    a = viewer_image_fingerprint({"src": "https://esatis.uyap.gov.tr/pp/x?evrakId=SECRET",
                                  "natural_width": 298, "natural_height": 298})
    b = viewer_image_fingerprint({"src": "https://esatis.uyap.gov.tr/pp/y?evrakId=OTHER",
                                  "natural_width": 298, "natural_height": 298})
    assert a == b                                          # aynı boyut/kind → aynı parmak izi
    assert "SECRET" not in a and "evrakId" not in a and len(a) == 16
    c = viewer_image_fingerprint({"src": "https://esatis.uyap.gov.tr/pp/x", "natural_width": 1000,
                                  "natural_height": 1400})
    assert c != a                                          # farklı boyut → farklı parmak izi


def test_fingerprint_does_not_leak_data_url_body():
    fp = viewer_image_fingerprint({"src": "data:image/png;base64,SECRETBODY", "natural_width": 10,
                                   "natural_height": 10})
    assert "SECRETBODY" not in (fp or "") and len(fp) == 16


# ======================================================================================= #
# G. Fix 8 yakalama stratejileri KORUNUR (data / blob / same-origin)
# ======================================================================================= #
def test_same_origin_capture_strategy_preserved():
    cap = image_source_capture_supported("http_resource", same_origin=True)
    assert cap["supported"] is True and cap["strategy"] == "same_origin_page_fetch"


def test_cross_origin_still_unsupported():
    cap = image_source_capture_supported("http_resource", same_origin=False)
    assert cap["supported"] is False


def test_blob_capture_strategy_preserved():
    assert image_source_capture_supported("blob_url")["strategy"] == "blob_scoped_fetch"


def test_data_url_capture_preserved_exact_bytes():
    b64 = base64.b64encode(PNG_BYTES).decode()
    data, mime, ext = decode_data_url(f"data:image/png;base64,{b64}")
    assert data == PNG_BYTES and mime == "image/png" and ext == ".png"


# ======================================================================================= #
# H. OCR YOK + known-truth ENJEKTE YOK + görüntü metin çıkarımına verilmez
# ======================================================================================= #
def test_no_ocr_dependency_added():
    src = inspect.getsource(_collect).lower()
    for banned in ("tesseract", "pytesseract", "easyocr", "image_to_string", "ocr(", "screenshot"):
        assert banned not in src


def test_image_artifact_not_fed_to_text_extraction():
    assert extraction_supported_for(".png") is False and extraction_supported_for(".jpg") is False


def test_shared_asset_run_does_not_inject_price_or_appraisal():
    # Shared asset promote edilmedi → belge yok → İhale Bedeli/appraisal ENJEKTE EDİLMEZ.
    caps = [{"artifact_type": "auction_result", "sha256": PNG_SHA, "extension": ".png"},
            {"artifact_type": "sale_notice", "sha256": PNG_SHA, "extension": ".png"}]
    docs, _ = _promoted_documents(caps)
    artifacts = [{"artifact_type": "status_card",
                  "text": "Muhammen Bedel 6.800.000,00 TL Satış Durumu Satıldı", "source_ref": "live://index"}] + docs
    ev = extract_evidence(artifacts, institution="Ankara", file_id=TARGET)
    au = audit_candidate(ev, reconcile(artifacts, "Ankara", TARGET))
    assert ev.ihale_bedeli is None                          # 5715000 ENJEKTE EDİLMEZ
    assert au.decision != "ADMISSIBLE_COMPLETED_SALE"


def test_known_truth_not_injected_from_image_doc():
    img = {"artifact_type": "auction_result", "source_ref": "viewer_image:.png", "extraction_supported": False}
    ev = extract_evidence([img], institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli is None


# ======================================================================================= #
# I. Fix 6.1 indirme-gerekli önceliği + auction önceliği KORUNUR
# ======================================================================================= #
def test_download_required_precedence_over_image_backed():
    msg = "Evrak Görüntülenemedi, Evrağı indirerek Görüntüleyebilirsiniz."
    assert classify_viewer_outcome(msg, "image_only") == "download_required"


def test_generic_image_alone_does_not_trigger_download():
    assert viewer_download_instruction_detected("") is False
    assert classify_viewer_outcome("", "image_only") == "image_backed"


def test_auction_result_priority_preserved():
    from sold.ingestion.uyap.collect import _DOC_PRIORITY

    assert _DOC_PRIORITY["auction_result"] < _DOC_PRIORITY["appraisal_report"] < _DOC_PRIORITY["sale_notice"]


# ======================================================================================= #
# J. Non-mutation + yapısal donma (pilot ASLA admit; sayı 7; SMM 4 moment; TOKİ external)
# ======================================================================================= #
def test_run_pilot_fix9_shared_asset_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    # OUTCOME B: byte-identical shared viewer asset → NEITHER row promoted → yalnız status_card.
    artifacts = [{"artifact_type": "status_card",
                  "text": "Muhammen Bedel 6.800.000,00 TL KDV Oranı : %20 Satış Durumu Satıldı 50984 Ada 1 Parsel",
                  "source_ref": "live://index"}]
    r = run_pilot(offline_artifacts=artifacts, genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["pilot_outcome"] in ("NOT_RUN", "FAIL", "PARTIAL")   # shared asset → auction kanıtı YOK → PASS DEĞİL
    assert "auction_result" not in r["artifact_types_collected"]
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"] and after["sha256"] == before["sha256"]
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert sum(1 for x in recs if str(x.get("public_record_id")) == TARGET) == 1


def test_structural_freeze_four_moments_no_sale_prob():
    from sold.structural import build_observed_moments, load_genuine_datasets

    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    smm = {k for k in built["moments"] if k.startswith(("uyap_win", "kap_log"))}
    assert smm == {"uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd",
                   "kap_log_ratio_mean", "kap_log_ratio_sd"}
    assert "uyap_sale_prob" not in built["moments"]


def test_toki_external_zero_moments_and_conditional_on_trade():
    from sold.structural import (
        DEFAULT_FREE,
        PRICE_ESTIMATE_CONDITION,
        StructuralParams,
        build_observed_moments,
        context_from_datasets,
        load_genuine_datasets,
        source_jacobian_ranks,
    )

    assert PRICE_ESTIMATE_CONDITION == "conditional_on_trade"
    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    ctx = context_from_datasets(g["uyap"], g["kap"])
    sj = source_jacobian_ranks(StructuralParams(), ctx, DEFAULT_FREE, built["moments"], built["provenance"])
    assert sj["J_TOKI"]["rank"] == 0 and sj["J_TOKI"]["n_moments"] == 0
