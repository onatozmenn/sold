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
    DEFAULT_FREE,
    FUTURE_METHODOLOGY_NOTE,
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
        "central_structural_estimate": pred["central_structural_estimate"],
        "within_theta_negotiation_interval": list(pred["within_theta_negotiation_interval"]),
        "between_theta_near_fit_band": list(pred["between_theta_near_fit_band"]),
        "structural_sensitivity_range": list(pred["structural_sensitivity_range"]),
        "trade_probability_band": list(pred["trade_probability_band"]),
        "ask_to_fair_value_ratio": round(ic["ask_to_fair_value_ratio"], 4),
        "input_conflict": ic["input_conflict"],
        "input_conflict_warning": ic["message"],
        "input_conflict_candidate_explanations": ic["candidate_explanations"],
        "smm_moments_used": list(rep["moment_keys"]),
        "jacobian_rank": int(rep["rank"]),
        "parameter_dimension": int(rep["n_structural_parameters"]),
        "near_fit_parameter_count": int(res.n_admissible),
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
        "disclaimer": PRODUCT_DISCLAIMER,
    }
