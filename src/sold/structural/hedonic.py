"""TCMB-çıpalı hedonik fair-value katmanı.

İki katman AYRI tutulur:
1. GÖRECELİ prim: ilan kesitinden log-lineer hedonik regresyonla nitelik primleri
   (m², yaş, kat, oda…) tahmin edilir. Katsayılar KULLANILIR ama **ilan-fiyatı
   intercept'i piyasa SEVİYESİ olarak KULLANILMAZ** (ilan fiyatları asking'tir,
   seviye olarak yanlıdır).
2. SEVİYE çıpası: piyasa seviyesi TCMB il medyan EKSPERTİZ birim fiyatına (TL/m²) BAĞLANIR.
   **TCMB verisi EKSPERTİZ değeridir, gerçekleşen işlem DEĞİL** — bu ayrım korunur.

ZAMANSAL ÇİFT-SAYIM YASAĞI (audit): TCMB il TL/m² bir SEVİYE çıpasıdır; KFE/YÖKFE bir
DEĞİŞİM endeksidir. ÇAĞDAŞ (hedef döneme ait) bir TL/m² seviyesi TAM KFE seviyesiyle
TEKRAR ÇARPILMAZ. Eğer ``unit_prices.csv`` hedef dönemin il medyan TL/m²'sini içeriyorsa
o değer DOĞRUDAN seviye çıpası olarak kullanılır:

    V_it = U_gt · gross_m2 · exp(β'·(Z_i − Z̄_gt))

KFE ORANI YALNIZCA eski bir birim-fiyat çıpasını t0'dan t'ye TAŞIRKEN kullanılır:

    U_gt = U_g,t0 · (KFE_gt / KFE_g,t0)

Ardından hedonik göreceli-nitelik ayarlaması (çarpan) uygulanır.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Hedonik göreceli primde kullanılan sayısal nitelikler (intercept'e GÜVENİLMEZ)
HEDONIC_NUM = ["gross_m2", "building_age", "floor", "room_count_num"]


class HedonicPremium:
    """İlan kesitinden GÖRECELİ nitelik primleri (log-lineer). Seviye çıpası DEĞİL.

    ``fit`` log(ilan fiyatı) ~ nitelikler regresyonundan eğimleri saklar; INTERCEPT
    bilinçli olarak ATILIR (piyasa seviyesi TCMB'den gelir). ``multiplier`` bir
    taşınmazın ortalama taşınmaza göre göreli değer çarpanını (exp(Σβ·Δx)) verir.
    """

    def __init__(self, columns: list[str] | None = None) -> None:
        self.columns = columns or HEDONIC_NUM
        self.coef_: dict[str, float] = {}
        self.reference_: dict[str, float] = {}
        self._fitted = False

    def fit(self, listings: pd.DataFrame, price_col: str = "last_price") -> "HedonicPremium":
        df = listings.copy()
        cols = [c for c in self.columns if c in df.columns]
        X_parts, used = [], []
        for c in cols:
            x = pd.to_numeric(df[c], errors="coerce")
            if x.notna().sum() >= 5 and x.std(skipna=True) > 0:
                used.append(c)
                X_parts.append(x)
        price = pd.to_numeric(df[price_col], errors="coerce")
        ok = price > 0
        if not used or ok.sum() < len(used) + 2:
            self._fitted = False
            return self
        Xdf = pd.concat(X_parts, axis=1)
        Xdf.columns = used
        mask = ok & Xdf.notna().all(axis=1)
        Xd = Xdf[mask]
        y = np.log(price[mask].to_numpy(dtype=float))
        self.reference_ = {c: float(Xd[c].mean()) for c in used}
        # Merkezle: intercept'i piyasa seviyesi olarak KULLANMAMAK için nitelikleri
        # ortalamadan çıkarırız; regresyon eğimleri (göreceli primler) kalır.
        Xc = np.column_stack(
            [Xd[c].to_numpy(dtype=float) - self.reference_[c] for c in used]
        )
        design = np.column_stack([np.ones(len(Xc)), Xc])
        beta, *_ = np.linalg.lstsq(design, y, rcond=None)
        # beta[0] = merkezli intercept = ORTALAMA log ilan fiyatı → SEVİYE olarak
        # KULLANILMAZ (atılır). Yalnızca eğimler (göreceli prim) saklanır.
        self.coef_ = {c: float(b) for c, b in zip(used, beta[1:])}
        self._fitted = True
        return self

    def multiplier(self, features: dict) -> float:
        """Ortalama taşınmaza göre göreli değer çarpanı exp(Σβ·(x−x_ref)). Fit yoksa 1.0."""
        if not self._fitted:
            return 1.0
        s = 0.0
        for c, b in self.coef_.items():
            x = features.get(c)
            if x is None:
                continue
            try:
                s += b * (float(x) - self.reference_[c])
            except (TypeError, ValueError):
                continue
        return float(np.exp(s))


def tcmb_fair_value(
    unit_price_tl_m2: float | None,
    gross_m2: float | None,
    premium_multiplier: float = 1.0,
) -> float | None:
    """EKSPERTİZ-çıpalı fair value = U_gt · gross_m2 · exp(β'·ΔZ) = U_gt · m² · göreceli_prim.

    ``unit_price_tl_m2`` = hedef dönemin ÇAĞDAŞ (ya da ``roll_unit_price`` ile t'ye TAŞINMIŞ)
    il medyan EKSPERTİZ TL/m²'sidir (gerçekleşen işlem DEĞİL). **EK BİR TAM KFE ÇARPANI
    UYGULANMAZ** (seviye × endeks çift-sayaca yol açar). Girdi eksikse None.
    """
    if unit_price_tl_m2 is None or gross_m2 is None:
        return None
    try:
        base = float(unit_price_tl_m2) * float(gross_m2)
    except (TypeError, ValueError):
        return None
    if base <= 0:
        return None
    return base * float(premium_multiplier)


def roll_unit_price(unit_price_t0: float, kfe_t: float, kfe_t0: float) -> float:
    """Eski bir birim-fiyat çıpasını KFE ORANIYLA t0'dan t'ye taşır: U_gt = U_g,t0 · (KFE_t/KFE_t0).

    YALNIZCA çıpa eski bir dönemden geldiğinde kullanılır; çağdaş bir çıpa DOĞRUDAN
    (taşınmadan) kullanılır. Böylece seviye ile endeks ÇİFT SAYILMAZ.
    """
    if not kfe_t0:
        raise ValueError("kfe_t0 sıfır olamaz (KFE oranı tanımsız).")
    return float(unit_price_t0) * (float(kfe_t) / float(kfe_t0))
