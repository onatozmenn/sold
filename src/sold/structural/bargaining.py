"""Genelleştirilmiş Nash pazarlık — sıradan yeniden-satış fiyat oluşumunun çekirdeği.

B = alıcı değeri, S = satıcı rezervasyon değeri. Ticaret YALNIZCA B ≥ S iken olur.
Ticaret koşuluyla müzakere edilen fiyat:

    P = eta · B + (1 − eta) · S,   eta = satıcı pazarlık gücü ∈ (0,1).

eta HARD-CODE EDİLMEZ; SMM ile tahmin edilir. Alıcı/satıcı değerleri fair value'ya
çıpalı log-normal dağılımlardan çekilir; asking fiyatı (varsa) satıcı rezervasyonuna
dair GÜRÜLTÜLÜ stratejik sinyaldir (ne ground-truth ne tavan).
"""

from __future__ import annotations

import numpy as np

from .params import StructuralParams


def negotiated_price(B, S, eta: float):
    """Nash pazarlık fiyatı P = eta·B + (1−eta)·S (vektörize)."""
    B = np.asarray(B, dtype=float)
    S = np.asarray(S, dtype=float)
    return eta * B + (1.0 - eta) * S


def trade_mask(B, S) -> np.ndarray:
    """Ticaret gerçekleşir mi? (B ≥ S)."""
    return np.asarray(B, dtype=float) >= np.asarray(S, dtype=float)


def draw_buyer_values(
    rng: np.random.Generator,
    fair_value,
    params: StructuralParams,
    size: int,
    tightness: float = 0.0,
    log_shift: float = 0.0,
) -> np.ndarray:
    """Alıcı değeri B = V·exp(mu_b + tightness_beta·τ + log_shift + ε), ε~N(0,sigma_b²).

    ``log_shift`` mekanizma kayması için (KAP kurumsal / icra açık artırma).
    """
    V = np.asarray(fair_value, dtype=float)
    mean = params.mu_b + params.tightness_beta * float(tightness) + float(log_shift)
    eps = rng.normal(mean, params.sigma_b, size)
    return V * np.exp(eps)


def draw_seller_values(
    rng: np.random.Generator,
    fair_value,
    params: StructuralParams,
    size: int,
    tightness: float = 0.0,
    log_shift: float = 0.0,
    asking: object = None,
) -> np.ndarray:
    """Satıcı rezervasyon S = V·exp(mu_s + asking_signal·log(asking/V) + kayma + ε).

    ``asking`` verilirse satıcı rezervasyonu asking fiyatına KOŞULLANIR (gürültülü
    sinyal): yüksek asking → yüksek çıkarımsal rezervasyon, ama asking ≠ S ve tavan
    değildir. ``asking`` yoksa asking_signal terimi düşer.
    """
    V = np.asarray(fair_value, dtype=float)
    mean = params.mu_s - params.tightness_beta * float(tightness) + float(log_shift)
    if asking is not None:
        A = np.asarray(asking, dtype=float)
        mean = mean + params.asking_signal * (np.log(A) - np.log(V))
    eps = rng.normal(0.0, params.sigma_s, size)
    return V * np.exp(mean + eps)


def simulate_negotiations(
    rng: np.random.Generator,
    fair_value,
    params: StructuralParams,
    size: int,
    tightness: float = 0.0,
    mechanism: str = "ordinary",
    asking: object = None,
) -> dict:
    """Bir kohort için (B, S) çekip Nash sonucu döndürür.

    mechanism='kap' → kurumsal müzakere kayması (kap_shift) uygulanır; SIRADAN
    yeniden-satış GERÇEĞİ olarak ALINMAZ (ayrı mekanizma).
    """
    shift = params.kap_shift if mechanism == "kap" else 0.0
    B = draw_buyer_values(rng, fair_value, params, size, tightness, log_shift=shift)
    S = draw_seller_values(
        rng, fair_value, params, size, tightness, log_shift=shift, asking=asking
    )
    traded = trade_mask(B, S)
    P = negotiated_price(B, S, params.eta)
    return {"B": B, "S": S, "traded": traded, "price": P}
