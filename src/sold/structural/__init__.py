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
    auction_moments,
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
from .hedonic import HedonicPremium, roll_unit_price, tcmb_fair_value
from .identify import (
    dataset_summary,
    identification_report,
    moment_jacobian,
    profile_objective,
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
    context_from_datasets,
    observed_moments,
    simulated_moments,
)
from .params import DEFAULT_FREE, StructuralParams
from .predict import StructuralClosingPredictor
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
    "context_from_datasets",
    "align",
    "estimate_smm",
    "SMMResult",
    "smm_objective",
    "nelder_mead",
    # kimliklendirme (identification)
    "moment_jacobian",
    "identification_report",
    "dataset_summary",
    "profile_objective",
    # tahmin
    "StructuralClosingPredictor",
]
