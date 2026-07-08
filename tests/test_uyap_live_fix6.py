"""UYAP Live Browser Pilot 1 — Live Interoperability Fix 6 testleri (OFFLINE; ağ/canlı YOK).

Altıncı gerçek-canlı FAIL: Fix 5 canlıda çalıştı (modal açıldı, 4 gerçek belge satırı + her satırda
action_count=2 gözlendi), ama her satır için view_action_resolved=false VE download_action_detected=
false → tüm denemeler row_action_unresolved:no_view_action ile durdu. Kök neden: satır-yerel İKON-yalnız
kontrollerin semantiği (SVG <use href="#...">, ikon-font class, svg <title>) çözümlenmiyordu.

Fix 6: satır-yerel ikon eylem introspeksiyonu + deterministik download/view çözümü (erişilebilirlik →
torun ikon token → href/download → onclick precedence; yalnız POZİTİF view; konum/Nth YOK; renk YOK).
Bu testler yalnız saf/çevrimdışı davranışı doğrular; CANLI PASS DEĞİL (operatör yeniden-çalıştırması gerekir).
"""

from __future__ import annotations

import json
import shutil

from bs4 import BeautifulSoup

from sold.ingestion.uyap import (
    audit_candidate,
    classify_action_semantic,
    classify_document_label,
    detect_document_list,
    extract_document_rows_semantic,
    extract_evidence,
    genuine_fingerprint,
    reconcile,
    resolve_row_view_action,
    run_pilot,
)
from sold.ingestion.uyap.collect import _action_summary, _row_action_specs

TARGET = "2026/263 Esas"


def _acts(inner_html: str):
    """Bir satır (<div>...</div>) içindeki eylem spec'lerini çıkarır."""
    return _row_action_specs(BeautifulSoup(f"<div class=row>{inner_html}</div>", "html.parser"))


def _sem(inner_html: str):
    return [s["semantic"] for s in _acts(inner_html)]


# --- Gerçek gözlenen ikon-yalnız satır (SVG-sprite): indirme + eye ------------------------ #
_ROW_SVG = (
    '<a href="#"><svg><use href="#icon-download"></use></svg></a>'
    '<a href="#"><svg><use href="#icon-eye"></use></svg></a>'
)


# --- A. Run-6 reprodüksiyonu: tanınmayan ikon-yalnız kontroller çözülemez ------------------ #
def test_opaque_icon_only_row_is_unresolved_like_run6():
    # İki tıklanabilir, hiçbir tanınır token yok → view_action_resolved=false, download=false (Run 6)
    specs = _acts('<a href="#"><svg></svg></a><a href="#"><svg></svg></a>')
    assert len(specs) == 2
    res = resolve_row_view_action(specs)
    assert res["resolved"] is False and res["download_action_detected"] is False
    assert res["reason"] == "no_view_action"


def test_svg_use_fragment_row_now_resolves():
    specs = _acts(_ROW_SVG)
    assert len(specs) == 2
    assert [s["semantic"] for s in specs] == ["download", "view"]
    res = resolve_row_view_action(specs)
    assert res["resolved"] is True and res["download_action_detected"] is True
    assert res["reason"] == "positive_view"


# --- B. Torun ikon token semantiği -------------------------------------------------------- #
def test_nested_i_download_class_classifies_download():
    assert _sem('<a class=btn><i class="fa fa-download"></i></a>') == ["download"]


def test_nested_i_eye_class_classifies_view():
    assert _sem('<a class=btn><i class="fa fa-eye"></i></a>') == ["view"]


def test_svg_title_view_classifies_view():
    assert _sem('<button><svg><title>Goruntule</title></svg></button>') == ["view"]


def test_svg_use_eye_fragment_classifies_view():
    assert _sem('<a href="#"><svg><use href="#eye"></use></svg></a>') == ["view"]


def test_glyphicon_and_mdi_tokens():
    assert _sem('<a><span class="glyphicon glyphicon-download"></span></a>') == ["download"]
    assert _sem('<a><span class="mdi mdi-eye-outline"></span></a>') == ["view"]


