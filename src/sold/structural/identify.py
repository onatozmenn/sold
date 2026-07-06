"""Yapısal kimliklendirme (identification) tanılamaları — SMM sonucunu SUNMADAN ÖNCE.

Optimizer'ın yakınsaması KİMLİKLENDİRME DEĞİLDİR. Bir Nelder-Mead parametre vektörü,
optimizer başarıyla döndü diye "yapısal olarak tahmin edildi" DİYE TANIMLANAMAZ.

Bu modül θ etrafında sayısal moment Jacobian'ı hesaplar:

    J(θ) = ∂ m_sim(θ) / ∂ θ'

MERKEZİ SONLU FARKLARLA ve ORTAK RASTGELE SAYILARLA (her simülasyon aynı tohum), böylece
simülasyon gürültüsü türevi domine etmez. Raporlar: Jacobian rank, parametre boyutu,
tekil değerler, koşul sayısı, zayıf-kimliklendirilmiş yönler. Eğer ``rank(J) < dim(θ)``
ise durum ``NOT_IDENTIFIED`` döner ve tahmin arayüzü DUYARLILIK (sensitivity) moduna geçer.

Ayrıca en az ``eta`` ve mekanizma-kayma parametreleri için PROFİL tanılaması: parametre
makul bir ızgarada gezdirilir (profil stratejisi: KALAN parametreler adayda SABİT tutulur);
SMM hedefi geniş bir aralıkta belirgin biçimde DÜZ ise parametre zayıf-kimliklendirilmiş
raporlanır. Epistemik katılık: kimliklendirme iddiaları UYDURULMAZ.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .moments import MomentContext, align, simulated_moments
from .params import StructuralParams

# Sonlu fark adımı (KISITSIZ parametre uzayında, merkezi fark). Dökümanlı ve sabit.
JACOBIAN_STEP = 1e-3
# Tekil değer bir yönü "zayıf" saymanın göreli eşiği (s_i / s_max < WEAK_REL → zayıf)
WEAK_SINGULAR_REL = 1e-3
# Profil hedefi göreli aralığı bunun altındaysa parametre zayıf-kimliklendirilmiş
PROFILE_FLAT_REL = 1e-2
# Tam rank olsa bile bu koşul sayısının üzerinde: WEAKLY_IDENTIFIED (ağır kondisyon bozukluğu)
ILL_CONDITIONED = 1e6


def moment_jacobian(
    params: StructuralParams,
    ctx: MomentContext,
    free_names: tuple[str, ...],
    seed: int = 12345,
    step: float = JACOBIAN_STEP,
    moment_keys: list[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """J(θ)=∂m_sim/∂θ' — merkezi sonlu fark + ortak rastgele sayılar. (J, moment_keys).

    ``moment_keys`` verilirse Jacobian YALNIZCA o momentler üzerinde hesaplanır (gerçekten
    GÖZLENEN momentler). Böylece simülatörün ürettiği ama VERİDE OLMAYAN momentler
    kimliklendirme gücünü şişirmez (ör. tek gözlemde sd gerçekte yok).
    """

    def msim(x: np.ndarray) -> dict:
        p = params.with_free(free_names, x)
        # ORTAK rastgele sayılar: her değerlendirmede AYNI tohum → gürültü sabit.
        return simulated_moments(p, ctx, np.random.default_rng(seed))

    x0 = params.free_vector(free_names)
    base = msim(x0)
    keys = [k for k in sorted(base) if np.isfinite(base[k])]
    if moment_keys is not None:
        allowed = set(moment_keys)
        keys = [k for k in keys if k in allowed]
    if not keys or len(free_names) == 0:
        return np.zeros((len(keys), len(free_names))), keys
    J = np.zeros((len(keys), len(free_names)))
    for j in range(len(free_names)):
        xp, xm = x0.copy(), x0.copy()
        xp[j] += step
        xm[j] -= step
        mp, mm = msim(xp), msim(xm)
        vp = np.array([mp.get(k, np.nan) for k in keys])
        vm = np.array([mm.get(k, np.nan) for k in keys])
        J[:, j] = (vp - vm) / (2.0 * step)
    return J, keys


def _weight(obs: np.ndarray) -> np.ndarray:
    return np.diag(1.0 / (np.abs(obs) + 1e-3) ** 2)


def profile_objective(
    m_obs: dict,
    ctx: MomentContext,
    params: StructuralParams,
    param: str,
    grid,
    seed: int = 777,
) -> dict:
    """Profil: ``param`` ızgarada gezdirilir, KALANLAR adayda SABİT (dökümanlı strateji).

    SMM hedefi geniş aralıkta DÜZ ise (``PROFILE_FLAT_REL``) parametre zayıf-kimliklendirilmiş.
    """
    grid = list(grid)
    values: list[float] = []
    for g in grid:
        p = replace(params, **{param: float(g)})
        m_sim = simulated_moments(p, ctx, np.random.default_rng(seed))
        obs, sim, keys = align(m_obs, m_sim)
        if not keys:
            values.append(float("nan"))
            continue
        d = obs - sim
        values.append(float(d @ _weight(obs) @ d))
    finite = [v for v in values if np.isfinite(v)]
    if not finite:
        return {"param": param, "grid": grid, "objective": values,
                "weakly_identified": True, "reason": "no_moments"}
    lo, hi = min(finite), max(finite)
    rel_range = (hi - lo) / (lo + 1e-12)
    return {
        "param": param,
        "grid": grid,
        "objective": values,
        "argmin": float(grid[int(np.nanargmin(values))]),
        "relative_range": float(rel_range),
        "weakly_identified": bool(rel_range < PROFILE_FLAT_REL),
    }


def dataset_summary(
    auctions: pd.DataFrame | None = None,
    kap: pd.DataFrame | None = None,
    toki_result: dict | None = None,
) -> dict:
    """Gerçek yapısal veri kümesi sayıları (identification raporu için)."""
    s: dict = {
        "uyap_total": 0,
        "uyap_sold": 0,
        "uyap_unsold": 0,
        "uyap_offer_count_observed": 0,
        "uyap_bidder_count_observed": 0,
        "uyap_exact_legal_floor_observed": 0,
        "kap_negotiated_disposals": 0,
        "toki_valid_project_period_strata": 0,
    }
    if auctions is not None and len(auctions):
        sold = auctions["sold"].astype(bool)
        s["uyap_total"] = int(len(auctions))
        s["uyap_sold"] = int(sold.sum())
        s["uyap_unsold"] = int((~sold).sum())
        s["uyap_offer_count_observed"] = int(pd.to_numeric(auctions["offer_count"], errors="coerce").notna().sum())
        s["uyap_bidder_count_observed"] = int(pd.to_numeric(auctions["bidder_count"], errors="coerce").notna().sum())
        s["uyap_exact_legal_floor_observed"] = int(auctions["legal_floor_exact"].astype(bool).sum())
    if kap is not None and len(kap):
        s["kap_negotiated_disposals"] = int(len(kap))
    if toki_result:
        s["toki_valid_project_period_strata"] = int(len(toki_result.get("cohorts", [])))
    return s


def identification_report(
    ctx: MomentContext,
    params: StructuralParams,
    free_names: tuple[str, ...],
    m_obs: dict | None = None,
    auctions: pd.DataFrame | None = None,
    kap: pd.DataFrame | None = None,
    toki_result: dict | None = None,
    provenance: dict | None = None,
    unavailable: list | None = None,
    seed: int = 12345,
    step: float = JACOBIAN_STEP,
) -> dict:
    """Tam kimliklendirme raporu. 3'lü durum:

    - ``rank(J) < dim(θ)`` → ``NOT_IDENTIFIED`` (sensitivity mode),
    - tam rank ama ağır kondisyon bozukluğu / düz profiller → ``WEAKLY_IDENTIFIED`` (sensitivity),
    - rank + tekil-değer yapısı + profiller destekliyorsa → ``IDENTIFIED``.

    Gözlem sayısı eşiği (50/100/500 gibi) kimliklendirme ölçütü OLARAK KULLANILMAZ.
    """
    summary = dataset_summary(auctions, kap, toki_result)
    # Kimliklendirme YALNIZCA gerçekten gözlenen momentlerden gelir (m_obs anahtarları).
    obs_keys = list(m_obs.keys()) if m_obs else None
    J, keys = moment_jacobian(
        params, ctx, free_names, seed=seed, step=step, moment_keys=obs_keys
    )
    dim = len(free_names)
    n_moments = len(keys)

    report: dict = {
        "dataset": summary,
        "n_structural_parameters": dim,
        "n_observed_moments": n_moments,
        "free_parameters": list(free_names),
        "moment_keys": keys,
        "moment_provenance": provenance or {},
        "unavailable_moments": unavailable or [],
        "jacobian_step": step,
    }

    if n_moments == 0 or dim == 0:
        report.update(
            {
                "status": "NOT_IDENTIFIED",
                "rank": 0,
                "singular_values": [],
                "condition_number": float("inf"),
                "weakly_identified_directions": [],
                "prediction_mode": "sensitivity_mode",
                "reason": "no_observed_moments" if n_moments == 0 else "no_free_parameters",
            }
        )
        return report

    s = np.linalg.svd(J, compute_uv=False)
    smax = float(s.max()) if s.size else 0.0
    tol = max(J.shape) * np.finfo(float).eps * smax
    rank = int((s > tol).sum())
    cond = float(smax / s.min()) if s.size and s.min() > 0 else float("inf")

    # Zayıf yönler: küçük tekil değerlere karşılık gelen sağ-tekil vektörler
    _, s_full, vt = np.linalg.svd(J, full_matrices=False)
    weak_dirs = []
    for i, sv in enumerate(s_full):
        if smax > 0 and sv / smax < WEAK_SINGULAR_REL:
            vec = vt[i]
            loading = {
                free_names[k]: round(float(vec[k]), 4)
                for k in np.argsort(-np.abs(vec))[:3]
            }
            weak_dirs.append({"singular_value": float(sv), "direction": loading})

    # Profil tanılaması: en az eta + mekanizma-kaymaları (m_obs varsa)
    profiles: dict = {}
    if m_obs:
        profile_grids = {
            "eta": np.linspace(0.05, 0.95, 19),
            "kap_shift": np.linspace(-0.3, 0.3, 13),
            "auction_shift": np.linspace(-0.3, 0.3, 13),
        }
        for pname, grid in profile_grids.items():
            profiles[pname] = profile_objective(m_obs, ctx, params, pname, grid, seed=seed)
        report["profiles"] = profiles

    # Durum: yalnızca SERBEST parametrelerin düz profili kimliklendirmeyi zayıflatır
    # (serbest OLMAYAN mekanizma-kaymalarının profili bilgi amaçlıdır, durumu belirlemez).
    any_flat_profile = any(
        profiles[p].get("weakly_identified") for p in free_names if p in profiles
    )

    if rank < dim:
        status, mode = "NOT_IDENTIFIED", "sensitivity_mode"
    elif cond > ILL_CONDITIONED or any_flat_profile:
        # Tam rank ama ağır kondisyon bozukluğu ya da düz profiller
        status, mode = "WEAKLY_IDENTIFIED", "sensitivity_mode"
    else:
        status, mode = "IDENTIFIED", "identified"

    report.update(
        {
            "status": status,
            "rank": rank,
            "singular_values": [float(x) for x in s],
            "condition_number": cond,
            "weakly_identified_directions": weak_dirs,
            "prediction_mode": mode,
        }
    )
    return report
