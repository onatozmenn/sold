"""Yapısal parametre vektörü θ — SMM ile tahmin edilir (hard-code YOK).

θ, sıradan pazarlıklı yeniden-satış fiyat oluşumunun yapısal ilkelerini taşır:
- alıcı-değeri dağılımı (mu_b, sigma_b) — fair value'ya GÖRE (log ölçek),
- satıcı-rezervasyon dağılımı (mu_s, sigma_s),
- pazarlık gücü (eta) — Nash P = eta·B + (1−eta)·S,
- alıcı varışı / piyasa sıkılığı (arrival_rate, tightness_beta),
- kaynak/mekanizma kaymaları (kap_shift, auction_shift) — KAP kurumsal müzakere ve
  icra açık artırması SIRADAN yeniden-satıştan AYRI mekanizmalardır,
- asking_signal — sıradan ilanda asking fiyatının satıcı rezervasyonuna dair
  GÜRÜLTÜLÜ stratejik sinyal ağırlığı (asking ne ground-truth ne tavan).

Optimizasyon için parametreler KISITSIZ uzaya dönüştürülür (sigma>0 → log; eta∈(0,1)
→ logit; arrival>0 → log; diğerleri özdeş). Böylece Nelder-Mead serbestçe arar.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

STRUCTURAL_MODEL_SEMANTICS_VERSION = "2026-07-09.interval-floor-v1"

# Alan → kısıt türü ("id" özdeş, "pos" pozitif, "unit" (0,1))
_TRANSFORM: dict[str, str] = {
    "mu_b": "id",
    "sigma_b": "pos",
    "mu_s": "id",
    "sigma_s": "pos",
    "eta": "unit",
    "arrival_rate": "pos",
    "tightness_beta": "id",
    "kap_shift": "id",
    "auction_shift": "id",
    "asking_signal": "id",
}


def _to_unconstrained(name: str, value: float) -> float:
    kind = _TRANSFORM[name]
    v = float(value)
    if kind == "pos":
        return float(np.log(max(v, 1e-8)))
    if kind == "unit":
        p = min(max(v, 1e-6), 1 - 1e-6)
        return float(np.log(p / (1 - p)))
    return v


def _from_unconstrained(name: str, x: float) -> float:
    kind = _TRANSFORM[name]
    if kind == "pos":
        return float(np.exp(x))
    if kind == "unit":
        return float(1.0 / (1.0 + np.exp(-x)))
    return float(x)


@dataclass(frozen=True)
class StructuralParams:
    """Yapısal ilke vektörü. Varsayılanlar makul BİR ÖNSEL'dir — tahmin edilmemiş
    (SMM ile ``estimate_smm`` sonrası gerçek veriye kalibre edilir)."""

    mu_b: float = 0.06       # alıcı değeri ort. (log, fair value'ya göre)
    sigma_b: float = 0.20    # alıcı değeri std (log)
    mu_s: float = 0.03       # satıcı rezervasyon ort. (log)
    sigma_s: float = 0.14    # satıcı rezervasyon std (log)
    eta: float = 0.5         # satıcı pazarlık gücü (0..1) — SMM tahmin eder
    arrival_rate: float = 3.0  # açık artırmada ort. teklif veren sayısı (Poisson)
    tightness_beta: float = 0.10  # piyasa sıkılığı duyarlılığı
    kap_shift: float = 0.0   # KAP kurumsal müzakere mekanizma/domain kayması (log)
    auction_shift: float = -0.08  # icra açık artırma (zorunlu satış) alıcı iskontosu (log)
    asking_signal: float = 0.5    # asking'in satıcı rezervasyonuna sinyal ağırlığı

    def free_vector(self, names: tuple[str, ...]) -> np.ndarray:
        """Belirtilen serbest alanları KISITSIZ vektöre çevirir (optimizer için)."""
        return np.array(
            [_to_unconstrained(n, getattr(self, n)) for n in names], dtype=float
        )

    def with_free(self, names: tuple[str, ...], vector) -> "StructuralParams":
        """Kısıtsız vektörden serbest alanları güncelleyip yeni params döndürür."""
        updates = {
            n: _from_unconstrained(n, float(x)) for n, x in zip(names, np.asarray(vector))
        }
        return replace(self, **updates)

    def as_dict(self) -> dict[str, float]:
        return {n: float(getattr(self, n)) for n in _TRANSFORM}


# Tüm yapısal primitive'ler yakın-uyum uzayında tutulur. Momentlere duyarsız yönler
# rank eksikliği/null-space olarak görünür; tek bir varsayılan değere gizlice sabitlenmez.
DEFAULT_FREE = (
    "mu_b",
    "sigma_b",
    "mu_s",
    "sigma_s",
    "eta",
    "arrival_rate",
    "tightness_beta",
    "kap_shift",
    "auction_shift",
    "asking_signal",
)