# --- C. Href / download attribute / handler semantiği ------------------------------------- #
def test_download_html_attribute_classifies_download():
    assert _sem('<a href="/pp/file.udf" download>x</a>') == ["download"]


def test_viewer_href_kind_classifies_view():
    assert _sem('<a href="/pp/viewer.jsp?mimeType=Udf&evrakId=9">y</a>') == ["view"]


def test_handler_token_goruntule_classifies_view():
    assert _sem('<span role="button" onclick="goruntuleEvrak(123)">a</span>') == ["view"]


def test_handler_token_indir_classifies_download():
    assert _sem('<span role="button" onclick="indirEvrak(123)">a</span>') == ["download"]


# --- D. Renk / konum ASLA semantik değildir ----------------------------------------------- #
def test_visual_color_alone_does_not_classify():
    # kırmızı indirme oku ama ikon token'ı yok → color ile download DEĞİL
    assert classify_action_semantic({"class_tokens": ["red"], "css": "red"}) == "unknown"
    assert _sem('<a class="red"></a><a class="blue"></a>') == ["unknown", "unknown"]


def test_second_action_position_alone_does_not_classify_view():
    res = resolve_row_view_action(_acts('<a class=x></a><a class=y></a>'))
    assert res["resolved"] is False   # ikinci/sağdaki eylem otomatik view DEĞİL


def test_non_download_not_auto_inferred_as_view():
    # biri download, diğeri tanınmayan → 'diğeri view' ÇIKARIMI YOK
    res = resolve_row_view_action(_acts('<a title="Indir"></a><a class=y></a>'))
    assert res["resolved"] is False and res["download_action_detected"] is True
    assert res["reason"] == "no_view_action"


def test_rightmost_download_and_unknown_first_not_inferred():
    res = resolve_row_view_action(_acts('<a class=y></a><a title="Indir"></a>'))
    assert res["resolved"] is False   # konum yok; pozitif view yok


# --- E. Çözüm sayısı ----------------------------------------------------------------------- #
def test_exactly_one_positive_view_resolves():
    res = resolve_row_view_action(_acts(_ROW_SVG))
    assert res["resolved"] is True and res["view_action"]["semantic"] == "view"


def test_two_view_candidates_are_ambiguous():
    res = resolve_row_view_action(_acts('<a title="Goruntule"></a><a title="Onizleme"></a>'))
    assert res["resolved"] is False and res["reason"] == "ambiguous_multiple_view_candidates"


def test_unknown_icon_only_controls_remain_unresolved():
    res = resolve_row_view_action(_acts('<a class="icon-foo"></a><a class="icon-bar"></a>'))
    assert res["resolved"] is False


def test_download_only_row_unresolved():
    res = resolve_row_view_action(_acts('<a class="fa-download"></a>'))
    assert res["resolved"] is False and res["reason"] == "download_only"


# --- F. Gizlilik-güvenli metadata --------------------------------------------------------- #
def test_action_summary_is_privacy_safe():
    spec = _acts('<a href="/pp/viewer.jsp?mimeType=Udf&evrakId=16737826545" onclick="secretHandler(\'tok_ABC123\')">v</a>')[0]
    summ = _action_summary(spec, 0)
    dump = json.dumps(summ).lower()
    # ham href / opak evrakId / mimeType / onclick gövdesi ASLA
    assert "16737826545" not in dump
    assert "mimetype" not in dump
    assert "secrethandler" not in dump and "tok_abc123" not in dump
    assert "href" not in summ  # yalnız href_kind
    assert summ["href_kind"] == "viewer"
    assert summ["onclick_present"] is True
    assert summ["resolved_semantic"] == "view"


def test_action_summary_bounded():
    spec = _acts('<a class="' + " ".join(f"c{i}" for i in range(40)) + '"><i class="fa fa-eye"></i></a>')[0]
    summ = _action_summary(spec, 0)
    assert len(summ["safe_class_tokens"]) <= 12
    assert len(summ["descendant_icon_tokens"]) <= 16


