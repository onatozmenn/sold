"""UYAP Evidence Ingestion Pipeline V1 testleri (OFFLINE; ağ GEREKTİRMEZ).

Kapsam: 6 regresyon vakası (fiyat semantiği), çıkarım≠admisyon, denetim uyap.json'u
değiştirmez, idempotent admisyon, duplicate → kopya genuine gözlem YOK, eksik-kanıt
yönlendirmesi, Ödenmesi Gereken Bedel asla auction price DEĞİL, ALACAĞA MAHSUBEN geçerli
İhale Bedeli'ni geçersiz kılmaz, KDV P/Q'yu değiştirmez, uyap_sale_prob YOK, terminal-olmayan
negatif sınıfa ÇEVRİLMEZ, SMM tam olarak 4 moment kalır (yapısal FREEZE).
"""

from __future__ import annotations

import json
import shutil

import pytest

from sold.ingestion.uyap import (
    ADMISSIBLE_COMPLETED_SALE,
    DUPLICATE,
    EXCLUDED_NON_TERMINAL,
    MISSING_APPRAISAL,
    MISSING_AUCTION_PRICE,
    MISSING_TERMINAL_EVIDENCE,
    admit_candidate,
    discover,
    import_artifact,
    needs_review,
    parse_tl_amount,
    record_exclusion,
    review_queue,
    run_audit,
    run_extract,
    status_summary,
    store,
)


# --------------------------------------------------------------------------- #
# Artifact kurucusu (offline fixture; kişisel veri YOK)
# --------------------------------------------------------------------------- #
def _artifacts(appraisal, ihale=None, status="Satıldı Satış İşlemleri Tamamlandı",
               odenmesi=None, teminat=None, share=False, alacaga=False, kdv=None,
               ada="6646", parsel="6", card=None, appraisal_in_result=True):
    appr = (f"Bilirkişi Raporu Muhammen Bedel {appraisal} TL Kıymeti {appraisal} TL "
            f"Takdir Olunan Değer {appraisal} TL {ada} ada {parsel} parsel mesken")
    res = f"İhale Artırma Sonuç Tutanağı {ada} ada {parsel} parsel "
    if ihale is not None:
        res += f"İhale Bedeli: {ihale} TL "
    if odenmesi is not None:
        res += f"Ödenmesi Gereken Bedel: {odenmesi} "
    if teminat is not None:
        res += f"Teminat: {teminat} TL "
    if share:
        res += "İhale Alıcısı Hisse Oranı %33.33 Satılan Hisse Oranı %66.66 "
    if alacaga:
        res += "Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN "
    if kdv is not None:
        res += f"KDV %{kdv} "
    card_amt = card if card is not None else (ihale or "")
    cardtxt = f"{status} Satış Tutarı: {card_amt} TL"
    arts = [
        {"artifact_type": "appraisal_report", "text": appr},
        {"artifact_type": "auction_result", "text": res},
        {"artifact_type": "status_card", "text": cardtxt},
    ]
    return arts


def _pipeline_to_audit(file_id, artifacts, store_dir, genuine_path=None, institution="Ankara İcra"):
    """discover → import → extract → audit; adayı döndürür (admisyon YOK)."""
    c = discover(institution, file_id, store_dir=store_dir)
    for a in artifacts:
        import_artifact(c, a["artifact_type"], text=a["text"], store_dir=store_dir, persist=False)
    store.upsert(c, store_dir)
    c = run_extract(c, store_dir)
    c = run_audit(c, store_dir, genuine_path)
    return c


