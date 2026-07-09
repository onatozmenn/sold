"""UYAP Live Browser Pilot 1 testleri (OFFLINE; ağ / canlı tarayıcı GEREKTİRMEZ).

Doğrulama katmanı (known-truth karşılaştırması), mutasyon-korumu, NOT_RUN semantiği ve
yapısal FREEZE test edilir. Offline fixture PASS'i CANLI PASS'e DÖNÜŞMEZ. Genuine uyap.json
DEĞİŞTİRİLMEZ; 2026/263 zaten admitte → 8. gözlem OLUŞMAZ (sayı 7 kalır).
"""

from __future__ import annotations

import json
import shutil

from sold.ingestion.uyap import (
    KNOWN_TRUTH,
    compare_to_truth,
    discover_document_links,
    genuine_fingerprint,
    run_pilot,
    verify_pilot,
)
from sold.ingestion.uyap.audit import audit_candidate
from sold.ingestion.uyap.extract import extract_evidence
from sold.ingestion.uyap.reconcile import reconcile


def _pilot_artifacts(appraisal="6.800.000,00", ihale="5.715.000,00",
                     status="Satıldı Satış İşlemleri Tamamlandı", alacaga=True, kdv="20",
                     ada="50984", parsel="1", section="60"):
    appr = (f"Bilirkişi Raporu Muhammen Bedel {appraisal} TL Kıymeti {appraisal} TL "
            f"Takdir Olunan Değer {appraisal} TL {ada} ada {parsel} parsel {section} no.lu konut")
    res = f"İhale Artırma Sonuç Tutanağı {ada} ada {parsel} parsel {section} no.lu "
    if ihale is not None:
        res += f"İhale Bedeli: {ihale} TL "
    if alacaga:
        res += "Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN "
    if kdv is not None:
        res += f"KDV %{kdv} "
    card = f"{status} Satış Tutarı: {ihale if ihale else ''} TL"
    return [
        {"artifact_type": "appraisal_report", "text": appr, "source_ref": "fixture://appraisal"},
        {"artifact_type": "auction_result", "text": res, "source_ref": "fixture://result"},
        {"artifact_type": "status_card", "text": card, "source_ref": "fixture://card"},
    ]


def _seed_genuine_dir(tmp_path):
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    return gdir


# --------------------------------------------------------------------------- #
# GERÇEK DOM belge-bağlantısı keşfi (offline)
# --------------------------------------------------------------------------- #
def test_discover_document_links_matches_and_flags_unsupported():
    html = (
        '<html><body>'
        '<a href="/dosya/satis-ilani/263">Satış İlanı</a>'
        '<a href="/dosya/bilirkisi/263">Bilirkişi Raporu</a>'
        '<a href="javascript:openSonuc()">Artırma Sonuç Tutanağı</a>'
        '<a href="/misc">Diğer</a>'
        '</body></html>'
    )
    links = discover_document_links(html)
    by_type = {d["artifact_type"]: d for d in links}
    assert "sale_notice" in by_type and by_type["sale_notice"]["usable_href"] is True
    assert "appraisal_report" in by_type and by_type["appraisal_report"]["usable_href"] is True
    # javascript: handler → DESTEKLENMİYOR olarak işaretlenir (uydurma yok)
    assert by_type["auction_result"]["usable_href"] is False
    assert "Diğer" not in {d["text"] for d in links}


# --------------------------------------------------------------------------- #
# Doğrulama katmanı — 2026/263 (offline fixture; CANLI PASS DEĞİL)
# --------------------------------------------------------------------------- #
def test_verify_pilot_offline_required_pass_but_not_live():
    r = verify_pilot(_pilot_artifacts(), file_id="2026/263 Esas")
    assert r["mode"] == "offline_simulated" and r["live_page_reached"] is False
    assert r["extracted_appraisal"] == 6_800_000.0
    assert r["extracted_auction_price"] == 5_715_000.0          # açık İhale Bedeli
    assert r["audit_decision"] == "ADMISSIBLE_COMPLETED_SALE"
    cmp = r["known_truth_comparison"]
    assert cmp["required_all_passed"] is True
    assert cmp["required"]["p_over_q"]["actual"] == KNOWN_TRUTH["p_over_q"]
    assert r["verification_layer_result"] == "PASS"
    # offline fixture CANLI PASS'e DÖNÜŞMEZ
    assert r["pilot_outcome"] == "NOT_RUN"


def test_verify_pilot_optional_corroboration():
    r = verify_pilot(_pilot_artifacts(alacaga=True, kdv="20"), file_id="2026/263 Esas")
    assert r["known_truth_comparison"]["optional_all_passed"] is True
    # opsiyonel eksikse ZORUNLU doğrulamalar hâlâ geçebilir (tam başarısızlık değil)
    r2 = verify_pilot(_pilot_artifacts(alacaga=False, kdv=None), file_id="2026/263 Esas")
    assert r2["known_truth_comparison"]["required_all_passed"] is True
    assert r2["known_truth_comparison"]["optional_all_passed"] is False


def test_verify_pilot_appraisal_mismatch_reported():
    r = verify_pilot(_pilot_artifacts(appraisal="6.000.000,00"), file_id="2026/263 Esas")
    req = r["known_truth_comparison"]["required"]
    assert req["appraisal_value_tl"]["match"] is False
    assert "appraisal_value_tl" in r["known_truth_comparison"]["required_wrong"]
    assert r["verification_layer_result"] == "FAIL"


