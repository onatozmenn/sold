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

DISCLAIMER = (
    "YAPISAL ÇIKARIM — gözlenen closing fiyatı veya ölçülen sıradan-yeniden-satış "
    "doğruluğu DEĞİL. Sonuç, mekanizma-transfer varsayımlarına duyarlıdır."
)


class StructuralClosingPredictor:
    """Tahmin edilmiş (veya önsel) θ ile koşullu-ticaret closing dağılımı üretir."""

    def __init__(self, params: StructuralParams | None = None) -> None:
        self.params = params or StructuralParams()

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
        if traded_prices.size == 0:
            return {
                "inferred_closing_median": None,
                "inferred_closing_mean": None,
                "interval_80": (None, None),
                "trade_probability": round(trade_prob, 4),
                "asking_price": float(asking_price),
                "fair_value": float(fair_value),
                "mechanism_transfer_sensitivity": {},
                "note": DISCLAIMER + " (bu senaryoda ticaret olasılığı ~0).",
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
            "note": DISCLAIMER,
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
