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

import json
import threading
from dataclasses import replace
from pathlib import Path

from ..model.synthetic import load_province_ppm2
from ..structural import (
    CENTRAL_ESTIMATE_DEFINITION,
    CONDITIONAL_ON_TRADE_STATEMENT,
    DEFAULT_FREE,
    FUTURE_METHODOLOGY_NOTE,
    PRICE_ESTIMATE_CONDITION,
    REPRESENTATIVE_THETA_RULE,
    STRUCTURAL_MODEL_SEMANTICS_VERSION,
    TRADE_SHARE_CALIBRATION,
    IdentificationAwarePredictor,
    AdmissibleNearFitResult,
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
_CACHE_LOCK = threading.RLock()
STABILITY_SNAPSHOT = Path(__file__).resolve().parents[1] / "evidence" / "stability.json"
NEAR_FIT_SNAPSHOT = Path(__file__).resolve().parents[1] / "evidence" / "near_fit.json"


def reset_near_fit_cache() -> None:
    """Önbelleği temizler (testler küçük aday sayısıyla yeniden kurmak için kullanır)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _near_fit():
    """Θ_A + identification raporunu BİR KEZ kurup önbelleğe alır (gerçek momentlerden)."""
    with _CACHE_LOCK:
        if "res" not in _CACHE:
            g = load_genuine_datasets()
            built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
            ctx = context_from_datasets(g["uyap"], g["kap"])
            if not NEAR_FIT_SNAPSHOT.exists():
                raise RuntimeError("Structural near-fit snapshot is not available")
            snapshot = json.loads(NEAR_FIT_SNAPSHOT.read_text(encoding="utf-8"))
            current_moments = {
                key: float(value) for key, value in built["moments"].items()
                if key.startswith(("uyap_win", "kap_log"))
            }
            stored_moments = {
                key: float(value) for key, value in snapshot.get("evidence_moments", {}).items()
            }
            if current_moments != stored_moments:
                raise RuntimeError("Structural near-fit snapshot is stale for the current evidence")
            if tuple(snapshot.get("free_names", ())) != tuple(DEFAULT_FREE):
                raise RuntimeError("Structural near-fit snapshot parameter space is stale")
            if snapshot.get("model_semantics_version") != STRUCTURAL_MODEL_SEMANTICS_VERSION:
                raise RuntimeError("Structural near-fit snapshot model semantics are stale")
            base = StructuralParams()
            admissible = [replace(base, **params) for params in snapshot["admissible_params"]]
            best = replace(base, **snapshot["best_params"])
            res = AdmissibleNearFitResult(
                free_names=tuple(snapshot["free_names"]),
                best_objective=float(snapshot["best_objective"]),
                tolerance=float(snapshot["tolerance"]),
                tolerance_rule=str(snapshot["tolerance_rule"]),
                n_candidates=int(snapshot["n_candidates"]),
                n_admissible=int(snapshot["n_admissible"]),
                admissible_params=admissible,
                best_params=best,
                param_ranges=snapshot["param_ranges"],
                correlations=snapshot["correlations"],
                bounds={key: tuple(value) for key, value in snapshot["bounds"].items()},
            )
            rep = identification_report(
                ctx, StructuralParams(), DEFAULT_FREE, m_obs=built["moments"],
                auctions=g["uyap"], kap=g["kap"], toki_result=g["toki_result"],
                provenance=built["provenance"], unavailable=built["unavailable"],
                ineligible=built["ineligible"],
            )
            _CACHE.update({"res": res, "rep": rep, "status": dataset_status()})
    return _CACHE["res"], _CACHE["rep"], _CACHE["status"]


def _evidence_report():
    """Evidence and identification metadata without running the near-fit search."""
    with _CACHE_LOCK:
        if "rep" not in _CACHE:
            g = load_genuine_datasets()
            built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
            ctx = context_from_datasets(g["uyap"], g["kap"])
            rep = identification_report(
                ctx, StructuralParams(), DEFAULT_FREE, m_obs=built["moments"],
                auctions=g["uyap"], kap=g["kap"], toki_result=g["toki_result"],
                provenance=built["provenance"], unavailable=built["unavailable"],
                ineligible=built["ineligible"],
            )
            _CACHE.update({"rep": rep, "status": dataset_status()})
    return _CACHE["rep"], _CACHE["status"]


def fair_value_for(province: str | None, gross_m2: float | None) -> float | None:
    """TCMB çağdaş ekspertiz TL/m² çıpasından fair value (EK KFE çarpanı YOK)."""
    if not province:
        return None
    ppm2 = load_province_ppm2().get(province)
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
        asking_price, fv, tightness=tightness, include_assumption_sensitivity=True
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
        "between_assumption_sensitivity_band": list(pred["between_assumption_sensitivity_band"]),
        "assumption_sensitivity_parameters": pred["assumption_sensitivity_parameters"],
        "assumption_sensitivity_ranges": pred["assumption_sensitivity_ranges"],
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
    rep, status = _evidence_report()
    counts = _genuine_counts(rep, status)
    return {
        **counts,
        "smm_moments_used": list(rep["moment_keys"]),
        "n_smm_moments_used": int(rep["n_observed_moments"]),
        "jacobian_rank": int(rep["rank"]),
        "parameter_dimension": int(rep["n_structural_parameters"]),
        "identification_status": rep["status"],
        "property_scope": "mixed_property_structural_evidence; not property-type-specific",
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
    report, _ = _evidence_report()
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
            "jacobian_rank": int(report["rank"]),
            "parameter_dimension": int(report["n_structural_parameters"]),
            "identification_status": report["status"],
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
# YAPILANDIRILABİLİR sayısal eşikler (ekonometrik anlamlılık düzeyi DEĞİL). Sınıflandırma
# yalnızca yakın-uyum SAYISI büyümesine dayanmaz; çok-parçalı yakınsama kuralı kullanır.
STABILITY_BEST_OBJ_TOL = 0.10    # kümülatif best-objektif göreli iyileşmesi (hâlâ düşüyorsa → yetersiz)
STABILITY_SUPPORT_EXPAND = 0.10  # ortak-eşik parametre desteği DIŞA genişlemesi (bound-genişliğine göre)
STABILITY_ENDPOINT_TOL = 0.12    # parametre bant-payı endpoint hareketi (bound-genişliğine göre)
STABILITY_ENVELOPE_TOL = 0.15    # zarf/band endpoint göreli hareketi
STABILITY_CENTRAL_TOL = 0.10     # merkezi tahmin göreli hareketi


def _env_width_center(env):
    if env is None or env[0] is None or env[1] is None:
        return None, None
    return float(env[1] - env[0]), float((env[0] + env[1]) / 2.0)


def _rel_env_move(a, b) -> bool:
    wa, ca = _env_width_center(a)
    wb, cb = _env_width_center(b)
    if wa is None or wb is None:
        return (wa is None) != (wb is None)  # biri null diğeri değil → MATERYAL hareket
    return (abs(wa - wb) / max(wb, 1.0) > STABILITY_ENVELOPE_TOL) or (
        abs(ca - cb) / max(cb, 1.0) > STABILITY_ENVELOPE_TOL
    )


def _classify_stability(rows: list[dict], exp: dict) -> tuple[str, str]:
    """ÇOK-PARÇALI sayısal yakınsama kuralı (yalnızca sayı büyümesine dayanMAZ).

    Son iki kümülatif bütçeyi karşılaştırır:
    - INSUFFICIENT_COVERAGE: kümülatif best-obj hâlâ materyal iyileşiyor VEYA ortak-eşik
      parametre desteği DIŞA genişliyor (yeni bölge keşfi → yargı erken),
    - SEARCH_SENSITIVE: kapsama büyüdü ama tahmin zarfı/destek endpoint'leri materyal hareket ediyor,
    - STABLE: son bütçe genişlemesi zarf ve destek endpoint'lerinde yalnızca küçük değişim üretir.
    """
    from ..structural.partial import PARAM_BOUNDS

    if len(rows) < 2:
        return "INSUFFICIENT_COVERAGE", "kararlılık yargısı için en az iki kümülatif bütçe gerekir"
    prev, last = rows[-2], rows[-1]
    cb = exp["cumulative_best_objective"]
    best_improve = (cb[-2] - cb[-1]) / max(abs(cb[-2]), 1e-9)
    obj_still_improving = best_improve > STABILITY_BEST_OBJ_TOL
    support_expands = False
    endpoints_move = False
    for p, (lo_l, hi_l) in last["common_threshold_param_ranges"].items():
        lo_p, hi_p = prev["common_threshold_param_ranges"][p]
        if None in (lo_l, hi_l, lo_p, hi_p):
            continue
        bw = PARAM_BOUNDS[p][1] - PARAM_BOUNDS[p][0]
        if bw <= 0:
            continue
        if (lo_l < lo_p - STABILITY_SUPPORT_EXPAND * bw) or (hi_l > hi_p + STABILITY_SUPPORT_EXPAND * bw):
            support_expands = True
        if abs((hi_l - lo_l) - (hi_p - lo_p)) / bw > STABILITY_ENDPOINT_TOL:
            endpoints_move = True
    env_move = _rel_env_move(last["structural_sensitivity_range"], prev["structural_sensitivity_range"])
    band_move = _rel_env_move(last["between_theta_near_fit_band"], prev["between_theta_near_fit_band"])
    cl, cp = last["central_structural_estimate"], prev["central_structural_estimate"]
    central_move = ((cl is None) != (cp is None)) or (
        cl is not None and cp is not None and abs(cl - cp) / max(cp, 1.0) > STABILITY_CENTRAL_TOL
    )
    predictions_move = env_move or band_move or central_move or endpoints_move
    if obj_still_improving or support_expands:
        why = (f"cumulative best-objective still improving ({best_improve:.1%} > {STABILITY_BEST_OBJ_TOL:.0%})"
               if obj_still_improving else
               "common-threshold parameter support still expanding outward")
        return "INSUFFICIENT_COVERAGE", why + " — stability judgment premature"
    if predictions_move:
        return "SEARCH_SENSITIVE", ("coverage grew adequately but the prediction envelope / support "
                                   "endpoints still move materially between the final two budgets")
    return "STABLE", ("final-budget expansion produced only small documented changes in prediction "
                     "envelopes and parameter-support endpoints")


def search_budget_stability(
    budgets=(750, 1500, 3000, 6000),
    asking: float = 5_000_000.0,
    province: str = "İstanbul",
    gross_m2: float = 100.0,
    seed: int = NEAR_FIT_SEED,
) -> dict:
    """KÜMÜLATİF (iç-içe, INCUMBENT-koruyan) arama-bütçesi KARARLILIK çalışması.

    Artan KÜMÜLATİF bütçeler kullanır; her bütçenin aday havuzu bir öncekini KAPSAR ve global
    incumbent korunur → ``cumulative_best_objective`` MONOTON AZALMAYAN. Tüm bütçeler ORTAK bir
    ``Q_ref``/``tol_ref`` altında karşılaştırılır (hareketli eşik YOK); tahmin özetleri bu
    ORTAK-EŞİK diagnostik kümesinden hesaplanır. Üretim ``admissible_near_fit_set`` tanımı,
    tolerans kuralı, SMM momentleri ve bound DEĞİŞMEZ."""
    from ..structural import cumulative_near_fit_experiment

    g = load_genuine_datasets()
    built = build_observed_moments(g["uyap"], g["kap"], g["toki_result"])
    ctx = context_from_datasets(g["uyap"], g["kap"])
    fv = fair_value_for(province, gross_m2)
    exp = cumulative_near_fit_experiment(built["moments"], ctx, budgets=budgets, seed=seed)

    rows: list[dict] = []
    for r in exp["rows"]:
        params = r["admissible_params"]  # ORTAK-EŞİK diagnostik kümesi (Θ_A_stability(b))
        if params:
            pred = IdentificationAwarePredictor(params, best_params=params[0]).predict(
                asking, fv, n=12000, seed=0
            )
            central = pred["central_structural_estimate"]
            between = list(pred["between_theta_near_fit_band"])
            env = list(pred["structural_sensitivity_range"])
            n_tr = int(pred["trading_near_fit_parameter_count"])
            n_nt = int(pred["nontrading_near_fit_parameter_count"])
        else:
            central, between, env, n_tr, n_nt = None, [None, None], [None, None], 0, 0
        rows.append({
            "budget": r["budget"],
            "new_candidates_added": r["new_candidates_added"],
            "cumulative_unique_candidate_count": r["cumulative_unique_candidate_count"],
            "cumulative_best_objective": r["cumulative_best_objective"],
            "production_near_fit_count": r["production_near_fit_count"],
            "common_threshold_stability_near_fit_count": r["common_threshold_stability_near_fit_count"],
            "common_threshold_param_ranges": r["common_threshold_param_ranges"],
            "eta_range": r["eta_range"],
            "trading_theta_count": n_tr,
            "nontrading_theta_count": n_nt,
            "central_structural_estimate": central,
            "between_theta_near_fit_band": between,
            "structural_sensitivity_range": env,
        })

    status, reason = _classify_stability(rows, exp)
    _CACHE["stability_status"] = status
    cb = exp["cumulative_best_objective"]
    monotone = all(cb[i + 1] <= cb[i] + 1e-9 for i in range(len(cb) - 1))
    return {
        "model_semantics_version": STRUCTURAL_MODEL_SEMANTICS_VERSION,
        "diagnostic": "near_fit_search_stability",
        "design": "cumulative incumbent-preserving (nested candidate pools; global incumbent retained; "
                  "common Q_ref/tol_ref across budgets)",
        "note": "numerical approximation diagnostic of the reproducible CUMULATIVE search over the "
                "bounded theta space — NOT an econometric identification classification. Tolerance "
                "rule, SMM moments and bounds are unchanged.",
        "identification_separation": "The rank-deficient structural system may produce extended "
                "near-fit directions, which can make numerical coverage more demanding. The "
                "search-stability diagnostic measures computational approximation quality separately "
                "and does NOT establish underidentification. identification_status "
                f"(STRUCTURALLY_UNDERIDENTIFIED, Jacobian rank 4 / dim {len(DEFAULT_FREE)}) is a separate "
                "econometric/local-identification diagnostic.",
        "reference_scenario": {"asking_price": asking, "province": province, "gross_m2": gross_m2,
                               "fair_value": round(float(fv), 0)},
        "budgets": exp["budgets"],
        "Q_ref": round(exp["Q_ref"], 6),
        "tol_ref": round(exp["tol_ref"], 6),
        "cumulative_best_objective": exp["cumulative_best_objective"],
        "cumulative_best_objective_monotone_nonincreasing": bool(monotone),
        "deterministic_objective_reproducible": exp["deterministic_objective_reproducible"],
        "incumbent_reeval_delta": exp["incumbent_reeval_delta"],
        "best_theta": exp["best_theta"],
        "table": rows,
        "near_fit_search_stability": status,
        "rule": reason,
    }


def stability_snapshot() -> dict:
    """Return the audited precomputed stability report without running a search."""
    if not STABILITY_SNAPSHOT.exists():
        raise RuntimeError("Structural stability snapshot is not available")
    report = json.loads(STABILITY_SNAPSHOT.read_text(encoding="utf-8"))
    if report.get("model_semantics_version") != STRUCTURAL_MODEL_SEMANTICS_VERSION:
        raise RuntimeError("Structural stability snapshot model semantics are stale")
    _CACHE["stability_status"] = report.get("near_fit_search_stability", "not_computed")
    return report
