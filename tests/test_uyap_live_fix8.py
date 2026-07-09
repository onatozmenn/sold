"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 8 testleri (OFFLINE; ağ/canlı YOK).

Sekizinci gerçek-canlı FAIL: Fix 7 (mantıksal satır sınırı) + Fix 6 (fa-arrow-down→download, fa-eye→view)
CANLI çalıştı; collector auction_result ve sale_notice satırlarının GERÇEK eye kontrolünü çözdü, tıkladı ve
GERÇEK UYAP UDF görüntüleyici sekmeleri açtı (viewer_pages_opened=2). Ama her görüntüleyici:
text=false, iframe=0, canvas=0, image=1 → collector bunu 'unsupported_representation:canvas_image_only'
sayıp resmin KAYNAĞINI incelemedi. İndirme yönergesi YOKtu → Fix 6.1 doğru biçimde TETİKLENMEDİ.

Fix 8: kesin temsil isimlendirmesi (image_only/canvas_only/canvas_and_image), görüntü-eleman kaynak
introspeksiyonu, belge-render aday tespiti ve KESİN kaynak-bayt yakalama (data/blob/same-origin) — OCR YOK.
Görüntü artifact'ı KORUNUR ama metin çıkarımını DESTEKLEMEZ (İhale Bedeli UYDURULMAZ). CANLI PASS DEĞİL.
"""

from __future__ import annotations

import base64
import inspect
import json
import shutil

from sold.ingestion.uyap import (
    audit_candidate,
    classify_document_image_candidate,
    classify_image_src_kind,
    classify_viewer_outcome,
    classify_viewer_representation,
    decode_data_url,
    extract_evidence,
    extraction_supported_for,
    genuine_fingerprint,
    image_mime_to_extension,
    image_source_capture_supported,
    reconcile,
    run_pilot,
    select_viewer_image_candidate,
    viewer_download_instruction_detected,
    viewer_image_candidate_summary,
)
from sold.ingestion.uyap import collect as _collect
from sold.ingestion.uyap.collect import BrowserCollector, _DOC_PRIORITY

TARGET = "2026/263 Esas"
PNG_BYTES = b"\x89PNG\r\n\x1a\nHELLO_IMAGE_BYTES"


# --- A. Kesin temsil isimlendirmesi (Run-8: canvas=0, image=1 → image_only) --------------- #
def test_image_only_not_canvas_image_only():
    assert classify_viewer_representation({"canvas": 0, "image": 1}) == "image_only"


def test_canvas_only_classified_separately():
    assert classify_viewer_representation({"canvas": 3, "image": 0}) == "canvas_only"


def test_canvas_and_image_classified_separately():
    assert classify_viewer_representation({"canvas": 1, "image": 1}) == "canvas_and_image"


def test_representation_precedence_unchanged():
    assert classify_viewer_representation({"text_available": True, "image": 1}) == "dom_text"
    assert classify_viewer_representation({"iframe": 1, "image": 1}) == "iframe"
    assert classify_viewer_representation({"embed": 1, "image": 1}) == "embed_object"
    assert classify_viewer_representation({}) == "unknown"


def test_image_representation_outcome_is_image_backed():
    assert classify_viewer_outcome("", "image_only") == "image_backed"
    assert classify_viewer_outcome("", "canvas_and_image") == "image_backed"
    assert classify_viewer_outcome("", "canvas_only") == "unsupported_representation"


# --- B. Belge-render aday tespiti (dekoratif/ikon reddedilir; global ilk seçilmez) --------- #
_DOC_IMG = {"visible": True, "natural_width": 1000, "natural_height": 1400,
            "rendered_width": 800, "rendered_height": 1100, "viewer_content_scoped": True, "src": "blob:x"}
_LOGO = {"visible": True, "natural_width": 90, "natural_height": 30, "rendered_width": 90,
         "rendered_height": 30, "viewer_content_scoped": False, "src": "/logo.png"}
_TINY = {"visible": True, "natural_width": 16, "natural_height": 16, "viewer_content_scoped": True, "src": "/i.png"}


def test_decorative_logo_is_not_document_candidate():
    c = classify_document_image_candidate({**_LOGO, "src_kind": classify_image_src_kind(_LOGO["src"])})
    assert c["document_image_candidate"] is False


def test_tiny_icon_is_rejected():
    c = classify_document_image_candidate({**_TINY, "src_kind": classify_image_src_kind(_TINY["src"])})
    assert c["document_image_candidate"] is False and c["candidate_reason"] == "too_small_icon_or_logo"


def test_material_visible_scoped_image_is_candidate():
    c = classify_document_image_candidate({**_DOC_IMG, "src_kind": classify_image_src_kind(_DOC_IMG["src"])})
    assert c["document_image_candidate"] is True


def test_image_outside_viewer_content_scope_rejected():
    m = {**_DOC_IMG, "viewer_content_scoped": False, "src_kind": "blob_url"}
    assert classify_document_image_candidate(m)["candidate_reason"] == "outside_viewer_content_scope"


def test_first_image_globally_not_auto_selected():
    # logo İLK; belge İKİNCİ → belge seçilir (global ilk DEĞİL)
    cands = [
        {**_LOGO, "document_image_candidate": False},
        {**_DOC_IMG, "document_image_candidate": True},
    ]
    assert select_viewer_image_candidate(cands) == 1


def test_no_document_candidate_returns_none():
    assert select_viewer_image_candidate([{**_LOGO, "document_image_candidate": False}]) is None


def test_largest_material_document_candidate_selected():
    cands = [
        {"natural_width": 400, "natural_height": 400, "document_image_candidate": True},
        {"natural_width": 1000, "natural_height": 1400, "document_image_candidate": True},
    ]
    assert select_viewer_image_candidate(cands) == 1


# --- C. Kaynak türü + yakalama-desteği ---------------------------------------------------- #
def test_src_kind_classification():
    assert classify_image_src_kind("data:image/png;base64,AAAA") == "data_url"
    assert classify_image_src_kind("blob:https://esatis.uyap.gov.tr/abc") == "blob_url"
    assert classify_image_src_kind("https://esatis.uyap.gov.tr/x.png") == "http_resource"
    assert classify_image_src_kind("/pp/render/9") == "relative_resource"
    assert classify_image_src_kind("") == "empty"


def test_capture_supported_by_kind():
    assert image_source_capture_supported("data_url")["strategy"] == "data_url_decode"
    assert image_source_capture_supported("blob_url")["strategy"] == "blob_scoped_fetch"
    assert image_source_capture_supported("http_resource", True)["strategy"] == "same_origin_page_fetch"
    assert image_source_capture_supported("http_resource", False)["supported"] is False
    assert image_source_capture_supported("unknown")["supported"] is False


def test_data_url_decodes_exact_bytes_and_mime():
    b64 = base64.b64encode(PNG_BYTES).decode()
    data, mime, ext = decode_data_url(f"data:image/png;base64,{b64}")
    assert data == PNG_BYTES and mime == "image/png" and ext == ".png"


def test_data_url_percent_encoded_decodes():
    got = decode_data_url("data:image/svg+xml,%3Csvg%3E%3C/svg%3E")
    assert got is not None and got[0] == b"<svg></svg>" and got[1] == "image/svg+xml"


def test_image_mime_to_extension():
    assert image_mime_to_extension("image/jpeg") == ".jpg"
    assert image_mime_to_extension("image/png") == ".png"
    assert image_mime_to_extension("application/udf") is None


# --- D. Gizlilik-güvenli aday özeti (ham src/blob/data-gövde/evrakId ASLA) ----------------- #
def test_candidate_summary_privacy_safe():
    meta = {**_DOC_IMG, "src": "https://esatis.uyap.gov.tr/pp/render?evrakId=16737826545&mimeType=Udf",
            "same_origin": True}
    summ = viewer_image_candidate_summary(meta, 0)
    dump = json.dumps(summ).lower()
    assert "src" not in summ                        # ham src ANAHTAR olarak yok
    assert "16737826545" not in dump               # evrakId ASLA
    assert "mimetype=udf" not in dump               # ham sorgu ASLA
    assert "esatis.uyap.gov.tr" not in dump         # ham host ASLA
    assert summ["src_kind"] == "http_resource"


def test_candidate_summary_no_data_or_blob_body():
    b64 = base64.b64encode(PNG_BYTES).decode()
    s1 = viewer_image_candidate_summary({**_DOC_IMG, "src": f"data:image/png;base64,{b64}"}, 0)
    s2 = viewer_image_candidate_summary({**_DOC_IMG, "src": "blob:https://esatis.uyap.gov.tr/xyz-abc"}, 1)
    assert b64.lower() not in json.dumps(s1).lower() and "base64" not in json.dumps(s1).lower()
    assert "xyz-abc" not in json.dumps(s2).lower() and s2["src_kind"] == "blob_url"


# --- E. Canlı yakalama orkestrasyonu (sahte görüntüleyici; ağ YOK) ------------------------- #
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
    # Fix 9: _collect_viewer_image artık documents almaz / PROMOTE ETMEZ; capture kaydı döner.
    capture = BrowserCollector()._collect_viewer_image(fake, attempt, {"artifact_type": artifact_type}, diag)
    return attempt, capture, diag


def test_data_url_image_captured_stored_and_provenance(tmp_path, monkeypatch):
    b64 = base64.b64encode(PNG_BYTES).decode()
    fake = _FakeViewerPage([{**_DOC_IMG, "src": f"data:image/png;base64,{b64}"}])
    attempt, capture, diag = _collect_image(fake, "auction_result", tmp_path, monkeypatch)
    assert attempt["viewer_image_source_bytes_captured"] is True
    assert attempt["viewer_image_artifact_collected"] is True   # görüntüleyici-asset yakalandı
    assert attempt["viewer_asset_captured"] is True
    assert attempt["viewer_image_artifact_extension"] == ".png"
    assert attempt["viewer_image_artifact_mime_hint"] == "image/png"
    assert attempt["viewer_image_artifact_size"] == len(PNG_BYTES)
    assert attempt["viewer_image_text_extraction_supported"] is False   # görüntü metin çıkarımı DESTEKLENMEZ
    # Fix 9: TEK yakalama TEK BAŞINA belge kaynağı DEĞİL (kimlik post-loop; document_source False)
    assert attempt["document_source_artifact_collected"] is False
    # KESİN baytlar diske yazıldı (mevcut artifact deposu) — Fix 8 KORUNUR
    stored = list((tmp_path / "artifacts" / "viewer_images").glob("*.png"))
    assert len(stored) == 1 and stored[0].read_bytes() == PNG_BYTES
    # capture kaydı TAM sha döner (kısa prefix DEĞİL); DocumentRow'a doğrudan eklenmez
    assert capture is not None and capture["artifact_type"] == "auction_result"
    assert len(capture["sha256"]) == 64 and capture["extension"] == ".png"


def test_blob_image_captured_via_scoped_fetch(tmp_path, monkeypatch):
    b64 = base64.b64encode(PNG_BYTES).decode()
    fake = _FakeViewerPage([{**_DOC_IMG, "src": "blob:https://esatis.uyap.gov.tr/xyz", "same_origin": True}],
                           fetch={"blob:https://esatis.uyap.gov.tr/xyz": {"ok": True, "b64": b64, "mime": "image/jpeg"}})
    attempt, capture, diag = _collect_image(fake, "sale_notice", tmp_path, monkeypatch)
    assert attempt["viewer_image_source_kind"] == "blob_url"
    assert attempt["viewer_image_source_capture_strategy"] == "blob_scoped_fetch"
    assert attempt["viewer_image_source_bytes_captured"] is True
    assert attempt["viewer_image_artifact_extension"] == ".jpg"
    assert (tmp_path / "artifacts" / "viewer_images").exists()


def test_same_origin_http_image_captured(tmp_path, monkeypatch):
    b64 = base64.b64encode(PNG_BYTES).decode()
    url = "https://esatis.uyap.gov.tr/pp/render"
    fake = _FakeViewerPage([{**_DOC_IMG, "src": url, "same_origin": True}],
                           fetch={url: {"ok": True, "b64": b64, "mime": "image/png"}})
    attempt, capture, diag = _collect_image(fake, "auction_result", tmp_path, monkeypatch)
    assert attempt["viewer_image_source_bytes_captured"] is True
    assert attempt["viewer_image_artifact_size"] == len(PNG_BYTES)


def test_unsupported_source_kind_reports_blocker(tmp_path, monkeypatch):
    fake = _FakeViewerPage([{**_DOC_IMG, "src": "javascript:void(0)", "same_origin": True}])
    attempt, capture, diag = _collect_image(fake, "auction_result", tmp_path, monkeypatch)
    assert attempt["viewer_image_source_capture_supported"] is False
    assert attempt["viewer_image_capture_blocking_reason"].startswith("unsupported_source_kind")
    assert capture is None


def test_no_document_candidate_reports_blocker(tmp_path, monkeypatch):
    fake = _FakeViewerPage([{**_LOGO}])   # yalnız logo → belge adayı yok
    attempt, capture, diag = _collect_image(fake, "auction_result", tmp_path, monkeypatch)
    assert attempt["viewer_image_capture_blocking_reason"] == "no_document_image_candidate"
    assert attempt["viewer_image_document_identity"] == "not_document_candidate"
    assert capture is None


# --- F. Görüntü artifact'ı metin/fiyat çıkarımı İMA ETMEZ; known-truth enjekte YOK -------- #
def test_image_artifact_not_fed_to_text_extraction():
    assert extraction_supported_for(".png") is False and extraction_supported_for(".jpg") is False


def test_image_artifact_does_not_imply_price_or_appraisal():
    img_doc = {"artifact_type": "auction_result", "source_ref": "viewer_image:.png", "extraction_supported": False}
    ev = extract_evidence([img_doc], institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli is None                  # 5715000 ENJEKTE EDİLMEZ
    img_ap = {"artifact_type": "appraisal_report", "source_ref": "viewer_image:.png", "extraction_supported": False}
    ev2 = extract_evidence([img_ap], institution="Ankara", file_id=TARGET)
    assert ev2.appraisal_value is None              # 6800000 ENJEKTE EDİLMEZ


def test_no_ocr_dependency_added():
    src = inspect.getsource(_collect).lower()
    for banned in ("tesseract", "pytesseract", "easyocr", "image_to_string", "ocr("):
        assert banned not in src


# --- G. Fix-6.1 download_required önceliği KORUNUR; görüntü indirme TETİKLEMEZ ------------- #
def test_download_required_precedence_over_image():
    msg = "Evrak Görüntülenemedi, Evrağı indirerek Görüntüleyebilirsiniz."
    assert classify_viewer_outcome(msg, "image_only") == "download_required"


def test_generic_image_does_not_trigger_download_fallback():
    # görüntü-destekli görüntüleyici download_required DEĞİL → same-row fallback TETİKLENMEZ
    assert classify_viewer_outcome("", "image_only") != "download_required"
    assert viewer_download_instruction_detected("") is False


# --- H. auction_result önceliği + sale_notice appraisal-side yolu ------------------------- #
def test_auction_result_priority_first():
    assert _DOC_PRIORITY["auction_result"] < _DOC_PRIORITY["appraisal_report"]
    assert _DOC_PRIORITY["appraisal_report"] < _DOC_PRIORITY["sale_notice"]


def test_sale_notice_is_appraisal_side_reconcilable():
    appraisal_src = {"artifact_type": "sale_notice",
                     "text": "SATIŞ İLANI Muhammen Bedel 6.800.000,00 TL 50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm"}
    auction_src = {"artifact_type": "auction_result",
                   "text": "ARTIRMA SONUÇ TUTANAĞI 50984 Ada 1 Parsel İhale Bedeli: 5.715.000,00 Satış İşlemleri Tamamlandı"}
    rec = reconcile([appraisal_src, auction_src], "Ankara", TARGET)
    assert rec.status == "reconciled"               # sale_notice appraisal-side olarak kabul


# --- I. Non-mutation + freeze ------------------------------------------------------------- #
def test_run_pilot_fix8_offline_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    # OUTCOME A: image-backed viewer → renderer artifact preserved but text extraction unsupported
    artifacts = [
        {"artifact_type": "status_card",
         "text": "Muhammen Bedel 6.800.000,00 TL KDV Oranı : %20 Satış Durumu Satıldı 50984 Ada 1 Parsel",
         "source_ref": "live://index"},
        {"artifact_type": "auction_result", "source_ref": "viewer_image:.png", "extraction_supported": False},
    ]
    r = run_pilot(offline_artifacts=artifacts, genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["pilot_outcome"] in ("NOT_RUN", "FAIL", "PARTIAL")   # görüntü metni çıkarılamadı → PASS DEĞİL
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"] and after["sha256"] == before["sha256"]
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert sum(1 for x in recs if str(x.get("public_record_id")) == TARGET) == 1


def test_result_card_satis_tutari_not_substituted():
    card = {"artifact_type": "status_card",
            "text": "Satış Durumu Satıldı Satış Tutarı 5.715.000,00 TL 50984 Ada 1 Parsel"}
    ev = extract_evidence([card], institution="Ankara", file_id=TARGET)
    au = audit_candidate(ev, reconcile([card], "Ankara", TARGET))
    assert ev.ihale_bedeli is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


def test_structural_freeze_four_moments_no_sale_prob():
    from sold.structural import build_observed_moments, load_genuine_datasets

    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    smm = {k for k in built["moments"] if k.startswith(("uyap_win", "kap_log"))}
    assert smm == {
        "uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd",
        "kap_log_ratio_mean", "kap_log_ratio_sd",
    }
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
