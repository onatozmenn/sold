"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 13 testleri (OFFLINE; ağ/canlı YOK).

On üçüncü gerçek-canlı FAIL: Run 13 native UDF yolunun TEKNİK olarak çalıştığını kanıtladı (download event,
.udf 12109B, zip_valid, content.xml, xml parse, content element, source_text — hepsi TRUE). AMA otomatik
auction_result yolu SALE_NOTICE UDF'ini indirip artifact_type=auction_result olarak MİSBIND etti. Byte-bayt
inceleme: otomatik artifact başlığı "TAŞINMAZIN ELEKTRONİK SATIŞ ORTAMINDA AÇIK ARTIRMA İLANI" (=sale_notice),
elle indirilen doğru artifact ise "Artırma Sonuç Tutanağı" + İhale Bedeli + Ödenmesi Gereken Bedel. Ayrıca
sale-notice prose'undaki "ihale bedelini/ihale bedelinin" auction_price_field_label_found=true (count 0) yaptı.

Fix 13: (a) DocumentRow KİMLİĞİ native indirme boyunca korunur (select_unique_document_row; belirsiz→indirme
YOK); (b) satır-yerel TEKİL download kontrolü (çapraz-satır/ilk/global/Nth YOK); (c) native belge-türü
KORROBORASYONU (classify_udf_document_type/corroborate) — istenen tür uyuşmazsa PROMOTE EDİLMEZ; (d) açık
İhale Bedeli alan-etiketi tam-sözcük (çekimli prose etiket-bulundu SAYILMAZ). NO OCR/ML/known-truth. PASS DEĞİL.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import shutil
import zipfile

import pytest

from sold.ingestion.uyap import (
    classify_udf_document_type,
    corroborate_native_document_type,
    extract_evidence,
    extract_udf_source_text,
    genuine_fingerprint,
    run_pilot,
    select_unique_document_row,
)
from sold.ingestion.uyap import collect as _collect
from sold.ingestion.uyap import extract as _extract
from sold.ingestion.uyap.collect import BrowserCollector

TARGET = "2099/999 Esas"
_AUCTION_LABEL = "1- Artırma Sonuç / Uzatma Tutanağı"
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


# Sanitized sentetik kaynaklar (SENTETİK değer/başlık; gerçek belge/değer COMMİT EDİLMEZ).
_AUCTION_RESULT_SRC = _content(
    "ARTIRMA SONUÇ TUTANAĞI\n123 Ada 4 Parsel 6 No'lu 5. Kat\n"
    "İhale Bedeli : 1.234.567,89\nÖdenmesi Gereken Bedel : ALACAĞA MAHSUBEN\nSatış İşlemleri Tamamlandı")
_SALE_NOTICE_SRC = _content(
    "TAŞINMAZIN ELEKTRONİK SATIŞ ORTAMINDA AÇIK ARTIRMA İLANI\n123 Ada 4 Parsel 6 No'lu 5. Kat Mesken\n"
    "Kıymeti : 2.000.000,00 TL\nKDV Oranı : %20\n"
    "Borçlunun ihale bedelini yatırmaması halinde ihale bedelinin tahsili için işlem yapılır.")


# ======================================================================================= #
# E. DocumentRow kimlik korunumu — select_unique_document_row
# ======================================================================================= #
def _row(atype, label, count=1):
    return {"artifact_type": atype, "normalized_label": label, "logical_row_recognized_type_count": count}


def test_unique_row_selected_by_identity():
    rows = [_row("sale_notice", "satis ilani"), _row("auction_result", _AUCTION_LABEL_NORM),
            _row("appraisal_report", "3- bilirkisi raporu")]
    r = select_unique_document_row(rows, "auction_result", _AUCTION_LABEL)
    assert r is not None and r["artifact_type"] == "auction_result"


def test_two_auction_rows_are_ambiguous_no_selection():
    rows = [_row("auction_result", _AUCTION_LABEL_NORM), _row("auction_result", "1b- ek tutanak")]
    assert select_unique_document_row(rows, "auction_result", None) is None   # belirsiz → indirme YOK


def test_artifact_type_mismatch_no_selection():
    rows = [_row("sale_notice", "satis ilani")]
    assert select_unique_document_row(rows, "auction_result", _AUCTION_LABEL) is None


def test_normalized_label_mismatch_no_selection():
    rows = [_row("auction_result", "farkli bir etiket")]
    assert select_unique_document_row(rows, "auction_result", _AUCTION_LABEL) is None


def test_multi_identity_row_rejected():
    rows = [_row("auction_result", _AUCTION_LABEL_NORM, count=2)]   # tek satırda >1 belge kimliği
    assert select_unique_document_row(rows, "auction_result", _AUCTION_LABEL) is None


