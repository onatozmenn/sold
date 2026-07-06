"""Gerçek-veri değerleme motoru — MOCK YOK.

Tahmini satış fiyatı = ilan (asking) fiyatı × (1 − pazarlık payı). Burada hiçbir
sayı uydurulmaz:
- **pazarlık payı**: YAYINLANMIŞ sektör verisi (il bazında; İstanbul ~%10,
  Ankara ~%5, İzmir ~%8; yavaş/stoğu bol piyasada %15-20).
- **talep ayarı**: TÜİK konut satış hacminden türeyen ``market_heat`` (gerçek) —
  talep düşükse pay artar, yüksekse azalır.
- **piyasa değeri çıprazı**: TCMB ekspertiz TL/m² × m² (gerçek, bağımsız kontrol).

Gerçek satış etiketi (ground_truth) eklendiğinde ML modeli (RealizedValuator) bu
oranları veriden öğrenip bu formülü devralır. Yani formül yalnızca "soğuk start".

Akademik dayanak: isteme fiyatını işlem verisi yerine kullanmak geçerli bir
yöntemdir (Springer 2021, "listings as substitute for transaction data";
MDPI 2024, Varşova "predictions with no transaction information").
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..features.demand import HeatIndex, load_heat_index
from .synthetic import load_province_ppm2

# FALLBACK PRIOR — yalnızca eşleşmiş (asking ↔ closing) GERÇEK etiket YOKKEN kullanılır.
# Bu bir ground-truth DEĞİL; yayınlanmış sektör pazarlık payıdır (il bazlı).
# Kaynak: emlakhaberi (İstanbul %10, Ankara %5, İzmir %8); emlak365 2026 (%15-20 yavaş piyasa).
# Gerçek etiketler geldikçe RealizedValuator (ML) bu prior'ı devralır ve geçersiz kılar.
FALLBACK_DISCOUNT_PRIOR: dict[str, float] = {
    "İstanbul": 0.10,
    "Ankara": 0.05,
    "İzmir": 0.08,
}
DEFAULT_DISCOUNT = 0.08  # ulusal tipik fallback (yayınlı ~%5-10)
MIN_DISCOUNT, MAX_DISCOUNT = 0.02, 0.20


def effective_discount(province: object, market_heat: float = 1.0) -> float:
    """Yayınlı pazarlık payını gerçek talep (market_heat) ile ayarlar.

    market_heat: ~1 normal, <1 durgun (daha çok pazarlık), >1 hareketli (daha az).
    """
    base = FALLBACK_DISCOUNT_PRIOR.get(str(province), DEFAULT_DISCOUNT)
    heat = float(market_heat) if market_heat == market_heat else 1.0  # NaN → 1.0
    disc = base * (2.0 - float(np.clip(heat, 0.5, 1.5)))
    return float(np.clip(disc, MIN_DISCOUNT, MAX_DISCOUNT))


class RealValuator:
    """FALLBACK tahminci (market-adjusted asking price) — eşleşmiş closing etiketi yokken.

    Tahmin = ilan × (1 − FALLBACK pazarlık prior'ı). Girdiler gerçek: TL/m² (TCMB),
    pazarlık payı (yayınlı prior), talep (TÜİK). Bu bir realized-price ground-truth
    DEĞİL; gerçek (asking↔closing) etiket geldiğinde RealizedValuator (ML) devralır.
    """

    def __init__(
        self,
        ppm2_map: dict[str, float] | None = None,
        heat_index: HeatIndex | None = None,
    ) -> None:
        self.ppm2 = ppm2_map if ppm2_map is not None else load_province_ppm2()
        self.heat = heat_index if heat_index is not None else load_heat_index()

    def market_value(self, province: object, gross_m2: object) -> float | None:
        """TCMB ekspertiz TL/m² × m² (il ORTALAMASINA göre kaba değer; ilçe/yaş hariç).

        Uyarı: il ortalaması olduğundan tek bir ilanın gerçek değerinden sapabilir;
        yalnızca çıpraz kontrol içindir. Asıl tahmin ilan fiyatından türetilir.
        """
        try:
            m2 = float(gross_m2)
        except (TypeError, ValueError):
            return None
        if m2 <= 0:
            return None
        ppm2 = self.ppm2.get(str(province))
        return float(ppm2) * m2 if ppm2 else None

    def province_ppm2(self, province: object) -> float | None:
        """İlin GERÇEK TCMB ortalama ekspertiz TL/m² değeri (yoksa None)."""
        v = self.ppm2.get(str(province))
        return float(v) if v is not None else None

    def _row_heat(self, province: object, given: object) -> float:
        if given is not None and given == given:  # NaN değilse
            return float(given)
        return 1.0  # ay/talep verilmemişse nötr → yayınlı taban pay

    def estimate(self, df: pd.DataFrame) -> np.ndarray:
        """Her satır için tahmini satış fiyatı = ilan × (1 − pazarlık payı)."""
        n = len(df)
        out = np.empty(n, dtype=float)
        heat_col = df["market_heat"] if "market_heat" in df.columns else None
        prices = df["last_price"] if "last_price" in df.columns else df.get("initial_price")
        provinces = df["province"] if "province" in df.columns else pd.Series([None] * n)
        for i in range(n):
            asking = float(prices.iloc[i]) if prices is not None else 0.0
            province = provinces.iloc[i] if hasattr(provinces, "iloc") else None
            heat = self._row_heat(province, heat_col.iloc[i] if heat_col is not None else None)
            out[i] = asking * (1.0 - effective_discount(province, heat))
        return out
