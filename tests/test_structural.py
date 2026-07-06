"""Yapısal ekonometrik motor testleri.

Kapsam: Nash pazarlık sınırları, kanunî taban (tam + kısmî; muhammen_bedel = Q ≠ rezerv),
TCMB-çıpalı hedonik (intercept SEVİYE olarak kullanılmaz), TOKİ farklama + revizyon
guard'ı, açık artırma satılan/satılmayan, SMM ile parametre geri kazanımı ve yapısal
tahminci (asking = sinyal, tavan değil).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from sold.structural import (
    DEFAULT_FREE,
    HedonicPremium,
    MomentContext,
    StructuralClosingPredictor,
    StructuralParams,
    auction_moments,
    build_observed_moments,
    compare_snapshots,
    context_from_datasets,
    dataset_status,
    dataset_summary,
    difference_disclosures,
    estimate_smm,
    identification_report,
    kap_observed_moments,
    legal_floor,
    load_auctions,
    load_genuine_datasets,
    load_kap_candidates,
    load_kap_disposals,
    moment_jacobian,
    negotiated_price,
    normalize_auction,
    normalize_kap_disposal,
    observed_moments,
    roll_unit_price,
    simulate_auctions,
    simulate_negotiations,
    smm_objective,
    source_jacobian_ranks,
    tcmb_fair_value,
    toki_composition_moments,
    trade_mask,
)


# --- Nash pazarlık ---------------------------------------------------------- #
def test_nash_price_bounds_and_extremes():
    B = np.array([100.0, 200.0])
    S = np.array([80.0, 150.0])
    p = negotiated_price(B, S, 0.6)
    assert np.all((p >= np.minimum(B, S)) & (p <= np.maximum(B, S)))
    assert np.allclose(negotiated_price(B, S, 1.0), B)  # eta=1 → alıcıya
    assert np.allclose(negotiated_price(B, S, 0.0), S)  # eta=0 → satıcıya


def test_trade_only_when_buyer_ge_seller():
    B = np.array([100.0, 90.0])
    S = np.array([80.0, 120.0])
    assert list(trade_mask(B, S)) == [True, False]


# --- Kanunî taban (İİK) — legal_floor = max(0.5Q, priority_claims) + realization_costs -- #
def test_legal_floor_statutory_formula_costs_added():
    # AYIRT EDİCİ: claims < 0.5Q → yeni formul max(500k,300k)+400k=900k; ESKİ (max(0.5Q,pc+rc))=700k
    floor, exact = legal_floor(1_000_000, priority_claims=300_000, realization_costs=400_000)
    assert exact is True
    assert floor == max(500_000, 300_000) + 400_000 == 900_000


def test_legal_floor_claims_dominate_then_costs_added():
    floor, exact = legal_floor(1_000_000, priority_claims=700_000, realization_costs=100_000)
    assert exact is True
    assert floor == 700_000 + 100_000 == 800_000


def test_legal_floor_partial_when_unobserved():
    floor, exact = legal_floor(1_000_000)  # bileşen yok
    assert exact is False and floor == 500_000  # en az 0.5Q alt sınırı
    f2, e2 = legal_floor(1_000_000, priority_claims=700_000)  # costs yok
    assert e2 is False and f2 == 700_000  # max(0.5Q, 700k) + 0
    f3, e3 = legal_floor(1_000_000, realization_costs=100_000)  # claims yok
    assert e3 is False and f3 == 600_000  # max(0.5Q, 0) + 100k (alt sınır)


def test_normalize_auction_preserves_Q_and_bidders():
    rec = {
        "muhammen_bedel": 2_000_000,
        "winning_bid": 1_800_000,
        "katilimci_sayisi": 4,
        "sold": True,
    }
    a = normalize_auction(rec)
    assert a["appraised_value"] == 2_000_000  # Q korunur (rezerv/floor ile eşitlenmez)
    assert a["legal_floor"] == 1_000_000  # 0.5Q (kısmî)
    assert a["legal_floor_exact"] is False
    assert a["bidder_count"] == 4  # teklif sayısı KORUNUR
    assert a["appraised_value"] != a["legal_floor"]


# --- TCMB-çıpalı hedonik ---------------------------------------------------- #
def test_hedonic_level_from_tcmb_not_listing_intercept():
    rng = np.random.default_rng(0)
    n = 300
    age = rng.integers(0, 40, n).astype(float)
    m2 = rng.uniform(60, 180, n)
    # İlan fiyatları TCMB seviyesinin 10 KATI (kasıtlı çarpık seviye) + yaş etkisi
    price = 500_000 * 10 * (m2 / 100) * np.exp(-0.01 * age) * np.exp(rng.normal(0, 0.05, n))
    listings = pd.DataFrame(
        {"last_price": price, "gross_m2": m2, "building_age": age, "floor": 3, "room_count_num": 3}
    )
    hed = HedonicPremium().fit(listings)
    # Ortalama taşınmazın çarpanı ~1 (seviye intercept'ten GELMEZ, atılır)
    mean_feat = {c: hed.reference_[c] for c in hed.coef_}
    assert hed.multiplier(mean_feat) == pytest.approx(1.0, abs=1e-9)
    # Seviye TCMB'den: ppm2×m2; ilan fiyatı 10x olsa da fair value TCMB çıpalı
    fv = tcmb_fair_value(50_000, 100, premium_multiplier=1.0)
    assert fv == 5_000_000
    assert tcmb_fair_value(100_000, 100) == 2 * fv  # ppm2 iki katı → fair value iki katı


def test_hedonic_relative_premium_direction():
    rng = np.random.default_rng(1)
    n = 300
    age = rng.integers(0, 40, n).astype(float)
    m2 = rng.uniform(60, 180, n)
    price = 1_000_000 * (m2 / 100) * np.exp(-0.02 * age) * np.exp(rng.normal(0, 0.04, n))
    listings = pd.DataFrame(
        {"last_price": price, "gross_m2": m2, "building_age": age, "floor": 3, "room_count_num": 3}
    )
    hed = HedonicPremium().fit(listings)
    young = {**{c: hed.reference_[c] for c in hed.coef_}, "building_age": 0.0}
    old = {**{c: hed.reference_[c] for c in hed.coef_}, "building_age": 39.0}
    assert hed.multiplier(young) > hed.multiplier(old)  # yeni bina daha değerli


# --- TOKİ farklama + revizyon guard ----------------------------------------- #
def test_toki_differencing_cohorts_and_composition():
    disclosures = [
        {"as_of_date": "2019-06-30", "strata": [
            {"room_type": "2+1", "cum_count": 100, "cum_total": 100_000_000},
            {"room_type": "3+1", "cum_count": 50, "cum_total": 75_000_000},
        ]},
        {"as_of_date": "2019-12-31", "strata": [
            {"room_type": "2+1", "cum_count": 140, "cum_total": 145_000_000},
            {"room_type": "3+1", "cum_count": 60, "cum_total": 93_000_000},
        ]},
    ]
    res = difference_disclosures(disclosures)
    assert not res["revisions"]
    c21 = next(c for c in res["cohorts"] if c["room_type"] == "2+1")
    assert c21["cohort_count"] == 40  # 140-100
    assert c21["cohort_total"] == 45_000_000  # 145M-100M
    assert c21["cohort_avg"] == pytest.approx(45_000_000 / 40)
    comp = toki_composition_moments(res["cohorts"])
    shares = [v for k, v in comp.items() if k.startswith("toki_share_")]
    assert sum(shares) == pytest.approx(1.0, abs=1e-9)


def test_toki_revision_guard_skips_inconsistent():
    disclosures = [
        {"as_of_date": "2020-01-01", "strata": [{"room_type": "2+1", "cum_count": 100, "cum_total": 100_000_000}]},
        {"as_of_date": "2020-06-01", "strata": [{"room_type": "2+1", "cum_count": 80, "cum_total": 82_000_000}]},  # AZALDI → revizyon
    ]
    res = difference_disclosures(disclosures)
    assert res["cohorts"] == []  # tutarsız → farklama UYDURULMAZ
    assert len(res["revisions"]) == 1
    assert res["revisions"][0]["reason"] == "cumulative_decreased"


# --- Açık artırma simülasyonu ----------------------------------------------- #
def test_auction_sold_and_unsold_probability():
    rng = np.random.default_rng(3)
    p = StructuralParams(arrival_rate=2.0)
    Q = np.full(3000, 1_000_000.0)
    high = simulate_auctions(rng, Q, np.full(3000, 1_400_000.0), p)  # yüksek taban
    low = simulate_auctions(rng, Q, np.full(3000, 300_000.0), p)     # düşük taban
    mh = auction_moments(high["sold"], high["win_over_appraisal"])
    ml = auction_moments(low["sold"], low["win_over_appraisal"])
    assert 0.0 <= mh["uyap_sale_prob"] <= 1.0
    assert mh["uyap_sale_prob"] < ml["uyap_sale_prob"]  # yüksek taban → az satış
    assert np.isfinite(ml["uyap_win_over_appraisal_mean"])


# --- SMM parametre geri kazanımı -------------------------------------------- #
def _kap_observed(theta, n=40000, seed=2024):
    """theta0 altında sentetik KAP gerçekleşen/ekspertiz gözlemi üretir."""
    rng = np.random.default_rng(seed)
    V = np.ones(n)
    neg = simulate_negotiations(rng, V, theta, n, mechanism="kap")
    traded = neg["traded"]
    return neg["price"][traded], V[traded]


def test_smm_recovers_eta():
    theta0 = StructuralParams(eta=0.62)  # GERÇEK (diğerleri varsayılan)
    realized, appraisal = _kap_observed(theta0)
    m_obs = observed_moments(kap_realized=realized, kap_appraisal=appraisal)
    ctx = MomentContext(
        auction_appraised=np.array([]),
        auction_floors=np.array([]),
        kap_appraisal=np.ones(60),
        reps=400,
    )
    start = StructuralParams(eta=0.40)  # yanlış başlangıç, diğerleri truth
    res = estimate_smm(m_obs, ctx, free_names=("eta",), start=start, seed=777)
    assert abs(res.params.eta - 0.62) < 0.05  # eta GERİ KAZANILDI (hard-code YOK)


def test_smm_objective_improves_from_start():
    theta0 = StructuralParams(eta=0.55)
    realized, appraisal = _kap_observed(theta0, seed=99)
    m_obs = observed_moments(kap_realized=realized, kap_appraisal=appraisal)
    ctx = MomentContext(
        auction_appraised=np.array([]),
        auction_floors=np.array([]),
        kap_appraisal=np.ones(60),
        reps=400,
    )
    start = StructuralParams(eta=0.35)
    f_start = smm_objective(
        start.free_vector(("eta",)), ("eta",), start, m_obs, ctx, 777
    )
    res = estimate_smm(m_obs, ctx, free_names=("eta",), start=start, seed=777)
    assert res.objective <= f_start  # SMM hedefi başlangıçtan İYİLEŞTİ


# --- Yapısal tahminci ------------------------------------------------------- #
def test_predictor_output_shape_and_labeling():
    pred = StructuralClosingPredictor(StructuralParams())
    out = pred.predict(asking_price=3_800_000, fair_value=3_600_000, n=30000, seed=1)
    assert out["inferred_closing_median"] is not None
    lo, hi = out["interval_80"]
    assert lo <= out["inferred_closing_median"] <= hi  # 80% aralık sıralı
    assert 0.0 <= out["trade_probability"] <= 1.0
    assert "mechanism_transfer_sensitivity" in out
    # Etiketleme: açıkça "gözlenen closing DEĞİL / ölçülen doğruluk DEĞİL" der
    note = out["note"]
    assert "YAPISAL" in note and "DEĞİL" in note


def test_asking_is_signal_not_ceiling():
    pred = StructuralClosingPredictor(StructuralParams())
    # asking, fair value'nun ÇOK ALTINDA → closing asking'i AŞABİLİR (tavan değil)
    out = pred.predict(asking_price=2_200_000, fair_value=3_600_000, n=40000, seed=2)
    assert out["inferred_closing_median"] > out["asking_price"]


def test_higher_asking_shifts_inferred_closing_up():
    pred = StructuralClosingPredictor(StructuralParams())
    low = pred.predict(3_400_000, 3_600_000, n=40000, seed=5)["inferred_closing_median"]
    high = pred.predict(4_200_000, 3_600_000, n=40000, seed=5)["inferred_closing_median"]
    assert high > low  # asking = satıcı rezervasyonuna gürültülü sinyal


# --- TCMB çıpa çift-sayım audit -------------------------------------------- #
def test_tcmb_anchor_no_double_kfe():
    # Çağdaş TL/m² DOĞRUDAN seviye çıpası; EK TAM KFE çarpanı YOK
    assert tcmb_fair_value(50_000, 100) == 5_000_000
    assert tcmb_fair_value(50_000, 100, premium_multiplier=1.1) == pytest.approx(5_500_000)


def test_roll_unit_price_uses_kfe_ratio_only():
    # Eski çıpayı t'ye taşımak SADECE KFE ORANIYLA (seviye × endeks çift-sayımı yok)
    assert roll_unit_price(40_000, kfe_t=120, kfe_t0=100) == pytest.approx(48_000)
    # Taşınmış çıpa doğrudan kullanılır; üstüne EK KFE UYGULANMAZ
    assert tcmb_fair_value(48_000, 100) == 4_800_000


# --- UYAP alan semantiği + genişletilmiş şema ------------------------------- #
def test_normalize_auction_area_semantics_not_substituted():
    a = normalize_auction(
        {
            "muhammen_bedel": 2_000_000,
            "sold": False,
            "parcel_area_m2": 509,   # parsel
            "unit_net_m2": 32.5,     # birim net
            # unit_gross_m2 YOK → net/parsel ENJEKTE EDİLMEZ
            "offer_count": 0,
            "source_audited": True,
        }
    )
    assert a["parcel_area_m2"] == 509
    assert a["unit_net_m2"] == 32.5
    assert a["unit_gross_m2"] is None  # gross gözlenmedi → başka alandan doldurulmaz
    assert a["sold"] is False and a["winning_bid"] is None  # satılmayan da toplanır
    assert a["source_audited"] is True


def test_uyap_and_unsold_both_loaded():
    df = load_auctions(
        [
            {"muhammen_bedel": 1_000_000, "sold": True, "winning_bid": 900_000},
            {"muhammen_bedel": 2_000_000, "sold": False},
        ]
    )
    assert len(df) == 2
    assert df["sold"].sum() == 1  # satılan + satılmayan birlikte


# --- KAP yapısal veri kümesi ------------------------------------------------ #
def test_kap_excludes_related_party_and_missing():
    assert normalize_kap_disposal(
        {"related_party": True, "sale_price": 1, "appraisal_value": 1,
         "reference_price_type": "appraisal", "property_type": "arsa"}
    ) is None  # ilişkili taraf → dışlanır
    assert normalize_kap_disposal(
        {"sale_price": 1_000_000, "property_type": "konut"}
    ) is None  # ekspertiz/referans yok
    ok = normalize_kap_disposal(
        {"sale_price": 5_400_000, "appraisal_value": 5_000_000,
         "reference_price_type": "appraisal", "property_type": "konut", "related_party": False}
    )
    assert ok is not None and ok["related_party"] is False


def test_kap_log_ratio_moments():
    recs = [
        {"sale_price": 5_400_000, "appraisal_value": 5_000_000,
         "reference_price_type": "appraisal", "property_type": "konut",
         "value_method": "negotiation"}
        for _ in range(5)
    ]
    m = kap_observed_moments(load_kap_disposals(recs))
    assert m["kap_n"] == 5
    assert m["kap_log_ratio_mean"] == pytest.approx(np.log(5.4 / 5.0), abs=1e-9)


# --- TOKİ guard'ları -------------------------------------------------------- #
def test_toki_revision_detected_on_decrease():
    res = difference_disclosures(
        [
            {"as_of_date": "2020-01-01", "project_id": "P", "strata": [{"room_type": "2+1", "cum_count": 100, "cum_total": 100_000_000}]},
            {"as_of_date": "2020-06-01", "project_id": "P", "strata": [{"room_type": "2+1", "cum_count": 80, "cum_total": 82_000_000}]},
        ]
    )
    assert res["revision_detected"] is True
    assert res["cohorts"] == []


def test_toki_table_semantics_change_blocks_differencing():
    res = difference_disclosures(
        [
            {"as_of_date": "2020-01-01", "project_id": "P", "table_semantics": "v1", "strata": [{"room_type": "2+1", "cum_count": 100, "cum_total": 100_000_000}]},
            {"as_of_date": "2020-06-01", "project_id": "P", "table_semantics": "v2", "strata": [{"room_type": "2+1", "cum_count": 140, "cum_total": 145_000_000}]},
        ]
    )
    assert res["revision_detected"] is True
    assert res["cohorts"] == []


def test_toki_stratum_disappearance_flagged():
    res = difference_disclosures(
        [
            {"as_of_date": "2020-01-01", "project_id": "P", "strata": [
                {"room_type": "2+1", "cum_count": 100, "cum_total": 100_000_000},
                {"room_type": "3+1", "cum_count": 50, "cum_total": 75_000_000}]},
            {"as_of_date": "2020-06-01", "project_id": "P", "strata": [
                {"room_type": "2+1", "cum_count": 140, "cum_total": 145_000_000}]},  # 3+1 KAYBOLDU
        ]
    )
    assert res["revision_detected"] is True
    assert any(c["room_type"] == "2+1" for c in res["cohorts"])  # 2+1 hâlâ farklanır
    assert any(r.get("reason") == "stratum_disappeared" for r in res["revisions"])


def test_toki_projects_not_mixed():
    res = difference_disclosures(
        [
            {"as_of_date": "2020-01-01", "project_id": "A", "strata": [{"room_type": "2+1", "cum_count": 100, "cum_total": 100_000_000}]},
            {"as_of_date": "2020-06-01", "project_id": "B", "strata": [{"room_type": "2+1", "cum_count": 140, "cum_total": 145_000_000}]},
        ]
    )
    assert res["cohorts"] == []  # farklı projeler farklanmaz


# --- Kimliklendirme (identification) ---------------------------------------- #
def test_identification_not_identified_when_moments_lt_params():
    theta0 = StructuralParams(eta=0.6)
    realized, appraisal = _kap_observed(theta0)
    m_obs = observed_moments(kap_realized=realized, kap_appraisal=appraisal)  # 2 KAP momenti
    ctx = MomentContext(
        auction_appraised=np.array([]), auction_floors=np.array([]),
        kap_appraisal=np.ones(50), reps=200,
    )
    rep = identification_report(ctx, StructuralParams(), DEFAULT_FREE, m_obs=m_obs)
    assert rep["n_structural_parameters"] == len(DEFAULT_FREE)
    assert rep["n_observed_moments"] <= 2
    assert rep["status"] == "NOT_IDENTIFIED"  # 2 moment < 6 param
    assert rep["prediction_mode"] == "sensitivity_mode"
    assert rep["rank"] < rep["n_structural_parameters"]


def test_identification_empty_dataset_not_identified():
    ctx = context_from_datasets(None, None)
    rep = identification_report(ctx, StructuralParams(), DEFAULT_FREE, m_obs={})
    assert rep["status"] == "NOT_IDENTIFIED"
    assert rep["n_observed_moments"] == 0


def test_moment_jacobian_shape():
    ctx = MomentContext(
        auction_appraised=np.array([]), auction_floors=np.array([]),
        kap_appraisal=np.ones(40), reps=200,
    )
    J, keys = moment_jacobian(StructuralParams(), ctx, ("eta", "mu_s"))
    assert J.shape == (len(keys), 2)
    assert len(keys) >= 1


def test_dataset_summary_counts():
    auctions = load_auctions(
        [
            {"muhammen_bedel": 1_000_000, "sold": True, "winning_bid": 900_000, "bidder_count": 3},
            {"muhammen_bedel": 2_000_000, "sold": False, "priority_claims": 100_000, "realization_costs": 50_000},
        ]
    )
    s = dataset_summary(auctions=auctions)
    assert s["uyap_total"] == 2 and s["uyap_sold"] == 1 and s["uyap_unsold"] == 1
    assert s["uyap_bidder_count_observed"] == 1
    assert s["uyap_exact_legal_floor_observed"] == 1  # ikinci kayıt tam taban gözlemli


# --- Tahmin terminolojisi (prototip / sensitivity) -------------------------- #
def test_predictor_prototype_mode_labeling():
    out = StructuralClosingPredictor(StructuralParams(), identified=False).predict(
        3_800_000, 3_600_000, n=20000, seed=1
    )
    assert out["mode"] == "sensitivity_mode" and out["identified"] is False
    assert "PROTOT" in out["note"] and "PROVİZYONEL" in out["note"]


def test_predictor_identified_mode_labeling():
    out = StructuralClosingPredictor(StructuralParams(), identified=True).predict(
        3_800_000, 3_600_000, n=20000, seed=1
    )
    assert out["mode"] == "identified"
    assert "ÇIKARIMSAL" in out["note"] and "DEĞİL" in out["note"]


# --- GERÇEK denetlenmiş yapısal veri kümesi (Level-2 türevi) ---------------- #
def test_genuine_datasets_load_and_reconcile_with_level2():
    g = load_genuine_datasets()
    # UYAP: 1 denetlenmiş açık artırma; appraised_value = doğrulanmış Q (rezerv DEĞİL)
    assert len(g["uyap"]) == 1
    a = g["uyap"].iloc[0]
    assert a["appraised_value"] == 4_500_000  # doğrulanmış reference_price (Q)
    assert a["winning_bid"] == 4_545_000       # doğrulanmış realized_price
    assert bool(a["sold"]) is True
    assert a["parcel_area_m2"] == 509.0 and a["unit_net_m2"] == 32.5
    assert pd.isna(a["unit_gross_m2"])         # gross null (509 ENJEKTE EDİLMEZ)
    assert bool(a["legal_floor_exact"]) is False  # kısmî (claims/costs yok)
    # KAP: 1 müzakereli disposal (prior_appraisal)
    assert len(g["kap"]) == 1
    k = g["kap"].iloc[0]
    assert k["sale_price"] == 5_508_474.60 and k["appraisal_value"] == 5_200_000
    assert k["reference_price_type"] == "prior_appraisal"
    assert bool(k["negotiated"]) is True and bool(k["related_party"]) is False
    # TOKİ: 3 ardışık denetlenmiş disclosure → 4 geçerli dönem kohortu (mutabakat-bloklu strata hariç)
    assert len(g["toki_disclosures"]) == 3
    assert len(g["toki_result"]["cohorts"]) == 4
    assert g["toki_result"]["revision_detected"] is False


def test_dataset_status_genuine_counts():
    st = dataset_status()
    g = st["genuine"]
    assert g["uyap"]["total_audited_auctions"] == 1 and g["uyap"]["sold"] == 1 and g["uyap"]["unsold"] == 0
    assert g["uyap"]["exact_legal_floors_observed"] == 0  # kısmî taban
    assert g["kap"]["audited_eligible_disposals"] == 1
    assert g["kap"]["negotiated_calibration_observations"] == 1
    assert g["kap"]["prior_appraisal_observations"] == 1 and g["kap"]["appraisal_observations"] == 0
    assert g["toki"]["audited_disclosures"] == 3
    assert g["toki"]["valid_derived_period_cohorts"] == 4
    assert g["toki"]["reconciliation_blocked_strata"] == 3
    assert st["non_audited_records"]["uyap"] == 0


# --- Dürüst moment kullanılabilirliği (tek gözlemde varyans YOK) ------------ #
def test_single_observation_variance_is_unavailable_not_zero():
    m = auction_moments(np.array([True]), np.array([1.01]))
    assert np.isnan(m["uyap_win_over_appraisal_sd"])  # n=1 → sd tanımsız (0 DEĞİL)
    df = load_kap_disposals(
        [{"sale_price": 5.4e6, "appraisal_value": 5e6, "reference_price_type": "appraisal",
          "property_type": "konut", "value_method": "negotiation"}]
    )
    km = kap_observed_moments(df)
    assert "kap_log_ratio_sd" not in km  # n=1 → sd HESAPLANMAZ (uydurulmaz)


def test_build_observed_moments_reports_provenance_and_unavailable():
    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    m, prov, un = built["moments"], built["provenance"], built["unavailable"]
    assert "uyap_sale_prob" in m and "uyap_win_over_appraisal_mean" in m and "kap_log_ratio_mean" in m
    assert prov["kap_log_ratio_mean"] == "kap" and prov["uyap_sale_prob"] == "uyap"
    un_moments = {u["moment"] for u in un}
    assert "uyap_win_over_appraisal_sd" in un_moments  # n=1 sold
    assert "kap_log_ratio_sd" in un_moments            # n=1 negotiated
    # TOKİ kohort momenti artık AVAILABLE (3 gerçek disclosure → 4 kohort), unavailable DEĞİL
    assert "toki_cohort_avg_price" in m and "toki_cohort_moments" not in un_moments
    assert m["kap_log_ratio_mean"] == pytest.approx(np.log(5_508_474.60 / 5_200_000), abs=1e-6)


def test_kap_negotiated_subset_only():
    df = load_kap_disposals(
        [
            {"sale_price": 5.4e6, "appraisal_value": 5e6, "reference_price_type": "appraisal",
             "property_type": "konut", "value_method": "negotiation"},
            {"sale_price": 6e6, "appraisal_value": 5e6, "reference_price_type": "appraisal",
             "property_type": "konut", "value_method": "tender"},  # müzakereli DEĞİL
        ]
    )
    assert int(df["negotiated"].sum()) == 1
    assert kap_observed_moments(df, negotiated_only=True)["kap_n"] == 1  # yalnızca müzakereli


# --- Kimliklendirme: gerçek veri → NOT_IDENTIFIED; 3'lü durum --------------- #
def test_identify_genuine_data_not_identified():
    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    ctx = context_from_datasets(g["uyap"], g["kap"])
    rep = identification_report(
        ctx, StructuralParams(), DEFAULT_FREE, m_obs=built["moments"],
        auctions=g["uyap"], kap=g["kap"], toki_result=g["toki_result"],
        provenance=built["provenance"], unavailable=built["unavailable"],
    )
    assert rep["status"] == "NOT_IDENTIFIED"
    assert rep["rank"] < rep["n_structural_parameters"]
    assert rep["n_observed_moments"] <= 3  # 3 gerçek tek-gözlem ortalaması
    assert rep["prediction_mode"] == "sensitivity_mode"
    assert rep["moment_provenance"] and rep["unavailable_moments"]


def test_identify_single_param_can_be_identified():
    # 1 param (eta) + 1 bilgilendirici moment (kap mean) → IDENTIFIED (3'lü durum çalışır)
    theta0 = StructuralParams(eta=0.6)
    realized, appraisal = _kap_observed(theta0)
    full = observed_moments(kap_realized=realized, kap_appraisal=appraisal)
    m_obs = {"kap_log_ratio_mean": full["kap_log_ratio_mean"]}  # tam olarak 1 moment
    ctx = MomentContext(
        auction_appraised=np.array([]), auction_floors=np.array([]),
        kap_appraisal=np.ones(60), reps=300,
    )
    rep = identification_report(ctx, StructuralParams(), ("eta",), m_obs=m_obs)
    assert rep["rank"] == 1 and rep["n_structural_parameters"] == 1
    assert rep["status"] == "IDENTIFIED" and rep["prediction_mode"] == "identified"


def test_jacobian_restricted_to_observed_moments():
    # Simülatör 5 moment üretse de, m_obs 1 moment ise Jacobian 1 sütun-satır sınırında
    ctx = MomentContext(
        auction_appraised=np.array([]), auction_floors=np.array([]),
        kap_appraisal=np.ones(40), reps=200,
    )
    J, keys = moment_jacobian(
        StructuralParams(), ctx, ("eta", "mu_s"), moment_keys=["kap_log_ratio_mean"]
    )
    assert keys == ["kap_log_ratio_mean"]  # yalnızca gözlenen moment
    assert J.shape == (1, 2)


# --- Kaynağa özgü Jacobian (hangi kaynak bağımsız yön ekliyor?) -------------- #
def test_source_jacobian_ranks_on_genuine_data():
    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    ctx = context_from_datasets(g["uyap"], g["kap"])
    sj = source_jacobian_ranks(
        StructuralParams(), ctx, DEFAULT_FREE, built["moments"], built["provenance"]
    )
    assert sj["J_TOKI"]["rank"] == 0 and sj["J_TOKI"]["n_moments"] == 0  # kohort yok
    assert sj["J_KAP"]["rank"] == 1 and sj["J_KAP"]["n_moments"] == 1
    assert sj["J_UYAP"]["rank"] == 1  # sale_prob=1.0 dejenere → 1 bağımsız yön
    assert sj["J_combined"]["rank"] == 2  # birleşik = ölçülen rank


# --- Snapshot karşılaştırması (identification-katkı) ------------------------ #
def test_compare_snapshots_detects_unlock_and_rank_change():
    before = {
        "available_moments": ["uyap_sale_prob"],
        "sample_sizes": {"uyap_win_over_appraisal_sd": 1},
        "rank": 2, "smallest_nonzero_singular_value": 0.5,
        "condition_number": float("inf"), "status": "NOT_IDENTIFIED",
    }
    after = {
        "available_moments": ["uyap_sale_prob", "uyap_win_over_appraisal_sd"],
        "sample_sizes": {"uyap_win_over_appraisal_sd": 2},
        "rank": 3, "smallest_nonzero_singular_value": 0.4,
        "condition_number": 1e5, "status": "NOT_IDENTIFIED",
    }
    cmp = compare_snapshots(before, after)
    assert cmp["moments_newly_unlocked"] == ["uyap_win_over_appraisal_sd"]
    assert cmp["moments_sample_increased"][0]["moment"] == "uyap_win_over_appraisal_sd"
    assert cmp["rank"]["before"] == 2 and cmp["rank"]["after"] == 3


# --- Milestone MEKANİZMASI (FIXTURE — genuine DEĞİL — makinenin çalıştığını gösterir) --- #
def test_second_sold_auction_unlocks_win_sd_fixture():
    df = load_auctions(
        [
            {"muhammen_bedel": 1_000_000, "sold": True, "winning_bid": 950_000},
            {"muhammen_bedel": 2_000_000, "sold": True, "winning_bid": 2_100_000},
        ]
    )
    built = build_observed_moments(df, None, None)
    assert "uyap_win_over_appraisal_sd" in built["moments"]  # ≥2 satış → sd AÇILDI
    assert built["sample_sizes"]["uyap_win_over_appraisal_mean"] == 2


def test_unsold_auction_makes_sale_prob_non_degenerate_fixture():
    df = load_auctions(
        [
            {"muhammen_bedel": 1_000_000, "sold": True, "winning_bid": 950_000},
            {"muhammen_bedel": 2_000_000, "sold": False},
        ]
    )
    built = build_observed_moments(df, None, None)
    assert built["moments"]["uyap_sale_prob"] == 0.5  # artık mekanik 1.0 DEĞİL


def test_two_consecutive_disclosures_unlock_toki_cohort_fixture():
    disc = [
        {"as_of_date": "2019-12-31", "project_id": "PMVR3", "table_semantics": "crs",
         "strata": [{"room_type": "2+1", "cum_count": 165, "cum_total": 118_418_019.69}]},
        {"as_of_date": "2020-06-30", "project_id": "PMVR3", "table_semantics": "crs",
         "strata": [{"room_type": "2+1", "cum_count": 180, "cum_total": 130_000_000.0}]},
    ]
    res = difference_disclosures(disc)
    assert res["revision_detected"] is False and len(res["cohorts"]) == 1
    built = build_observed_moments(None, None, res)
    assert "toki_cohort_avg_price" in built["moments"]  # kohort momenti AÇILDI


def test_second_kap_unlocks_log_ratio_sd_fixture():
    df = load_kap_disposals(
        [
            {"sale_price": 5.4e6, "appraisal_value": 5e6, "reference_price_type": "appraisal",
             "property_type": "konut", "value_method": "negotiation"},
            {"sale_price": 6.2e6, "appraisal_value": 5.5e6, "reference_price_type": "appraisal",
             "property_type": "konut", "value_method": "negotiation"},
        ]
    )
    built = build_observed_moments(None, df, None)
    assert "kap_log_ratio_sd" in built["moments"]  # ≥2 müzakereli → sd AÇILDI


# --- GERÇEK PMVR3 serisi (3 denetlenmiş disclosure) dönem kohortları ---------- #
def test_genuine_pmvr3_series_derives_expected_cohorts():
    g = load_genuine_datasets()
    res = g["toki_result"]
    assert res["revision_detected"] is False
    cohorts = {(c["period_start"], c["period_end"], c["room_type"]): c for c in res["cohorts"]}
    # Oct→Nov geçerli
    c = cohorts[("2019-10-31", "2019-11-30", "2+1")]
    assert c["cohort_count"] == 7 and c["cohort_total"] == pytest.approx(7113710.07)
    assert c["cohort_avg"] == pytest.approx(1016244.2957142857)
    c = cohorts[("2019-10-31", "2019-11-30", "3+1")]
    assert c["cohort_count"] == 2 and c["cohort_avg"] == pytest.approx(1435225.00)
    # Nov→Dec geçerli
    c = cohorts[("2019-11-30", "2019-12-31", "2+1")]
    assert c["cohort_count"] == 5 and c["cohort_avg"] == pytest.approx(1003690.00)
    c = cohorts[("2019-11-30", "2019-12-31", "4+1")]
    assert c["cohort_count"] == 1 and c["cohort_avg"] == pytest.approx(1684074.65)
    assert len(res["cohorts"]) == 4  # 4+1 Oct→Nov (delta=0) kohort DEĞİL
    # mutabakat-bloklu (delta_count=0 & delta_total≠0): 5+1 Oct→Nov, 3+1 & 5+1 Nov→Dec
    recon = {(r["room_type"], r["period_end"]) for r in res["reconciliation"]}
    assert ("5+1", "2019-11-30") in recon
    assert ("3+1", "2019-12-31") in recon
    assert ("5+1", "2019-12-31") in recon
    assert len(res["reconciliation"]) == 3


def test_genuine_toki_moment_available_but_no_sim_counterpart():
    # TOKİ kohort momenti artık AÇIK, ama mevcut modelde sim KARŞILIĞI YOK → J_TOKI rank 0
    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    assert "toki_cohort_avg_price" in built["moments"]
    assert built["provenance"]["toki_cohort_avg_price"] == "toki"
    ctx = context_from_datasets(g["uyap"], g["kap"])
    rep = identification_report(
        ctx, StructuralParams(), DEFAULT_FREE, m_obs=built["moments"],
        auctions=g["uyap"], kap=g["kap"], toki_result=g["toki_result"],
        provenance=built["provenance"],
    )
    assert "toki_cohort_avg_price" in rep["observed_without_simulated_counterpart"]
    assert rep["source_jacobians"]["J_TOKI"]["rank"] == 0  # sim karşılığı yok
    assert rep["source_jacobians"]["J_combined"]["rank"] == 2  # değişmedi
    assert rep["status"] == "NOT_IDENTIFIED"


# --- TOKİ external_cross_mechanism_benchmark reclassification ---------------- #
def test_toki_reclassified_as_external_benchmark_not_unavailable():
    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    ctx = context_from_datasets(g["uyap"], g["kap"])
    rep = identification_report(
        ctx, StructuralParams(), DEFAULT_FREE, m_obs=built["moments"],
        auctions=g["uyap"], kap=g["kap"], toki_result=g["toki_result"],
        provenance=built["provenance"],
    )
    eb = rep["external_benchmarks"]
    assert "toki" in eb
    assert eb["toki"]["genuine_observed_moments"] == 5
    assert eb["toki"]["smm_role"] == "external_cross_mechanism_benchmark"
    assert eb["toki"]["moments_used_in_identification"] == 0
    assert eb["toki"]["reason"] == "no current model-implied primary-market counterpart"
    # TOKİ momentleri "unavailable" DEĞİL (gözlenir + kullanılabilir, ama SMM DIŞI)
    un_moments = {u["moment"] for u in rep["unavailable_moments"]}
    assert not any(str(m).startswith("toki") for m in un_moments)
    assert rep["status"] == "NOT_IDENTIFIED"  # SMM durumu değişmez


# --- KAP 265789 -> 312317 PENDING_AUDIT adayı (kabul EDİLMEZ) ---------------- #
def test_kap_candidate_pending_not_admitted():
    g = load_genuine_datasets()
    assert len(g["kap"]) == 1  # aday admitte KAP setine GİRMEZ
    cands = load_kap_candidates()
    assert len(cands) == 1
    c = cands[0]
    assert c["audit_status"] == "PENDING_AUDIT"
    assert c["source_record_ids"] == [265789, 312317]  # bağlı açıklama zinciri korunur
    # currency (5,6), VAT (7,8) ve related-party (4) doğrulanamaz → bloklu
    blocked = {b["condition"] for b in c["blocking_conditions"]}
    assert {4, 5, 6, 7, 8}.issubset(blocked)
    # KAP momenti henüz sd AÇILMAZ (yalnızca 1 admitte gözlem)
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    assert "kap_log_ratio_sd" not in built["moments"]


def test_dataset_status_reports_pending_kap_candidate():
    st = dataset_status()
    assert st["genuine"]["kap"]["audited_eligible_disposals"] == 1  # admitte değişmez
    pend = st["kap_pending_candidates"]
    assert len(pend) == 1 and pend[0]["candidate_id"] == "KAP-265789-312317"
    assert pend[0]["source_record_ids"] == [265789, 312317]