def test_requested_auction_does_not_select_sale_notice_row():
    rows = [_row("sale_notice", "satis ilani"), _row("auction_result", _AUCTION_LABEL_NORM)]
    r = select_unique_document_row(rows, "auction_result", _AUCTION_LABEL)
    assert r["artifact_type"] == "auction_result" and "satis ilani" not in (r["normalized_label"] or "")


# ======================================================================================= #
# G. Native belge-türü korroborasyonu (semantik; DEĞERDEN DEĞİL)
# ======================================================================================= #
def test_classify_auction_result_title():
    assert classify_udf_document_type("ARTIRMA SONUÇ TUTANAĞI ... İhale Bedeli : 1.234,00") == "auction_result"


def test_classify_sale_notice_title():
    assert classify_udf_document_type(
        "TAŞINMAZIN ELEKTRONİK SATIŞ ORTAMINDA AÇIK ARTIRMA İLANI Kıymeti : 2.000.000,00") == "sale_notice"


def test_classify_explicit_ihale_bedeli_field_is_auction_result():
    assert classify_udf_document_type("Bir belge İhale Bedeli : 5.000,00 içerir") == "auction_result"


def test_classify_sale_notice_prose_ihale_bedelini_not_auction_result():
    # prose "ihale bedelini/bedelinin" auction_result YAPMAZ; başlık sale_notice belirler
    assert classify_udf_document_type(_SALE_NOTICE_SRC) == "sale_notice"


def test_classify_unknown():
    assert classify_udf_document_type("Alakasız bir metin.") == "unknown"


def test_corroborate_auction_result_match():
    c = corroborate_native_document_type(_AUCTION_RESULT_SRC, "auction_result")
    assert c["native_document_type_corroborated"] is True and c["native_document_type_mismatch"] is False
    assert c["native_detected_document_type"] == "auction_result"


def test_corroborate_measured_run13_mismatch():
    # ÖLÇÜLEN Run-13 vakası: istenen auction_result, native kaynak sale_notice → MİSMATCH
    c = corroborate_native_document_type(_SALE_NOTICE_SRC, "auction_result")
    assert c["native_document_type_corroborated"] is False
    assert c["native_document_type_mismatch"] is True
    assert c["native_detected_document_type"] == "sale_notice"
    assert c["native_requested_artifact_type"] == "auction_result"


def test_corroborate_unknown_is_indeterminate_not_mismatch():
    c = corroborate_native_document_type("Alakasız metin.", "auction_result")
    assert c["native_document_type_corroborated"] is False and c["native_document_type_mismatch"] is False
    assert c["native_document_type_corroboration_reason"] == "document_type_indeterminate"


# ======================================================================================= #
# H. Uyuşmayan native artifact PROMOTE EDİLMEZ (sahte page/download; ağ YOK)
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


def _native(tmp_path, monkeypatch, udf_bytes, rows=None):
    monkeypatch.setattr(_collect.store, "DEFAULT_STORE_DIR", tmp_path)
    col = BrowserCollector()
    monkeypatch.setattr(col, "_locate_row_download_action", lambda p, l, s: _FakeControl())
    page = _FakeDownloadPage(_FakeDownload(udf_bytes, "download.udf"))
    attempt: dict = {}
    diag = {"document_collection_failures": 0,
            "recognized_document_rows": rows if rows is not None else [_row("auction_result", _AUCTION_LABEL_NORM)]}
    documents: list = []
    resolution = {"download_action_resolved": True, "download_action": {"kind": "download"}}
    ok = col._collect_native_udf_download(page, _AUCTION_LABEL, {"artifact_type": "auction_result"},
                                          resolution, attempt, diag, documents)
    return ok, attempt, documents


def test_corroborated_auction_udf_is_promoted(tmp_path, monkeypatch):
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_AUCTION_RESULT_SRC))
    assert ok is True
    assert attempt["native_document_type_corroborated"] is True
    assert attempt["native_document_type_mismatch"] is False
    assert attempt["document_source_artifact_collected"] is True
    assert documents and documents[0]["artifact_type"] == "auction_result"


def test_mismatched_sale_notice_udf_not_promoted_as_auction(tmp_path, monkeypatch):
    # Run-13: auction_result istendi ama sale_notice UDF indirildi → PROMOTE EDİLMEZ
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_SALE_NOTICE_SRC))
    assert ok is False
    assert attempt["native_detected_document_type"] == "sale_notice"
    assert attempt["native_document_type_mismatch"] is True
    assert attempt.get("document_source_artifact_collected") is not True   # auction_result kanıtı DEĞİL
    assert not documents                                               # DocumentRow'a EKLENMEZ
    # baytlar tanı için KORUNUR (diagnostic store)
    assert attempt["native_artifact_collected"] is True
    assert list((tmp_path / "artifacts" / "downloads").glob("*.udf"))