def test_verify_pilot_ihale_and_pq_mismatch_reported():
    r = verify_pilot(_pilot_artifacts(ihale="5.000.000,00"), file_id="2026/263 Esas")
    req = r["known_truth_comparison"]["required"]
    assert req["official_auction_price_tl"]["match"] is False
    assert req["p_over_q"]["match"] is False          # P/Q de uyuşmaz
    assert "official_auction_price_tl" in r["known_truth_comparison"]["required_wrong"]


def test_verify_pilot_audit_decision_mismatch_reported():
    r = verify_pilot(_pilot_artifacts(status="Birinci Alıcıya Süre Verildi"), file_id="2026/263 Esas")
    req = r["known_truth_comparison"]["required"]
    assert req["audit_decision"]["match"] is False    # EXCLUDED_NON_TERMINAL != ADMISSIBLE
    assert req["terminal_completed_sale"]["match"] is False
    assert r["verification_layer_result"] == "FAIL"


# --------------------------------------------------------------------------- #
# Ödenmesi Gereken Bedel / ALACAĞA MAHSUBEN / KDV semantiği (pilot yolu)
# --------------------------------------------------------------------------- #
def test_pilot_alacaga_mahsuben_and_kdv_do_not_change_pq():
    ev = extract_evidence(_pilot_artifacts(), institution="Ankara", file_id="2026/263 Esas")
    au = audit_candidate(ev, reconcile(_pilot_artifacts(), "Ankara", "2026/263 Esas"))
    assert ev.alacaga_mahsuben is True and ev.kdv_rate == 20.0
    assert au.auction_price == 5_715_000.0                       # nakit uydurulmaz
    assert au.win_over_appraisal == KNOWN_TRUTH["p_over_q"]      # KDV P/Q'yu değiştirmez


# --------------------------------------------------------------------------- #
# NON-MUTATION garantisi + NOT_RUN (canlı oturum yok)
# --------------------------------------------------------------------------- #
def test_run_pilot_offline_is_non_mutating_and_not_run(tmp_path):
    gdir = _seed_genuine_dir(tmp_path)
    gp = gdir / "uyap.json"
    before = genuine_fingerprint(gp)
    assert before["genuine_uyap_count"] == 18
    r = run_pilot(offline_artifacts=_pilot_artifacts(), genuine_path=gp,
                  store_dir=tmp_path, report_path=tmp_path / "report.json")
    assert r["pilot_outcome"] == "NOT_RUN"          # offline → canlı PASS değil
    mg = r["mutation_guard"]
    assert mg["uyap_json_unchanged"] is True
    assert mg["genuine_uyap_count_unchanged"] is True
    assert mg["smm_moments_unchanged"] is True
    assert mg["uyap_sale_prob_absent"] is True
    after = genuine_fingerprint(gp)
    assert after["genuine_uyap_count"] == before["genuine_uyap_count"]   # yeni gözlem OLUŞMADI (pilot admit çağırmaz)
    assert after["sha256"] == before["sha256"]       # byte-for-byte değişmedi
    assert (tmp_path / "report.json").exists()


def test_run_pilot_live_without_browser_is_not_run(tmp_path):
    gdir = _seed_genuine_dir(tmp_path)
    gp = gdir / "uyap.json"
    # Playwright kurulu değil / CDP yok → canlı yol UYDURMA YAPMADAN NOT_RUN döner (exception YOK)
    r = run_pilot(cdp_endpoint="http://127.0.0.1:59999", genuine_path=gp,
                  store_dir=tmp_path, report_path=tmp_path / "live.json")
    assert r["pilot_outcome"] == "NOT_RUN"
    assert r["live_page_reached"] is False
    assert r["browser_connection_status"] in ("playwright_missing", "cdp_unavailable", "browser_error")
    assert r["mutation_guard"]["genuine_uyap_count_unchanged"] is True


def test_already_admitted_2026_263_cannot_create_eighth(tmp_path):
    gdir = _seed_genuine_dir(tmp_path)
    gp = gdir / "uyap.json"
    before = len(json.loads(gp.read_text(encoding="utf-8")))
    # pilot ASLA admit çağırmaz → 2026/263 zaten mevcut, sayı DEĞİŞMEZ (yeni gözlem yok)
    run_pilot(offline_artifacts=_pilot_artifacts(), genuine_path=gp,
              store_dir=tmp_path, report_path=tmp_path / "r.json")
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert len(recs) == before
    assert sum(1 for x in recs if str(x.get("public_record_id")) == "2026/263 Esas") == 1


# --------------------------------------------------------------------------- #
# YAPISAL FREEZE
# --------------------------------------------------------------------------- #
def test_pilot_structural_freeze_four_moments_no_sale_prob():
    fp = genuine_fingerprint()   # gerçek genuine dizini
    smm = fp["smm_moments"]
    assert set(smm) == {
        "uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd",
        "kap_log_ratio_mean", "kap_log_ratio_sd",
    }
    assert "uyap_sale_prob" not in smm
    assert fp["genuine_uyap_count"] == 18


def test_compare_to_truth_file_id_alias_accepted():
    ev = extract_evidence(_pilot_artifacts(), institution="Ankara", file_id="2026/263 İcra")
    au = audit_candidate(ev, reconcile(_pilot_artifacts(), "Ankara", "2026/263 İcra"))
    cmp = compare_to_truth(ev, au, "2026/263 İcra")   # etiket 'İcra' de kabul (aynı resmî kimlik)
    assert cmp["required"]["file_id"]["match"] is True
