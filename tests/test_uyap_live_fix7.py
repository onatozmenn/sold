"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 7 testleri (OFFLINE; ağ/canlı YOK).

Yedinci gerçek-canlı FAIL: Fix 5 (modal/satır algısı) + Fix 6 (fa-arrow-down → download) CANLI çalıştı
(download_action_resolved=true), ama HER satır için view_action_resolved=false (no_view_action) ve
viewer_pages_opened=0. Kök neden: MANTIKSAL satır sınırı ÇOK DAR; introspektör bare ``<i>`` ikon
descendant'larını (belge ikonu + fa-arrow-down) ActionSpec sayıyor; AYRI eye/view kontrolü (kardeş)
seçili semantik-etiket-atasının DIŞINDA kalıyor.

Fix 7: MANTIKSAL satır atası = GERÇEK tıklanabilir kontrol içeren, tek-belge-etiketli en küçük ata
(kardeş-actionable genişlemesiyle ayrı eye kontrolünü kapsar); ikon descendant'lar sahip actionable
kontrolün metadata'sıdır. Ek: pre-opened/stale liste durumunda kart-kontrolüne yeniden tıklamadan
geçerli görünür listeyi yeniden kullan. Bu testler yalnız çevrimdışı davranışı doğrular; CANLI PASS DEĞİL.
"""

from __future__ import annotations

import json
import shutil

from bs4 import BeautifulSoup

from sold.ingestion.uyap import (
    audit_candidate,
    classify_document_label,
    detect_document_list,
    extract_document_rows_semantic,
    extract_evidence,
    genuine_fingerprint,
    preopened_document_list_reusable,
    reconcile,
    resolve_row_view_action,
    run_pilot,
)
from sold.ingestion.uyap.collect import _row_action_specs, _semantic_row_for_label, _semantic_label_elements, _bs_soup

TARGET = "2026/263 Esas"


# --- Run-7 gerçek-stil satır: etiket + belge ikonu + indirme oku BİR butonda; AYRI eye butonu kardeş -- #
def _row(label, primary_dl="fa fa-arrow-down", view_icon="fa fa-eye"):
    return (
        '<div class="logical-document-row">'
        '<button class="document-primary-control">'
        f'<i class="icon-docs icon-special-pink"></i><span>{label}</span>'
        f'<i class="{primary_dl}"></i></button>'
        f'<button class="document-view-control"><i class="{view_icon}"></i></button>'
        '</div>'
    )


def _modal(*labels, title="Ihale Evrak Listesi"):
    body = "".join(_row(l) for l in labels)
    return f'<div class="modal-overlay"><div class=hd>{title}</div>{body}</div>'


_FOUR = ("Satis Ilani", "2- Satis Sartnamesi Ve Tutanagi",
         "3- BILIRKISI RAPORU 2026 263 ESAS.udf", "1- Artirma Sonuc / Uzatma Tutanagi")


def _rows(html):
    return extract_document_rows_semantic(html)


def _by_type(html):
    return {classify_document_label(r["label"]): r for r in _rows(html)}


# --- A. Run-7 reprodüksiyonu: mantıksal satır iki BUTONU kapsar (bare <i> DEĞİL) ---------- #
def test_run7_row_captures_primary_and_sibling_view_button():
    rows = _rows(_modal(*_FOUR))
    assert len(rows) == 4
    for r in rows:
        assert len(r["actions"]) == 2                       # iki BUTON (bare <i> değil)
        assert r["row_boundary"]["actionable_control_tags"] == ["button", "button"]
        res = resolve_row_view_action(r["actions"])
        assert res["resolved"] is True                      # ayrı eye butonu artık dahil
        assert res["download_action_resolved"] is True      # primary buton = download


def test_auction_result_resolves_view_and_download():
    r = _by_type(_modal(*_FOUR))["auction_result"]
    res = resolve_row_view_action(r["actions"])
    assert res["view_action"]["semantic"] == "view" and res["download_action"]["semantic"] == "download"


# --- B. İkon descendant ≠ ActionSpec ------------------------------------------------------ #
def test_two_icons_in_one_button_is_one_actionable_control():
    specs = _row_action_specs(BeautifulSoup(
        '<div><button><i class="icon-docs"></i><span>x</span><i class="fa fa-arrow-down"></i></button></div>',
        "html.parser"))
    assert len(specs) == 1                                   # tek buton (iki <i> ayrı eylem DEĞİL)
    assert specs[0]["tag"] == "button" and specs[0]["semantic"] == "download"
    assert "fa-arrow-down" in specs[0]["icon_tokens"]        # ikon token sahip butonda


def test_one_download_button_plus_one_view_button_is_two_controls():
    specs = _row_action_specs(BeautifulSoup(
        '<div><button><i class="fa fa-arrow-down"></i></button><button><i class="fa fa-eye"></i></button></div>',
        "html.parser"))
    assert len(specs) == 2 and [s["tag"] for s in specs] == ["button", "button"]
    assert {s["semantic"] for s in specs} == {"download", "view"}


def test_nested_download_arrow_icon_is_not_its_own_actionspec():
    r = _by_type(_modal("1- Artirma Sonuc / Uzatma Tutanagi"))["auction_result"]
    # eylemler butonlar; fa-arrow-down descendant token olarak primary butonda
    assert all(a["tag"] == "button" for a in r["actions"])
    prim = [a for a in r["actions"] if a["semantic"] == "download"][0]
    assert "fa-arrow-down" in prim["icon_tokens"]


def test_nested_fa_eye_gives_view_to_owner_button():
    r = _by_type(_modal("1- Artirma Sonuc / Uzatma Tutanagi"))["auction_result"]
    view = [a for a in r["actions"] if a["semantic"] == "view"][0]
    assert view["tag"] == "button" and "fa-eye" in view["icon_tokens"]


# --- C. Actionable-sibling genişlemesi (label-butonun iç tıklanabilirinden kaçış) --------- #
def test_actionable_sibling_expansion_captures_view():
    # primary buton İÇİNDE bir <a> download var (iç tıklanabilir); yine de kardeş eye kapsanmalı
    html = (
        '<div class="row">'
        '<button class="primary"><span>1- Artirma Sonuc / Uzatma Tutanagi</span>'
        '<a class="fa fa-arrow-down"></a></button>'
        '<button class="eye"><i class="fa fa-eye"></i></button></div>'
    )
    label_els = _semantic_label_elements(_bs_soup(html))
    _el, actions, meta = _semantic_row_for_label(label_els[0][0])
    assert meta["row_boundary_strategy"] == "actionable_sibling_expansion"
    assert meta["logical_row_actionable_control_count"] == 2
    res = resolve_row_view_action(actions)
    assert res["resolved"] is True and res["download_action_resolved"] is True


# --- D. Satır sınırı guard'ları: tek belge kimliği; modal/body reddedilir ----------------- #
def test_logical_row_contains_exactly_one_recognized_type():
    for r in _rows(_modal(*_FOUR)):
        assert r["row_boundary"]["logical_row_recognized_type_count"] == 1


def test_row_ancestor_is_not_the_whole_modal():
    rows = _rows(_modal(*_FOUR))
    # satır atası modal-overlay DEĞİL (logical-document-row div); 4 tür değil 1 tür
    assert all(r["row_boundary"]["logical_row_ancestor_kind"] == "div" for r in rows)
    assert all(r["row_boundary"]["logical_row_recognized_type_count"] == 1 for r in rows)


def test_multiple_recognized_types_ancestor_rejected_as_row():
    # tek satır iki belge etiketi içerse (bozuk), satır o kadar geniş olmaz — tür sayısı 1 kalır
    rows = _rows(_modal(*_FOUR))
    assert max(r["row_boundary"]["logical_row_recognized_type_count"] for r in rows) == 1


# --- E. Satır-yerellik: her belgenin kendi eylemi (global/Nth/index eşleme YOK) ----------- #
def test_row_locality_each_document_has_own_view_and_download():
    bt = _by_type(_modal(*_FOUR))
    for atype in ("auction_result", "appraisal_report", "sale_notice", "sale_spec"):
        res = resolve_row_view_action(bt[atype]["actions"])
        assert res["resolved"] is True and res["download_action_resolved"] is True
        # her satırda TAM 2 kontrol (kendi butonları); global eye listesi/Nth eşleme yok
        assert len(bt[atype]["actions"]) == 2


def test_bare_icon_rows_still_work_via_icon_fallback():
    # Buton yoksa (Fix-5/6 bare-icon satırı) ikon-yalnız fallback korunur
    html = ('<div class="modal-overlay"><div class=hd>Ihale Evrak Listesi</div>'
            '<div class=item><span>Satis Ilani</span><i title=Indir></i><i title=Goruntule></i></div>'
            '<div class=item><span>1- Artirma Sonuc / Uzatma Tutanagi</span><i title=Indir></i><i title=Goruntule></i></div>'
            '</div>')
    rows = _rows(html)
    assert len(rows) == 2
    assert all(r["row_boundary"]["row_boundary_strategy"] == "icon_only_ancestor" for r in rows)
    for r in rows:
        assert resolve_row_view_action(r["actions"])["resolved"] is True


# --- F. Belirsiz/pozisyon: kardeş konumu view ANLAMINA GELMEZ ----------------------------- #
def test_unknown_sibling_control_remains_unresolved():
    html = ('<div class="row"><button class="primary"><span>Satis Ilani</span>'
            '<i class="fa fa-arrow-down"></i></button>'
            '<button class="mystery"><i class="icon-foo"></i></button></div>')
    _el, actions, _m = _semantic_row_for_label(_semantic_label_elements(_bs_soup(html))[0][0])
    res = resolve_row_view_action(actions)
    assert res["resolved"] is False                         # kardeş var ama view semantiği YOK
    assert res["download_action_resolved"] is True          # primary yine download


def test_sibling_position_alone_does_not_imply_view():
    html = ('<div class="row"><button class="a"><span>Satis Ilani</span><i class="icon-x"></i></button>'
            '<button class="b"><i class="icon-y"></i></button></div>')
    _el, actions, _m = _semantic_row_for_label(_semantic_label_elements(_bs_soup(html))[0][0])
    assert resolve_row_view_action(actions)["resolved"] is False  # ikinci/sağdaki view DEĞİL


# --- G. Fix-6.1 aynı-satır fallback düzeltilmiş satırın KENDİ download eylemini alır ------ #
def test_same_row_fallback_uses_corrected_rows_own_download():
    r = _by_type(_modal(*_FOUR))["auction_result"]
    res = resolve_row_view_action(r["actions"])
    # aynı DocumentRow hem view hem download çözer; fallback bu satırın download'ını kullanır
    assert res["download_action"]["semantic"] == "download"
    assert res["download_action"] in r["actions"] and res["view_action"] in r["actions"]


# --- H. Pre-opened / stale belge listesi yeniden kullanımı -------------------------------- #
def _listing_with_preopened(card_file="2026/263 Icra", card_inst="Ankara Gayrimenkul Satis Icra Dairesi"):
    return (
        f'<div class=ilan-card>{card_inst} {card_file} '
        '<button>Incele</button><button>Ihale Evrak Listesi</button></div>'
        + _modal(*_FOUR)
    )


LISTING_URL = "https://esatis.uyap.gov.tr/pp/index.jsp"


def test_valid_preopened_list_is_reusable():
    assert preopened_document_list_reusable(_listing_with_preopened(), LISTING_URL, TARGET) is True


def test_preopened_reuse_flows_to_row_collection_with_fix7_boundary():
    rows = _rows(_listing_with_preopened())
    assert len(rows) == 4
    for r in rows:
        assert r["row_boundary"]["actionable_control_tags"] == ["button", "button"]
        assert resolve_row_view_action(r["actions"])["resolved"] is True


def test_hidden_preloaded_labels_do_not_trigger_preopened_reuse():
    hidden_modal = _modal(*_FOUR).replace('class="modal-overlay"', 'class="modal-overlay" style="display:none"')
    html = ('<div class=ilan-card>Ankara 2026/263 Icra <button>Incele</button>'
            '<button>Ihale Evrak Listesi</button></div>' + hidden_modal)
    assert preopened_document_list_reusable(html, LISTING_URL, TARGET) is False


def test_raw_html_only_labels_without_container_not_reused():
    # etiketler var ama ortak-ata konteyner yok (ayrı üst-düzey div'ler) → detect False
    html = ('<div class=ilan-card>Ankara 2026/263 Icra <button>Incele</button>'
            '<button>Ihale Evrak Listesi</button></div>'
            '<div>Satis Ilani</div><div>1- Artirma Sonuc / Uzatma Tutanagi</div>')
    assert preopened_document_list_reusable(html, LISTING_URL, TARGET) is False


def test_stale_list_from_unrelated_candidate_not_reused():
    html = _listing_with_preopened(card_file="2024/99 Icra", card_inst="Izmir 6. Icra Dairesi")
    assert preopened_document_list_reusable(html, LISTING_URL, TARGET) is False  # hedef kimlik yok


def test_preopened_requires_supported_page_state():
    # viewer sayfası pre-opened liste SAYILMAZ
    assert preopened_document_list_reusable(_modal(*_FOUR),
                                            "https://esatis.uyap.gov.tr/pp/viewer.jsp?mimeType=Udf", TARGET) is False


# --- I. Fiyat/appraisal semantiği ve known-truth enjeksiyon YOK --------------------------- #
def test_result_card_satis_tutari_not_substituted():
    card = {"artifact_type": "status_card",
            "text": "Satış Durumu Satıldı Satış Tutarı 5.715.000,00 TL 50984 Ada 1 Parsel"}
    ev = extract_evidence([card], institution="Ankara", file_id=TARGET)
    au = audit_candidate(ev, reconcile([card], "Ankara", TARGET))
    assert ev.ihale_bedeli is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


def test_no_known_truth_injected_for_unextracted_auction():
    downloaded = {"artifact_type": "auction_result", "source_ref": "download:.udf", "extraction_supported": False}
    ev = extract_evidence([downloaded], institution="Ankara", file_id=TARGET)
    assert ev.ihale_bedeli is None            # 5715000 enjekte EDİLMEZ


# --- J. Non-mutation + freeze ------------------------------------------------------------- #
def test_run_pilot_fix7_offline_non_mutating(tmp_path):
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
    assert r["pilot_outcome"] == "NOT_RUN"
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
