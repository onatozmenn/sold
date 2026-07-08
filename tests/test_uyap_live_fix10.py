"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 10 testleri (OFFLINE; ağ/canlı YOK).

Onuncu gerçek-canlı FAIL: Run 10 görüntüleyici bariyerini AŞTI — auction_result image_only→dom_text→
dom_text, sale_notice dom_text×3, her ikisi viewer_ready_state=stable_text_representation,
document_source_artifact_collected=true, artifact_types_collected=[auction_result,sale_notice,status_card],
reconciliation RECONCILED, KDV=20.0. AMA extraction_status=ambiguous: appraisal [1.0, 6800000.0], açık İhale
Bedeli EKSİK, ALACAĞA MAHSUBEN false, audit PENDING_REVIEW.

İki yapısal kök-neden (kod-kanıtlı): (1) appraisal/İhale çıkarımı geniş pencerede İLK sayıyı alıyordu —
çıplak "1" (parsel/sıra) admit ediliyordu; (2) extract.py mojibake ONARMIYORDU (collect.py onarıyor) →
İhale Bedeli/ALACAĞA (Türkçe İ/Ğ) eşleşmiyor, ASCII Muhammen Bedel/KDV/ada/parsel eşleşiyordu.

Fix 10: (a) SON kararlı görüntüleyici tanı semantiği (initial_/final_); (b) LABEL-BOUNDED parasal alan
çıkarımı (yalnız Türk parasal literali; çıplak sayı YAPISAL olarak dışlanır — değer/eşik/max sezgisi YOK);
(c) açık İhale Bedeli alan çıkarımı; (d) gerçek ALACAĞA MAHSUBEN semantiği + mojibake onarımı. OCR/ML YOK;
known-truth ENJEKTE EDİLMEZ. CANLI PASS DEĞİL.
"""

from __future__ import annotations

import inspect
import json
import shutil

from sold.ingestion.uyap import (
    audit_candidate,
    classify_viewer_ready_state,
    extract_evidence,
    genuine_fingerprint,
    reconcile,
    run_pilot,
)
from sold.ingestion.uyap import extract as _extract
from sold.ingestion.uyap.models import demojibake
from sold.ingestion.uyap.pilot import _final_viewer_state

TARGET = "2026/263 Esas"


# --- Sanitized Run-10-benzeri kaynak düzenleri (KİŞİSEL VERİ YOK; yalnız ayrıştırıcı-ilişkili düzen) --- #
# Not: parasal değerler test GİRDİSİdir (mevcut CASE5 gibi); ayrıştırıcı bunları LABEL-ilişkisinden
# çıkarır, HARDCODE ETMEZ. Bilinen gerçek ayrıştırıcıya ENJEKTE EDİLMEZ.
def _run10_auction_result() -> str:
    return (
        "T.C. ANKARA GAYRIMENKUL SATIS ICRA DAIRESI ARTIRMA SONUC / UZATMA TUTANAGI\n"
        "Tasinmaz: 50984 Ada 1 Parsel 60 No'lu Bagimsiz Bolum 12. Kat\n"
        "İhale Bedeli: 5.715.000,00 TL\n"
        "Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN\n"
        "Satış İşlemleri Tamamlandı\n"
    )


def _run10_sale_notice() -> str:
    return (
        "T.C. ANKARA ... SATIŞ İLANI\n"
        "Tasinmazin Tapu Kaydi: 50984 Ada 1 Parsel 60 No'lu Bagimsiz Bolum 12. Kat Mesken\n"
        "Kıymeti Takdir Edilen 1 (Bir) Adet Tasinmaz\n"       # OLD parser buradan 1.0 harvest ederdi
        "Muhammen Bedeli: 6.800.000,00 TL\n"
        "KDV Oranı : %20\n"
    )


def _run10_status_card() -> str:
    return "Satıldı Satış İşlemleri Tamamlandı Satış Tutarı: 5.715.000,00 TL 50984 Ada 1 Parsel 60 No'lu 12. Kat"


def _run10_artifacts() -> list:
    return [
        {"artifact_type": "auction_result", "text": _run10_auction_result()},
        {"artifact_type": "sale_notice", "text": _run10_sale_notice()},
        {"artifact_type": "status_card", "text": _run10_status_card()},
    ]


# ======================================================================================= #
# A. SON kararlı görüntüleyici tanı semantiği (image_only -> dom_text -> dom_text)
# ======================================================================================= #
def _obs(rep, fp="A", cnt=0, **kw):
    return {"representation": rep, "candidate_count": cnt, "selected_dimension": None,
            "selected_src_kind": None, "selected_fingerprint": fp,
            "download_required": kw.get("download_required", False),
            "viewer_error": kw.get("viewer_error", False)}


def test_run10_image_to_text_transition_finalizes_as_text():
    ready = classify_viewer_ready_state(
        [_obs("image_only", "IMG", cnt=2), _obs("dom_text", "T"), _obs("dom_text", "T")])
    assert ready["ready_state"] == "stable_text_representation"
    assert ready["transition_detected"] is True            # image_only -> dom_text geçişi KORUNUR


def test_final_viewer_state_reflects_final_not_initial():
    diag = {"document_collection_attempts": [{
        "artifact_type": "auction_result",
        "initial_viewer_representation": "image_only",
        "initial_viewer_outcome": "image_backed",
        "initial_viewer_text_available": False,
        "final_viewer_representation": "dom_text",
        "final_viewer_outcome": "content_available",
        "final_viewer_text_available": True,
        "document_source_artifact_collected": True,
    }]}
    assert _final_viewer_state(diag, "final_viewer_representation") == "dom_text"
    assert _final_viewer_state(diag, "final_viewer_outcome") == "content_available"
    assert _final_viewer_state(diag, "final_viewer_text_available") is True
    # ilk (kararlılık-öncesi) durum AYRICA gözlemlenebilir kalır
    a = diag["document_collection_attempts"][0]
    assert a["initial_viewer_representation"] == "image_only" and a["initial_viewer_outcome"] == "image_backed"


def test_final_viewer_state_prefers_auction_result_collected():
    diag = {"document_collection_attempts": [
        {"artifact_type": "sale_notice", "final_viewer_representation": "dom_text",
         "document_source_artifact_collected": True},
        {"artifact_type": "auction_result", "final_viewer_representation": "dom_text",
         "document_source_artifact_collected": True},
    ]}
    assert _final_viewer_state(diag, "final_viewer_representation") == "dom_text"


# ======================================================================================= #
# B. LABEL-BOUNDED appraisal — çıplak "1"/kimlikler YAPISAL olarak dışlanır (Run-10 [1.0, 6800000] düzeltilir)
# ======================================================================================= #
def _appr(text):
    return extract_evidence([{"artifact_type": "sale_notice", "text": text}], institution="Ankara", file_id=TARGET)


def test_run10_spurious_one_is_not_admitted_appraisal_candidate():
    ev = _appr(_run10_sale_notice())
    assert ev.appraisal_candidates == [6_800_000.0]        # 1.0 YAPISAL olarak dışlandı
    assert ev.appraisal_value == 6_800_000.0
    assert ev.appraisal_field_label_found is True and ev.appraisal_candidate_count == 1


def test_appraisal_same_line_money():
    ev = _appr("Muhammen Bedeli: 6.800.000,00 TL")
    assert ev.appraisal_value == 6_800_000.0
    assert "same_segment" in ev.appraisal_value_relation_strategies


def test_appraisal_next_line_money():
    ev = _appr("Muhammen Bedeli\n6.800.000,00 TL")
    assert ev.appraisal_value == 6_800_000.0
    assert "adjacent_segment" in ev.appraisal_value_relation_strategies


def test_appraisal_adjacent_segment_money():
    ev = _appr("Takdir Olunan Değer :\n7.250.000,00 TL")
    assert ev.appraisal_value == 7_250_000.0


def test_unrelated_one_parsel_not_appraisal_candidate():
    ev = _appr("Muhammen Bedeli: 6.800.000,00 TL 50984 Ada 1 Parsel")
    assert 1.0 not in ev.appraisal_candidates and ev.appraisal_value == 6_800_000.0


def test_row_number_one_not_appraisal_candidate():
    ev = _appr("1- Bilirkişi Raporu\nKıymeti Takdir Edilen 1 Adet\nMuhammen Bedel: 6.800.000,00 TL")
    assert ev.appraisal_candidates == [6_800_000.0]


def test_section_and_ada_not_appraisal_candidates():
    ev = _appr("Muhammen Bedel: 6.800.000,00 TL 50984 Ada 1 Parsel 60 No'lu 12. Kat")
    assert ev.appraisal_candidates == [6_800_000.0]        # 60, 50984, 1, 12 → para DEĞİL


def test_unrelated_currency_outside_field_relation_not_candidate():
    # Etiketsiz bir parasal değer (alan-ilişkisi yok) appraisal adayı OLMAZ.
    ev = _appr("Dosya masrafı 1.234,00 TL ödenmiştir. Muhammen Bedeli: 6.800.000,00 TL")
    assert ev.appraisal_candidates == [6_800_000.0]


def test_multiple_label_associated_values_remain_ambiguous_no_max():
    ev = _appr("Muhammen Bedel: 6.800.000,00 TL\nTakdir Olunan Değer: 7.000.000,00 TL")
    assert sorted(ev.appraisal_candidates) == [6_800_000.0, 7_000_000.0]
    assert ev.appraisal_value is None                      # belirsiz — max(7.000.000) SEÇİLMEZ
    assert ev.extraction_status == "ambiguous"


def test_no_minimum_price_threshold_small_money_accepted():
    # Küçük ama düzgün-biçimli parasal değer, label-ilişkiliyse KABUL edilir (magnitude eşiği YOK).
    ev = _appr("Muhammen Bedeli: 1.500,00 TL")
    assert ev.appraisal_value == 1_500.0


# ======================================================================================= #
# C. Açık İhale Bedeli çıkarımı (Satış Tutarı / Ödenmesi Gereken Bedel / ALACAĞA MAHSUBEN DEĞİL)
# ======================================================================================= #
def _ev(artifacts):
    return extract_evidence(artifacts, institution="Ankara", file_id=TARGET)


def test_ihale_bedeli_same_line():
    ev = _ev([{"artifact_type": "auction_result", "text": "İhale Bedeli: 5.715.000,00 TL"}])
    assert ev.ihale_bedeli == 5_715_000.0
    assert ev.auction_price_field_label_found is True and ev.auction_price_candidate_count == 1
    assert ev.auction_price_value_relation_strategy == "same_segment"


def test_ihale_bedeli_next_line():
    ev = _ev([{"artifact_type": "auction_result", "text": "İhale Bedeli\n5.715.000,00 TL"}])
    assert ev.ihale_bedeli == 5_715_000.0
    assert ev.auction_price_value_relation_strategy == "adjacent_segment"


def test_ihale_bedeli_adjacent_segment_with_identifiers():
    ev = _ev([{"artifact_type": "auction_result", "text": _run10_auction_result()}])
    assert ev.ihale_bedeli == 5_715_000.0                  # 50984/1/60/12 çıplak → dışlandı


def test_satis_tutari_is_not_auction_price():
    ev = _ev([{"artifact_type": "status_card", "text": "Satış Tutarı: 5.715.000,00 TL"}])
    assert ev.ihale_bedeli is None                          # Satış Tutarı açık İhale Bedeli DEĞİL
    assert ev.result_card_amount == 5_715_000.0            # yalnız korroborasyon


def test_odenmesi_gereken_bedel_not_auction_price():
    ev = _ev([{"artifact_type": "auction_result",
               "text": "Ödenmesi Gereken Bedel: 4.114.000,00 TL"}])
    assert ev.ihale_bedeli is None                          # açık İhale Bedeli etiketi yok


def test_alacaga_mahsuben_text_is_not_auction_price():
    ev = _ev([{"artifact_type": "auction_result",
               "text": "İhale Bedeli: ALACAĞA MAHSUBEN"}])
    assert ev.ihale_bedeli is None                          # mahsuben sayıdan önce → nakit UYDURULMAZ


def test_explicit_ihale_bedeli_required():
    ev = _ev([{"artifact_type": "auction_result", "text": "Satış İşlemleri Tamamlandı 5.715.000,00 TL"}])
    assert ev.ihale_bedeli is None and ev.auction_price_field_label_found is False


# ======================================================================================= #
# D. ALACAĞA MAHSUBEN semantiği (bölünmüş-blok + mojibake) — kaynak metinden gelir
# ======================================================================================= #
def test_odenmesi_gereken_bedel_alacaga_mahsuben_recognized():
    ev = _ev([{"artifact_type": "auction_result", "text": "Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN"}])
    assert ev.alacaga_mahsuben is True and ev.alacaga_mahsuben_detected is True
    assert ev.settlement_field_label_found is True


def test_split_block_alacaga_mahsuben_recognized():
    ev = _ev([{"artifact_type": "auction_result", "text": "Ödenmesi Gereken Bedel:\nALACAĞA\nMAHSUBEN"}])
    assert ev.alacaga_mahsuben is True                      # bloklara bölünse de yakalanır


def test_generic_mahsuben_elsewhere_does_not_imply_settlement():
    ev = _ev([{"artifact_type": "auction_result",
               "text": "Vergi mahsuben işlemi ayrıca yapılmıştır. İhale Bedeli: 5.715.000,00 TL"}])
    assert ev.alacaga_mahsuben is False                    # "alacaga mahsuben" yok, odenmesi seg yok


def test_mojibake_ihale_and_alacaga_repaired_and_extracted():
    # Gerçek görüntüleyici mojibake üretebilir; extract.py artık onarır (İhale/ALACAĞA eşleşir).
    moji = _run10_auction_result().encode("utf-8").decode("latin-1")
    assert "İhale" not in moji                              # gerçekten mojibake
    ev = _ev([{"artifact_type": "auction_result", "text": moji}])
    assert ev.ihale_bedeli == 5_715_000.0                  # onarım sonrası açık İhale Bedeli
    assert ev.alacaga_mahsuben is True                     # onarım sonrası ALACAĞA MAHSUBEN


def test_demojibake_reverses_utf8_as_latin1():
    original = "İhale Bedeli ALACAĞA MAHSUBEN Kıymeti"
    assert demojibake(original.encode("utf-8").decode("latin-1")) == original
    assert demojibake(original) == original                # temiz metin değişmez


# ======================================================================================= #
# E. Türk parasal ayrıştırma (alan-ilişkisinden SONRA) + kimlikler para DEĞİL
# ======================================================================================= #
def test_turkish_money_5_715_000_parses_after_field_association():
    ev = _ev([{"artifact_type": "auction_result", "text": "İhale Bedeli: 5.715.000,00 TL"}])
    assert ev.ihale_bedeli == 5_715_000.0


def test_turkish_money_6_800_000_parses_after_field_association():
    assert _appr("Muhammen Bedeli: 6.800.000,00 TL").appraisal_value == 6_800_000.0


def test_property_identifiers_are_not_money_fields():
    ev = _appr("50984 Ada 1 Parsel 60 No'lu Bağımsız Bölüm 12. Kat")
    assert ev.appraisal_candidates == [] and ev.appraisal_value is None


# ======================================================================================= #
# F. KDV + reconciliation KORUNUR (Run-10 başarıları regres OLMAZ)
# ======================================================================================= #
def test_kdv_still_extracted_from_run10_fixture():
    ev = _ev(_run10_artifacts())
    assert ev.kdv_rate == 20.0                             # hardcode YOK; "KDV Oranı : %20"dan


def test_reconciliation_matches_same_asset_identifiers():
    rec = reconcile(_run10_artifacts(), "Ankara", TARGET)
    assert rec.status == "reconciled"
    assert any(m.startswith("ada=") for m in rec.matched_on)
    assert any(m.startswith("parsel=") for m in rec.matched_on)


def test_run10_fixture_full_extraction_deterministic():
    ev = _ev(_run10_artifacts())
    assert ev.appraisal_value == 6_800_000.0               # sale_notice Muhammen Bedeli
    assert ev.ihale_bedeli == 5_715_000.0                  # auction_result açık İhale Bedeli
    assert ev.alacaga_mahsuben is True and ev.kdv_rate == 20.0
    assert ev.extraction_status == "deterministic"
    au = audit_candidate(ev, reconcile(_run10_artifacts(), "Ankara", TARGET))
    assert au.auction_price == 5_715_000.0 and au.decision == "ADMISSIBLE_COMPLETED_SALE"


# ======================================================================================= #
# G. auction_result fiyat kaynağı / sale_notice appraisal-side (roller KORUNUR)
# ======================================================================================= #
def test_auction_result_remains_auction_price_source():
    ev = _ev(_run10_artifacts())
    assert ev.ihale_bedeli_source == "auction_result"


def test_sale_notice_accepted_appraisal_side_source():
    ev = _ev(_run10_artifacts())
    assert ev.appraisal_source == "sale_notice"            # Muhammen Bedeli sale_notice'ten


# ======================================================================================= #
# H. OCR YOK + known-truth ENJEKTE YOK + görüntü-only doc fiyat/appraisal İMA ETMEZ
# ======================================================================================= #
def test_no_ocr_dependency_added():
    src = inspect.getsource(_extract).lower()
    for banned in ("tesseract", "pytesseract", "easyocr", "image_to_string", "ocr(", "screenshot"):
        assert banned not in src


def test_image_only_document_injects_nothing():
    img = {"artifact_type": "auction_result", "source_ref": "viewer_image:.png", "extraction_supported": False}
    ev = _ev([img])
    assert ev.ihale_bedeli is None and ev.appraisal_value is None   # 5715000/6800000 ENJEKTE EDİLMEZ


def test_known_truth_not_used_as_extraction_fallback():
    # Etiketsiz/desteksiz metin → hiçbir alan "beklenen değere" ZORLANMAZ.
    ev = _ev([{"artifact_type": "auction_result", "text": "Bu belgede tutar bulunmamaktadır."}])
    assert ev.ihale_bedeli is None and ev.appraisal_value is None and ev.alacaga_mahsuben is False


# ======================================================================================= #
# I. Non-mutation + yapısal donma (pilot ASLA admit; sayı 7; SMM 4 moment; TOKİ external)
# ======================================================================================= #
def test_run_pilot_fix10_offline_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    r = run_pilot(offline_artifacts=_run10_artifacts(), genuine_path=gp, store_dir=tmp_path,
                  report_path=tmp_path / "r.json")
    assert r["pilot_outcome"] in ("NOT_RUN", "FAIL", "PARTIAL")   # OFFLINE → canlı PASS DEĞİL
    fe = r["field_extraction"]
    assert fe["auction_price_field_label_found"] is True and fe["auction_price_candidate_count"] == 1
    assert fe["appraisal_candidate_count"] == 1 and fe["alacaga_mahsuben_detected"] is True
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == 7 and after["sha256"] == before["sha256"]
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