# --------------------------------------------------------------------------- #
# 6 regresyon vakası — fiyat semantiği
# --------------------------------------------------------------------------- #
CASES = {
    "CASE1": (_artifacts("4.400.000,00", "4.238.000,00", odenmesi="3.393.710,93", share=True, ada="6646", parsel="6"),
              ADMISSIBLE_COMPLETED_SALE, 4_238_000.0, 4_400_000.0),
    "CASE2": (_artifacts("3.000.000,00", "3.025.000,00", odenmesi="2.725.000,00", teminat="300.000", ada="6441", parsel="9"),
              ADMISSIBLE_COMPLETED_SALE, 3_025_000.0, 3_000_000.0),
    "CASE3": (_artifacts("5.750.000,00", "4.575.000,00", odenmesi="3.050.000,02", share=True, ada="25043", parsel="1"),
              ADMISSIBLE_COMPLETED_SALE, 4_575_000.0, 5_750_000.0),
    "CASE4": (_artifacts("5.400.000,00", "4.654.000,00", odenmesi="4.114.000", ada="14708", parsel="3"),
              ADMISSIBLE_COMPLETED_SALE, 4_654_000.0, 5_400_000.0),
    "CASE5": (_artifacts("6.800.000,00", "5.715.000,00", alacaga=True, kdv="20", ada="50984", parsel="1"),
              ADMISSIBLE_COMPLETED_SALE, 5_715_000.0, 6_800_000.0),
    "CASE6": (_artifacts("8.000.000,00", "6.700.000,00", status="Birinci Alıcıya Süre Verildi", ada="123", parsel="4"),
              EXCLUDED_NON_TERMINAL, None, 8_000_000.0),
}


@pytest.mark.parametrize("name", list(CASES))
def test_regression_case_decision_and_price(name, tmp_path):
    artifacts, exp_dec, exp_price, exp_appraisal = CASES[name]
    c = _pipeline_to_audit(f"{name}/2026", artifacts, tmp_path)
    au = c["audit"]
    assert au["decision"] == exp_dec
    if exp_price is not None:
        assert au["auction_price"] == exp_price          # pay = açık İhale Bedeli
        assert au["appraisal_value"] == exp_appraisal     # payda = ekspertiz Q
        # Ödenmesi Gereken Bedel / payable ASLA seçilmez
        og = c["extracted"].get("odenmesi_gereken_bedel")
        if og is not None:
            assert au["auction_price"] != og


def test_case5_alacaga_mahsuben_valid_and_kdv_no_effect(tmp_path):
    artifacts, _, _, _ = CASES["CASE5"]
    c = _pipeline_to_audit("CASE5/2026", artifacts, tmp_path)
    au = c["audit"]
    assert au["decision"] == ADMISSIBLE_COMPLETED_SALE
    assert au["auction_price"] == 5_715_000.0            # ALACAĞA MAHSUBEN açık İhale Bedeli'ni GEÇERSİZ KILMAZ
    assert c["extracted"]["alacaga_mahsuben"] is True
    assert c["extracted"]["kdv_rate"] == 20.0
    # KDV P veya Q'yu DEĞİŞTİRMEZ (moment tanımı korunur)
    assert au["win_over_appraisal"] == pytest.approx(5_715_000.0 / 6_800_000.0, abs=1e-12)


def test_odenmesi_gereken_bedel_never_auction_price(tmp_path):
    artifacts, _, _, _ = CASES["CASE2"]
    c = _pipeline_to_audit("CASE2b/2026", artifacts, tmp_path)
    assert c["audit"]["auction_price"] == 3_025_000.0
    assert c["extracted"]["odenmesi_gereken_bedel"] == 2_725_000.0
    assert c["audit"]["auction_price"] != c["extracted"]["odenmesi_gereken_bedel"]
    # kural izinde Ödenmesi Gereken Bedel'in kullanılmadığı açıkça yazılı
    assert any("NOT used as auction price" in t for t in c["audit"]["rule_trace"])


def test_non_terminal_excluded_not_negative_class(tmp_path):
    artifacts, _, _, _ = CASES["CASE6"]
    c = _pipeline_to_audit("2026/316 Talimat", artifacts, tmp_path)
    assert c["audit"]["decision"] == EXCLUDED_NON_TERMINAL
    # dışlanan manifest kaydı: genuine sete / SMM'e GİRMEZ; negatif sınıf YOK
    excl_path = tmp_path / "uyap_candidates.json"
    r = record_exclusion(c, candidates_path=excl_path, store_dir=tmp_path)
    assert r["status"] == "recorded"
    manifest = json.loads(excl_path.read_text(encoding="utf-8"))
    entry = manifest[0]
    assert entry["audit_status"] == EXCLUDED_NON_TERMINAL
    assert entry["enters_genuine_uyap"] is False and entry["enters_smm"] is False
    # negatif sınıf / sale_prob VERİ ALANI üretilmez (not: not'ta olumsuzlama olarak geçebilir)
    assert not any("sale_prob" in str(k) for k in entry.keys())