def test_mismatched_native_not_in_artifact_types_collected(tmp_path, monkeypatch):
    _, _, documents = _native(tmp_path, monkeypatch, _make_udf(_SALE_NOTICE_SRC))
    types = {d.get("artifact_type") for d in documents}
    assert "auction_result" not in types                              # yanlış artifact toplanmadı


def test_ambiguous_row_reacquisition_does_not_download(tmp_path, monkeypatch):
    rows = [_row("auction_result", _AUCTION_LABEL_NORM), _row("auction_result", _AUCTION_LABEL_NORM)]
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_AUCTION_RESULT_SRC), rows=rows)
    assert ok is False and attempt["native_row_reacquired"] is False
    assert attempt["native_udf_blocking_reason"] == "ambiguous_or_unresolved_row_reacquisition"
    assert not documents


def test_row_reacquisition_records_identity(tmp_path, monkeypatch):
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(_AUCTION_RESULT_SRC))
    assert attempt["native_requested_artifact_type"] == "auction_result"
    assert attempt["native_row_reacquired"] is True
    assert attempt["native_row_reacquired_artifact_type"] == "auction_result"
    assert attempt["native_row_reacquired_label_match"] is True


# ======================================================================================= #
# I. Açık İhale Bedeli alan-etiketi tanısı — çekimli prose etiket SAYILMAZ
# ======================================================================================= #
def _ev(text):
    return extract_evidence([{"artifact_type": "auction_result", "text": text}], institution="Ankara", file_id=TARGET)


def test_prose_ihale_bedelini_does_not_set_label_found():
    ev = _ev("Borçlunun ihale bedelini yatırmaması halinde işlem yapılır.")
    assert ev.auction_price_field_label_found is False and ev.auction_price_candidate_count == 0


def test_prose_ihale_bedelinin_does_not_set_label_found():
    ev = _ev("İhale bedelinin ödenmemesi durumunda satış düşer.")
    assert ev.auction_price_field_label_found is False and ev.auction_price_candidate_count == 0


def test_explicit_ihale_bedeli_field_sets_label_and_one_candidate():
    ev = _ev("İhale Bedeli : 1.234.567,89")
    assert ev.auction_price_field_label_found is True and ev.auction_price_candidate_count == 1
    assert ev.ihale_bedeli == 1_234_567.89


def test_sale_notice_source_yields_no_auction_price_label():
    text, _ = extract_udf_source_text(_make_udf(_SALE_NOTICE_SRC))
    ev = _ev(text)
    assert ev.auction_price_field_label_found is False and ev.ihale_bedeli is None


def test_satis_tutari_still_excluded():
    ev = extract_evidence([{"artifact_type": "status_card", "text": "Satış Tutarı : 1.234.567,89"}],
                          institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli is None


# ======================================================================================= #
# J. Native UDF parser DEĞİŞMEDEN çalışır (Fix 12 korunur)
# ======================================================================================= #
def test_native_udf_parser_unchanged():
    text, diag = extract_udf_source_text(_make_udf(_AUCTION_RESULT_SRC))
    assert text is not None and diag["zip_valid"] and diag["content_element_found"]
    assert "İhale Bedeli : 1.234.567,89" in text
    before = hashlib.sha256(_make_udf(_AUCTION_RESULT_SRC)).hexdigest()
    extract_udf_source_text(_make_udf(_AUCTION_RESULT_SRC))
    assert hashlib.sha256(_make_udf(_AUCTION_RESULT_SRC)).hexdigest() == before


# ======================================================================================= #
# K/L/N. no-OCR + non-mutation + yapısal donma
# ======================================================================================= #
def test_no_ocr_no_ml_in_corroboration():
    src = inspect.getsource(_extract).lower()
    for banned in ("tesseract", "pytesseract", "easyocr", "ocr(", "sklearn", "torch", "tensorflow"):
        assert banned not in src


def test_corroboration_uses_no_verifier_numbers():
    src = inspect.getsource(classify_udf_document_type) + inspect.getsource(corroborate_native_document_type)
    for banned in ("5715000", "6800000", "50984", "2026/263", "1.234.567"):
        assert banned not in src


def test_run_pilot_fix13_offline_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    text, _ = extract_udf_source_text(_make_udf(_AUCTION_RESULT_SRC))
    arts = [{"artifact_type": "auction_result", "text": text, "source_ref": "native_udf:.udf"},
            {"artifact_type": "status_card", "text": "Satıldı Satış İşlemleri Tamamlandı"}]
    r = run_pilot(offline_artifacts=arts, genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["pilot_outcome"] in ("NOT_RUN", "FAIL", "PARTIAL")
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == 7 and after["sha256"] == before["sha256"]


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
