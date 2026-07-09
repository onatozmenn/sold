"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 4 testleri (OFFLINE; ağ/canlı YOK).

Dördüncü gerçek-canlı FAIL: operatör TAM OLARAK gerçek arama/listeleme sayfasından (aktif URL
``/pp/index.jsp``, 2026/263 İcra kartı görünür) çalıştırdı; ancak ``classify_page_state`` HAM
HTML'deki zayıf gömülü görüntüleyici referanslarını ("Evrak Görüntüleme" / ``viewer.jsp`` /
``mimeType=Udf``) AKTİF sayfa semantiğinin ÜSTÜNE koyup sayfayı ``udf_viewer`` sınıfladı →
``document_entry_path=unsupported`` → Fix-3 hedef-kart mantığı HİÇ çalışmadı.

Fix 4: AKTİF-sayfa URL/state kanıt önceliği + yanlış-pozitif önleme + güvenli sekme seçimi.
Bu testler yalnızca saf/çevrimdışı davranışı doğrular; CANLI PASS DEĞİL (operatör yeniden-çalıştırması gerekir).
"""

from __future__ import annotations

import json
import shutil

from sold.ingestion.uyap import (
    audit_candidate,
    classify_document_entry_path,
    classify_page_state,
    extract_evidence,
    file_identity_matches,
    find_target_record_card,
    genuine_fingerprint,
    page_state_evidence,
    reconcile,
    run_pilot,
    select_target_page_index,
)

TARGET = "2026/263 Esas"
LISTING_URL = "https://esatis.uyap.gov.tr/pp/index.jsp"
VIEWER_URL = "https://esatis.uyap.gov.tr/pp/viewer.jsp?mimeType=Udf&evrakId=16737826545"


# --- Fixture'lar --------------------------------------------------------------------------- #
def _listing_with_hidden_viewer_refs():
    """4. canlı FAIL reprodüksiyonu: gerçek listeleme sayfası + GİZLİ/script görüntüleyici referansları.

    Aktif URL /pp/index.jsp; görünür güçlü listeleme semantiği (İncele + İhale Evrak Listesi +
    2026/263 İcra + Satış İşlemleri Tamamlandı); ham HTML ayrıca zayıf viewer.jsp / mimeType=Udf /
    Evrak Görüntüleme referansları içerir (gizli/script/şablon)."""
    return (
        '<html><body><div class="results-list">'
        '<div class="ilan-card">Ankara Gayrimenkul Satış İcra Dairesi 2026/263 İcra '
        'Muhammen Bedel 6.800.000,00 TL Satış Durumu Satıldı Satış İşlemleri Tamamlandı '
        '<button>İncele</button><button>İhale Evrak Listesi</button></div>'
        '</div>'
        '<script>var _tmpl="/pp/viewer.jsp?mimeType=Udf&evrakId=" + id;</script>'
        '<span style="display:none" title="Evrak Görüntüleme">Evrak Görüntüleme</span>'
        '</body></html>'
    )


def _pure_viewer_page():
    """Gerçek görüntüleyici sayfası: görünür 'Evrak Görüntüleme', listeleme/detay semantiği YOK."""
    return '<html><body><div id="viewer">Evrak Görüntüleme</div><canvas></canvas></body></html>'


def _detail_page_with_tab():
    return ('<html><body><ul class="tabs">'
            '<li><a href="#detay">Detaylı İnceleme</a></li>'
            '<li><a href="#evrak">İhale Evrak Listesi</a></li>'
            '<li><a href="#teklif">İhaleye Ait Tekliflerim</a></li>'
            '</ul><div>Ankara 2026/263 İcra Muhammen Bedel 6.800.000,00 TL Satıldı</div>'
            '</body></html>')


def _detail_with_hidden_viewer_refs():
    return _detail_page_with_tab().replace(
        "</body>",
        '<script>var v="viewer.jsp?mimeType=Udf&evrakId=9";</script></body>')


# --- A. Dördüncü-canlı yanlış-pozitif reprodüksiyonu (regresyon) --------------------------- #
def test_fourth_live_false_positive_listing_not_udf_viewer():
    # Pre-Fix-4: "evrak goruntuleme" ham HTML'de olduğundan udf_viewer dönerdi. Fix 4: search_listing.
    assert classify_page_state(_listing_with_hidden_viewer_refs(), LISTING_URL) == "search_listing"


def test_index_jsp_with_embedded_viewer_jsp_is_listing():
    html = '<div>İncele</div><div>İhale Evrak Listesi</div><a href="viewer.jsp?mimeType=Udf&evrakId=1">x</a>'
    assert classify_page_state(html, LISTING_URL) == "search_listing"


def test_index_jsp_with_embedded_mimetype_udf_is_listing():
    html = '<div>İncele</div><div>İhale Evrak Listesi</div><!-- mimeType=Udf -->'
    assert classify_page_state(html, LISTING_URL) == "search_listing"


# --- B. Aktif-sayfa görüntüleyici (URL doğru olduğunda) ------------------------------------ #
def test_active_viewer_url_is_udf_viewer():
    assert classify_page_state("", VIEWER_URL) == "udf_viewer"


def test_active_viewer_url_overrides_empty_body():
    assert classify_page_state("<html><body></body></html>", VIEWER_URL) == "udf_viewer"


def test_visible_evrak_goruntuleme_viewer_context_is_udf_viewer():
    # Aktif URL viewer değil ama sayfa gerçekten görüntüleyici (listeleme/detay YOK) → udf_viewer
    assert classify_page_state(_pure_viewer_page(), "https://esatis.uyap.gov.tr/pp/goster.jsp") == "udf_viewer"


def test_generic_hidden_viewer_reference_alone_not_viewer():
    # Yalnızca gizli viewer.jsp/mimeType=Udf metni (Evrak Görüntüleme YOK, listeleme/detay YOK) → unknown
    html = '<script>var u="/pp/viewer.jsp?mimeType=Udf&evrakId=1";</script>'
    assert classify_page_state(html, LISTING_URL) == "unknown"


# --- C. Güçlü görünür semantik ------------------------------------------------------------- #
def test_strong_visible_detail_semantics_is_record_detail():
    assert classify_page_state(_detail_page_with_tab(), "https://esatis.uyap.gov.tr/pp/detay.jsp") == "record_detail"


def test_strong_visible_listing_semantics_is_search_listing():
    html = '<div class="ilan-card">2026/263 İcra <button>İncele</button><button>İhale Evrak Listesi</button></div>'
    assert classify_page_state(html, LISTING_URL) == "search_listing"


def test_unknown_page_remains_unknown():
    assert classify_page_state("<div>alakasız içerik</div>", "https://esatis.uyap.gov.tr/pp/x.jsp") == "unknown"


# --- D. Zayıf görüntüleyici ipucu güçlü durumu GEÇERSİZ KILAMAZ ---------------------------- #
def test_weak_viewer_hint_cannot_override_listing():
    ev = page_state_evidence(_listing_with_hidden_viewer_refs(), LISTING_URL)
    assert ev["page_state"] == "search_listing"
    assert "visible_listing_semantics" in ev["evidence"]
    assert "weak_embedded_viewer_reference_ignored" in ev["evidence"]


def test_weak_viewer_hint_cannot_override_detail():
    ev = page_state_evidence(_detail_with_hidden_viewer_refs(), "https://esatis.uyap.gov.tr/pp/detay.jsp")
    assert ev["page_state"] == "record_detail"
    assert "visible_detail_semantics" in ev["evidence"]
    assert "weak_embedded_viewer_reference_ignored" in ev["evidence"]


# --- E. Aktif URL, gömülü/gezinti URL'lerinden AYRIŞIR (Req #5) ---------------------------- #
def test_active_url_index_with_embedded_viewer_url_stays_listing():
    html = '<div>İncele</div><div>İhale Evrak Listesi</div>viewer.jsp?mimeType=Udf&evrakId=123'
    assert classify_page_state(html, "https://esatis.uyap.gov.tr/pp/index.jsp") == "search_listing"


def test_active_url_is_viewer_classifies_udf():
    assert classify_page_state("", "https://esatis.uyap.gov.tr/pp/viewer.jsp?mimeType=Udf&evrakId=123") == "udf_viewer"


# --- F. Kanıt önceliği deterministik ------------------------------------------------------- #
def test_classifier_evidence_precedence_deterministic():
    html, url = _listing_with_hidden_viewer_refs(), LISTING_URL
    a = page_state_evidence(html, url)
    b = page_state_evidence(html, url)
    assert a == b
    assert a["evidence"][0] == "visible_listing_semantics"  # aktif-URL viewer yok → listeleme kazanır
    assert page_state_evidence("", VIEWER_URL)["evidence"] == ["active_url_udf_viewer"]


# --- G. Sayfa-durumu → Fix-3 giriş yollarına yönlendirir ---------------------------------- #
def test_search_listing_routes_to_listing_card_path():
    ps = classify_page_state(_listing_with_hidden_viewer_refs(), LISTING_URL)
    assert classify_document_entry_path(ps) == "listing_card_modal"


def test_record_detail_routes_to_detail_panel_path():
    ps = classify_page_state(_detail_page_with_tab(), "https://esatis.uyap.gov.tr/pp/detay.jsp")
    assert classify_document_entry_path(ps) == "detail_tab_panel"


def test_udf_viewer_is_not_a_document_entry_page():
    assert classify_document_entry_path("udf_viewer") == "unsupported"


# --- H. Çoklu-sekme güvenli seçim: bayat görüntüleyici DEĞİL hedef listeleme --------------- #
def test_multiple_page_selection_prefers_target_listing_over_stale_viewer():
    cands = [
        {"url": VIEWER_URL, "html": _pure_viewer_page()},                 # bayat UDF görüntüleyici
        {"url": LISTING_URL, "html": _listing_with_hidden_viewer_refs()},  # hedef listeleme
    ]
    r = select_target_page_index(cands, TARGET)
    assert r["index"] == 1
    assert r["selected_page_state"] == "search_listing"
    assert r["selected_page_target_identity_match"] is True
    assert len(r["page_candidates_seen"]) == 2
    assert r["page_candidates_seen"][0]["state"] == "udf_viewer"


def test_page_selection_not_udf_merely_because_uyap():
    cands = [{"url": VIEWER_URL, "html": _pure_viewer_page()}]
    r = select_target_page_index(cands, TARGET)
    # Tek sekme görüntüleyiciyse ona düşer ama desteklenmediği dürüstçe raporlanır (state=udf_viewer)
    assert r["selected_page_state"] == "udf_viewer"
    assert r["selected_page_target_identity_match"] is False


def test_page_selection_identity_is_file_id_based_not_price():
    # Yüksek bedelli decoy (yanlış dosya no) kimlik eşleşmesi vermez; hedef 2026/263 eşleşir
    decoy = '<div class="ilan-card">İzmir 2024/99 İcra Muhammen Bedel 25.000.000,00 TL <button>İncele</button><button>İhale Evrak Listesi</button></div>'
    cands = [
        {"url": LISTING_URL, "html": decoy},
        {"url": LISTING_URL, "html": _listing_with_hidden_viewer_refs()},
    ]
    r = select_target_page_index(cands, TARGET)
    assert r["index"] == 1 and r["selected_page_target_identity_match"] is True


def test_page_state_evidence_is_privacy_safe():
    # Kanıt yalnızca güvenli enum/etiketler; ham HTML/URL sızmaz
    ev = page_state_evidence(_listing_with_hidden_viewer_refs(), LISTING_URL)
    allowed = {"active_url_udf_viewer", "active_url_viewer", "visible_detail_semantics",
               "visible_listing_semantics", "visible_viewer_semantics",
               "weak_embedded_viewer_reference_ignored"}
    assert set(ev["evidence"]) <= allowed


# --- I. Fix-3 hedef-kart mantığı KORUNDU (dosya kimliği, fiyat/nth değil) ------------------ #
def test_fix3_target_card_still_file_identity_based():
    card = find_target_record_card(_listing_with_hidden_viewer_refs(), TARGET,
                                   "Ankara Gayrimenkul Satış İcra Dairesi")
    assert card is not None and "2026/263" in card["file_text"]
    assert "file_id" in card["match_fields"]


def test_fix3_alias_and_no_price_selection_preserved():
    assert file_identity_matches("Ankara ... 2026/263 İcra Dairesi", TARGET) is True
    # fiyat metni kimlik değil
    assert file_identity_matches("Muhammen Bedel 6.800.000,00 TL", TARGET) is False


# --- J. Non-mutation + verification (canlı PASS DEĞİL) ------------------------------------- #
def test_run_pilot_fix4_offline_non_mutating(tmp_path):
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
         "source_ref": "viewer:udf_viewer"},
        {"artifact_type": "appraisal_report",
         "text": "BİLİRKİŞİ RAPORU 6.800.000,00 TL 50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm",
         "source_ref": "viewer:udf_viewer"},
    ]
    r = run_pilot(offline_artifacts=artifacts, genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["verification_layer_result"] == "PASS"
    assert r["pilot_outcome"] == "NOT_RUN"       # offline → CANLI PASS DEĞİL
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"] and after["sha256"] == before["sha256"]
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert sum(1 for x in recs if str(x.get("public_record_id")) == TARGET) == 1  # 8. gözlem YOK


def test_result_card_satis_tutari_not_substituted():
    card = {"artifact_type": "status_card",
            "text": "Satış Durumu Satıldı Satış Tutarı 5.715.000,00 TL 50984 Ada 1 Parsel"}
    ev = extract_evidence([card], institution="Ankara", file_id=TARGET)
    au = audit_candidate(ev, reconcile([card], "Ankara", TARGET))
    assert ev.ihale_bedeli is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


# --- K. Yapısal freeze (4 moment; sale_prob yok; TOKİ external 0; conditional_on_trade) ---- #
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
    assert sj["J_TOKI"]["rank"] == 0 and sj["J_TOKI"]["n_moments"] == 0  # external benchmark, SMM'e 0 moment
