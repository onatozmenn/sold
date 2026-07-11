"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 5 testleri (OFFLINE; ağ/canlı YOK).

Beşinci gerçek-canlı FAIL: page_state=search_listing, hedef 2026/263 kartı bulundu, kart-yerel
"İhale Evrak Listesi" kontrolü bulundu (card_link), tıklama başarılı ve operatör GERÇEK Chrome
penceresinde MODAL'ın görünür şekilde AÇILDIĞINI doğruladı. Ancak toplayıcı modalı tanıyamadı
(document_list_opened=false, labels=[], actions=0). Kök neden: modal/overlay algısı `.modal` /
role=dialog / <tr> gibi SEMANTİK-OLMAYAN işaretlemeye bağlıydı; gerçek UYAP overlay'i div/portal.

Fix 5: görünür belge-listesi SEMANTİĞİ (başlık + ≥2 distinkt tür), ORTAK-ATA konteyner keşfi,
etiket-anchoring ile satır bulma ve kısıtlı mojibake normalizasyonu. Bu testler yalnızca saf/
çevrimdışı davranışı doğrular; CANLI PASS DEĞİL (operatör yeniden-çalıştırması gerekir).
"""

from __future__ import annotations

import json
import shutil

from sold.ingestion.uyap import (
    audit_candidate,
    classify_document_label,
    classify_view_access_pattern,
    detect_document_container,
    detect_document_list,
    document_container_kind_for_entry,
    document_list_semantic_transition,
    extract_document_rows_semantic,
    extract_evidence,
    genuine_fingerprint,
    reconcile,
    resolve_row_view_action,
    run_pilot,
    visible_document_types,
)
from sold.ingestion.uyap.collect import _DOC_PRIORITY
from sold.ingestion.uyap.collect import (
    candidate_document_response_request_matches,
    document_modal_matches_response,
    document_response_uris,
    filter_document_rows_by_response_uris,
    newly_opened_document_modal,
    scope_open_document_modal,
)

TARGET = "2026/263 Esas"


# --- Fixture: gerçek gözlenen DIV-tabanlı overlay (.modal / role=dialog / <tr> YOK) ------- #
def _modal_html(labels=None, title="Ihale Evrak Listesi", hidden=False, overlay_cls="box"):
    labels = labels if labels is not None else [
        "Satis Ilani", "1- Belediye Imar Durumu", "2- Satis Sartnamesi Ve Tutanagi",
        "3- BILIRKISI RAPORU 2026 263 ESAS.udf", "1- Artirma Sonuc / Uzatma Tutanagi",
    ]
    style = " style='display:none'" if hidden else ""
    items = "".join(
        f"<div class=item{style}><span>{l}</span><i title=Indir></i><i title=Goruntule></i></div>"
        for l in labels
    )
    return (
        "<div class=page><div class=results><div class=card>Ankara 2026/263 Icra "
        "<button>Incele</button><button>Ihale Evrak Listesi</button></div></div>"
        f"<div class={overlay_cls}><div class=hd>{title}</div>{items}</div></div>"
    )


def _moji(s, enc="latin-1"):
    """Doğru Türkçe Unicode → tarayıcı/HTML mojibake eşdeğeri (UTF-8-as-Latin-1/cp1252)."""
    return s.encode("utf-8").decode(enc)


# --- A. Div-tabanlı görünür modal SEMANTİKle tanınır (.modal/dialog/role GEREKMEZ) -------- #
def test_div_overlay_recognized_as_document_modal_by_semantics():
    d = detect_document_list(_modal_html())
    assert d["detected"] is True
    assert d["container_strategy"] == "semantic_common_ancestor"  # .modal/dialog class YOK
    assert set(d["recognized_types"]) == {"appraisal_report", "auction_result", "sale_notice", "sale_spec"}


def test_modal_title_alone_is_insufficient():
    # Başlık var ama yalnız 1 tanınan tür (Belediye Imar Durumu tür DEĞİL) → detected False
    d = detect_document_list(_modal_html(labels=["Satis Ilani", "1- Belediye Imar Durumu"]))
    assert d["title_present"] is True
    assert d["detected"] is False


def test_at_least_two_distinct_document_types_required():
    two = detect_document_list(_modal_html(labels=["Satis Ilani", "1- Artirma Sonuc / Uzatma Tutanagi"]))
    assert two["detected"] is True and len(two["recognized_types"]) == 2


def test_single_document_type_requires_exact_modal_scope():
    single = _modal_html(labels=["Satis Ilani"])
    assert detect_document_list(single)["detected"] is False

    scoped = detect_document_list(single, min_types=1, trusted_scope=True)
    assert scoped["detected"] is True
    assert scoped["recognized_types"] == ["sale_notice"]


def test_scope_selects_only_open_exact_document_modal():
    hidden = (
        '<div id="ihale_evraklari_modal" class="modal fade" style="display: none">'
        '<h4>İhale Evrak Listesi</h4><div id="ihaleEvrakListesiResult">Satış İlanı</div></div>'
    )
    opened = (
        '<div id="ihale_evraklari_modal" class="modal fade in">'
        '<h4>İhale Evrak Listesi</h4><div id="ihaleEvrakListesiResult">Satış İlanı</div></div>'
    )
    assert scope_open_document_modal(hidden) is None
    scoped = scope_open_document_modal(hidden + opened)
    assert scoped is not None
    assert 'class="modal fade in"' in scoped
    assert newly_opened_document_modal(hidden, hidden + opened) is True
    assert newly_opened_document_modal(opened, opened) is False

    compact_hidden = hidden.replace('class="modal fade"', 'class="modal fade in"').replace(
        "display: none", "display:none"
    )
    assert scope_open_document_modal(compact_hidden + opened) == scoped

    hidden_ancestor = (
        '<div style="visibility:hidden">'
        + opened
        + "</div>"
    )
    assert scope_open_document_modal(hidden_ancestor) is None

    non_exact = (
        '<div id="other_modal" class="modal fade in">'
        '<h4>İhale Evrak Listesi</h4><div>Satış İlanı</div>'
        '<div>Artırma Sonuç Tutanağı</div></div>'
    )
    assert scope_open_document_modal(non_exact) is None


def test_candidate_bound_response_must_match_open_modal_uri():
    assert candidate_document_response_request_matches(
        "https://esatis.uyap.gov.tr/pp/getIhaleEvrakBilgileri_brd.ajx",
        "POST",
        "kayitId=17215252239",
        "17215252239",
    ) is True
    assert candidate_document_response_request_matches(
        "https://esatis.uyap.gov.tr/pp/getIhaleEvrakBilgileri_brd.ajx",
        "POST",
        "kayitId=111",
        "17215252239",
    ) is False
    assert candidate_document_response_request_matches(
        "https://evil.example/pp/getIhaleEvrakBilgileri_brd.ajx",
        "POST",
        "kayitId=17215252239",
        "17215252239",
    ) is False
    assert candidate_document_response_request_matches(
        "https://esatis.uyap.gov.tr/not-pp/getIhaleEvrakBilgileri_brd.ajx",
        "POST",
        "kayitId=17215252239",
        "17215252239",
    ) is False
    assert candidate_document_response_request_matches(
        "https://esatis.uyap.gov.tr/pp/getIhaleEvrakBilgileri_brd.ajx",
        "GET",
        "kayitId=17215252239",
        "17215252239",
    ) is False
    assert candidate_document_response_request_matches(
        "https://esatis.uyap.gov.tr/pp/getIhaleEvrakBilgileri_brd.ajx",
        "POST",
        "kayitId=111&kayitId=17215252239",
        "17215252239",
    ) is False

    uris = document_response_uris({"0": [{"evrakUri": "owned-token"}]})
    assert uris == ("owned-token",)
    assert document_modal_matches_response(
        '<div id="ihaleEvrakListesiResult"><button onclick="open(owned-token)"></button></div>',
        uris,
    ) is True
    assert document_modal_matches_response(
        '<div id="ihaleEvrakListesiResult"><button onclick="open(stale-token)"></button></div>',
        uris,
    ) is False

    owned_action = {
        "ownership_blob": "open(owned-token)",
        "semantic": "view",
        "response_owned": True,
    }
    stale_action = {"ownership_blob": "open(stale-token)"}
    download_action = {"ownership_blob": "downloadDocURL(x)", "semantic": "download"}
    owned = {
        "label": "Satış İlanı",
        "actions": [stale_action, download_action, owned_action],
    }
    stale = {"label": "Artırma Sonuç Tutanağı", "actions": [{"ownership_blob": "open(stale-token)"}]}
    assert filter_document_rows_by_response_uris([stale, owned], uris) == [
        {
            "label": "Satış İlanı",
            "actions": [
                owned_action,
                {**download_action, "row_owner_uri": "owned-token", "response_owned": True},
            ],
        }
    ]
    assert filter_document_rows_by_response_uris([stale], uris) == []

    unsupported_uri = ("unsupported-token",)
    assert filter_document_rows_by_response_uris([owned], uris + unsupported_uri) == [
        {
            "label": "Satış İlanı",
            "actions": [
                owned_action,
                {**download_action, "row_owner_uri": "owned-token", "response_owned": True},
            ],
        }
    ]

    duplicate_owner = {
        "label": "Satış İlanı",
        "actions": [
            owned_action,
            {"ownership_blob": "preview(owned-token)", "semantic": "view"},
            download_action,
        ],
    }
    filtered = filter_document_rows_by_response_uris([duplicate_owner], uris)
    assert all(action.get("semantic") != "download" for action in filtered[0]["actions"])

    prefix_collision = {
        "label": "Satış İlanı",
        "actions": [{"ownership_blob": "open(owned-token-10)", "semantic": "view"}],
    }
    assert filter_document_rows_by_response_uris(
        [prefix_collision], ("owned-token-1",)
    ) == []
    assert document_modal_matches_response(
        '<button onclick="open(owned-token-10)"></button>',
        ("owned-token-1",),
    ) is False

    actionless = {"label": "Satış Şartnamesi", "actions": []}
    assert filter_document_rows_by_response_uris([actionless, owned], uris)

    direct_download = {
        "label": "Satış İlanı",
        "actions": [{
            "ownership_blob": "download(owned-token)",
            "semantic": "download",
        }],
    }
    direct_filtered = filter_document_rows_by_response_uris([direct_download], uris)
    assert len(direct_filtered[0]["actions"]) == 1


def test_hidden_document_labels_are_ignored():
    d = detect_document_list(_modal_html(hidden=True))
    assert d["detected"] is False           # tüm etiketler display:none → görünür kanıt yok
    assert visible_document_types(_modal_html(hidden=True)) == []


# --- B. Pre-click / post-click semantik geçiş -------------------------------------------- #
def test_pre_click_none_post_click_multiple_transition():
    before = "<div class=card>Ankara 2026/263 Icra <button>Incele</button></div>"
    t = document_list_semantic_transition(before, _modal_html())
    assert t["pre_click_visible_document_types"] == []
    assert len(t["post_click_visible_document_types"]) >= 2
    assert t["transition_detected"] is True


def test_same_content_hidden_before_visible_after_detected():
    before = _modal_html(hidden=True)   # şablon var ama gizli
    after = _modal_html(hidden=False)   # aynı içerik görünür
    t = document_list_semantic_transition(before, after)
    assert t["pre_click_visible_document_types"] == []
    assert t["transition_detected"] is True


# --- C. Ortak-ata konteyner keşfi; body/html/tüm-sonuç SEÇİLMEZ -------------------------- #
def test_nearest_shared_ancestor_is_selected_not_body():
    c = detect_document_container(_modal_html())
    assert c["found"] is True
    assert c["container_tag"] == "div"          # .box (satırların ortak atası), body DEĞİL
    assert c["strategy"] == "semantic_common_ancestor"


def test_body_or_html_cannot_be_document_container():
    # İki etiket AYRI üst-düzey kapsayıcıda → ortak ata çok geniş (document/body) → bulunamaz
    html = "<div>Satis Ilani</div><div>1- Artirma Sonuc / Uzatma Tutanagi</div>"
    c = detect_document_container(html)
    assert c["found"] is False and c["reason"] == "ancestor_too_broad"


def test_whole_search_results_container_cannot_be_selected():
    html = ("<div class=results>"
            "<div class=card>2024/99 Icra Satis Ilani</div>"
            "<div class=card>2026/263 Icra 1- Artirma Sonuc / Uzatma Tutanagi</div></div>")
    c = detect_document_container(html)
    assert c["found"] is False and c["reason"] == "spans_multiple_records"


def test_semantic_modal_class_strategy_when_overlay_class_present():
    c = detect_document_container(_modal_html(overlay_cls="modal-overlay"))
    assert c["found"] is True and c["strategy"] == "semantic_modal_class"


# --- D. Türkçe / mojibake normalizasyonu ------------------------------------------------- #
def test_mojibake_title_normalizes_compatibly():
    moji_title = _moji("İhale Evrak Listesi")
    d = detect_document_list(_modal_html(title=moji_title))
    assert d["title_present"] is True and d["detected"] is True


def test_mojibake_satis_ilani_maps_to_sale_notice():
    assert classify_document_label(_moji("Satış İlanı")) == "sale_notice"
    assert classify_document_label(_moji("Satış İlanı", "cp1252")) == "sale_notice"


def test_mojibake_bilirkisi_maps_to_appraisal_report():
    assert classify_document_label(_moji("BİLİRKİŞİ RAPORU")) == "appraisal_report"
    assert classify_document_label(_moji("3- BİLİRKİŞİ RAPORU 2026 263 ESAS.udf", "cp1252")) == "appraisal_report"


def test_mojibake_artirma_sonuc_maps_to_auction_result():
    assert classify_document_label(_moji("1- Artırma Sonuç / Uzatma Tutanağı")) == "auction_result"
    assert classify_document_label(_moji("Uzatma Tutanağı", "cp1252")) == "auction_result"


def test_mojibake_sartnamesi_maps_to_sale_spec():
    assert classify_document_label(_moji("2- Satış Şartnamesi Ve Tutanağı")) == "sale_spec"


def test_correct_turkish_unicode_maps_identically():
    assert classify_document_label("Satış İlanı") == "sale_notice"
    assert classify_document_label("BİLİRKİŞİ RAPORU") == "appraisal_report"
    assert classify_document_label("1- Artırma Sonuç / Uzatma Tutanağı") == "auction_result"
    assert classify_document_label("2- Satış Şartnamesi Ve Tutanağı") == "sale_spec"


def test_mojibake_modal_fully_detected():
    labels = [_moji("Satış İlanı"), _moji("2- Satış Şartnamesi Ve Tutanağı"),
              _moji("3- BİLİRKİŞİ RAPORU 2026 263 ESAS.udf"), _moji("1- Artırma Sonuç / Uzatma Tutanağı")]
    d = detect_document_list(_modal_html(labels=labels, title=_moji("İhale Evrak Listesi")))
    assert d["detected"] is True
    assert set(d["recognized_types"]) == {"appraisal_report", "auction_result", "sale_notice", "sale_spec"}


# --- E. Satır keşfi tr/li/class*=row OLMADAN; etiket-ata + eylemler ---------------------- #
def test_row_discovery_without_tr_li_or_row_class():
    rows = extract_document_rows_semantic(_modal_html())
    types = {classify_document_label(r["label"]) for r in rows}
    assert {"appraisal_report", "auction_result", "sale_notice", "sale_spec"} <= types
    # Belediye Imar Durumu satır DEĞİL (gerekli kanıt artifact'ı değil)
    assert all("belediye" not in (r["label"] or "").lower() for r in rows)


def test_semantic_label_ancestor_becomes_document_row_with_actions():
    rows = extract_document_rows_semantic(_modal_html())
    for r in rows:
        assert len(r["actions"]) >= 1   # etiket-ata eyleme sahip satır (indirme + eye)


# --- F. Satır-yerel indirme vs eye/görüntüle ayrımı -------------------------------------- #
def test_download_and_eye_action_distinguished():
    row = {"label": "Satis Ilani", "actions": [{"kind": "control", "text": "Indir"}, {"kind": "eye", "text": "Goruntule"}]}
    res = resolve_row_view_action(row["actions"])
    assert res["resolved"] is True and res["view_action"]["kind"] == "eye"
    assert res["download_action_detected"] is True


def test_download_only_row_is_unresolved_not_clicked():
    res = resolve_row_view_action([{"kind": "control", "text": "Indir"}])
    assert res["resolved"] is False and res["view_action"] is None and res["reason"] == "download_only"


def test_ambiguous_actions_are_unresolved_not_clicked():
    res = resolve_row_view_action([{"kind": "control", "text": "A"}, {"kind": "control", "text": "B"}])
    assert res["resolved"] is False and res["view_action"] is None


def test_extracted_rows_resolve_to_view_actions():
    rows = extract_document_rows_semantic(_modal_html())
    resolved = {classify_document_label(r["label"]): resolve_row_view_action(r["actions"])["resolved"] for r in rows}
    assert resolved.get("auction_result") is True and resolved.get("appraisal_report") is True


# --- G. Listing-modal vs detail-panel + öncelik + shared akış ----------------------------- #
def test_listing_entry_classifies_container_as_listing_modal():
    assert document_container_kind_for_entry("search_listing") == "listing_modal"


def test_detail_entry_remains_same_page_tab_panel():
    assert document_container_kind_for_entry("record_detail") == "same_page_tab_panel"


def test_listing_modal_access_pattern_maps_to_modal_prefix():
    p = classify_view_access_pattern("listing_modal", {"new_page": True, "is_udf": True})
    assert p == "modal_new_tab_udf_viewer"
    p2 = classify_view_access_pattern("same_page_tab_panel", {"new_page": True, "is_udf": True})
    assert p2 == "same_page_tab_new_tab_udf_viewer"


def test_document_collection_priority_auction_first():
    assert _DOC_PRIORITY["auction_result"] < _DOC_PRIORITY["appraisal_report"]
    assert _DOC_PRIORITY["appraisal_report"] < _DOC_PRIORITY["sale_notice"]
    assert _DOC_PRIORITY["sale_notice"] < _DOC_PRIORITY["sale_spec"]


# --- H. Fiyat semantiği: result-card Satış Tutarı SUBSTİTÜE EDİLMEZ ----------------------- #
def test_result_card_satis_tutari_not_substituted():
    card = {"artifact_type": "status_card",
            "text": "Satış Durumu Satıldı Satış Tutarı 5.715.000,00 TL 50984 Ada 1 Parsel"}
    ev = extract_evidence([card], institution="Ankara", file_id=TARGET)
    au = audit_candidate(ev, reconcile([card], "Ankara", TARGET))
    assert ev.ihale_bedeli is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


# --- I. Non-mutation + verification (canlı PASS DEĞİL) ----------------------------------- #
def test_run_pilot_fix5_offline_non_mutating(tmp_path):
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


# --- J. Yapısal freeze (4 moment; sale_prob yok; TOKİ external 0; conditional_on_trade) --- #
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
