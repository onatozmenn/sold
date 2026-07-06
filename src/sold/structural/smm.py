"""Simüle Momentler Yöntemi (SMM) tahmincisi.

    θ̂ = argmin_θ  (m_obs − m_sim(θ))′ W (m_obs − m_sim(θ))

m_sim, ortak rastgele sayılarla (sabit tohum) simüle edilir; böylece hedef θ'da
düzgündür ve türevsiz Nelder-Mead ile aranabilir. W varsayılanı, her momenti kendi
ölçeğine normalize eder (oran momentleri ~1, olasılıklar ~0..1). SciPy GEREKMEZ —
Nelder-Mead numpy ile uygulanır.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .moments import MomentContext, align, simulated_moments
from .params import DEFAULT_FREE, StructuralParams


@dataclass
class SMMResult:
    params: StructuralParams
    objective: float
    n_iter: int
    moment_keys: list[str]
    m_obs: dict
    m_sim: dict


def _weight_matrix(obs: np.ndarray) -> np.ndarray:
    """W = diag(1/(|m_obs|+ε)²) — momentleri kendi ölçeğine göre normalize eder."""
    scale = np.abs(obs) + 1e-3
    return np.diag(1.0 / scale**2)


def smm_objective(
    free_vec: np.ndarray,
    free_names: tuple[str, ...],
    base: StructuralParams,
    m_obs: dict,
    ctx: MomentContext,
    seed: int,
) -> float:
    params = base.with_free(free_names, free_vec)
    # Ortak rastgele sayılar: her değerlendirmede AYNI tohum → düzgün hedef.
    m_sim = simulated_moments(params, ctx, np.random.default_rng(seed))
    obs, sim, keys = align(m_obs, m_sim)
    if not keys:
        return 1e12
    d = obs - sim
    W = _weight_matrix(obs)
    return float(d @ W @ d)


def nelder_mead(
    f,
    x0: np.ndarray,
    max_iter: int = 400,
    tol: float = 1e-9,
    step: float = 0.4,
) -> tuple[np.ndarray, float, int]:
    """Türevsiz Nelder-Mead (numpy). (x_best, f_best, n_iter) döndürür."""
    x0 = np.asarray(x0, dtype=float)
    n = len(x0)
    if n == 0:
        return x0, f(x0), 0
    # Başlangıç sadeleştirilmiş cismi (simplex)
    sim = np.vstack([x0] + [x0 + step * np.eye(n)[i] for i in range(n)])
    fvals = np.array([f(x) for x in sim])
    alpha, gamma, rho, sigma = 1.0, 2.0, 0.5, 0.5
    nit = 0
    for nit in range(1, max_iter + 1):
        order = np.argsort(fvals)
        sim, fvals = sim[order], fvals[order]
        if abs(fvals[-1] - fvals[0]) <= tol * (abs(fvals[0]) + tol):
            break
        centroid = sim[:-1].mean(axis=0)
        xr = centroid + alpha * (centroid - sim[-1])
        fr = f(xr)
        if fr < fvals[0]:
            xe = centroid + gamma * (xr - centroid)
            fe = f(xe)
            sim[-1], fvals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < fvals[-2]:
            sim[-1], fvals[-1] = xr, fr
        else:
            xc = centroid + rho * (sim[-1] - centroid)
            fc = f(xc)
            if fc < fvals[-1]:
                sim[-1], fvals[-1] = xc, fc
            else:
                for i in range(1, n + 1):
                    sim[i] = sim[0] + sigma * (sim[i] - sim[0])
                    fvals[i] = f(sim[i])
    best = int(np.argmin(fvals))
    return sim[best], float(fvals[best]), nit


def estimate_smm(
    m_obs: dict,
    ctx: MomentContext,
    free_names: tuple[str, ...] = DEFAULT_FREE,
    start: StructuralParams | None = None,
    seed: int = 12345,
    max_iter: int = 400,
) -> SMMResult:
    """θ̂ = argmin (m_obs−m_sim(θ))′W(m_obs−m_sim(θ)) — SMM ile yapısal tahmin.

    ``free_names`` tahmin edilecek serbest parametreler; kalanlar ``start``'ta sabittir.
    Ortak rastgele sayılar (``seed``) hedefi düzgün tutar.
    """
    base = start or StructuralParams()
    x0 = base.free_vector(free_names)

    def obj(x: np.ndarray) -> float:
        return smm_objective(x, free_names, base, m_obs, ctx, seed)

    x_best, f_best, nit = nelder_mead(obj, x0, max_iter=max_iter)
    params_hat = base.with_free(free_names, x_best)
    m_sim = simulated_moments(params_hat, ctx, np.random.default_rng(seed))
    _, _, keys = align(m_obs, m_sim)
    return SMMResult(
        params=params_hat,
        objective=f_best,
        n_iter=nit,
        moment_keys=keys,
        m_obs=m_obs,
        m_sim=m_sim,
    )
