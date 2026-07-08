"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 12 testleri (OFFLINE; ağ/canlı YOK).

On ikinci gerçek-canlı FAIL sonrası operatör GERÇEK auction_result ``.udf``'ini elle indirdi ve bayt-bayt
inceledi: 4406 bayt, PK/ZIP konteyner, üyeler documentproperties.xml + content.xml + sign.sgn; content.xml
UTF-8 XML olup belge kaynak metnini ``content`` CDATA'sında taşıyor. Kök-neden: viewer en iyi kanıt katmanı
DEĞİL; indirilen ``.udf`` ZIP'inin content.xml'i deterministik UTF-8 metin içeriyor.

Fix 12: DETERMİNİSTİK NATIVE UDF konteyner çıkarımı (ZIP doğrula → content.xml doğrudan oku → güvenli XML →
content metni) + AYNI-SATIR resmî indirme kaynağı (auction_result öncelik-1). OCR/ML/render/LibreOffice YOK;
known-truth ENJEKTE YOK. GERÇEK belge/tam content.xml COMMİT EDİLMEZ — sanitized sentetik fixture kullanılır.

Sentetik fixture değerleri SENTETİKtir (1.234.567,89 / 123 Ada); ayrıştırıcı bunları LABEL-ilişkisinden
çıkarır, HARDCODE ETMEZ. Gerçek 5715000/6800000/50984/2026-263 fixture'a KONMAZ.
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
    audit_candidate,
    extract_evidence,
    extract_udf_source_text,
    genuine_fingerprint,
    native_udf_supported,
    reconcile,
    run_pilot,
)
from sold.ingestion.uyap import collect as _collect
from sold.ingestion.uyap import udf as _udf
from sold.ingestion.uyap.collect import NATIVE_DOWNLOAD_TYPES, BrowserCollector

TARGET = "2099/999 Esas"   # sentetik (gerçek 2026/263 fixture'a konmaz)

# --- Sanitized sentetik content.xml (ölçülen yapı; SENTETİK değer/kimlik) ------------------ #
_SYNTH_CONTENT = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<template format_id="1.8">\n'
    '  <content><![CDATA[\n'
    'İhale Edilen Malın Muhtevası : ÖRNEK İL, ÖRNEK İLÇE, 123 Ada, 4 Parsel, 5. Kat, 6 Nolu B.B.\n'
    'İhale Bedeli : 1.234.567,89\n'
    'Ödenmesi Gereken Bedel : ALACAĞA MAHSUBEN\n'
    'KDV Oranı : %20\n'
    'Satış İşlemleri Tamamlandı\n'
    ']]></content>\n'
    '</template>\n'
)


