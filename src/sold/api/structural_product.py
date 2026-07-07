"""Ürün-yüzeyi MONTAJI — DONMUŞ yapısal çekirdeği yalnızca ÇAĞIRIR (yeni ekonometri YOK).

Bu katman sunum/montajdır: kabul edilebilir yakın-uyum kümesi (Θ_A) BİR KEZ kurulup
önbelleğe alınır (gerçek kamu momentlerinden; asking/fair-value'dan bağımsız). Her istek
``IdentificationAwarePredictor`` ile yapısal DUYARLILIK zarfı üretir ve
``input_conflict_diagnostic`` yüzeye çıkarılır. Model Evidence ve Method görünümleri
gerçek kamu kanıtını DÜRÜSTÇE (fixture/test'ten AYRI) raporlar. Çekirdek dokunulmaz.

Not: burada hiçbir eşik/parametre EKONOMETRİK olarak tahmin edilmez. ``[0.5, 2.0]``
ask/fair-value çelişki sınırları YAPILANDIRILABİLİR ÜRÜN tanılamalarıdır (tahmin DEĞİL);
near-fit toleransı da örnekleme-kalibreli bir güven-kümesi eşiği DEĞİL, belgeli sayısal
duyarlılık kuralıdır.
"""

from __future__ import annotations

from ..model.synthetic import load_province_ppm2
from ..structural import (
    CENTRAL_ESTIMATE_DEFINITION,
    CONDITIONAL_ON_TRADE_STATEMENT,
    DEFAULT_FREE,
    FUTURE_METHODOLOGY_NOTE,
    PRICE_ESTIMATE_CONDITION,
    REPRESENTATIVE_THETA_RULE,
    TRADE_SHARE_CALIBRATION,
    IdentificationAwarePredictor,
    StructuralParams,
    admissible_near_fit_set,
    build_observed_moments,
    context_from_datasets,
    dataset_status,
    identification_report,
    input_conflict_diagnostic,
    load_genuine_datasets,
    tcmb_fair_value,
)

# Θ_A kurulumu için aday sayısı (YAPILANDIRILABİLİR; ekonometrik eşik DEĞİL).
NEAR_FIT_CANDIDATES = 1500
NEAR_FIT_SEED = 12345

# Tek, kalıcı metodolojik disclaimer (sayfada bir kez gösterilir).
PRODUCT_DISCLAIMER = (
    "This system infers a transaction-price distribution from a structural economic model "
    "calibrated to public appraisal, auction and negotiated-sale evidence. It does not observe "
    "the property's actual closing price and does not report measured ordinary-resale prediction "
    "accuracy. Structural sensitivity ranges are not confidence intervals."
)

NO_COVERAGE_STATEMENT = "This is not a confidence interval and carries no frequentist coverage claim."

_CACHE: dict = {}


def reset_near_fit_cache() -> None:
    """Önbelleği temizler (testler küçük aday sayısıyla yeniden kurmak için kullanır)."""
    _CACHE.clear()


def _near_fit():
    """Θ_A + identification raporunu BİR KEZ kurup önbelleğe alır (gerçek momentlerden)."""
    if "res" not in _CACHE:
        g = load_genuine_datasets()
        built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
        ctx = context_from_datasets(g["uyap"], g["kap"])
        res = admissible_near_fit_set(
            built["moments"], ctx, n_candidates=NEAR_FIT_CANDIDATES, seed=NEAR_FIT_SEED
        )
        rep = identification_report(
            ctx, StructuralParams(), DEFAULT_FREE, m_obs=built["moments"],
            auctions=g["uyap"], kap=g["kap"], toki_result=g["toki_result"],
            provenance=built["provenance"], unavailable=built["unavailable"],
            ineligible=built["ineligible"],
        )
        _CACHE.update({"res": res, "rep": rep, "status": dataset_status()})
    return _CACHE["res"], _CACHE["rep"], _CACHE["status"]


def fair_value_for(province: str | None, gross_m2: float | None) -> float | None:
    """TCMB çağdaş ekspertiz TL/m² çıpasından fair value (EK KFE çarpanı YOK)."""
    ppm2 = load_province_ppm2().get(province or "İstanbul")
    return tcmb_fair_value(ppm2, gross_m2 or 0.0)


def _genuine_counts(rep: dict, status: dict) -> dict:
    """Gerçek kamu kanıtı sayıları — fixture/test'ten AYRI, otomatik güncel."""
    ug = status["genuine"]["uyap"]
    kg = status["genuine"]["kap"]
    toki_ext = (rep.get("external_benchmarks") or {}).get("toki", {})
    return {
        "genuine_uyap_observations": int(ug.get("sold", 0)),  # tamamlanmış satış açık artırmaları
        "genuine_kap_observations": int(kg.get("negotiated_calibration_observations", 0)),
        "toki_external_moments": int(toki_ext.get("genuine_observed_moments", 0)),
    }


