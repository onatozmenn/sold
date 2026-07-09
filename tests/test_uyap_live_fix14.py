"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 14 testleri (OFFLINE; ağ/canlı YOK).

On dördüncü gerçek-canlı FAIL: Run 14 Fix 13'ü CANLI kanıtladı — auction_result native yolu uçtan uca çalıştı
(row reacquired, action_owner_same_row, corroborated; .udf 4406B, detected auction_result), MEVCUT çıkarıcı
İhale Bedeli 5.715.000,0 + ALACAĞA MAHSUBEN çıkardı. Auction-result native yolu CANLI ÇÖZÜLDÜ — DEĞİŞTİRİLMEZ.

Run 14 SADECE sale_notice hâlâ VIEWER yolunda (image_only→unknown→dom_text, timeout_unstable, collected=false)
olduğu için FAIL: appraisal null, kdv null, reconciliation ambiguous, audit MISSING_APPRAISAL. Aynı sale_notice
satırının POZİTİF çözülmüş satır-yerel download eylemi VAR.

Fix 14 = DAR: NATIVE_DOWNLOAD_TYPES'a sale_notice EKLENİR — aynı DocumentRow-bound + pre-click revalidated +
belge-türü korroboreli native UDF yolu sale_notice için de kullanılır (auction_result öncelik-1, sale_notice
öncelik-2). Native UDF parser / auction-result çıkarımı / viewer / structural DEĞİŞMEZ. NO OCR/ML/known-truth.
"""

from __future__ import annotations

import inspect
import io
import json
import shutil
import zipfile

from sold.ingestion.uyap import (
    audit_candidate,
    classify_udf_document_type,
    corroborate_native_document_type,
    extract_evidence,
    extract_udf_source_text,
    genuine_fingerprint,
    reconcile,
    run_pilot,
    select_unique_document_row,
)
from sold.ingestion.uyap import collect as _collect
from sold.ingestion.uyap.collect import NATIVE_DOWNLOAD_TYPES, _DOC_PRIORITY, BrowserCollector

TARGET = "2099/999 Esas"
_SALE_LABEL = "Satış İlanı"
_SALE_LABEL_NORM = "satis ilani"
_AUCTION_LABEL_NORM = "1- artirma sonuc / uzatma tutanagi"


def _content(cdata):
    return ('<?xml version="1.0" encoding="UTF-8"?><template format_id="1.8"><content><![CDATA[\n'
            + cdata + '\n]]></content></template>')


def _make_udf(content_xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("documentproperties.xml", "<properties/>")
        zf.writestr("content.xml", content_xml)
        zf.writestr("sign.sgn", b"SYNTHETIC_SIGNATURE")
    return buf.getvalue()


# Sanitized sentetik kaynaklar (SENTETİK değer; gerçek belge/değer COMMİT EDİLMEZ).
_SALE_NOTICE_SRC = _content(
    "TAŞINMAZIN ELEKTRONİK SATIŞ ORTAMINDA AÇIK ARTIRMA İLANI\n123 Ada 4 Parsel 6 No'lu 5. Kat Mesken\n"
    "Muhammen Bedeli : 2.000.000,00 TL\nKDV Oranı : %20\n"
    "Borçlunun ihale bedelini yatırmaması halinde satış düşer.")
_AUCTION_RESULT_SRC = _content(
    "ARTIRMA SONUÇ TUTANAĞI\n123 Ada 4 Parsel 6 No'lu 5. Kat\n"
    "İhale Bedeli : 1.234.567,89\nÖdenmesi Gereken Bedel : ALACAĞA MAHSUBEN\nSatış İşlemleri Tamamlandı")


# ======================================================================================= #
# A. Native indirme politikası + öncelik (auction_result 1, sale_notice 2)
# ======================================================================================= #
def test_native_download_types_are_auction_result_and_sale_notice_only():
    assert set(NATIVE_DOWNLOAD_TYPES) == {"auction_result", "sale_notice"}


def test_auction_result_priority_before_sale_notice():
    assert _DOC_PRIORITY["auction_result"] < _DOC_PRIORITY["sale_notice"]


def test_appraisal_and_spec_not_native_download_types():
    assert "appraisal_report" not in NATIVE_DOWNLOAD_TYPES and "sale_spec" not in NATIVE_DOWNLOAD_TYPES


# ======================================================================================= #
# B. sale_notice belge-türü korroborasyonu (semantik; değerden DEĞİL)
# ======================================================================================= #
def test_sale_notice_source_corroborates_as_sale_notice():
    c = corroborate_native_document_type(_SALE_NOTICE_SRC, "sale_notice")
    assert c["native_detected_document_type"] == "sale_notice"
    assert c["native_document_type_corroborated"] is True and c["native_document_type_mismatch"] is False


def test_requested_sale_notice_detected_auction_result_is_mismatch():
    c = corroborate_native_document_type(_AUCTION_RESULT_SRC, "sale_notice")
    assert c["native_detected_document_type"] == "auction_result"
    assert c["native_document_type_corroborated"] is False and c["native_document_type_mismatch"] is True


def test_classify_sale_notice_title():
    assert classify_udf_document_type(_SALE_NOTICE_SRC) == "sale_notice"


# ======================================================================================= #
# C. sale_notice AYNI-SATIR native indirme (sahte page/download; DocumentRow-bound)
# ======================================================================================= #
class _FakeDownload:
    def __init__(self, data, fname):
        self._data = data
        self.suggested_filename = fname

    def save_as(self, path):
        from pathlib import Path
        Path(path).write_bytes(self._data)


class _FakeExpectDownload:
    def __init__(self, dl):
        self._dl = dl

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def value(self):
        return self._dl


class _FakeControl:
    def click(self, timeout=None):
        pass


class _FakeDownloadPage:
    def __init__(self, dl):
        self._dl = dl

    def expect_download(self, timeout=None):
        return _FakeExpectDownload(self._dl)


def _native(tmp_path, monkeypatch, udf_bytes, artifact_type, label, rows=None, locate=True):
    monkeypatch.setattr(_collect.store, "DEFAULT_STORE_DIR", tmp_path)
    col = BrowserCollector()
    monkeypatch.setattr(col, "_locate_row_download_action",
                        (lambda p, l, s: _FakeControl()) if locate else (lambda p, l, s: None))
    page = _FakeDownloadPage(_FakeDownload(udf_bytes, "download.udf"))
    attempt: dict = {}
    diag = {"document_collection_failures": 0,
            "recognized_document_rows": rows if rows is not None else [
                {"artifact_type": artifact_type, "normalized_label": label, "logical_row_recognized_type_count": 1}]}
    documents: list = []
    resolution = {"download_action_resolved": True, "download_action": {"kind": "download"}}
    ok = col._collect_native_udf_download(page, label, {"artifact_type": artifact_type},
                                          resolution, attempt, diag, documents)
    return ok, attempt, documents


def test_sale_notice_native_download_bound_and_corroborated(tmp_path, monkeypatch):
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_SALE_NOTICE_SRC), "sale_notice", _SALE_LABEL_NORM)
    assert ok is True
    assert attempt["native_requested_artifact_type"] == "sale_notice"
    assert attempt["native_row_reacquired"] is True
    assert attempt["native_row_reacquired_artifact_type"] == "sale_notice"
    assert attempt["native_action_owner_same_row"] is True
    assert attempt["native_download_event_detected"] is True
    assert attempt["native_artifact_collected"] is True
    assert attempt["native_udf_source_text_available"] is True
    assert attempt["native_detected_document_type"] == "sale_notice"
    assert attempt["native_document_type_corroborated"] is True and attempt["native_document_type_mismatch"] is False
    assert attempt["document_source_artifact_collected"] is True
    assert documents and documents[0]["artifact_type"] == "sale_notice"
    assert documents[0]["source_ref"].startswith("native_udf:")


def test_sale_notice_wrong_row_click_impossible_ambiguous(tmp_path, monkeypatch):
    rows = [{"artifact_type": "sale_notice", "normalized_label": _SALE_LABEL_NORM, "logical_row_recognized_type_count": 1},
            {"artifact_type": "sale_notice", "normalized_label": _SALE_LABEL_NORM, "logical_row_recognized_type_count": 1}]
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_SALE_NOTICE_SRC), "sale_notice", _SALE_LABEL_NORM, rows=rows)
    assert ok is False and attempt["native_row_reacquired"] is False
    assert attempt["native_udf_blocking_reason"] == "ambiguous_or_unresolved_row_reacquisition"
    assert not documents


def test_sale_notice_requires_uniquely_located_same_row_control(tmp_path, monkeypatch):
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_SALE_NOTICE_SRC), "sale_notice", _SALE_LABEL_NORM, locate=False)
    assert ok is False and attempt["native_udf_blocking_reason"] == "same_row_download_control_not_located_uniquely"


def test_requested_sale_notice_detected_auction_not_promoted(tmp_path, monkeypatch):
    # sale_notice istendi ama indirilen UDF auction_result → PROMOTE EDİLMEZ (korroborasyon reddeder)
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_AUCTION_RESULT_SRC), "sale_notice", _SALE_LABEL_NORM)
    assert ok is False
    assert attempt["native_detected_document_type"] == "auction_result"
    assert attempt["native_document_type_mismatch"] is True
    assert not documents
    assert attempt["native_artifact_collected"] is True     # baytlar tanı için KORUNUR


def test_sale_notice_unique_row_selected():
    rows = [{"artifact_type": "auction_result", "normalized_label": _AUCTION_LABEL_NORM, "logical_row_recognized_type_count": 1},
            {"artifact_type": "sale_notice", "normalized_label": _SALE_LABEL_NORM, "logical_row_recognized_type_count": 1}]
    r = select_unique_document_row(rows, "sale_notice", _SALE_LABEL)
    assert r is not None and r["artifact_type"] == "sale_notice"


# ======================================================================================= #
# D. Korrobore sale_notice kaynağı MEVCUT çıkarıcıya beslenir (appraisal + KDV)
# ======================================================================================= #
def test_corroborated_sale_notice_feeds_extract_evidence():
    text, _ = extract_udf_source_text(_make_udf(_SALE_NOTICE_SRC))
    ev = extract_evidence([{"artifact_type": "sale_notice", "text": text}], institution="Ankara", file_id=TARGET)
    assert ev.appraisal_value == 2_000_000.0 and ev.appraisal_candidate_count == 1
    assert ev.kdv_rate == 20.0
    assert ev.auction_price_field_label_found is False      # prose 'ihale bedelini' etiket SAYILMAZ


def test_sale_notice_appraisal_remains_one_candidate():
    text, _ = extract_udf_source_text(_make_udf(_SALE_NOTICE_SRC))
    ev = extract_evidence([{"artifact_type": "sale_notice", "text": text}], institution="Ankara", file_id=TARGET)
    assert ev.appraisal_candidates == [2_000_000.0]


def test_auction_result_extraction_unchanged():
    text, _ = extract_udf_source_text(_make_udf(_AUCTION_RESULT_SRC))
    ev = extract_evidence([{"artifact_type": "auction_result", "text": text}], institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli == 1_234_567.89 and ev.alacaga_mahsuben is True


def test_native_auction_plus_sale_notice_admissible():
    au_text, _ = extract_udf_source_text(_make_udf(_AUCTION_RESULT_SRC))
    sn_text, _ = extract_udf_source_text(_make_udf(_SALE_NOTICE_SRC))
    arts = [{"artifact_type": "auction_result", "text": au_text},
            {"artifact_type": "sale_notice", "text": sn_text},
            {"artifact_type": "status_card", "text": "Satıldı Satış İşlemleri Tamamlandı"}]
    ev = extract_evidence(arts, institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli == 1_234_567.89 and ev.appraisal_value == 2_000_000.0 and ev.kdv_rate == 20.0
    au = audit_candidate(ev, reconcile(arts, "Ankara", TARGET))
    assert au.auction_price == 1_234_567.89 and au.decision == "ADMISSIBLE_COMPLETED_SALE"


# ======================================================================================= #
# E. Native başarısında viewer beklemesi GEREKMEZ; UDF parser DEĞİŞMEZ
# ======================================================================================= #
def test_native_success_does_not_require_viewer(tmp_path, monkeypatch):
    # _collect_native_udf_download True dönerse _collect_from_container 'continue' eder (viewer yok).
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_SALE_NOTICE_SRC), "sale_notice", _SALE_LABEL_NORM)
    assert ok is True   # native başarı → çağıran viewer'a düşmez
    assert "viewer_ready_state" not in attempt  # native başarı yolunda viewer kararlılık gözlemi ÇALIŞMAZ


def test_native_udf_parser_unchanged():
    src = inspect.getsource(_collect.extract_udf_source_text)
    assert "extractall" not in src and "is_zipfile" in src


# ======================================================================================= #
# F. no-OCR/ML + non-mutation + yapısal donma
# ======================================================================================= #
def test_no_ocr_no_ml():
    src = inspect.getsource(_collect).lower()
    for banned in ("tesseract", "pytesseract", "easyocr", "ocr(", "sklearn", "torch", "tensorflow"):
        assert banned not in src


def test_run_pilot_fix14_offline_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    au_text, _ = extract_udf_source_text(_make_udf(_AUCTION_RESULT_SRC))
    sn_text, _ = extract_udf_source_text(_make_udf(_SALE_NOTICE_SRC))
    arts = [{"artifact_type": "auction_result", "text": au_text, "source_ref": "native_udf:.udf"},
            {"artifact_type": "sale_notice", "text": sn_text, "source_ref": "native_udf:.udf"},
            {"artifact_type": "status_card", "text": "Satıldı Satış İşlemleri Tamamlandı"}]
    r = run_pilot(offline_artifacts=arts, genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["pilot_outcome"] in ("NOT_RUN", "FAIL", "PARTIAL")
    assert "sale_notice" in r["artifact_types_collected"]
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"] and after["sha256"] == before["sha256"]


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
