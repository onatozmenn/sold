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
DISCLAIMER_SENSITIVITY = (
    "KİMLİKLENDİRME-FARKINDA YAPISAL DUYARLILIK ZARFI (near-fit structural parameter "
    "uncertainty envelope). Yapısal duyarlılık aralığı İKİ belirsizlik kaynağını birlikte "
    "yansıtır: (1) örtük pazarlık (within-θ negotiation) ve (2) yakın-uyum parametre "
    "belirsizliği (between-θ near-fit). Bu bir gözlenen closing fiyatı, ölçülen sıradan-"
    "yeniden-satış doğruluğu, GÜVEN ARALIĞI ya da frekansçı KAPSAMA iddiası DEĞİLDİR "
    "(admissible_near_fit_set bir identified set / confidence region DEĞİLdir)."
)

# Girdi-çelişki tanılaması: asking ile TCMB-çıpalı fair value CİDDİ biçimde uyuşmuyorsa
# AÇIK uyarı verilir (tahmin REDDEDİLMEZ/KIRPILMAZ). Sınırlar belgeli ve yapılandırılabilir.
INPUT_CONFLICT_LOW = 0.5
INPUT_CONFLICT_HIGH = 2.0

# Olası ekonomik açıklamalar — YALNIZCA tanı ADAYI kategorileri (kanıtsız ATANMAZ).
CONFLICT_EXPLANATION_CATEGORIES = (
    "possible_input_error",
    "geographic_anchor_mismatch",
    "property_characteristic_mismatch",
    "distressed_or_nonstandard_sale",
    "fractional_or_encumbered_interest",
    "strategic_underpricing",
)


def input_conflict_diagnostic(
    asking_price: float,
    fair_value: float,
    low: float = INPUT_CONFLICT_LOW,
    high: float = INPUT_CONFLICT_HIGH,
) -> dict:
    """asking ile fair value arasındaki CİDDİ uyuşmazlık için AÇIK uyarı (REDDETMEZ/KIRPMAZ).

    ``ask_to_fair_value_ratio = asking_price / fair_value`` belgeli sınırların DIŞINDAysa
    ``input_conflict=True`` verilir ve yapısal çıkarımın bu senaryoda YÜKSEK DUYARLILIK
    gösterebileceği açıklanır (asking sinyali ekspertiz-çıpalı fair value ile GÜÇLÜ ÇELİŞİR).
    Olası açıklamalar YALNIZCA tanı ADAYI kategorileridir; kanıt olmadan hiçbiri ATANMAZ.
    """
    ratio = float(asking_price / fair_value) if fair_value else float("inf")
    conflict = not (low <= ratio <= high)
    msg = None
    if conflict:
        msg = (
            f"asking/fair_value = {ratio:.2f}, belgeli [{low:g}, {high:g}] aralığının DIŞINDA. "
            "Yapısal çıkarım YÜKSEK DUYARLI olabilir: gözlenen asking sinyali ekspertiz-çıpalı "
            "fair value ile GÜÇLÜ ÇELİŞİYOR. Tahmin reddedilmedi/kırpılmadı."
        )
    return {
        "ask_to_fair_value_ratio": ratio,
        "input_conflict": bool(conflict),
        "bounds": (float(low), float(high)),
        "message": msg,
        "candidate_explanations": list(CONFLICT_EXPLANATION_CATEGORIES) if conflict else [],
        "note": "Kategoriler yalnızca TANI ADAYIDIR; kanıt olmadan hiçbiri ATANMAZ (çıkarılan gerçek DEĞİL).",
    }


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
        conflict = input_conflict_diagnostic(asking_price, fair_value)
        if traded_prices.size == 0:
            return {
                "inferred_closing_median": None,
                "inferred_closing_mean": None,
                "interval_80": (None, None),
                "trade_probability": round(trade_prob, 4),
                "asking_price": float(asking_price),
                "fair_value": float(fair_value),
                "mechanism_transfer_sensitivity": {},
                "input_conflict": conflict,
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
            "input_conflict": conflict,
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


class IdentificationAwarePredictor:
    """Kabul edilebilir YAKIN-UYUM kümesi (Θ_A) üzerinde kimliklendirme-farkında DUYARLILIK.

    Tek bir keyfi provizyonel θ'dan nihai aralık ÜRETİLMEZ. Her ``θ ∈ Θ_A`` için yapısal
    pazarlık simülasyonu (``trade iff B≥S``, ``P=η·B+(1−η)·S``) çalıştırılır ve KOŞULLU-
    TİCARET closing dağılımı toplanır. İKİ belirsizlik AYRILIR:

    - within-θ pazarlık belirsizliği: sabit θ'da örtük B,S çekimlerinin fiyat yayılımı,
    - between-θ yakın-uyum parametre belirsizliği: θ ∈ Θ_A arasında merkezi eğilimin yayılımı.

    Birleşik aralık bir ``structural sensitivity range``dır — GÜVEN ARALIĞI / kapsama iddiası
    DEĞİL. ORTAK rastgele sayılar: her θ AYNI tohumla değerlendirilir → medyan farkları θ'dan.
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
        # within-θ (negotiation) aralığı: EN İYİ uyum θ'sının fiyat dağılımı
        best_prices, best_tp = self._draw(
            self.best, asking_price, fair_value, tightness, n, np.random.default_rng(seed)
        )
        conflict = input_conflict_diagnostic(asking_price, fair_value)
        base = {
            # STATÜ: biçimsel identified set/confidence DEĞİL — yapısal ALTKİMLİKLENDİRME.
            "identification_status": "STRUCTURALLY_UNDERIDENTIFIED",
            "coverage_claim": None,  # frekansçı KAPSAMA iddiası YOK
            "parameter_set_size": len(thetas),
            "asking_price": float(asking_price),
            "fair_value": float(fair_value),
            "input_conflict": conflict,
        }
        if best_prices.size == 0 or not per_theta_median:
            base.update({
                "central_structural_estimate": None,
                "within_theta_negotiation_interval": (None, None),
                "between_theta_near_fit_band": (None, None),
                "sensitivity_envelope_lower": None,
                "sensitivity_envelope_upper": None,
                "structural_sensitivity_range": (None, None),
                "trade_probability_band": (None, None),
                "note": DISCLAIMER_SENSITIVITY + " (bu senaryoda ticaret olasılığı ~0).",
            })
            return base
        w_lo, w_hi = np.percentile(best_prices, [10, 90])
        env_lo, env_hi = round(min(per_theta_p10), 0), round(max(per_theta_p90), 0)
        base.update({
            # merkezi yapısal tahmin (en iyi uyum θ medyanı)
            "central_structural_estimate": round(float(np.median(best_prices)), 0),
            "central_across_theta": round(float(np.median(per_theta_median)), 0),
            # within-θ (YALNIZCA pazarlık belirsizliği; en iyi θ)
            "within_theta_negotiation_interval": (round(float(w_lo), 0), round(float(w_hi), 0)),
            # between-θ (YALNIZCA yakın-uyum parametre belirsizliği; θ-medyanları bandı)
            "between_theta_near_fit_band": (round(min(per_theta_median), 0), round(max(per_theta_median), 0)),
            # yapısal duyarlılık zarfı (İKİ belirsizlik birlikte) = structural sensitivity range
            "sensitivity_envelope_lower": env_lo,
            "sensitivity_envelope_upper": env_hi,
            "structural_sensitivity_range": (env_lo, env_hi),
            "trade_probability_band": (round(min(per_theta_trade), 4), round(max(per_theta_trade), 4)),
            "note": DISCLAIMER_SENSITIVITY,
        })
        return base
