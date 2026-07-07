"""Mekanizma-farkındalıklı YAPISAL ekonometrik çıkarım motoru (yeni çekirdek).

Zayıf denetim (weak-label) yerine, sıradan pazarlıklı yeniden-satış fiyat oluşumu
YAPISAL olarak modellenir: genelleştirilmiş Nash pazarlığı (P = eta·B + (1−eta)·S,
ticaret iff B≥S), TCMB-çıpalı hedonik fair-value, yapısal UYAP açık artırması (kanunî
taban), KAP müzakere momentleri ve TOKİ agregat kohortları. Yapısal parametre vektörü
θ, Simüle Momentler Yöntemi (SMM) ile tahmin edilir. Tahmin, bir sıradan ilan için
KOŞULLU-TİCARET closing dağılımı verir — gözlenen fiyat DEĞİL.

Sağlayıcı registry ve doğrulanmış KAP/TOKİ/UYAP Level-2 kayıtları KORUNUR; tüketici
yolu opsiyonel gelecekteki DOĞRULAMA kanalı olarak DONDURULMUŞTUR. SaleProbability YOK.
"""

from __future__ import annotations

from .auction import (
    AUCTION_FIELDS,
    UYAP_OUTCOME_STATUSES,
    auction_moments,
    classify_auction_outcome,
    legal_floor,
    load_auctions,
    normalize_auction,
    simulate_auctions,
    uyap_observed_moments,
)
from .bargaining import (
    draw_buyer_values,
    draw_seller_values,
    negotiated_price,
    simulate_negotiations,
    trade_mask,
)
from .datasets import (
    GENUINE_DIR,
    dataset_status,
    load_genuine_datasets,
    load_kap_candidates,
    load_kap_records,
    load_toki_records,
    load_uyap_records,
)
from .hedonic import HedonicPremium, roll_unit_price, tcmb_fair_value
from .identify import (
    compare_snapshots,
    dataset_summary,
    identification_report,
    load_snapshot,
    moment_jacobian,
    profile_objective,
    save_snapshot,
    snapshot_metrics,
    source_jacobian_ranks,
)
from .kap import (
    KAP_FIELDS,
    kap_observed_moments,
    load_kap_disposals,
    normalize_kap_disposal,
)
from .moments import (
    MomentContext,
    align,
    build_observed_moments,
    context_from_datasets,
    observed_moments,
    simulated_moments,
)
from .params import DEFAULT_FREE, StructuralParams
from .partial import (
    FUTURE_METHODOLOGY_NOTE,
    PARAM_BOUNDS,
    AdmissibleNearFitResult,
    admissible_near_fit_set,
    identification_tolerance,
    objective_value,
    tolerance_sensitivity,
)
from .predict import (
    CONFLICT_EXPLANATION_CATEGORIES,
    IdentificationAwarePredictor,
    StructuralClosingPredictor,
    input_conflict_diagnostic,
)
from .smm import SMMResult, estimate_smm, nelder_mead, smm_objective
from .toki import difference_disclosures, toki_composition_moments

__all__ = [
    # parametreler
    "StructuralParams",
    "DEFAULT_FREE",
    # pazarlık
    "negotiated_price",
    "trade_mask",
    "draw_buyer_values",
    "draw_seller_values",
    "simulate_negotiations",
    # hedonik (TCMB-çıpalı)
    "HedonicPremium",
    "tcmb_fair_value",
    "roll_unit_price",
    # açık artırma (yasal taban)
    "legal_floor",
    "normalize_auction",
    "load_auctions",
    "simulate_auctions",
    "auction_moments",
    "uyap_observed_moments",
    "classify_auction_outcome",
    "UYAP_OUTCOME_STATUSES",
    "AUCTION_FIELDS",
    # KAP yapısal veri kümesi
    "normalize_kap_disposal",
    "load_kap_disposals",
    "kap_observed_moments",
    "KAP_FIELDS",
    # TOKİ agregat
    "difference_disclosures",
    "toki_composition_moments",
    # momentler + SMM
    "MomentContext",
    "observed_moments",
    "simulated_moments",
    "build_observed_moments",
    "context_from_datasets",
    "align",
    "estimate_smm",
    "SMMResult",
    "smm_objective",
    "nelder_mead",
    # gerçek veri kümesi (denetlenmiş)
    "load_genuine_datasets",
    "dataset_status",
    "load_uyap_records",
    "load_kap_records",
    "load_kap_candidates",
    "load_toki_records",
    "GENUINE_DIR",
    # kimliklendirme (identification)
    "moment_jacobian",
    "identification_report",
    "source_jacobian_ranks",
    "dataset_summary",
    "profile_objective",
    "snapshot_metrics",
    "save_snapshot",
    "load_snapshot",
    "compare_snapshots",
    # kabul edilebilir yakın-uyum kümesi (Θ_A) — identified set/confidence DEĞİL
    "admissible_near_fit_set",
    "AdmissibleNearFitResult",
    "PARAM_BOUNDS",
    "identification_tolerance",
    "tolerance_sensitivity",
    "objective_value",
    "FUTURE_METHODOLOGY_NOTE",
    # tahmin
    "StructuralClosingPredictor",
    "IdentificationAwarePredictor",
    "input_conflict_diagnostic",
    "CONFLICT_EXPLANATION_CATEGORIES",
]