def test_opaque_and_numeric_class_tokens_dropped():
    spec = _acts('<a class="123456 verylongopaquehashtokenthatexceedslimit0123456789 fa-eye"></a>')[0]
    toks = spec["class_tokens"]
    assert "123456" not in toks                       # sayısal hash atılır
    assert all(len(t) <= 32 for t in toks)            # uzun opak token atılır


# --- G. Satır-yerellik korunur ------------------------------------------------------------ #
def test_row_locality_preserved_no_global_nth():
    # İki AYRI satır; her biri kendi eylemleriyle çözülür (global Nth YOK)
    html = (
        '<div class=modal><div class=hd>Ihale Evrak Listesi</div>'
        f'<div class=item><span>1- Artirma Sonuc / Uzatma Tutanagi</span>{_ROW_SVG}</div>'
        f'<div class=item><span>3- BILIRKISI RAPORU 2026 263 ESAS.udf</span>{_ROW_SVG}</div>'
        f'<div class=item><span>Satis Ilani</span>{_ROW_SVG}</div>'
        f'<div class=item><span>2- Satis Sartnamesi Ve Tutanagi</span>{_ROW_SVG}</div></div>'
    )
    rows = extract_document_rows_semantic(html)
    resolved = {classify_document_label(r["label"]): resolve_row_view_action(r["actions"])["resolved"] for r in rows}
    assert resolved.get("auction_result") is True
    assert resolved.get("appraisal_report") is True
    # her satırın eylemleri kendi satırından (2 aksiyon)
    assert all(len(r["actions"]) == 2 for r in rows)


def test_nested_clickable_flattened_single_action():
    # <a><i></i></a> tek TIKLANABİLİR eylemdir (i ayrı eylem SAYILMAZ)
    specs = _acts('<a class=btn><i class="fa fa-eye"></i></a>')
    assert len(specs) == 1 and specs[0]["semantic"] == "view"


# --- H. Auction-result view çözümü mevcut viewer akışına bağlanır (bütünleşme) ------------- #
def test_icon_modal_detected_and_auction_row_resolves():
    html = (
        '<div class="modal-overlay"><div class=hd>Ihale Evrak Listesi</div>'
        f'<div class=item><span>Satis Ilani</span>{_ROW_SVG}</div>'
        f'<div class=item><span>2- Satis Sartnamesi Ve Tutanagi</span>{_ROW_SVG}</div>'
        f'<div class=item><span>3- BILIRKISI RAPORU 2026 263 ESAS.udf</span>{_ROW_SVG}</div>'
        f'<div class=item><span>1- Artirma Sonuc / Uzatma Tutanagi</span>{_ROW_SVG}</div></div>'
    )
    d = detect_document_list(html)
    assert d["detected"] is True
    rows = extract_document_rows_semantic(html)
    ar = [r for r in rows if classify_document_label(r["label"]) == "auction_result"][0]
    res = resolve_row_view_action(ar["actions"])
    assert res["resolved"] is True and res["view_action"]["semantic"] == "view"


# --- I. Fiyat semantiği: result-card Satış Tutarı SUBSTİTÜE EDİLMEZ ------------------------ #
def test_result_card_satis_tutari_not_substituted():
    card = {"artifact_type": "status_card",
            "text": "Satış Durumu Satıldı Satış Tutarı 5.715.000,00 TL 50984 Ada 1 Parsel"}
    ev = extract_evidence([card], institution="Ankara", file_id=TARGET)
    au = audit_candidate(ev, reconcile([card], "Ankara", TARGET))
    assert ev.ihale_bedeli is None and au.decision != "ADMISSIBLE_COMPLETED_SALE"


# --- J. Non-mutation + verification (canlı PASS DEĞİL) ------------------------------------ #
def test_run_pilot_fix6_offline_non_mutating(tmp_path):
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
    assert after["genuine_uyap_count"] == 7 and after["sha256"] == before["sha256"]
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert sum(1 for x in recs if str(x.get("public_record_id")) == TARGET) == 1  # 8. gözlem YOK


# --- K. Yapısal freeze -------------------------------------------------------------------- #
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