def _make_udf(content_xml=_SYNTH_CONTENT, *, members=None, omit_content=False, dup_content=False):
    """Ölçülen yapıda sentetik ``.udf`` (ZIP) üretir: documentproperties.xml + content.xml + sign.sgn."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if members is not None:
            for name, payload in members:
                zf.writestr(name, payload)
        else:
            zf.writestr("documentproperties.xml", "<properties/>")
            if not omit_content:
                zf.writestr("content.xml", content_xml)
                if dup_content:
                    zf.writestr("content.xml", content_xml)
            zf.writestr("sign.sgn", b"SYNTHETIC_SIGNATURE")
    return buf.getvalue()


# ======================================================================================= #
# E. Native UDF ZIP/konteyner doğrulama + güvenli üye işleme
# ======================================================================================= #
def test_synthetic_udf_is_zip_compatible():
    assert zipfile.is_zipfile(io.BytesIO(_make_udf())) is True


def test_root_content_xml_found_and_extracted():
    text, diag = extract_udf_source_text(_make_udf())
    assert text is not None
    assert diag["zip_valid"] and diag["content_xml_found"] and diag["xml_parse_succeeded"]
    assert diag["content_element_found"] and diag["source_text_available"]
    assert native_udf_supported(diag) is True


def test_no_extractall_used_in_reader():
    src = inspect.getsource(_udf)
    assert "extractall" not in src                          # keyfi üye diske AÇILMAZ


def test_path_traversal_member_not_extracted(tmp_path):
    evil = _make_udf(members=[("../evil.txt", "PWNED"), ("content.xml", _SYNTH_CONTENT),
                              ("sign.sgn", b"x")])
    text, diag = extract_udf_source_text(evil)
    assert text is None and diag["blocking_reason"] == "unsafe_archive_member_path"
    assert not (tmp_path.parent / "evil.txt").exists()      # hiçbir şey açılmadı


def test_missing_content_xml_reports_blocker():
    text, diag = extract_udf_source_text(_make_udf(omit_content=True))
    assert text is None and diag["blocking_reason"] == "content_xml_missing"


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_duplicate_content_xml_rejected():
    text, diag = extract_udf_source_text(_make_udf(dup_content=True))
    assert text is None and diag["blocking_reason"] == "ambiguous_content_xml_members"


def test_malformed_zip_unsupported():
    text, diag = extract_udf_source_text(b"NOT A ZIP FILE AT ALL")
    assert text is None and diag["blocking_reason"] == "not_a_zip_compatible_container"
    assert diag["zip_valid"] is False


def test_malformed_xml_reports_blocker():
    bad = _make_udf(content_xml="<template><content>oops</content")   # kapanmamış
    text, diag = extract_udf_source_text(bad)
    assert text is None and diag["blocking_reason"] == "malformed_xml"


def test_external_entity_not_enabled():
    xxe = ('<?xml version="1.0"?>\n<!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
           '<template><content>&x;</content></template>')
    text, diag = extract_udf_source_text(_make_udf(content_xml=xxe))
    assert text is None and diag["blocking_reason"] == "doctype_or_entity_not_allowed"
    assert diag["xml_parse_succeeded"] is False              # ayrıştırma bile denenmez


def test_empty_content_element_reports_blocker():
    empty = '<?xml version="1.0" encoding="UTF-8"?><template><content><![CDATA[   ]]></content></template>'
    text, diag = extract_udf_source_text(_make_udf(content_xml=empty))
    assert text is None and diag["blocking_reason"] == "empty_content_text"


def test_missing_content_element_reports_blocker():
    noel = '<?xml version="1.0" encoding="UTF-8"?><template><body>x</body></template>'
    text, diag = extract_udf_source_text(_make_udf(content_xml=noel))
    assert text is None and diag["blocking_reason"] == "content_element_missing"


# ======================================================================================= #
# F. content.xml güvenli XML/CDATA çıkarımı
# ======================================================================================= #
def test_cdata_text_returned_exactly_and_utf8_preserved():
    text, diag = extract_udf_source_text(_make_udf())
    assert "İhale Bedeli : 1.234.567,89" in text            # CDATA metni AYNEN
    assert "ALACAĞA MAHSUBEN" in text and "İhale Edilen" in text   # Türkçe UTF-8 korunur


def test_decompressed_size_bounded(monkeypatch):
    monkeypatch.setattr(_udf, "MAX_UDF_DECOMPRESSED_BYTES", 20)   # küçük sınır
    text, diag = extract_udf_source_text(_make_udf())
    assert text is None and diag["blocking_reason"] == "content_xml_too_large"


def test_support_based_on_structure_not_extension():
    # .udf uzantısı olsa bile ZIP değilse DESTEKLENMEZ (yapı temelli)
    text, diag = extract_udf_source_text(b"PK-lookalike-but-not-zip")
    assert native_udf_supported(diag) is False
    # geçerli ZIP + content.xml ise uzantıdan BAĞIMSIZ desteklenir
    _, diag2 = extract_udf_source_text(_make_udf())
    assert native_udf_supported(diag2) is True


def test_member_names_safe_summary_privacy():
    _, diag = extract_udf_source_text(_make_udf())
    s = diag["member_names_safe_summary"]
    assert s["has_content_xml"] is True and s["has_unsafe_member_path"] is False
    assert set(s["known_members"]) == {"content.xml", "documentproperties.xml", "sign.sgn"}


# ======================================================================================= #
# G/J. Native UDF metni MEVCUT alan çıkarıcıya beslenir (label-bounded parser)
# ======================================================================================= #
def _udf_ev():
    text, _ = extract_udf_source_text(_make_udf())
    return extract_evidence([{"artifact_type": "auction_result", "text": text}],
                            institution="Ankara", file_id=TARGET)


def test_synthetic_udf_ihale_one_candidate():
    ev = _udf_ev()
    assert ev.ihale_bedeli == 1_234_567.89
    assert ev.auction_price_field_label_found is True and ev.auction_price_candidate_count == 1


def test_synthetic_udf_settlement_alacaga_recognized():
    ev = _udf_ev()
    assert ev.settlement_field_label_found is True and ev.alacaga_mahsuben is True


def test_synthetic_udf_kdv_parses():
    assert _udf_ev().kdv_rate == 20.0


def test_synthetic_udf_asset_descriptors_parse():
    ev = _udf_ev()
    assert ev.ada == "123" and ev.parsel == "4" and ev.section_no == "6" and ev.floor == "5"


def test_satis_tutari_not_auction_price_via_udf():
    text = _SYNTH_CONTENT.replace("İhale Bedeli : 1.234.567,89", "Satış Tutarı : 1.234.567,89")
    ev = extract_evidence([{"artifact_type": "auction_result", "text": extract_udf_source_text(_make_udf(text))[0]}],
                          institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli is None                          # Satış Tutarı açık İhale Bedeli DEĞİL


def test_odenmesi_gereken_bedel_not_auction_price():
    ev = _udf_ev()
    # Ödenmesi Gereken Bedel değeri ALACAĞA MAHSUBEN (parasal değil) → auction price OLARAK KULLANILMAZ
    assert ev.ihale_bedeli == 1_234_567.89 and ev.odenmesi_gereken_bedel is None


# ======================================================================================= #
# H. AYNI-SATIR resmî native indirme toplama (sahte page/download; ağ YOK)
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
    def __init__(self, dl, raise_on_download=False):
        self._dl = dl
        self._raise = raise_on_download

    def expect_download(self, timeout=None):
        if self._raise:
            raise RuntimeError("no download")
        return _FakeExpectDownload(self._dl)


def _native(tmp_path, monkeypatch, data, fname="auction.udf", resolved=True, raise_dl=False, locate=True):
    monkeypatch.setattr(_collect.store, "DEFAULT_STORE_DIR", tmp_path)
    col = BrowserCollector()
    monkeypatch.setattr(col, "_locate_row_download_action",
                        (lambda p, l, s: _FakeControl()) if locate else (lambda p, l, s: None))
    page = _FakeDownloadPage(_FakeDownload(data, fname), raise_on_download=raise_dl)
    attempt: dict = {}
    diag = {"document_collection_failures": 0}
    documents: list = []
    resolution = {"download_action_resolved": resolved, "download_action": {"kind": "download"}}
    ok = col._collect_native_udf_download(page, "1- Artırma Sonuç / Uzatma Tutanağı",
                                          {"artifact_type": "auction_result"}, resolution, attempt, diag, documents)
    return ok, attempt, documents


def test_native_download_collects_extracts_and_feeds_extractor(tmp_path, monkeypatch):
    data = _make_udf()
    ok, attempt, documents = _native(tmp_path, monkeypatch, data)
    assert ok is True
    assert attempt["native_download_event_detected"] is True
    assert attempt["native_artifact_collected"] is True and attempt["native_artifact_extension"] == ".udf"
    assert attempt["native_udf_zip_valid"] is True and attempt["native_udf_content_xml_found"] is True
    assert attempt["native_udf_source_text_available"] is True
    assert attempt["native_udf_text_extraction_supported"] is True
    assert attempt["native_udf_source_relation"] == "official_same_row_native_udf"
    assert attempt["document_source_artifact_collected"] is True
    # KESİN baytlar değişmeden depolandı; sha/size kesin baytlardan
    stored = list((tmp_path / "artifacts" / "downloads").glob("*.udf"))
    assert stored and stored[0].read_bytes() == data
    assert attempt["native_artifact_size"] == len(data)
    assert attempt["native_artifact_sha256"] == hashlib.sha256(data).hexdigest()[:16]
    # DocumentRow türü + native_udf source_ref; MEVCUT çıkarıcıya beslenir
    assert documents[0]["artifact_type"] == "auction_result"
    assert documents[0]["source_ref"].startswith("native_udf:")
    ev = extract_evidence(documents, institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli == 1_234_567.89 and ev.alacaga_mahsuben is True


def test_native_download_requires_resolved_same_row_action(tmp_path, monkeypatch):
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(), resolved=False)
    assert ok is False and attempt["native_download_action_resolved"] is False
    assert attempt["native_udf_blocking_reason"] == "no_resolved_same_row_download_action"
    assert not documents


def test_native_download_requires_located_same_row_control(tmp_path, monkeypatch):
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(), locate=False)
    assert ok is False and attempt["native_udf_blocking_reason"] == "same_row_download_control_not_located"


def test_native_download_requires_real_download_event(tmp_path, monkeypatch):
    ok, attempt, documents = _native(tmp_path, monkeypatch, _make_udf(), raise_dl=True)
    assert ok is False and attempt["native_download_event_detected"] is False
    assert attempt["native_udf_blocking_reason"] == "no_download_event_detected"


def test_non_udf_download_falls_through(tmp_path, monkeypatch):
    ok, attempt, documents = _native(tmp_path, monkeypatch, b"not a zip", fname="x.udf")
    assert ok is False and not documents                    # ZIP değil → native başarısız → viewer'a düşer
    assert attempt["native_udf_zip_valid"] is False
    assert attempt["native_artifact_collected"] is True     # baytlar yine de KORUNUR (provenans)


def test_exact_bytes_unchanged_by_reader():
    data = _make_udf()
    before = hashlib.sha256(data).hexdigest()
    extract_udf_source_text(data)
    assert hashlib.sha256(data).hexdigest() == before       # okuyucu baytları DEĞİŞTİRMEZ


# ======================================================================================= #
# M. auction_result native öncelik-1; sale_notice viewer KORUNUR; viewer kodu MEVCUT
# ======================================================================================= #
def test_auction_result_is_native_priority_first():
    assert "auction_result" in NATIVE_DOWNLOAD_TYPES


def test_sale_notice_not_native_download_type():
    assert "sale_notice" not in NATIVE_DOWNLOAD_TYPES        # viewer yolu KORUNUR (bloklayıcı değil)


def test_viewer_code_remains_available():
    for m in ("_observe_viewer_stabilization", "_collect_viewer_image", "_viewer_source_text",
              "_same_row_download_fallback", "_persist_viewer_source"):
        assert callable(getattr(BrowserCollector, m, None))


# ======================================================================================= #
# I/K/L/N/Q. destek semantiği + no-OCR + non-mutation + yapısal donma
# ======================================================================================= #
def test_extraction_support_udf_extension_alone_insufficient():
    from sold.ingestion.uyap.collect import extraction_supported_for
    assert extraction_supported_for(".udf") is False        # ham .udf uzantısı tek başına DESTEK DEĞİL


def test_no_ocr_no_render_no_subprocess_in_udf_reader():
    src = inspect.getsource(_udf).lower()
    for banned in ("tesseract", "pytesseract", "easyocr", "ocr(", "libreoffice", "subprocess",
                   "os.system", "screenshot", "render"):
        assert banned not in src


def test_native_udf_source_feeds_extract_evidence_admissible():
    # auction UDF (İhale Bedeli) + sale_notice (Muhammen Bedel, aynı varlık) → ADMISSIBLE
    udf_text, _ = extract_udf_source_text(_make_udf())
    sale = ("SATIŞ İLANI\n123 Ada 4 Parsel 6 No'lu 5. Kat Mesken\n"
            "Muhammen Bedeli : 2.000.000,00 TL\nKDV Oranı : %20")
    arts = [{"artifact_type": "auction_result", "text": udf_text},
            {"artifact_type": "sale_notice", "text": sale},
            {"artifact_type": "status_card", "text": "Satıldı Satış İşlemleri Tamamlandı"}]
    ev = extract_evidence(arts, institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli == 1_234_567.89 and ev.appraisal_value == 2_000_000.0
    au = audit_candidate(ev, reconcile(arts, "Ankara", TARGET))
    assert au.auction_price == 1_234_567.89 and au.decision == "ADMISSIBLE_COMPLETED_SALE"


def test_no_known_truth_injection():
    ev = extract_evidence([{"artifact_type": "auction_result", "text": "Bu belgede tutar yok."}],
                          institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli is None and ev.appraisal_value is None


def test_run_pilot_fix12_offline_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    udf_text, _ = extract_udf_source_text(_make_udf())
    arts = [{"artifact_type": "auction_result", "text": udf_text, "source_ref": "native_udf:.udf"},
            {"artifact_type": "status_card", "text": "Satıldı Satış İşlemleri Tamamlandı"}]
    r = run_pilot(offline_artifacts=arts, genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["pilot_outcome"] in ("NOT_RUN", "FAIL", "PARTIAL")   # OFFLINE → canlı PASS DEĞİL
    assert "auction_result" in r["artifact_types_collected"]
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == 7 and after["sha256"] == before["sha256"]
    assert sum(1 for x in json.loads(gp.read_text(encoding="utf-8"))
               if str(x.get("public_record_id")) == TARGET) == 0   # sentetik hedef genuine'de YOK


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
