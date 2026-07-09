"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 1 testleri (OFFLINE; ağ/canlı YOK).

Gerçek ilk-canlı FAIL imzasını (yalnızca status_card toplandı → açık İhale Bedeli yok →
MISSING_AUCTION_PRICE; KDV Oranı : %20 ayrık düğüm; tanımlayıcılar normalize edilmedi)
offline yeniden üretir ve düzeltmeyi doğrular. Canlı PASS operatör yeniden-çalıştırması
gerektirir; bu testler CANLI PASS kanıtı DEĞİLdİr.
"""

from __future__ import annotations

import json
import shutil

from sold.ingestion.uyap import (
    asset_descriptors,
    audit_candidate,
    classify_access_pattern,
    classify_document_label,
    discover_document_links,
    extract_evidence,
    genuine_fingerprint,
    has_document_list_control,
    reconcile,
    run_pilot,
    select_row_document_actions,
    verify_pilot,
)


# --- Gerçek 2026/263 canlı içerik biçimini yansıtan fixture'lar (kişisel veri YOK) --------- #
def _detail_status_card(with_satis_tutari=False):
    txt = ("Muhammen Bedel 6.800.000,00 TL Teminat Miktarı 680.000,00 TL "
           "KDV Oranı : %20 Satış Durumu Satıldı "
           "50984 Ada 1 Parsel 12. Kat 60 Nolu B.B. konut")
    if with_satis_tutari:
        txt += " Satış Tutarı 5.715.000,00 TL"
    return {"artifact_type": "status_card", "text": txt, "source_ref": "live://detail"}


def _auction_result_doc():
    return {"artifact_type": "auction_result",
            "text": ("ARTIRMA SONUÇ TUTANAĞI 50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm "
                     "İhale Bedeli: 5.715.000,00 Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN "
                     "Satıldı Satış İşlemleri Tamamlandı"),
            "source_ref": "modal:artirma sonuc"}


def _appraisal_doc():
    return {"artifact_type": "appraisal_report",
            "text": "Bilirkişi Raporu Muhammen Bedel 6.800.000,00 TL 50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm",
            "source_ref": "modal:bilirkisi"}


# --- Belge-listesi kontrolü + etiket sınıflandırma ---------------------------------------- #
def test_document_list_control_may_be_non_anchor():
    html = '<html><body><button id="evrakBtn">İhale Evrak Listesi</button></body></html>'
    assert has_document_list_control(html) is True
    assert discover_document_links(html) == []   # <a> yok → anchor keşfi boş, kontrol yine de var


def test_document_label_variants_normalize():
    assert classify_document_label("BLR_BILIRKISI_RAPORU") == "appraisal_report"
    assert classify_document_label("Bilirkişi Raporu") == "appraisal_report"
    assert classify_document_label("Artırma Sonuç / Uzatma Tutanağı") == "auction_result"
    assert classify_document_label("İhale Artırma Sonuç Tutanağı") == "auction_result"
    assert classify_document_label("Satış İlanı") == "sale_notice"
    assert classify_document_label("Satış Şartnamesi Ve Tutanağı") == "sale_spec"
    assert classify_document_label("Alakasız Belge") is None


# --- Satır-yerel eye/view eşleme (global Nth-eye DEĞİL) ----------------------------------- #
def test_row_local_action_association_not_global_nth():
    rows = [
        {"label": "Satış İlanı", "actions": [{"kind": "link", "text": "Görüntüle", "href": "/a"}]},
        {"label": "Alakasız Satır", "actions": [{"kind": "eye", "text": "Görüntüle"}]},   # atlanmalı
        {"label": "Artırma Sonuç / Uzatma Tutanağı", "actions": [
            {"kind": "button", "text": "İndir"}, {"kind": "eye", "text": "Görüntüle"}]},
    ]
    sel = select_row_document_actions(rows)
    by_type = {s["artifact_type"]: s for s in sel}
    assert "auction_result" in by_type and by_type["auction_result"]["row_index"] == 2
    # seçilen eylem O SATIRA ait (global 1. eye değil); alakasız satır seçilmedi
    assert by_type["auction_result"]["action"]["text"] == "Görüntüle"
    assert all(s["label"] != "Alakasız Satır" for s in sel)


def test_access_pattern_classification():
    assert classify_access_pattern({"opened_new_tab": True, "is_pdf": False}) == "button_modal_popup_html"
    assert classify_access_pattern({"opened_new_tab": True, "is_pdf": True}) == "button_modal_new_tab_pdf"
    assert classify_access_pattern({"same_page_nav": True}) == "button_modal_same_page_viewer"
    assert classify_access_pattern({"download": True}) == "button_modal_download"
    assert classify_access_pattern({}) == "button_modal_unsupported"


def test_unsupported_javascript_action_reported_honestly():
    html = '<a href="javascript:openArtirma()">Artırma Sonuç Tutanağı</a>'
    links = discover_document_links(html)
    assert links and links[0]["artifact_type"] == "auction_result"
    assert links[0]["usable_href"] is False   # uydurma başarı YOK


# --- Canlı-biçim KDV + tanımlayıcı ayrıştırma --------------------------------------------- #
def test_live_style_kdv_separated_nodes():
    ev = extract_evidence([_detail_status_card()], institution="Ankara", file_id="2026/263 Esas")
    assert ev.kdv_rate == 20.0        # "KDV Oranı : %20" (ayrık düğüm) artık çözülür
    assert ev.appraisal_value == 6_800_000.0


def test_asset_identifier_wordings():
    assert asset_descriptors("50984 ada, 1 parsel")["ada"] == "50984"
    assert asset_descriptors("50984 ada, 1 parsel")["parsel"] == "1"
    assert asset_descriptors("ada 50984, parsel 1")["ada"] == "50984"
    assert asset_descriptors("ada 50984, parsel 1")["parsel"] == "1"
    d = asset_descriptors("12. kat, 60 nolu b.b.")
    assert d["floor"] == "12" and d["section_no"] == "60"
    assert asset_descriptors("60 no.lu bagimsiz bolum")["section_no"] == "60"


# --- İlk-canlı FAIL imzasının offline yeniden üretimi -------------------------------------- #
def test_reproduce_first_live_failure_status_card_only():
    ev = extract_evidence([_detail_status_card()], institution="Ankara", file_id="2026/263 Esas")
    au = audit_candidate(ev, reconcile([_detail_status_card()], "Ankara", "2026/263 Esas"))
    assert ev.appraisal_value == 6_800_000.0
    assert ev.terminal_status_text == "satildi"
    assert ev.ihale_bedeli is None                 # açık İhale Bedeli detay sayfada YOK
    assert au.auction_price is None
    assert au.decision == "MISSING_AUCTION_PRICE"  # gerçek ilk-canlı imza
    rec = reconcile([_detail_status_card()], "Ankara", "2026/263 Esas")
    assert rec.status == "ambiguous" and rec.matched_on == []


def test_result_card_satis_tutari_not_substituted_for_ihale_bedeli():
    # status_card Satış Tutarı gösterse de, açık İhale Bedeli belgesi yoksa ADMISSIBLE OLMAZ
    ev = extract_evidence([_detail_status_card(with_satis_tutari=True)], institution="Ankara", file_id="2026/263 Esas")
    au = audit_candidate(ev, reconcile([_detail_status_card(with_satis_tutari=True)], "Ankara", "2026/263 Esas"))
    assert ev.result_card_amount == 5_715_000.0
    assert ev.ihale_bedeli is None
    assert au.auction_price is None
    assert au.decision != "ADMISSIBLE_COMPLETED_SALE"   # sessizce terfi YOK


# --- Düzeltilmiş yol: resmî belge toplanınca ADMISSIBLE + reconciled ---------------------- #
def test_fixed_full_evidence_admissible_and_reconciled():
    artifacts = [_detail_status_card(), _auction_result_doc(), _appraisal_doc()]
    ev = extract_evidence(artifacts, institution="Ankara", file_id="2026/263 Esas")
    rec = reconcile(artifacts, "Ankara", "2026/263 Esas")
    au = audit_candidate(ev, rec)
    assert ev.ihale_bedeli == 5_715_000.0           # açık İhale Bedeli resmî belgeden
    assert ev.alacaga_mahsuben is True              # ALACAĞA MAHSUBEN tespit edildi
    assert au.auction_price == 5_715_000.0
    assert au.win_over_appraisal == 5_715_000.0 / 6_800_000.0   # 0.8404411764705882
    assert au.decision == "ADMISSIBLE_COMPLETED_SALE"
    assert rec.status == "reconciled"
    assert any(x.startswith("ada=") for x in rec.matched_on) and any(x.startswith("parsel=") for x in rec.matched_on)


def test_audit_admissible_only_when_ihale_and_terminal_present():
    # terminal var, İhale Bedeli yok → ADMISSIBLE değil
    only_detail = extract_evidence([_detail_status_card()], institution="Ankara", file_id="2026/263 Esas")
    assert audit_candidate(only_detail, reconcile([_detail_status_card()], "Ankara", "2026/263 Esas")).decision != "ADMISSIBLE_COMPLETED_SALE"
    # İhale Bedeli var ama terminal yok → ADMISSIBLE değil
    no_terminal = {"artifact_type": "auction_result", "text": "50984 Ada 1 Parsel İhale Bedeli: 5.715.000,00"}
    ev2 = extract_evidence([_appraisal_doc(), no_terminal], institution="Ankara", file_id="2026/263 Esas")
    assert audit_candidate(ev2, reconcile([_appraisal_doc(), no_terminal], "Ankara", "2026/263 Esas")).decision != "ADMISSIBLE_COMPLETED_SALE"


# --- Non-mutation + verification-layer PASS (canlı PASS DEĞİL) + yapısal freeze ----------- #
def test_run_pilot_fixed_offline_verification_pass_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    r = run_pilot(offline_artifacts=[_detail_status_card(), _auction_result_doc(), _appraisal_doc()],
                  genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["verification_layer_result"] == "PASS"    # düzeltme mantığı doğru
    assert r["pilot_outcome"] == "NOT_RUN"             # offline → CANLI PASS DEĞİL
    assert r["known_truth_comparison"]["required_all_passed"] is True
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"] and after["sha256"] == before["sha256"]
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert sum(1 for x in recs if str(x.get("public_record_id")) == "2026/263 Esas") == 1  # 8. gözlem YOK


def test_structural_freeze_four_moments_no_sale_prob():
    smm = genuine_fingerprint()["smm_moments"]
    assert set(smm) == {
        "uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd",
        "kap_log_ratio_mean", "kap_log_ratio_sd",
    }
    assert "uyap_sale_prob" not in smm