# --------------------------------------------------------------------------- #
# çıkarım ≠ admisyon ; denetim uyap.json'u değiştirmez
# --------------------------------------------------------------------------- #
def _seed_genuine(tmp_path):
    gp = tmp_path / "uyap.json"
    gp.write_text("[]", encoding="utf-8")
    return gp


def test_extract_is_not_admission(tmp_path):
    gp = _seed_genuine(tmp_path)
    artifacts, _, _, _ = CASES["CASE1"]
    c = discover("Ankara İcra", "EX/2026", store_dir=tmp_path)
    for a in artifacts:
        import_artifact(c, a["artifact_type"], text=a["text"], store_dir=tmp_path, persist=False)
    store.upsert(c, tmp_path)
    c = run_extract(c, tmp_path)
    assert c["extracted"]["ihale_bedeli"] == 4_238_000.0
    assert c.get("admitted_public_record_id") is None
    assert json.loads(gp.read_text(encoding="utf-8")) == []   # genuine değişmedi


def test_audit_does_not_mutate_uyap(tmp_path):
    gp = _seed_genuine(tmp_path)
    artifacts, _, _, _ = CASES["CASE1"]
    _pipeline_to_audit("AUD/2026", artifacts, tmp_path, genuine_path=gp)
    assert json.loads(gp.read_text(encoding="utf-8")) == []   # denetim yazmadı


def test_explicit_admission_idempotent(tmp_path):
    gp = _seed_genuine(tmp_path)
    artifacts, _, _, _ = CASES["CASE1"]
    c = _pipeline_to_audit("ADM/2026", artifacts, tmp_path, genuine_path=gp)
    r1 = admit_candidate(c, genuine_path=gp, store_dir=tmp_path)
    assert r1["status"] == "admitted"
    recs = json.loads(gp.read_text(encoding="utf-8"))
    assert len(recs) == 1 and recs[0]["public_record_id"] == "ADM/2026"
    assert recs[0]["winning_bid"] == 4_238_000.0 and recs[0]["appraised_value"] == 4_400_000.0
    # tekrar admisyon → KOPYA YOK
    c2 = store.get_candidate(c["candidate_id"], tmp_path)
    r2 = admit_candidate(c2, genuine_path=gp, store_dir=tmp_path)
    assert r2["status"] == "already_admitted"
    assert len(json.loads(gp.read_text(encoding="utf-8"))) == 1


def test_duplicate_candidate_no_duplicate_genuine(tmp_path):
    gp = _seed_genuine(tmp_path)
    artifacts, _, _, _ = CASES["CASE1"]
    c = _pipeline_to_audit("DUP/2026", artifacts, tmp_path, genuine_path=gp)
    admit_candidate(c, genuine_path=gp, store_dir=tmp_path)
    # aynı dosya yeniden denetlenirse DUPLICATE olarak işaretlenir
    c2 = _pipeline_to_audit("DUP/2026", artifacts, tmp_path, genuine_path=gp)
    assert c2["audit"]["decision"] == DUPLICATE
    assert len(json.loads(gp.read_text(encoding="utf-8"))) == 1


