"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 11 testleri (OFFLINE; ağ/canlı YOK).

On birinci gerçek-canlı FAIL: Run 11 Fix 10 appraisal'ını CANLI kanıtladı (extracted_appraisal=6800000.0,
appraisal_candidate_count=1, strategy same_segment → 1.0 LIVE-RESOLVED); reconciliation reconciled, KDV=20.0,
final_viewer_representation=dom_text. KALAN blocker: auction_price_field_label_found=TRUE ama
auction_price_candidate_count=0 (İhale Bedeli LABEL bulunuyor, LABEL→VALUE ilişkisi değere ULAŞMIYOR) ve
settlement_field_label_found=false (Ödenmesi Gereken Bedel bölünmüş serileştirme). audit PENDING_REVIEW.

Fix 11: (a) kararlı DOM-metin kaynağını YALNIZCA gitignored yerel depoya kalıcılaştır (gövde JSON/log/test/
README'e ASLA); (b) bounded çok-segmentli İhale Bedeli LABEL→VALUE ilişkisi (same→adjacent→bounded_following,
sınır etiketinde DUR); (c) settlement TOKEN-DİZİSİ etiket eşleştirme + bounded ALACAĞA MAHSUBEN; (d) gizlilik-
güvenli alan-komşuluğu tanıları. Layout TAHMİN EDİLMEZ; known-truth ENJEKTE EDİLMEZ; OCR/ML YOK. CANLI PASS DEĞİL.
"""

from __future__ import annotations

import inspect
import json
import shutil

from sold.ingestion.uyap import (
    audit_candidate,
    extract_evidence,
    genuine_fingerprint,
    reconcile,
    run_pilot,
)
from sold.ingestion.uyap import collect as _collect
from sold.ingestion.uyap import extract as _extract
from sold.ingestion.uyap.collect import BrowserCollector
from sold.ingestion.uyap.extract import (
    _bounded_token_sequence,
    _ihale_bedeli_relation,
    _settlement_relation,
    _tokens_in_order,
)

TARGET = "2026/263 Esas"


def _ev(text, atype="auction_result"):
    return extract_evidence([{"artifact_type": atype, "text": text}], institution="Ankara", file_id=TARGET)


def _segs(text):
    return _extract._artifact_segments({"artifact_type": "auction_result", "text": text})


# ======================================================================================= #
# A. Kararlı DOM-metin kaynağının YALNIZCA gitignored yerel depoya kalıcılaştırılması
# ======================================================================================= #
_SECRET = "MAHREM_KISISEL_ICERIK_BURADA_5715000_TC12345678901"


def test_source_text_persisted_only_to_local_gitignored_store(tmp_path, monkeypatch):
    monkeypatch.setattr(_collect.store, "DEFAULT_STORE_DIR", tmp_path)
    attempt = {}
    content = f"İhale Bedeli 5.715.000,00 TL {_SECRET}"
    path = BrowserCollector()._persist_viewer_source("auction_result", content, attempt)
    # gövde YALNIZCA yerel gitignored dosyada
    stored = list((tmp_path / "artifacts" / "viewer_sources").glob("auction_result_*.txt"))
    assert len(stored) == 1 and stored[0].read_text(encoding="utf-8") == content
    # yalnız provenans attempt'te; GÖVDE tanıda YOK
    assert attempt["source_text_persisted"] is True
    assert attempt["source_text_artifact_size"] == len(content.encode("utf-8"))
    assert len(attempt["source_text_artifact_sha256"]) == 16
    assert _SECRET not in json.dumps(attempt)               # gövde attempt'e SIZMAZ
    assert path is not None


def test_empty_content_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(_collect.store, "DEFAULT_STORE_DIR", tmp_path)
    attempt = {}
    assert BrowserCollector()._persist_viewer_source("auction_result", "", attempt) is None
    assert attempt["source_text_persisted"] is False


def test_full_source_text_absent_from_pilot_json(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    arts = [{"artifact_type": "auction_result", "text": f"İhale Bedeli: 5.715.000,00 TL {_SECRET}"},
            {"artifact_type": "status_card", "text": "Satıldı Satış İşlemleri Tamamlandı"}]
    report = tmp_path / "r.json"
    run_pilot(offline_artifacts=arts, genuine_path=gdir / "uyap.json", store_dir=tmp_path, report_path=report)
    raw = report.read_text(encoding="utf-8")
    assert _SECRET not in raw                                # ham kaynak gövdesi pilot JSON'da ASLA


# ======================================================================================= #
# B. Bounded çok-segmentli İhale Bedeli LABEL→VALUE ilişkisi
# ======================================================================================= #
def test_ihale_same_segment_still_works():
    val, strat, nb = _ihale_bedeli_relation(_segs("İhale Bedeli: 5.715.000,00 TL"))
    assert val == 5_715_000.0 and strat == "same_segment"


def test_ihale_adjacent_segment_still_works():
    val, strat, nb = _ihale_bedeli_relation(_segs("İhale Bedeli\n5.715.000,00 TL"))
    assert val == 5_715_000.0 and strat == "adjacent_segment"


def test_ihale_value_two_bounded_segments_later_parses():
    val, strat, nb = _ihale_bedeli_relation(_segs("İhale Bedeli\n(TL cinsinden)\n5.715.000,00"))
    assert val == 5_715_000.0 and strat == "bounded_following"
    assert nb["bounded_following_segments_inspected"] == 2


def test_ihale_formatting_only_intervening_segment_parses():
    val, strat, nb = _ihale_bedeli_relation(_segs("İhale Bedeli\n:\n5.715.000,00 TL"))
    assert val == 5_715_000.0 and strat == "bounded_following"


def test_another_recognized_field_boundary_stops_search():
    val, strat, nb = _ihale_bedeli_relation(_segs("İhale Bedeli\nÖdenmesi Gereken Bedel: 4.114.000,00"))
    assert val is None
    assert nb["boundary_stop_reason"].startswith("field_or_identifier_label")


def test_property_identifier_boundary_stops_search_and_unrelated_money_not_captured():
    val, strat, nb = _ihale_bedeli_relation(_segs("İhale Bedeli\n50984 Ada 1 Parsel\n9.999.999,00"))
    assert val is None                                       # ada sınırı durdurur; 9.999.999,00 YAKALANMAZ
    assert "ada" in nb["boundary_stop_reason"]


def test_ihale_header_row_then_detail_row_extracts_from_later_occurrence():
    # Gerçek Ankara 2026/23: İhale Bedeli etiketi ÖNCE başlık satırında (hemen ardından
    # 'Ödenmesi Gereken Bedel' sınırı → ilk oluşum değersiz), SONRA detay satırında değerle.
    # İLK oluşumda durmak yerine SONRAKİ oluşum denenir (uydurma yok: gerçek parasal literal gerekir).
    text = ("İhale Bedeli\nÖdenmesi Gereken Bedel\nKDV\n"
            "İhale Bedeli 4.654.000,00 TL\nÖdenmesi Gereken Bedel 4.114.000,00 TL")
    val, strat, nb = _ihale_bedeli_relation(_segs(text))
    assert val == 4_654_000.0
    assert nb["label_occurrence_count"] == 2
    assert nb["segment_shape"] and nb["segment_shape"][0].startswith("label:ihale bedeli")


def test_ihale_columnar_layout_returns_none_but_shape_reveals_columns():
    # Sütun düzeni (etiket bloğu + değer bloğu, etiket TEKRARLANMAZ): dürüstçe None döner
    # (pozisyonel eşleme UYDURMA olmaz), ama segment_shape sütun yerleşimini AÇIĞA çıkarır.
    text = "İhale Bedeli\nÖdenmesi Gereken Bedel\nKDV\n4.654.000,00 TL\n4.114.000,00 TL\n20"
    val, strat, nb = _ihale_bedeli_relation(_segs(text))
    assert val is None
    assert nb["label_occurrence_count"] == 1
    assert "money" in nb["segment_shape"]                    # değer bloğu iz'de görünür
    assert nb["segment_shape"][0].startswith("label:ihale bedeli")
    # belge-geneli KONUM haritası (yalnız indeks; DEĞER YOK): etiket blok[0], değer blok[3,4]
    assert nb["label_segment_indexes"] == [0]
    assert nb["money_segment_indexes"] == [3, 4]
    assert nb["document_segment_count"] == 6


def test_no_whole_document_money_scan():
    # değer 5 segment sonra → bounded (max_following=3) ULAŞMAZ; tüm-belge taraması YOK
    val, strat, nb = _ihale_bedeli_relation(_segs("İhale Bedeli\na\nb\nc\nd\n5.715.000,00"))
    assert val is None and nb["boundary_stop_reason"] == "max_segments"


def test_no_max_candidate_selection_first_bounded_value_wins():
    # İlk bounded değer (5.715.000,00) alınır; sonraki daha büyük değer max diye SEÇİLMEZ.
    val, strat, nb = _ihale_bedeli_relation(_segs("İhale Bedeli\n5.715.000,00\n9.999.999,00 TL"))
    assert val == 5_715_000.0


def test_ihale_extraction_via_extract_evidence_bounded():
    ev = _ev("İhale Bedeli\n(TL cinsinden)\n5.715.000,00 TL")
    assert ev.ihale_bedeli == 5_715_000.0
    assert ev.auction_price_field_label_found is True and ev.auction_price_candidate_count == 1
    assert ev.auction_price_value_relation_strategy == "bounded_following"


# ======================================================================================= #
# C. Settlement TOKEN-DİZİSİ etiket eşleştirme (bölünmüş serileştirme; TAM kimlik)
# ======================================================================================= #
def test_settlement_label_split_across_adjacent_segments_recognized():
    ev = _ev("Ödenmesi\nGereken\nBedel\nALACAĞA MAHSUBEN")
    assert ev.settlement_field_label_found is True
    assert ev.alacaga_mahsuben is True


def test_settlement_label_with_intervening_content_recognized_where_collapsed_fails():
    # collapsed 'odenmesi gereken bedel' BİTİŞİK DEĞİL (araya '(Kalan)') → token-dizisi yakalar.
    text = "Ödenmesi Gereken (Kalan) Bedel\nALACAĞA MAHSUBEN"
    assert "odenmesi gereken bedel" not in _collapse(text)
    ev = _ev(text)
    assert ev.settlement_field_label_found is True
    assert ev.settlement_value_relation_strategy in ("bounded_token_sequence", "alacaga_mahsuben_phrase")
    assert ev.alacaga_mahsuben is True


def test_incomplete_settlement_token_sequence_not_recognized():
    assert _bounded_token_sequence(_segs("Gereken Bedel"), ("odenmesi", "gereken", "bedel")) is None
    ev = _ev("Gereken Bedel: 4.114.000,00")
    assert ev.settlement_field_label_found is False


def test_generic_bedel_does_not_identify_settlement_field():
    ev = _ev("Muhammen Bedel: 6.800.000,00 TL")
    assert ev.settlement_field_label_found is False


def test_bounded_region_recognizes_split_alacaga_mahsuben():
    # ALACAĞA (borca) MAHSUBEN → collapsed bitişik DEĞİL; bounded token-dizisi (araya izin) yakalar.
    text = "Ödenmesi Gereken (Kalan) Bedel\nALACAĞA (borca) MAHSUBEN"
    assert "alacaga mahsuben" not in _collapse(text)
    ev = _ev(text)
    assert ev.alacaga_mahsuben is True


def test_mahsuben_elsewhere_does_not_set_settlement_flag():
    ev = _ev("Vergi mahsuben işlemi yapılmıştır. İhale Bedeli: 5.715.000,00 TL")
    assert ev.alacaga_mahsuben is False and ev.settlement_field_label_found is False


def test_tokens_in_order_helper():
    assert _tokens_in_order("odenmesi 1 gereken 2 bedel", ("odenmesi", "gereken", "bedel")) is True
    assert _tokens_in_order("bedel gereken odenmesi", ("odenmesi", "gereken", "bedel")) is False


# ======================================================================================= #
# D. Gizlilik-güvenli alan-komşuluğu tanıları (segment metni / değer içeriği YOK)
# ======================================================================================= #
def test_auction_price_neighborhood_structure_only():
    ev = _ev("İhale Bedeli\nÖdenmesi Gereken Bedel: 4.114.000,00")
    nb = ev.field_neighborhood["auction_price"]
    assert nb["label_segment_found"] is True
    assert isinstance(nb["label_segment_index"], int)
    assert isinstance(nb["bounded_following_segments_inspected"], int)
    assert nb["boundary_stop_reason"].startswith("field_or_identifier_label")
    # gizlilik: komşulukta ham para/segment metni YOK (yalnız etiket-TÜRÜ + sayaç)
    blob = json.dumps(ev.field_neighborhood)
    assert "4.114.000" not in blob and "5.715.000" not in blob


def test_settlement_neighborhood_structure_only():
    ev = _ev("Ödenmesi\nGereken\nBedel\nALACAĞA MAHSUBEN")
    s = ev.field_neighborhood["settlement"]
    assert s["settlement_label_token_sequence_found"] is True
    assert s["settlement_label_segment_span"] >= 1
    assert s["settlement_alacaga_token_found"] is True and s["settlement_mahsuben_token_found"] is True
    assert s["settlement_value_sequence_found"] is True


def test_field_neighborhood_surfaced_in_pilot_report(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    arts = [{"artifact_type": "auction_result", "text": "İhale Bedeli\nÖdenmesi Gereken Bedel: 4.114.000,00"},
            {"artifact_type": "status_card", "text": "Satıldı Satış İşlemleri Tamamlandı"}]
    r = run_pilot(offline_artifacts=arts, genuine_path=gdir / "uyap.json", store_dir=tmp_path,
                  report_path=tmp_path / "r.json")
    fe = r["field_extraction"]
    assert "field_neighborhood" in fe and "auction_price" in fe["field_neighborhood"]
    assert fe["field_neighborhood"]["auction_price"]["label_segment_found"] is True


def _collapse(text):
    from sold.ingestion.uyap.models import _ascii_lower, demojibake
    import re
    return re.sub(r"\s+", " ", _ascii_lower(demojibake(text)))


# ======================================================================================= #
# E. Fix-10 appraisal + KDV + reconciliation KORUNUR; explicit İhale semantiği KORUNUR
# ======================================================================================= #
def _run11_artifacts():
    return [
        {"artifact_type": "auction_result",
         "text": "ARTIRMA SONUC TUTANAGI\n50984 Ada 1 Parsel 60 No'lu 12. Kat\nİhale Bedeli: 5.715.000,00 TL\n"
                 "Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN\nSatış İşlemleri Tamamlandı"},
        {"artifact_type": "sale_notice",
         "text": "SATIŞ İLANI\n50984 Ada 1 Parsel 60 No'lu 12. Kat Mesken\nKıymeti Takdir Edilen 1 (Bir) Adet\n"
                 "Muhammen Bedeli: 6.800.000,00 TL\nKDV Oranı : %20"},
        {"artifact_type": "status_card",
         "text": "Satıldı Satış İşlemleri Tamamlandı Satış Tutarı: 5.715.000,00 TL 50984 Ada 1 Parsel"},
    ]


def test_fix10_appraisal_preserved_exactly_one_candidate():
    ev = extract_evidence(_run11_artifacts(), institution="Ankara", file_id=TARGET)
    assert ev.appraisal_candidates == [6_800_000.0]         # 1.0 hâlâ dışlanır (LIVE-RESOLVED)
    assert ev.appraisal_value == 6_800_000.0 and ev.appraisal_candidate_count == 1


def test_kdv_preserved():
    ev = extract_evidence(_run11_artifacts(), institution="Ankara", file_id=TARGET)
    assert ev.kdv_rate == 20.0


def test_reconciliation_preserved():
    rec = reconcile(_run11_artifacts(), "Ankara", TARGET)
    assert rec.status == "reconciled"


def test_satis_tutari_not_auction_price():
    ev = _ev("Satış Tutarı: 5.715.000,00 TL", atype="status_card")
    assert ev.ihale_bedeli is None


def test_odenmesi_gereken_bedel_not_auction_price():
    ev = _ev("Ödenmesi Gereken Bedel: 4.114.000,00 TL")
    assert ev.ihale_bedeli is None


def test_run11_full_fixture_admissible():
    arts = _run11_artifacts()
    ev = extract_evidence(arts, institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli == 5_715_000.0 and ev.appraisal_value == 6_800_000.0
    assert ev.alacaga_mahsuben is True and ev.kdv_rate == 20.0
    au = audit_candidate(ev, reconcile(arts, "Ankara", TARGET))
    assert au.auction_price == 5_715_000.0 and au.decision == "ADMISSIBLE_COMPLETED_SALE"


# ======================================================================================= #
# F. OCR YOK + known-truth ENJEKTE YOK + non-mutation + yapısal donma
# ======================================================================================= #
def test_no_ocr_dependency():
    src = inspect.getsource(_extract).lower()
    for banned in ("tesseract", "pytesseract", "easyocr", "image_to_string", "ocr(", "screenshot"):
        assert banned not in src


def test_known_truth_not_injected():
    ev = _ev("Bu belgede açık bir tutar bulunmamaktadır.")
    assert ev.ihale_bedeli is None and ev.appraisal_value is None and ev.alacaga_mahsuben is False


def test_run_pilot_fix11_offline_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    r = run_pilot(offline_artifacts=_run11_artifacts(), genuine_path=gp, store_dir=tmp_path,
                  report_path=tmp_path / "r.json")
    assert r["pilot_outcome"] in ("NOT_RUN", "FAIL", "PARTIAL")
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"] and after["sha256"] == before["sha256"]
    assert sum(1 for x in json.loads(gp.read_text(encoding="utf-8"))
               if str(x.get("public_record_id")) == TARGET) == 1


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
