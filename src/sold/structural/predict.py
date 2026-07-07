"""Sıradan ilan için YAPISAL closing çıkarımı (ölçülen değer DEĞİL).

Asking fiyatı, satıcı rezervasyon değerine dair GÜRÜLTÜLÜ stratejik bir gözlemdir —
ground-truth veya tavan DEĞİL. Satıcı-değeri dağılımı asking, fair value ve piyasa
sıkılığına koşullanır. Alıcı ve satıcı değerleri yapısal dağılımlardan çekilir, B ≥ S
olanlar tutulur, P = eta·B + (1−eta)·S hesaplanır ve KOŞULLU-TİCARET closing fiyat
dağılımı döndürülür.

Çıktı asla "gözlenen closing fiyatı" ya da "ölçülen sıradan-yeniden-satış doğruluğu"
olarak tanımlanmaz — bu bir YAPISAL ÇIKARIMDIR.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .bargaining import draw_buyer_values, draw_seller_values, negotiated_price
from .params import StructuralParams

DISCLAIMER_PROTOTYPE = (
    "YAPISAL-YÖNTEM PROTOTİPİ — PROVİZYONEL yapısal parametrelerle simülasyon tahmini. "
    "Kimliklendirme (identification) raporu bu parametre vektörünü DESTEKLEMEDEN, bu bir "
    "ölçülen Türk sıradan-yeniden-satış closing-fiyat modeli DEĞİLDİR. Sonuç mekanizma-"
    "transfer varsayımlarına duyarlıdır (sensitivity mode)."
)
DISCLAIMER_IDENTIFIED = (
    "ÇIKARIMSAL yapısal closing-fiyat DAĞILIMI — gözlenen closing fiyatı veya ölçülen "
    "sıradan-yeniden-satış doğruluğu DEĞİL. Sonuç mekanizma-transfer varsayımlarına duyarlıdır."
)
DISCLAIMER_PARTIAL = (
    "KISMİ-KİMLİKLENDİRİLMİŞ (PARTIALLY_IDENTIFIED) yapısal closing DAĞILIMI. Aralık İKİ "
    "belirsizlik kaynağını birlikte yansıtır: (1) örtük pazarlık (within-θ negotiation) ve "
    "(2) EKSİK kimliklendirmeden gelen yapısal parametre belirsizliği (between-θ). Bu bir "
    "gözlenen closing fiyatı ya da ölçülen sıradan-yeniden-satış doğruluğu DEĞİLDİR."
)


class StructuralClosingPredictor:
    """Tahmin edilmiş (veya provİzyonel/önsel) θ ile koşullu-ticaret closing dağılımı üretir.

    ``identified=False`` (varsayılan): θ henüz kimliklendirilmiş SMM tahmini DEĞİL → çıktı
    "structural-method prototype" olarak etiketlenir ve arayüz SENSITIVITY moduna geçer.
    """

    def __init__(
        self, params: StructuralParams | None = None, identified: bool = False
    ) -> None:
        self.params = params or StructuralParams()
        self.identified = bool(identified)

    def _draw(self, params, asking, fair_value, tightness, n, rng):
        B = draw_buyer_values(rng, fair_value, params, n, tightness)
        S = draw_seller_values(rng, fair_value, params, n, tightness, asking=asking)
        traded = B >= S
        P = negotiated_price(B, S, params.eta)
        return P[traded], float(traded.mean())

    def predict(
        self,
        asking_price: float,
        fair_value: float,
        tightness: float = 0.0,
        n: int = 40000,
        seed: int = 0,
    ) -> dict:
        """Koşullu-ticaret closing dağılımı + ticaret olasılığı + duyarlılık."""
        rng = np.random.default_rng(seed)
        traded_prices, trade_prob = self._draw(
            self.params, asking_price, fair_value, tightness, n, rng
        )
        mode = "identified" if self.identified else "sensitivity_mode"
        note = DISCLAIMER_IDENTIFIED if self.identified else DISCLAIMER_PROTOTYPE
        if traded_prices.size == 0:
            return {
                "inferred_closing_median": None,
                "inferred_closing_mean": None,
                "interval_80": (None, None),
                "trade_probability": round(trade_prob, 4),
                "asking_price": float(asking_price),
                "fair_value": float(fair_value),
                "mechanism_transfer_sensitivity": {},
                "identified": self.identified,
                "mode": mode,
                "note": note + " (bu senaryoda ticaret olasılığı ~0).",
            }
        median = float(np.median(traded_prices))
        mean = float(traded_prices.mean())
        lo, hi = np.percentile(traded_prices, [10, 90])
        return {
            "inferred_closing_median": round(median, 0),
            "inferred_closing_mean": round(mean, 0),
            "interval_80": (round(float(lo), 0), round(float(hi), 0)),
            "trade_probability": round(trade_prob, 4),
            "asking_price": float(asking_price),
            "fair_value": float(fair_value),
            "mechanism_transfer_sensitivity": self._sensitivity(
                asking_price, fair_value, tightness, n, seed, median
            ),
            "identified": self.identified,
            "mode": mode,
            "note": note,
        }

    def _sensitivity(self, asking, fair_value, tightness, n, seed, base_median) -> dict:
        """Mekanizma-transfer duyarlılığı: sonuç eta ve mekanizma kaymalarına ne kadar bağlı?

        Sıradan yeniden-satış primitifleri, KAP kurumsal / icra açık artırma
        mekanizmalarından TRANSFER edildikçe medyan nasıl kayar? Bu bant, tahminin
        mekanizma-transfer varsayımlarına duyarlılığını gösterir (kesinlik değil).
        """
        rng = np.random.default_rng(seed + 1)
        scenarios: dict[str, float] = {}
        variants = {
            "eta_minus_0.1": replace(self.params, eta=max(0.0, self.params.eta - 0.1)),
            "eta_plus_0.1": replace(self.params, eta=min(1.0, self.params.eta + 0.1)),
            "transfer_auction_shift": replace(
                self.params, mu_b=self.params.mu_b + self.params.auction_shift
            ),
            "transfer_kap_shift": replace(
                self.params, mu_b=self.params.mu_b + self.params.kap_shift,
                mu_s=self.params.mu_s + self.params.kap_shift,
            ),
        }
        for name, p in variants.items():
            prices, _ = self._draw(p, asking, fair_value, tightness, n, rng)
            scenarios[name] = round(float(np.median(prices)), 0) if prices.size else None
        med = [v for v in scenarios.values() if v is not None]
        band = (min(med), max(med)) if med else (None, None)
        return {
            "base_median": round(float(base_median), 0),
            "scenarios": scenarios,
            "median_band": band,
        }


class PartiallyIdentifiedPredictor:
    """Kabul edilebilir yapısal parametre KÜMESİ (Θ_I) üzerinde kimliklendirme-farkında tahmin.

    Tek bir keyfi provizyonel θ'dan nihai aralık ÜRETİLMEZ. Her ``θ ∈ Θ_I`` için yapısal
    pazarlık simülasyonu (``trade iff B≥S``, ``P=η·B+(1−η)·S``) çalıştırılır ve KOŞULLU-
    TİCARET closing dağılımı toplanır. İKİ belirsizlik AYRILIR:

    - within-θ pazarlık belirsizliği: sabit θ'da örtük B,S çekimlerinin fiyat yayılımı,
    - between-θ kimliklendirme belirsizliği: θ ∈ Θ_I arasında merkezi eğilimin yayılımı.

    ORTAK rastgele sayılar: her θ AYNI tohumla değerlendirilir → medyan farkları θ'dan gelir.
    """

    def __init__(self, admissible_params, best_params=None) -> None:
        self.admissible = list(admissible_params)
        self.best = best_params or (self.admissible[0] if self.admissible else StructuralParams())

    def _draw(self, params, asking, fair_value, tightness, n, rng):
        B = draw_buyer_values(rng, fair_value, params, n, tightness)
        S = draw_seller_values(rng, fair_value, params, n, tightness, asking=asking)
        traded = B >= S
        P = negotiated_price(B, S, params.eta)
        return P[traded], float(traded.mean())

    def predict(
        self,
        asking_price: float,
        fair_value: float,
        tightness: float = 0.0,
        n: int = 20000,
        seed: int = 0,
        max_thetas: int = 300,
    ) -> dict:
        thetas = self.admissible[:max_thetas] if self.admissible else [self.best]
        per_theta_median: list[float] = []
        per_theta_p10: list[float] = []
        per_theta_p90: list[float] = []
        per_theta_trade: list[float] = []
        for th in thetas:
            # ORTAK rastgele sayılar: her θ AYNI tohum → fark θ'dan
            prices, tp = self._draw(th, asking_price, fair_value, tightness, n, np.random.default_rng(seed))
            per_theta_trade.append(tp)
            if prices.size:
                per_theta_median.append(float(np.median(prices)))
                lo, hi = np.percentile(prices, [10, 90])
                per_theta_p10.append(float(lo))
                per_theta_p90.append(float(hi))
        # within-model (negotiation) aralığı: EN İYİ uyum θ'sının fiyat dağılımı
        best_prices, best_tp = self._draw(
            self.best, asking_price, fair_value, tightness, n, np.random.default_rng(seed)
        )
        if best_prices.size == 0 or not per_theta_median:
            return {
                "identification_status": "PARTIALLY_IDENTIFIED",
                "parameter_set_size": len(thetas),
                "central_structural_estimate": None,
                "within_model_interval": (None, None),
                "identification_aware_lower": None,
                "identification_aware_upper": None,
                "between_theta_median_band": (None, None),
                "trade_probability_band": (None, None),
                "note": DISCLAIMER_PARTIAL + " (bu senaryoda ticaret olasılığı ~0).",
            }
        w_lo, w_hi = np.percentile(best_prices, [10, 90])
        central = float(np.median(per_theta_median))  # Θ_I medyanlarının merkezi
        return {
            "identification_status": "PARTIALLY_IDENTIFIED",
            "parameter_set_size": len(thetas),
            "asking_price": float(asking_price),
            "fair_value": float(fair_value),
            # merkezi yapısal tahmin (en iyi uyum θ medyanı + Θ_I medyanlarının merkezi)
            "central_structural_estimate": round(float(np.median(best_prices)), 0),
            "central_across_theta": round(central, 0),
            # within-model (yalnızca pazarlık belirsizliği; en iyi θ)
            "within_model_interval": (round(float(w_lo), 0), round(float(w_hi), 0)),
            # between-θ (yalnızca kimliklendirme belirsizliği; θ-medyanları bandı)
            "between_theta_median_band": (round(min(per_theta_median), 0), round(max(per_theta_median), 0)),
            # kimliklendirme-farkında zarf (İKİ belirsizlik birlikte)
            "identification_aware_lower": round(min(per_theta_p10), 0),
            "identification_aware_upper": round(max(per_theta_p90), 0),
            "trade_probability_band": (round(min(per_theta_trade), 4), round(max(per_theta_trade), 4)),
            "note": DISCLAIMER_PARTIAL,
        }
