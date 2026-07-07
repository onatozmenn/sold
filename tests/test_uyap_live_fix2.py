"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 2 testleri (OFFLINE; ağ/canlı YOK).

İkinci gerçek-canlı FAIL: kontrol bulundu (link) ama kod MODAL bekledi; gerçek UYAP belge
listesi AYNI-SAYFA tab/panel'dir ve satır-yerel eye YENİ-SEKME UDF görüntüleyici açar
(viewer.jsp?mimeType=Udf). Bu testler aynı-sayfa panel algısını, satır-yerel eye eşlemeyi,
UDF görüntüleyici sınıflandırmasını ve temsil-farkındalığını doğrular. CANLI PASS operatör
yeniden-çalıştırması gerektirir; bu testler canlı PASS kanıtı DEĞİLdİr.
"""

from __future__ import annotations

import json
import shutil

from sold.ingestion.uyap import (
    audit_candidate,
    classify_document_label,
    classify_document_list_container,
    classify_view_access_pattern,
    classify_viewer_representation,
    classify_viewer_url,
    extract_evidence,
    extract_panel_document_rows,
    genuine_fingerprint,
    has_document_list_control,
    panel_has_documents,
    reconcile,
    run_pilot,
    select_row_document_actions,
    viewer_mime_hint,
)


# --- Gerçek gözlenen aynı-sayfa panel + belge-satırları fixture'ı --------------------------- #
def _detail_page_with_tab():
    return ('<html><body><ul class="tabs">'
            '<li><a href="#aciklama">Açıklama</a></li>'
            '<li><a href="#detay">Detaylı İnceleme</a></li>'
            '<li><a href="#evrak">İhale Evrak Listesi</a></li>'
            '<li><a href="#teklif">İhaleye Ait Tekliflerim</a></li>'
            '</ul><div>Muhammen Bedel 6.800.000,00 TL KDV Oranı : %20 Satış Durumu Satıldı '
            '50984 Ada 1 Parsel 12. Kat 60 Nolu B.B.</div></body></html>')


def _document_panel_html():
    return (
        '<table id="evrakListesi"><tbody>'
        '<tr><td>Satış İlanı</td><td><a title="İndir">D</a><a title="Görüntüle">G</a></td></tr>'
        '<tr><td>1- Belediye İmar Durumu</td><td><a title="Görüntüle">G</a></td></tr>'
        '<tr><td>2- Satış Şartnamesi Ve Tutanağı</td><td><a title="Görüntüle">G</a></td></tr>'
        '<tr><td>3- BILIRKISI RAPORU 2026 263 ESAS.udf</td><td><a title="Görüntüle">G</a></td></tr>'
        '<tr><td>1- Artırma Sonuç / Uzatma Tutanağı</td><td><a title="İndir">D</a><a title="Görüntüle">G</a></td></tr>'
        '</tbody></table>'
    )


# Görüntüleyici ile toplanmış gibi kaynak metinleri (canlı yol pragma; burada sonuç simüle) ---
def _auction_result_source():
    return {"artifact_type": "auction_result",
            "text": ("ARTIRMA SONUÇ TUTANAĞI 50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm "
                     "İhale Bedeli: 5.715.000,00 Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN "
                     "Satıldı Satış İşlemleri Tamamlandı"),
            "source_ref": "viewer:udf_viewer"}


def _appraisal_source():
    return {"artifact_type": "appraisal_report",
            "text": "BİLİRKİŞİ RAPORU 6.800.000,00 TL 50984 Ada 1 Parsel 60 Nolu Bağımsız Bölüm",
            "source_ref": "viewer:udf_viewer"}


def _detail_status_card():
    return {"artifact_type": "status_card",
            "text": "Muhammen Bedel 6.800.000,00 TL KDV Oranı : %20 Satış Durumu Satıldı 50984 Ada 1 Parsel 12. Kat 60 Nolu B.B.",
            "source_ref": "live://detail"}


# --- Aynı-sayfa tab/panel algısı ---------------------------------------------------------- #
def test_document_list_control_is_same_page_tab():
    assert has_document_list_control(_detail_page_with_tab()) is True


def test_tab_click_reveals_same_page_rows_without_modal():
    rows = extract_panel_document_rows(_document_panel_html())
    labels = {classify_document_label(r["label"]) for r in rows}
    assert "auction_result" in labels and "appraisal_report" in labels
    assert "sale_notice" in labels and "sale_spec" in labels
    # "Belediye İmar Durumu" ilgili değil → satır olmaz
    assert all("belediye" not in r["label"].lower() for r in rows)


def test_same_page_panel_success_detected_from_labels():
    assert panel_has_documents(_document_panel_html()) is True
    assert panel_has_documents("<div>alakasız içerik</div>") is False


def test_document_list_opened_true_while_modal_false():
    kind = classify_document_list_container({"panel_labels_visible": True})
    assert kind == "same_page_tab_panel"
    document_list_opened = kind not in ("not_opened", "unsupported")
    document_modal_opened = kind in ("modal", "dialog")
    assert document_list_opened is True and document_modal_opened is False
    assert classify_document_list_container({"modal_visible": True}) == "modal"
    assert classify_document_list_container({}) == "not_opened"


def test_real_label_variants_classify():
    assert classify_document_label("Satış İlanı") == "sale_notice"
    assert classify_document_label("BILIRKISI RAPORU 2026 263 ESAS.udf") == "appraisal_report"
    assert classify_document_label("1- Artırma Sonuç / Uzatma Tutanağı") == "auction_result"
    assert classify_document_label("2- Satış Şartnamesi Ve Tutanağı") == "sale_spec"
    assert classify_document_label("1- Belediye İmar Durumu") is None


# --- Satır-yerel eye eşleme (global Nth-eye DEĞİL) ---------------------------------------- #
def test_row_local_eye_association_not_global_nth():
    rows = extract_panel_document_rows(_document_panel_html())
    sel = select_row_document_actions(rows)
    by_type = {s["artifact_type"]: s for s in sel}
    assert "auction_result" in by_type
    # auction_result eylemi KENDİ satırından (İndir değil, Görüntüle)
    assert by_type["auction_result"]["action"]["kind"] == "eye"
    assert "goruntule" in by_type["auction_result"]["action"]["text"].lower() or by_type["auction_result"]["action"]["text"] == "Görüntüle"
    # her seçim ayrı satır (global tek eye değil)
    assert len({s["row_index"] for s in sel}) == len(sel)


# --- Yeni-sekme UDF görüntüleyici sınıflandırması ----------------------------------------- #
def test_udf_viewer_url_classification():
    assert classify_viewer_url("/pp/viewer.jsp?mimeType=Udf&evrakId=16737826545") == "udf_viewer"
    assert viewer_mime_hint("/pp/viewer.jsp?mimeType=Udf&evrakId=1") == "udf"
    assert classify_viewer_url("/pp/viewer.jsp?mimeType=Pdf&evrakId=1") == "pdf_viewer"
    assert classify_viewer_url("https://x/doc.html") == "html"


def test_udf_not_mislabeled_ordinary_popup_html():
    p = classify_view_access_pattern("same_page_tab_panel", {"new_page": True, "is_udf": True})
    assert p == "same_page_tab_new_tab_udf_viewer"      # popup_html DEĞİL
    assert classify_view_access_pattern("same_page_tab_panel", {"new_page": True, "is_pdf": True}) == "same_page_tab_new_tab_pdf"


def test_viewer_representation_diagnostics():
    assert classify_viewer_representation({"text_available": True}) == "dom_text"
    assert classify_viewer_representation({"iframe": 1}) == "iframe"
    assert classify_viewer_representation({"embed": 1}) == "embed_object"
    assert classify_viewer_representation({"canvas": 2}) == "canvas_image_only"   # görüntü-yalnız → desteklenmiyor
    assert classify_viewer_representation({"image": 3}) == "canvas_image_only"
    assert classify_viewer_representation({}) == "unknown"


# --- Fiyat semantiği + kaynak metinden çıkarım -------------------------------------------- #
def test_result_card_satis_tutari_not_substituted():
    card = {"artifact_type": "status_card", "text": "Satış Durumu Satıldı Satış Tutarı 5.715.000,00 TL 50984 Ada 1 Parsel"}
    ev = extract_evidence([card], institution="Ankara", file_id="2026/263 Esas")
    au = audit_candidate(ev, reconcile([card], "Ankara", "2026/263 Esas"))
    assert ev.ihale_bedeli is None and ev.result_card_amount == 5_715_000.0
    assert au.auction_price is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


def test_official_result_source_selects_ihale_bedeli_and_alacaga():
    ev = extract_evidence([_auction_result_source()], institution="Ankara", file_id="2026/263 Esas")
    assert ev.ihale_bedeli == 5_715_000.0
    assert ev.alacaga_mahsuben is True


def test_independent_artifacts_reconcile_and_pq():
    artifacts = [_detail_status_card(), _auction_result_source(), _appraisal_source()]
    ev = extract_evidence(artifacts, institution="Ankara", file_id="2026/263 Esas")
    rec = reconcile(artifacts, "Ankara", "2026/263 Esas")
    au = audit_candidate(ev, rec)
    assert rec.status == "reconciled"
    assert any(x.startswith("ada=50984") for x in rec.matched_on)
    assert any(x.startswith("parsel=1") for x in rec.matched_on)
    assert au.decision == "ADMISSIBLE_COMPLETED_SALE"
    assert au.win_over_appraisal == 5_715_000.0 / 6_800_000.0   # 0.8404411764705882


def test_audit_admissible_requires_ihale_terminal_appraisal():
    only_card = extract_evidence([_detail_status_card()], institution="Ankara", file_id="2026/263 Esas")
    assert audit_candidate(only_card, reconcile([_detail_status_card()], "Ankara", "2026/263 Esas")).decision == "MISSING_AUCTION_PRICE"


# --- Non-mutation + verification PASS (canlı PASS DEĞİL) + freeze ------------------------- #
def test_run_pilot_fix2_offline_non_mutating(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    r = run_pilot(offline_artifacts=[_detail_status_card(), _auction_result_source(), _appraisal_source()],
                  genuine_path=gp, store_dir=tmp_path, report_path=tmp_path / "r.json")
    assert r["verification_layer_result"] == "PASS"
    assert r["pilot_outcome"] == "NOT_RUN"       # offline → CANLI PASS DEĞİL
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] and mg["genuine_uyap_count_unchanged"] and mg["smm_moments_unchanged"]
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == 7 and after["sha256"] == before["sha256"]
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert sum(1 for x in recs if str(x.get("public_record_id")) == "2026/263 Esas") == 1  # 8. gözlem YOK


def test_structural_freeze_four_moments_no_sale_prob():
    smm = genuine_fingerprint()["smm_moments"]
    assert set(smm) == {
        "uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd",
        "kap_log_ratio_mean", "kap_log_ratio_sd",
    }
    assert "uyap_sale_prob" not in smm
