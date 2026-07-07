"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 3 testleri (OFFLINE; ağ/canlı YOK).

Üçüncü gerçek-canlı FAIL: operatör ARAMA/LİSTELEME sayfasından çalıştırdı; kod genel bir
sayfa-düzeyi metin locator'ıyla "İhale Evrak Listesi" aradı, hedef 2026/263 KAYIT KARTINA
kapsamlanmadı; genel metin düğümü tıklanamaz olduğundan zaman aşımına uğradı. Ayrıca iki
GERÇEK giriş yolu gözlendi: (A) listeleme kartı → kart-yerel kontrol → MODAL, (B) İncele →
detay sekmesi → AYNI-SAYFA panel. Her iki yol aynı belge-satırı soyutlamasında birleşir →
satır-yerel eye (indirme oku DEĞİL) → YENİ-SEKME UDF görüntüleyici.

Bu testler yalnızca saf/çevrimdışı davranışı doğrular. CANLI PASS değil; Fix 3 sonrası
operatör yeniden-çalıştırması gereklidir.
"""

from __future__ import annotations

import json
import shutil

from sold.ingestion.uyap import (
    audit_candidate,
    card_document_list_control,
    classify_document_entry_path,
    classify_document_label,
    classify_page_state,
    extract_evidence,
    extract_panel_document_rows,
    file_identity_matches,
    find_target_record_card,
    genuine_fingerprint,
    normalize_file_identity,
    reconcile,
    run_pilot,
    select_row_document_actions,
)

TARGET = "2026/263 Esas"
INSTITUTION = "Ankara Gayrimenkul Satış İcra Dairesi"


# --- Fixture'lar: iki gerçek gözlenen giriş yolu ------------------------------------------- #
def _listing_page_multi_card():
    """Arama/listeleme sayfası: FARKLI kayıtlar için birden çok kart. İLK kart bir decoy
    (İzmir 2024/99, DAHA YÜKSEK muhammen bedel); hedef 2026/263 İkinci karttır."""
    return (
        '<html><body><div class="results-list">'
        '<div class="ilan-card">İzmir 6. İcra Dairesi 2024/99 İcra '
        'Muhammen Bedel 25.000.000,00 TL <button>İncele</button>'
        '<button>İhale Evrak Listesi</button></div>'
        '<div class="ilan-card">Ankara Gayrimenkul Satış İcra Dairesi 2026/263 İcra '
        'Muhammen Bedel 6.800.000,00 TL <button>İncele</button>'
        '<button>İhale Evrak Listesi</button></div>'
        '<div class="ilan-card">Bursa 3. İcra Dairesi 2025/100 İcra '
        'Muhammen Bedel 9.000.000,00 TL <button>İncele</button>'
        '<button>İhale Evrak Listesi</button></div>'
        '</div></body></html>'
    )


def _listing_page_no_target():
    """Listeleme sayfası: HEDEF kayıt YOK; ama genel sayfa-düzeyi 'İhale Evrak Listesi' başlığı VAR."""
    return (
        '<html><body><h2>İhale Evrak Listesi</h2>'
        '<div class="ilan-card">İzmir 6. İcra Dairesi 2024/99 İcra '
        '<button>İncele</button><button>İhale Evrak Listesi</button></div>'
        '</body></html>'
    )


def _detail_page_with_tab():
    return ('<html><body><ul class="tabs">'
            '<li><a href="#aciklama">Açıklama</a></li>'
            '<li><a href="#detay">Detaylı İnceleme</a></li>'
            '<li><a href="#evrak">İhale Evrak Listesi</a></li>'
            '<li><a href="#teklif">İhaleye Ait Tekliflerim</a></li>'
            '</ul><div>Ankara ... 2026/263 İcra Muhammen Bedel 6.800.000,00 TL '
            'KDV Oranı : %20 Satış Durumu Satıldı 50984 Ada 1 Parsel 60 Nolu B.B.</div>'
            '</body></html>')


def _document_rows_html():
    """Belge listesi satırları — MODAL ve AYNI-SAYFA panel için AYNI soyutlama."""
    return (
        '<table><tbody>'
        '<tr><td>Satış İlanı</td><td><a title="İndir">D</a><a title="Görüntüle">G</a></td></tr>'
        '<tr><td>1- Belediye İmar Durumu</td><td><a title="Görüntüle">G</a></td></tr>'
        '<tr><td>2- Satış Şartnamesi Ve Tutanağı</td><td><a title="Görüntüle">G</a></td></tr>'
        '<tr><td>3- BILIRKISI RAPORU 2026 263 ESAS.udf</td><td><a title="Görüntüle">G</a></td></tr>'
        '<tr><td>1- Artırma Sonuç / Uzatma Tutanağı</td><td><a title="İndir">D</a><a title="Görüntüle">G</a></td></tr>'
        '</tbody></table>'
    )


def _modal_container(inner):
    return f'<div role="dialog" class="modal"><h3>İhale Evrak Listesi</h3>{inner}</div>'


def _panel_container(inner):
    return f'<div id="evrak" class="tab-panel">{inner}</div>'


# --- Sayfa-durumu sınıflandırıcı ---------------------------------------------------------- #
def test_page_state_search_listing():
    assert classify_page_state(_listing_page_multi_card(),
                               "https://esatis.uyap.gov.tr/pp/index.jsp") == "search_listing"


def test_page_state_record_detail():
    assert classify_page_state(_detail_page_with_tab(), "https://esatis.uyap.gov.tr/pp/detay.jsp") == "record_detail"


def test_page_state_udf_viewer():
    assert classify_page_state("", "https://esatis.uyap.gov.tr/pp/viewer.jsp?mimeType=Udf&evrakId=9") == "udf_viewer"


def test_page_state_unknown():
    assert classify_page_state("<div>alakasız</div>", "x") == "unknown"


# --- Dosya kimliği normalize + Esas/İcra alias -------------------------------------------- #
def test_normalize_file_identity():
    assert normalize_file_identity("2026/263 Esas") == "2026/263"
    assert normalize_file_identity("2026 / 263 İcra") == "2026/263"


def test_file_identity_alias_esas_matches_icra():
    assert file_identity_matches("Ankara ... 2026/263 İcra Dairesi", "2026/263 Esas") is True
    assert file_identity_matches("İzmir 2024/99 İcra", "2026/263 Esas") is False


# --- Hedef kayıt kartı: DOSYA KİMLİĞİYLE bulunur (fiyat/nth DEĞİL) ------------------------- #
def test_target_card_found_by_file_identity_not_price():
    card = find_target_record_card(_listing_page_multi_card(), TARGET, INSTITUTION)
    assert card is not None
    assert "file_id" in card["match_fields"] and "institution" in card["match_fields"]
    assert "2026/263" in card["file_text"]
    # DAHA YÜKSEK bedelli decoy (İzmir 25.000.000) SEÇİLMEZ
    assert "25.000.000" not in card["file_text"] and "İzmir" not in card["file_text"]


def test_first_global_card_not_auto_selected():
    # İlk DOM kartı İzmir 2024/99; hedef 2026/263 yine de doğru seçilir (first/nth DEĞİL)
    card = find_target_record_card(_listing_page_multi_card(), TARGET, INSTITUTION)
    assert card is not None and "2024/99" not in card["file_text"]


def test_multiple_fake_cards_choose_correct_target():
    card = find_target_record_card(_listing_page_multi_card(), TARGET)
    assert card is not None and "2026/263" in card["file_text"]
    assert "2025/100" not in card["file_text"] and "2024/99" not in card["file_text"]


def test_generic_page_level_evrak_text_not_treated_as_target():
    # Hedef kayıt olmayan sayfada genel 'İhale Evrak Listesi' başlığı → hedef kart YOK
    assert find_target_record_card(_listing_page_no_target(), TARGET, INSTITUTION) is None


def test_multi_record_container_not_mistaken_for_card():
    # Tüm listeyi saran container birden çok dosya no içerir → kart SAYILMAZ (yalnız tekil kart)
    card = find_target_record_card(_listing_page_multi_card(), TARGET)
    nums = {n.replace(" ", "") for n in __import__("re").findall(r"\d{3,4}\s*/\s*\d+", card["file_text"])}
    assert nums == {"2026/263"}  # tek dosya no → gerçek kart


# --- Kart-yerel AKSİYONE EDİLEBİLİR kontrol (metin-yalnız YETERSİZ) ------------------------ #
def test_card_local_actionable_control_selected():
    card = find_target_record_card(_listing_page_multi_card(), TARGET, INSTITUTION)
    ctrl = card_document_list_control(card["html"])
    assert ctrl["found"] is True and ctrl["actionable"] is True and ctrl["kind"] == "button"


def test_non_actionable_text_only_control_rejected():
    text_only = '<div class="ilan-card">2026/263 İcra <span>İhale Evrak Listesi</span></div>'
    ctrl = card_document_list_control(text_only)
    assert ctrl["found"] is False and ctrl["actionable"] is False
    assert ctrl["kind"] == "non_actionable_text_only"


# --- Giriş-yolu türetimi (listeleme→modal; detay→panel) ----------------------------------- #
def test_entry_path_listing_is_modal():
    assert classify_document_entry_path("search_listing") == "listing_card_modal"


def test_entry_path_detail_is_panel():
    assert classify_document_entry_path("record_detail") == "detail_tab_panel"


def test_entry_path_unknown_unsupported():
    assert classify_document_entry_path("udf_viewer") == "unsupported"


# --- İki yol AYNI belge-satırı soyutlamasında birleşir ------------------------------------ #
def test_both_paths_converge_same_document_rows():
    modal_rows = extract_panel_document_rows(_modal_container(_document_rows_html()))
    panel_rows = extract_panel_document_rows(_panel_container(_document_rows_html()))
    modal_labels = {classify_document_label(r["label"]) for r in modal_rows}
    panel_labels = {classify_document_label(r["label"]) for r in panel_rows}
    assert modal_labels == panel_labels
    assert {"appraisal_report", "auction_result", "sale_notice", "sale_spec"} <= modal_labels


def test_listing_modal_rows_classify():
    rows = extract_panel_document_rows(_modal_container(_document_rows_html()))
    labels = {classify_document_label(r["label"]) for r in rows}
    assert "appraisal_report" in labels and "auction_result" in labels
    assert all("belediye" not in r["label"].lower() for r in rows)  # ilgisiz satır yok


def test_detail_panel_rows_classify():
    rows = extract_panel_document_rows(_panel_container(_document_rows_html()))
    labels = {classify_document_label(r["label"]) for r in rows}
    assert "sale_notice" in labels and "sale_spec" in labels


# --- Satır-yerel eye ≠ indirme oku -------------------------------------------------------- #
def test_row_local_eye_distinguished_from_download():
    rows = extract_panel_document_rows(_document_rows_html())
    sel = select_row_document_actions(rows)
    by_type = {s["artifact_type"]: s for s in sel}
    # auction_result satırı hem İndir hem Görüntüle içerir → Görüntüle (eye) seçilir
    assert by_type["auction_result"]["action"]["kind"] == "eye"
    # her seçim KENDİ satırından (global tek eye/Nth-eye DEĞİL)
    assert len({s["row_index"] for s in sel}) == len(sel)


def test_download_only_action_not_selected_as_view():
    rows = [{"label": "3- BILIRKISI RAPORU.udf", "actions": [{"kind": "download", "text": "İndir"}]}]
    assert select_row_document_actions(rows) == []  # indirme oku eye DEĞİL → görüntüleme seçimi yok


def test_eye_preferred_even_when_download_first():
    rows = [{"label": "Satış İlanı", "actions": [
        {"kind": "download", "text": "İndir"}, {"kind": "eye", "text": "Görüntüle"}]}]
    sel = select_row_document_actions(rows)
    assert sel and sel[0]["action"]["kind"] == "eye"


# --- Fiyat/appraisal provenance (kart Satış Tutarı SUBSTİTÜE EDİLMEZ) ---------------------- #
def test_result_card_satis_tutari_not_substituted():
    card = {"artifact_type": "status_card",
            "text": "Satış Durumu Satıldı Satış Tutarı 5.715.000,00 TL 50984 Ada 1 Parsel"}
    ev = extract_evidence([card], institution="Ankara", file_id=TARGET)
    au = audit_candidate(ev, reconcile([card], "Ankara", TARGET))
    assert ev.ihale_bedeli is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


def test_auction_price_from_explicit_ihale_bedeli():
    src = {"artifact_type": "auction_result",
           "text": ("ARTIRMA SONUÇ TUTANAĞI 50984 Ada 1 Parsel İhale Bedeli: 5.715.000,00 "
                    "Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN Satıldı Satış İşlemleri Tamamlandı"),
           "source_ref": "viewer:udf_viewer"}
    ev = extract_evidence([src], institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli == 5_715_000.0 and ev.alacaga_mahsuben is True


# --- Non-mutation + verification (canlı PASS DEĞİL) --------------------------------------- #
def test_run_pilot_fix3_offline_non_mutating(tmp_path):
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
         "source_ref": "live://detail"},
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
    assert after["genuine_uyap_count"] == 7 and after["sha256"] == before["sha256"]
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert sum(1 for x in recs if str(x.get("public_record_id")) == TARGET) == 1  # 8. gözlem YOK


# --- Yapısal freeze (4 moment; sale_prob yok; TOKİ external 0; conditional_on_trade) ------ #
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