def test_public_record_alias_blocks_duplicate_admission(tmp_path):
    gp = tmp_path / "uyap.json"
    gp.write_text(json.dumps([{
        "public_record_id": "2026/45 Satış",
        "public_record_aliases": ["2026/45", "16926063468"],
    }]), encoding="utf-8")
    candidate = discover(
        "Ankara İcra",
        "2026/45",
        record_ref="16926063468",
        store_dir=tmp_path,
    )
    for artifact in CASES["CASE3"][0]:
        import_artifact(
            candidate,
            artifact["artifact_type"],
            text=artifact["text"],
            store_dir=tmp_path,
            persist=False,
        )

    audited = run_audit(candidate, tmp_path, gp)
    result = admit_candidate(audited, genuine_path=gp, store_dir=tmp_path)
    stored = store.get_candidate(candidate["candidate_id"], tmp_path)

    assert audited["audit"]["decision"] == DUPLICATE
    assert result["status"] == "already_admitted"
    assert result["public_record_id"] == "2026/45 Satış"
    assert stored["state"] == "admitted"
    assert stored["admitted_public_record_id"] == "2026/45 Satış"
    assert len(json.loads(gp.read_text(encoding="utf-8"))) == 1


# --------------------------------------------------------------------------- #
# eksik-kanıt yönlendirmesi
# --------------------------------------------------------------------------- #
def test_missing_terminal_goes_to_review(tmp_path):
    arts = _artifacts("4.400.000,00", "4.238.000,00", status="", ada="6646", parsel="6")
    c = _pipeline_to_audit("MT/2026", arts, tmp_path)
    assert c["audit"]["decision"] == MISSING_TERMINAL_EVIDENCE
    assert needs_review(c)


def test_missing_appraisal_blocked(tmp_path):
    arts = [
        {"artifact_type": "auction_result", "text": "6646 ada 6 parsel İhale Bedeli: 4.238.000,00 TL"},
        {"artifact_type": "status_card", "text": "Satıldı Satış İşlemleri Tamamlandı Satış Tutarı: 4.238.000,00 TL"},
    ]
    c = _pipeline_to_audit("MA/2026", arts, tmp_path)
    assert c["audit"]["decision"] in (MISSING_APPRAISAL,)  # ekspertiz yok → bloklandı
    assert needs_review(c)


def test_missing_explicit_ihale_blocked(tmp_path):
    # terminal + ekspertiz var; açık İhale Bedeli YOK ve sonuç kartı da yok → bloklandı
    arts = [
        {"artifact_type": "appraisal_report", "text": "Muhammen Bedel 4.400.000,00 TL 6646 ada 6 parsel"},
        {"artifact_type": "auction_result", "text": "6646 ada 6 parsel Satıldı Satış İşlemleri Tamamlandı"},
    ]
    c = _pipeline_to_audit("MI/2026", arts, tmp_path)
    assert c["audit"]["decision"] == MISSING_AUCTION_PRICE
    assert c["audit"]["auction_price"] is None


# --------------------------------------------------------------------------- #
# operatör status / review
# --------------------------------------------------------------------------- #
def test_status_summary_and_review_queue(tmp_path):
    _pipeline_to_audit("CASE1/2026", CASES["CASE1"][0], tmp_path)          # admissible
    _pipeline_to_audit("2026/316 Talimat", CASES["CASE6"][0], tmp_path)   # excluded
    _pipeline_to_audit("MT/2026", _artifacts("4.400.000,00", "4.238.000,00", status="", ada="6646", parsel="6"), tmp_path)  # review
    s = status_summary(tmp_path)
    assert s["total_candidates"] == 3
    assert s["admissible"] == 1 and s["excluded_non_terminal"] == 1
    assert s["review_blockers"] >= 1
    q = review_queue(tmp_path)
    assert any(it["audit_decision"] == MISSING_TERMINAL_EVIDENCE for it in q)


# --------------------------------------------------------------------------- #
# YAPISAL FREEZE — SMM tam olarak 4 moment; uyap_sale_prob YOK; TOKİ external
# --------------------------------------------------------------------------- #
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