def structural_valuation(
    asking_price: float,
    province: str | None = "İstanbul",
    gross_m2: float | None = 100.0,
    tightness: float = 0.0,
) -> dict | None:
    """Makine-okunur yapısal değerleme sonucu (donmuş çekirdek + input-conflict).

    ``confidence_interval`` / ``accuracy`` alanı ASLA döndürülmez. Fair value bulunamazsa
    ``None`` döner (çağıran 422 verir)."""
    fv = fair_value_for(province, gross_m2)
    if fv is None or fv <= 0:
        return None
    res, rep, status = _near_fit()
    pred = IdentificationAwarePredictor(res.admissible_params, res.best_params).predict(
        asking_price, fv, tightness=tightness
    )
    ic = pred["input_conflict"]
    counts = _genuine_counts(rep, status)
    return {
        "methodology": "structural_econometrics",
        "identification_status": pred["identification_status"],
        "coverage_claim": pred["coverage_claim"],
        "asking_price": float(asking_price),
        "fair_value": round(float(fv), 0),
        "province": province,
        "gross_m2": gross_m2,
        # KOŞULLU-TİCARET fiyat semantiği
        "price_estimate_condition": pred["price_estimate_condition"],
        "conditional_on_trade_statement": pred["conditional_on_trade_statement"],
        "central_estimate_definition": pred["central_estimate_definition"],
        "representative_theta_rule": pred["representative_theta_rule"],
        "central_structural_estimate": pred["central_structural_estimate"],
        "within_theta_negotiation_interval": list(pred["within_theta_negotiation_interval"]),
        "between_theta_near_fit_band": list(pred["between_theta_near_fit_band"]),
        "structural_sensitivity_range": list(pred["structural_sensitivity_range"]),
        # model-implied simüle B≥S payı — ampirik satış olasılığı DEĞİL
        "simulated_trade_share_band": list(pred["simulated_trade_share_band"]),
        "trade_probability_band": list(pred["trade_probability_band"]),  # geriye uyumluluk
        "trade_share_calibration": pred["trade_share_calibration"],
        "ask_to_fair_value_ratio": round(ic["ask_to_fair_value_ratio"], 4),
        "input_conflict": ic["input_conflict"],
        "input_conflict_warning": ic["message"],
        "input_conflict_candidate_explanations": ic["candidate_explanations"],
        "smm_moments_used": list(rep["moment_keys"]),
        "jacobian_rank": int(rep["rank"]),
        "parameter_dimension": int(rep["n_structural_parameters"]),
        # near-fit θ sayıları (ticaret eden + etmeyen = toplam; zarf yalnızca ticaret edenlerden)
        "near_fit_parameter_count": int(pred["near_fit_parameter_count"]),
        "trading_near_fit_parameter_count": int(pred["trading_near_fit_parameter_count"]),
        "nontrading_near_fit_parameter_count": int(pred["nontrading_near_fit_parameter_count"]),
        "price_envelope_theta_count": int(pred["price_envelope_theta_count"]),
        "near_fit_search_stability": _CACHE.get("stability_status", "not_computed"),
        "no_coverage_statement": NO_COVERAGE_STATEMENT,
        "disclaimer": PRODUCT_DISCLAIMER,
        "note": pred["note"],
        **counts,
    }


def model_evidence() -> dict:
    """Gerçek kamu yapısal kanıtı — DÜRÜST, fixture/test'ten AYRI. Test sayıları GÖSTERİLMEZ."""
    res, rep, status = _near_fit()
    counts = _genuine_counts(rep, status)
    return {
        **counts,
        "smm_moments_used": list(rep["moment_keys"]),
        "n_smm_moments_used": int(rep["n_observed_moments"]),
        "jacobian_rank": int(rep["rank"]),
        "parameter_dimension": int(rep["n_structural_parameters"]),
        "identification_status": rep["status"],
        "explanations": {
            "uyap": "UYAP moments (uyap_win_over_appraisal_mean/sd) are conditional on OBSERVED "
                    "COMPLETED auction sales — not unconditional auction-market moments.",
            "kap": "KAP records are CORPORATE negotiated disposals (arm's-length, non-related); "
                   "they are NOT ordinary residential resale ground truth.",
            "toki": "TOKİ is an EXTERNAL cross-mechanism benchmark and contributes ZERO moments "
                    "to the current SMM objective because the model does not simulate a "
                    "primary-market mechanism.",
        },
        "excluded_from_smm": {
            "uyap_sale_prob": "removed — public UYAP outcome taxonomy does not currently identify "
                              "a comparable negative auction trade class",
        },
        "disclaimer": PRODUCT_DISCLAIMER,
    }


