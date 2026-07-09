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

# Fiyat dağılımı KOŞULLU-TİCARET semantiği (P yalnızca B≥S simülasyonlarında tanımlıdır).
CONDITIONAL_ON_TRADE_STATEMENT = (
    "The transaction-price distribution is computed conditional on the structural "
    "simulation producing trade, B >= S. It is not an unconditional expected sale outcome."
)
PRICE_ESTIMATE_CONDITION = "conditional_on_trade"

# central_structural_estimate / within_theta tutarlılığı: TEK bir temsili θ'dan raporlanır.
CENTRAL_ESTIMATE_DEFINITION = (
    "conditional-on-trade median closing price of the single representative trading near-fit "
    "parameter configuration (see representative_theta_rule); reported together with that "
    "same configuration's conditional quantile interval so the central estimate lies inside "
    "its own within_theta_negotiation_interval by construction"
)
REPRESENTATIVE_THETA_RULE = (
    "among near-fit theta configurations that produce simulated trade, choose the one whose "
    "conditional-on-trade median is closest to the cross-theta median of those medians; "
    "deterministic tie-break by smallest conditional median. Both central_structural_estimate "
    "and within_theta_negotiation_interval are reported from this single configuration."
)

# trade-share alanı: model-implied simüle B≥S payı — GÖZLENEN UYAP no-trade'e KALİBRE DEĞİL.
TRADE_SHARE_CALIBRATION = "not_empirically_calibrated_to_observed_uyap_no_trade_outcomes"