def test_admission_feeds_existing_frozen_schema(tmp_path):
    from sold.structural import build_observed_moments, load_genuine_datasets
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    before = len(json.loads((gdir / "uyap.json").read_text(encoding="utf-8")))
    # genuine'de OLMAYAN benzersiz (P,Q) — mevcut vakalarla (P,Q)-dedup ÇAKIŞMASIN (CASE1-5 zaten genuine).
    uniq = _artifacts("7.777.777,00", "6.666.666,00", odenmesi="5.000.000,00", ada="9999", parsel="9")
    c = _pipeline_to_audit("NEWADM/2026", uniq, tmp_path, genuine_path=gdir / "uyap.json")
    r = admit_candidate(c, genuine_path=gdir / "uyap.json", store_dir=tmp_path)
    assert r["status"] == "admitted"
    after = len(json.loads((gdir / "uyap.json").read_text(encoding="utf-8")))
    assert after == before + 1                       # non-destructive append
    g = load_genuine_datasets(directory=gdir)
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    smm = {k for k in built["moments"] if k.startswith(("uyap_win", "kap_log"))}
    assert smm == {
        "uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd",
        "kap_log_ratio_mean", "kap_log_ratio_sd",
    }
    assert "uyap_sale_prob" not in built["moments"]   # negatif sınıf üretilmedi


def test_admission_does_not_treat_equal_prices_as_record_identity(tmp_path):
    # Farklı resmî kayıtlar aynı P/Q çiftine sahip olabilir; kimlik yalnız public_record_id'dir.
    from sold.structural.datasets import GENUINE_DIR

    gdir = tmp_path / "genuine"
    gdir.mkdir()
    for f in ("uyap.json", "kap.json", "toki.json"):
        shutil.copyfile(GENUINE_DIR / f, gdir / f)
    before = len(json.loads((gdir / "uyap.json").read_text(encoding="utf-8")))
    c = _pipeline_to_audit("2026/23", CASES["CASE4"][0], tmp_path, genuine_path=gdir / "uyap.json")
    r = admit_candidate(c, genuine_path=gdir / "uyap.json", store_dir=tmp_path)
    assert r["status"] == "admitted"
    after = len(json.loads((gdir / "uyap.json").read_text(encoding="utf-8")))
    assert after == before + 1


def test_admission_rejects_cached_decision_without_artifacts(tmp_path):
    gp = _seed_genuine(tmp_path)
    candidate = {
        "candidate_id": "tampered",
        "file_id": "2099/1 Esas",
        "institution": "Example",
        "artifacts": [],
        "extracted": {},
        "audit": {
            "decision": ADMISSIBLE_COMPLETED_SALE,
            "appraisal_value": 1_000_000,
            "auction_price": 800_000,
        },
    }

    result = admit_candidate(candidate, genuine_path=gp, store_dir=tmp_path)

    assert result["status"] == "error"
    assert "artifacts" in result["reason"]
    assert json.loads(gp.read_text(encoding="utf-8")) == []


def test_reconciliation_requires_more_than_one_matching_ada():
    from sold.ingestion.uyap.reconcile import reconcile

    result = reconcile([
        {"artifact_type": "sale_notice", "text": "Taşınmaz 123 ada üzerindedir"},
        {"artifact_type": "auction_result", "text": "İhale 123 ada üzerindedir"},
    ])

    assert result.status == "ambiguous"
    assert result.same_asset is False


# --------------------------------------------------------------------------- #
# parse_tl_amount birim testleri (fiyat semantiği çekirdeği)
# --------------------------------------------------------------------------- #
def test_parse_tl_amount_formats():
    assert parse_tl_amount("4.238.000,00 TL") == 4_238_000.0
    assert parse_tl_amount("3.025.000,00") == 3_025_000.0
    assert parse_tl_amount("6700000") == 6_700_000.0
    assert parse_tl_amount("ALACAĞA MAHSUBEN") is None          # nakit uydurulmaz
    assert parse_tl_amount(": ALACAĞA MAHSUBEN 20") is None      # mahsuben sayıdan önce → None
    # sayı mahsuben'den ÖNCE → gerçek tutar (İhale Bedeli, ardından mahsuben gelen alan)
    assert parse_tl_amount("5.715.000,00 TL Ödenmesi Gereken Bedel: ALACAĞA MAHSUBEN") == 5_715_000.0