def method_overview() -> dict:
    """Yapısal mekanizmanın özlü açıklaması (Method görünümü)."""
    return {
        "pipeline": [
            "TCMB appraisal anchor -> fair value",
            "UYAP completed auctions -> conditional auction outcome moments",
            "KAP negotiated disposals -> negotiated-sale moments",
            "SMM -> near-fit structural parameter configurations (Θ_A)",
            "asking price -> noisy seller signal",
            "trade iff B >= S",
            "P = eta * B + (1 - eta) * S",
            "simulation across Θ_A -> structural sensitivity range",
        ],
        "definitions": {
            "B": "buyer valuation",
            "S": "seller reservation value",
            "eta": "seller bargaining power",
            "P": "negotiated price (Nash): P = eta*B + (1-eta)*S",
        },
        "clarifications": [
            "eta is estimated JOINTLY within the structural model; it is NOT directly measured from KAP.",
            "UYAP appraised value (muhammen bedel = Q) is the COURT-APPRAISED value, NOT the auction reserve.",
            "asking price is a noisy strategic seller signal, not a ground-truth ceiling.",
        ],
        "identification": {
            "jacobian_rank": 4,
            "parameter_dimension": 6,
            "identification_status": "STRUCTURALLY_UNDERIDENTIFIED",
            "near_fit_tolerance": "documented computational sensitivity rule, NOT a sampling-calibrated cutoff",
            "future_methodology_note": FUTURE_METHODOLOGY_NOTE,
        },
        "conflict_bounds_note": "The default ask-to-fair-value conflict bounds [0.5, 2.0] are "
                                "CONFIGURABLE product diagnostics, not econometrically estimated thresholds.",
        "prediction_semantics": {
            "price_estimate_condition": PRICE_ESTIMATE_CONDITION,
            "conditional_on_trade_statement": CONDITIONAL_ON_TRADE_STATEMENT,
            "central_estimate_definition": CENTRAL_ESTIMATE_DEFINITION,
            "representative_theta_rule": REPRESENTATIVE_THETA_RULE,
            "trade_share_calibration": TRADE_SHARE_CALIBRATION,
            "trade_share_note": "trade_probability_band / simulated_trade_share_band is the Monte "
                                "Carlo simulated share satisfying B >= S under each near-fit theta; "
                                "it is NOT a probability of sale / sale likelihood / empirically "
                                "estimated trade probability. A simulated share of zero is not proof "
                                "that the population trade probability is mathematically zero.",
        },
        "disclaimer": PRODUCT_DISCLAIMER,
    }


# --- Θ_A arama-bütçesi sayısal kararlılık tanılaması (ekonometrik sınıflandırma DEĞİL) --- #
STABILITY_RANGE_TOL = 0.15       # parametre bant-payı değişimi (bound-genişliğine göre)
STABILITY_ENVELOPE_TOL = 0.25    # duyarlılık zarfı genişlik değişimi (göreli)
STABILITY_CENTER_TOL = 0.10      # zarf merkezi kayması (göreli)
STABILITY_COVERAGE_GROWTH = 1.5  # |Θ_A| bu katı büyürse yetersiz kapsama işareti


def _env_width_center(env):
    if env is None or env[0] is None or env[1] is None:
        return None, None
    return float(env[1] - env[0]), float((env[0] + env[1]) / 2.0)


