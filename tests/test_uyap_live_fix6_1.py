"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 6.1 testleri (OFFLINE; ağ/canlı YOK).

Ek GERÇEK operatör gözlemi: satır-yerel eye/view eylemi bir UYAP UDF görüntüleyici sekmesi açtı, ama
belge RENDER OLMADI; görüntüleyici görünürde şunu yazdı:
"Evrak Görüntülenemedi, Evrağı indirerek Görüntüleyebilirsiniz." Dolayısıyla görüntüleyiciye ulaşmak
belge içeriğinin erişilebilir olduğunu GARANTİ ETMEZ.

Fix 6.1: görüntüleyici-SONUÇ sınıflandırması (content_available / download_required / ...) + AYNI-SATIR
download geri-dönüşü (Fix-6 POZİTİF download semantiğini yeniden kullanır; global/Nth/başka-satır YOK;
ham UDF çıkarımı DESTEKLENMİYORSA dürüstçe raporlanır; UYDURMA/OCR/known-truth enjeksiyonu YOK).
Bu testler yalnız saf/çevrimdışı davranışı doğrular; CANLI PASS DEĞİL; post-Fix-6.1 operatör rerun gerekir.
"""

from __future__ import annotations

import json
import shutil

from bs4 import BeautifulSoup

from sold.ingestion.uyap import (
    audit_candidate,
    classify_viewer_outcome,
    extract_evidence,
    extraction_supported_for,
    genuine_fingerprint,
    reconcile,
    resolve_row_view_action,
    run_pilot,
    viewer_download_instruction_detected,
)
from sold.ingestion.uyap.collect import _row_action_specs

TARGET = "2026/263 Esas"
REAL_MSG = "Evrak Görüntülenemedi, Evrağı indirerek Görüntüleyebilirsiniz."


def _acts(inner_html: str):
    return _row_action_specs(BeautifulSoup(f"<div class=row>{inner_html}</div>", "html.parser"))


_ROW_DL_EYE = (
    '<a href="#"><svg><use href="#icon-download"></use></svg></a>'
    '<a href="#"><svg><use href="#icon-eye"></use></svg></a>'
)


# --- A. Görüntüleyici sonucu: içerik erişilebilir --------------------------------------- #
def test_accessible_viewer_content_is_content_available():
    txt = "ARTIRMA SONUÇ TUTANAĞI İhale Bedeli: 5.715.000,00 ALACAĞA MAHSUBEN"
    assert classify_viewer_outcome(txt) == "content_available"
    assert classify_viewer_outcome("herhangi", "dom_text") == "content_available"


# --- B. download_required: gerçek Türkçe / fold / mojibake ------------------------------ #
def test_real_turkish_download_instruction_is_download_required():
    assert viewer_download_instruction_detected(REAL_MSG) is True
    assert classify_viewer_outcome(REAL_MSG) == "download_required"


def test_folded_turkish_download_instruction():
    folded = "evrak goruntulenemedi, evragi indirerek goruntuleyebilirsiniz."
    assert classify_viewer_outcome(folded) == "download_required"


def test_mojibake_download_instruction():
    assert classify_viewer_outcome(REAL_MSG.encode("utf-8").decode("latin-1")) == "download_required"
    assert classify_viewer_outcome(REAL_MSG.encode("utf-8").decode("cp1252")) == "download_required"


# --- C. Katı: yalın indir / program indirme / eksik bileşen download_required DEĞİL ------ #
def test_generic_indir_alone_is_not_download_required():
    assert classify_viewer_outcome("Dosyayı indir") != "download_required"
    assert viewer_download_instruction_detected("Dosyayı indir") is False


def test_generic_program_download_instruction_is_not_download_required():
    assert classify_viewer_outcome("Programı indirmek için tıklayınız") != "download_required"


def test_viewer_failure_without_instruction_does_not_trigger_fallback():
    # 'Evrak Görüntülenemedi' TEK BAŞINA download_required değil (fallback tetiklemez) → viewer_error
    assert classify_viewer_outcome("Evrak Görüntülenemedi") == "viewer_error"
    assert viewer_download_instruction_detected("Evrak Görüntülenemedi") is False


def test_download_instruction_without_failure_does_not_trigger_fallback():
    assert viewer_download_instruction_detected("Evrağı indirerek Görüntüleyebilirsiniz") is False


# --- D. Fix-6 POZİTİF download eylemi AYNI SATIRDAN çözülür ------------------------------ #
def test_same_row_download_action_is_positively_resolved():
    acts = _acts(_ROW_DL_EYE)
    res = resolve_row_view_action(acts)
    assert res["resolved"] is True and res["view_action"]["semantic"] == "view"
    assert res["download_action_resolved"] is True
    assert res["download_action"]["semantic"] == "download"
    # AYNI satırın eylemlerinden biri (global/Nth/başka-satır DEĞİL)
    assert res["download_action"] in acts and res["view_action"] in acts


def test_download_only_row_has_no_resolved_view_so_no_viewer_opened():
    # view çözülmediğinde görüntüleyici HİÇ açılmaz → outcome/fallback devreye girmez
    res = resolve_row_view_action(_acts('<a class="fa-download"></a>'))
    assert res["resolved"] is False and res["view_action"] is None


def test_two_downloads_download_action_unresolved():
    res = resolve_row_view_action(_acts('<a class="fa-download"></a><a class="fa-download"></a>'))
    assert res["download_action_resolved"] is False and res["download_action"] is None


def test_view_only_row_download_action_unresolved():
    res = resolve_row_view_action(_acts('<a title="Goruntule"></a>'))
    assert res["resolved"] is True
    assert res["download_action_resolved"] is False and res["download_action"] is None


def test_non_view_unknown_not_auto_download():
    # [view, unknown] → download çıkarılmaz (unknown download DEĞİL)
    res = resolve_row_view_action(_acts('<a title="Goruntule"></a><a class="icon-foo"></a>'))
    assert res["resolved"] is True
    assert res["download_action_resolved"] is False and res["download_action_detected"] is False


def test_second_or_nth_action_not_auto_download():
    res = resolve_row_view_action(_acts('<a class="x"></a><a class="y"></a>'))
    assert res["download_action_resolved"] is False   # konum/Nth ile download SEÇİLMEZ


# --- E. Çıkarım-desteği dürüstlüğü (ham UDF/PDF DESTEKLENMEZ) ---------------------------- #
def test_extraction_supported_only_for_text_html():
    assert extraction_supported_for(".txt") is True
    assert extraction_supported_for(".html") is True and extraction_supported_for(".htm") is True
    assert extraction_supported_for("txt") is True            # nokta olmadan da
    assert extraction_supported_for(".udf") is False
    assert extraction_supported_for(".pdf") is False
    assert extraction_supported_for(".zip") is False
    assert extraction_supported_for(None) is False


def test_mimetype_udf_hint_alone_does_not_imply_support():
    # viewer URL mimeType=Udf tek başına destek ANLAMINA GELMEZ
    assert extraction_supported_for(".udf", "udf") is False
    assert extraction_supported_for(None, "udf") is False


# --- F. OUTCOME C dürüstlüğü: indirilen ham UDF çıkarılamaz, known-truth ENJEKTE EDİLMEZ -- #
def test_downloaded_unsupported_udf_yields_no_injected_price():
    # İndirilmiş ama çıkarımı desteklenmeyen resmî auction_result (metin YOK) → İhale Bedeli None
    downloaded = {"artifact_type": "auction_result", "source_ref": "download:.udf", "extraction_supported": False}
    ev = extract_evidence([downloaded], institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli is None                         # 5715000 ENJEKTE EDİLMEZ
    au = audit_candidate(ev, reconcile([downloaded], "Ankara", TARGET))
    assert au.auction_price is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


def test_result_card_satis_tutari_not_substituted():
    card = {"artifact_type": "status_card",
            "text": "Satış Durumu Satıldı Satış Tutarı 5.715.000,00 TL 50984 Ada 1 Parsel"}
    ev = extract_evidence([card], institution="Ankara", file_id=TARGET)
    au = audit_candidate(ev, reconcile([card], "Ankara", TARGET))
    assert ev.ihale_bedeli is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


# --- G. Non-mutation: indirilen ham artifact uyap.json'ı DEĞİŞTİRMEZ / ADMİT ETMEZ ------- #
def test_run_pilot_download_required_unsupported_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    # OUTCOME C: görüntüleyici download_required → aynı-satır indirme → ham UDF çıkarımı DESTEKLENMEZ
    artifacts = [
        {"artifact_type": "status_card",
         "text": "Muhammen Bedel 6.800.000,00 TL KDV Oranı : %20 Satış Durumu Satıldı 50984 Ada 1 Parsel",
         "source_ref": "live://index"},
        {"artifact_type": "auction_result", "source_ref": "download:.udf", "extraction_supported": False},
    ]
    r = run_pilot(offline_artifacts=artifacts, genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["pilot_outcome"] in ("NOT_RUN", "FAIL", "PARTIAL")   # ham UDF çıkarılamadı → PASS DEĞİL
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == 7 and after["sha256"] == before["sha256"]
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert sum(1 for x in recs if str(x.get("public_record_id")) == TARGET) == 1  # 8. gözlem YOK


def test_run_pilot_fix6_1_offline_supported_path_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    artifacts = [
        {"artifact_type": "status_card",
         "text": "Muhammen Bedel 6.800.000,00 TL KDV Oranı : %20 Satış Durumu Satıldı 50984 Ada 1 Parsel 60 Nolu B.B.",
         "source_ref": "live://index"},
        {"artifact_type": "auction_result",
         "text": ("ARTIRMA SONUÇ TUTANAĞI 50984 Ada 1 Parsel İhale Bedeli: 5.715.000,00 "
                  "Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN Satıldı Satış İşlemleri Tamamlandı"),
         "source_ref": "download:.html"},   # desteklenen indirilmiş format
        {"artifact_type": "appraisal_report",
         "text": "BİLİRKİŞİ RAPORU 6.800.000,00 TL 50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm",
         "source_ref": "viewer:udf_viewer"},
    ]
    r = run_pilot(offline_artifacts=artifacts, genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["verification_layer_result"] == "PASS"
    assert r["pilot_outcome"] == "NOT_RUN"       # offline → CANLI PASS DEĞİL
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"]
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == 7 and after["sha256"] == before["sha256"]


# --- H. Yapısal freeze -------------------------------------------------------------------- #
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
