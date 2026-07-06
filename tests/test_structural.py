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
    HedonicPremium,
    MomentContext,
    StructuralClosingPredictor,
    StructuralParams,
    auction_moments,
    difference_disclosures,
    estimate_smm,
    legal_floor,
    negotiated_price,
    normalize_auction,
    observed_moments,
    simulate_auctions,
    simulate_negotiations,
    smm_objective,
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


# --- Kanunî taban (İİK) — muhammen_bedel = Q, rezerv DEĞİL ------------------- #
def test_legal_floor_exact_when_components_observed():
    floor, exact = legal_floor(1_000_000, priority_claims=700_000, realization_costs=100_000)
    assert exact is True
    assert floor == max(500_000, 800_000) == 800_000


def test_legal_floor_half_dominates():
    floor, exact = legal_floor(1_000_000, priority_claims=200_000, realization_costs=50_000)
    assert exact is True
    assert floor == 500_000  # max(0.5Q=500k, 250k)


def test_legal_floor_partial_when_unobserved():
    floor, exact = legal_floor(1_000_000)  # bileşen yok
    assert exact is False  # KISMÎ — uydurulmaz
    assert floor == 500_000  # en az 0.5Q alt sınırı
    floor2, exact2 = legal_floor(1_000_000, priority_claims=700_000)  # costs yok
    assert exact2 is False
    assert floor2 == 700_000  # gözlenen bileşenle alt sınır


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