def _classify_stability(rows: list[dict]) -> tuple[str, str]:
    """İki en büyük bütçeyi karşılaştıran BELGELİ sayısal kural (kapsama/aralık/zarf).

    - |Θ_A| büyümesi >= STABILITY_COVERAGE_GROWTH → INSUFFICIENT_COVERAGE (arama yakınsamadı),
    - parametre bant-payları + zarf kararlıysa → STABLE,
    - aksi halde → SEARCH_SENSITIVE.
    """
    from ..structural.partial import PARAM_BOUNDS

    if len(rows) < 2:
        return "SEARCH_SENSITIVE", "en az iki bütçe gerekir"
    prev, last = rows[-2], rows[-1]
    n_prev = max(int(prev["near_fit_parameter_count"]), 1)
    coverage_growth = last["near_fit_parameter_count"] / n_prev
    # parametre bant-payı kararlılığı
    range_ok = True
    for p, (lo, hi) in {k: v for k, v in last["param_ranges"].items()}.items():
        w_last = hi - lo
        plo, phi = prev["param_ranges"][p]
        w_prev = phi - plo
        bw = PARAM_BOUNDS[p][1] - PARAM_BOUNDS[p][0]
        if bw > 0 and abs(w_last - w_prev) / bw > STABILITY_RANGE_TOL:
            range_ok = False
            break
    # zarf kararlılığı
    wl, cl = _env_width_center(last["structural_sensitivity_range"])
    wp, cp = _env_width_center(prev["structural_sensitivity_range"])
    if wl is None or wp is None:
        env_ok = (wl is None) and (wp is None)  # ikisi de null ise "kararlı" say (ikisi de ~0 ticaret)
    else:
        env_ok = (abs(wl - wp) / max(wl, 1.0) <= STABILITY_ENVELOPE_TOL) and (
            abs(cl - cp) / max(cl, 1.0) <= STABILITY_CENTER_TOL
        )
    if coverage_growth >= STABILITY_COVERAGE_GROWTH:
        return "INSUFFICIENT_COVERAGE", (
            f"|Θ_A| {prev['near_fit_parameter_count']}→{last['near_fit_parameter_count']} "
            f"(×{coverage_growth:.2f} ≥ {STABILITY_COVERAGE_GROWTH}); arama daha büyük bütçede "
            "belirgin biçimde daha çok yakın-uyum vektörü buluyor"
        )
    if range_ok and env_ok:
        return "STABLE", "en büyük iki bütçede parametre bant-payları ve duyarlılık zarfı kararlı"
    return "SEARCH_SENSITIVE", "parametre bant-payı ve/veya duyarlılık zarfı bütçeyle belirgin değişiyor"


def search_budget_stability(
    budgets=(750, 1500, 3000),
    asking: float = 5_000_000.0,
    province: str = "İstanbul",
    gross_m2: float = 100.0,
    seed: int = NEAR_FIT_SEED,
) -> dict:
    """Θ_A arama-bütçesi KARARLILIK çalışması (yeniden-üretilebilir; sayısal — ekonometrik DEĞİL).

    Aynı hedef/bound/CRN/near-fit kriteriyle artan aday yoğunluklarında Θ_A + tahmin
    ölçütlerini karşılaştırır. Tolerans kuralı, moment tanımı, bound DEĞİŞMEZ."""
    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    ctx = context_from_datasets(g["uyap"], g["kap"])
    fv = fair_value_for(province, gross_m2)
    rows: list[dict] = []
    for b in budgets:
        res = admissible_near_fit_set(built["moments"], ctx, n_candidates=b, seed=seed)
        pred = IdentificationAwarePredictor(res.admissible_params, res.best_params).predict(
            asking, fv, n=12000, seed=0
        )
        rows.append({
            "candidate_count": int(b),
            "near_fit_parameter_count": int(res.n_admissible),
            "best_objective": round(float(res.best_objective), 6),
            "param_ranges": {p: [round(r["min"], 3), round(r["max"], 3)] for p, r in res.param_ranges.items()},
            "eta_range": [round(res.param_ranges["eta"]["min"], 3), round(res.param_ranges["eta"]["max"], 3)],
            "central_structural_estimate": pred["central_structural_estimate"],
            "between_theta_near_fit_band": list(pred["between_theta_near_fit_band"]),
            "structural_sensitivity_range": list(pred["structural_sensitivity_range"]),
            "trading_near_fit_parameter_count": int(pred["trading_near_fit_parameter_count"]),
            "nontrading_near_fit_parameter_count": int(pred["nontrading_near_fit_parameter_count"]),
        })
    status, reason = _classify_stability(rows)
    _CACHE["stability_status"] = status
    return {
        "diagnostic": "near_fit_search_stability",
        "note": "numerical approximation diagnostic of the reproducible search over the bounded "
                "theta space — NOT an econometric identification classification. Tolerance rule, "
                "moments and bounds are unchanged.",
        "interpretation": "A non-STABLE result is the EXPECTED numerical signature of "
                          "STRUCTURALLY_UNDERIDENTIFIED (Jacobian rank 4 < dim 6): the near-fit "
                          "region is a large, near-flat manifold along ~2 weakly-constrained "
                          "directions, so a finite reproducible search does not stably enumerate "
                          "it. This is not a code defect; STABLE is not claimed for a genuinely "
                          "underidentified near-fit region. Each fixed (seed, budget) run is "
                          "itself deterministic and reproducible.",
        "reference_scenario": {"asking_price": asking, "province": province, "gross_m2": gross_m2,
                               "fair_value": round(float(fv), 0)},
        "budgets": list(budgets),
        "table": rows,
        "near_fit_search_stability": status,
        "rule": reason,
    }