FIXED_ASSUMPTION_BOUNDS = {
    "tightness_beta": (-0.3, 0.3),
    "asking_signal": (0.0, 1.0),
}

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
        if not self.admissible:
            raise ValueError("At least one admissible structural parameter configuration is required")
        self.best = best_params or self.admissible[0]

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
        include_assumption_sensitivity: bool = False,
    ) -> dict:
        thetas = self.admissible[:max_thetas] if self.admissible else [self.best]
        trading: list[tuple[float, float, float, float]] = []  # (median, p10, p90, share) — YALNIZCA ticaret eden θ
        assumption_trading: list[tuple[float, float, float, float]] = []
        all_shares: list[float] = []                            # her θ'nın simüle B≥S payı (ticaret etmeyenler dahil)
        for th in thetas:
            # ORTAK rastgele sayılar: her θ AYNI tohum → fark θ'dan
            prices, share = self._draw(th, asking_price, fair_value, tightness, n, np.random.default_rng(seed))
            all_shares.append(float(share))
            if prices.size:
                lo, hi = np.percentile(prices, [10, 90])
                trading.append((float(np.median(prices)), float(lo), float(hi), float(share)))
            if include_assumption_sensitivity:
                for name, (lower, upper) in FIXED_ASSUMPTION_BOUNDS.items():
                    for value in (lower, upper):
                        variant = replace(th, **{name: value})
                        variant_prices, variant_share = self._draw(
                            variant,
                            asking_price,
                            fair_value,
                            tightness,
                            n,
                            np.random.default_rng(seed),
                        )
                        if variant_prices.size:
                            variant_lo, variant_hi = np.percentile(variant_prices, [10, 90])
                            assumption_trading.append((
                                float(np.median(variant_prices)),
                                float(variant_lo),
                                float(variant_hi),
                                float(variant_share),
                            ))
        conflict = input_conflict_diagnostic(asking_price, fair_value)
        n_total = len(thetas)
        n_trading = len(trading)
        share_band = (
            (round(min(all_shares), 4), round(max(all_shares), 4)) if all_shares else (None, None)
        )
        base = {
            # STATÜ: biçimsel identified set/confidence DEĞİL — yapısal ALTKİMLİKLENDİRME.
            "identification_status": "STRUCTURALLY_UNDERIDENTIFIED",
            "coverage_claim": None,  # frekansçı KAPSAMA iddiası YOK
            "parameter_set_size": n_total,
            "asking_price": float(asking_price),
            "fair_value": float(fair_value),
            "input_conflict": conflict,
            # KOŞULLU-TİCARET semantiği (fiyat yalnızca B≥S simülasyonlarında tanımlı)
            "price_estimate_condition": PRICE_ESTIMATE_CONDITION,
            "conditional_on_trade_statement": CONDITIONAL_ON_TRADE_STATEMENT,
            "central_estimate_definition": CENTRAL_ESTIMATE_DEFINITION,
            "representative_theta_rule": REPRESENTATIVE_THETA_RULE,
            # near-fit θ sayıları (ticaret eden + etmeyen = toplam; zarf yalnızca ticaret edenlerden)
            "near_fit_parameter_count": n_total,
            "trading_near_fit_parameter_count": n_trading,
            "nontrading_near_fit_parameter_count": n_total - n_trading,
            "price_envelope_theta_count": n_trading,
            "assumption_sensitivity_parameters": list(FIXED_ASSUMPTION_BOUNDS),
            "assumption_sensitivity_ranges": {
                name: list(bounds) for name, bounds in FIXED_ASSUMPTION_BOUNDS.items()
            },
            "assumption_sensitivity_scenario_count": len(assumption_trading),
            # model-implied simüle B≥S payı — GÖZLENEN UYAP no-trade'e KALİBRE DEĞİL (olasılık İDDİA edilmez)
            "simulated_trade_share_band": share_band,
            "trade_probability_band": share_band,  # geriye uyumluluk (AYNI değer; ampirik olasılık DEĞİL)
            "trade_share_calibration": TRADE_SHARE_CALIBRATION,
        }
        # HİÇBİR near-fit θ simüle ticaret üretmiyorsa DÜRÜST null (no-trade fiyatı ATANMAZ).
        # Simüle pay ~0 → pop. ticaret olasılığı MATEMATİKSEL 0 DEĞİL (Monte Carlo semantiği korunur).
        if not trading:
            base.update({
                "central_structural_estimate": None,
                "within_theta_negotiation_interval": (None, None),
                "between_theta_near_fit_band": (None, None),
                "sensitivity_envelope_lower": None,
                "sensitivity_envelope_upper": None,
                "structural_sensitivity_range": (None, None),
                "note": DISCLAIMER_SENSITIVITY + " " + CONDITIONAL_ON_TRADE_STATEMENT
                        + " (bu senaryoda Θ_A boyunca simüle ticaret payı ~0).",
            })
            return base
        medians = [r[0] for r in trading]
        assumption_medians = [r[0] for r in assumption_trading]
        cross_theta_median = float(np.median(medians))
        # TEMSİLİ ticaret eden θ: koşullu medyanı cross-theta medyanına EN YAKIN; tie-break en küçük medyan.
        rep = min(trading, key=lambda r: (abs(r[0] - cross_theta_median), r[0]))
        central, w_lo, w_hi, _ = rep  # central ∈ [w_lo, w_hi] İNŞA GEREĞİ (aynı θ'nın medyanı+aralığı)
        envelope_rows = trading + assumption_trading
        env_lo = round(min(r[1] for r in envelope_rows), 0)
        env_hi = round(max(r[2] for r in envelope_rows), 0)
        base.update({
            # merkezi yapısal tahmin = TEMSİLİ ticaret eden θ'nın KOŞULLU medyanı (kendi aralığında)
            "central_structural_estimate": round(central, 0),
            "central_across_theta": round(cross_theta_median, 0),
            # within-θ (YALNIZCA pazarlık belirsizliği; TEMSİLİ tek θ'nın koşullu quantile aralığı)
            "within_theta_negotiation_interval": (round(w_lo, 0), round(w_hi, 0)),
            # between-θ (YALNIZCA yakın-uyum parametre belirsizliği; ticaret eden θ-medyanları bandı)
            "between_theta_near_fit_band": (round(min(medians), 0), round(max(medians), 0)),
            "between_assumption_sensitivity_band": (
                (round(min(assumption_medians), 0), round(max(assumption_medians), 0))
                if assumption_medians else (None, None)
            ),
            # yapısal duyarlılık zarfı (İKİ belirsizlik birlikte) = structural sensitivity range
            "sensitivity_envelope_lower": env_lo,
            "sensitivity_envelope_upper": env_hi,
            "structural_sensitivity_range": (env_lo, env_hi),
            "note": DISCLAIMER_SENSITIVITY + " " + CONDITIONAL_ON_TRADE_STATEMENT,
        })
        return base
