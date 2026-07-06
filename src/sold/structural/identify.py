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

import json
from pathlib import Path

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


def _rank_and_svd(J: np.ndarray) -> dict:
    """Bir Jacobian bloğunun sayısal rank'ı + tekil değerleri + en küçük sıfır-olmayan sv."""
    if J.size == 0 or J.shape[0] == 0:
        return {"rank": 0, "n_moments": int(J.shape[0]), "singular_values": [], "smallest_nonzero_sv": None}
    s = np.linalg.svd(J, compute_uv=False)
    smax = float(s.max()) if s.size else 0.0
    tol = max(J.shape) * np.finfo(float).eps * smax
    nz = s[s > tol]
    return {
        "rank": int((s > tol).sum()),
        "n_moments": int(J.shape[0]),
        "singular_values": [float(x) for x in s],
        "smallest_nonzero_sv": float(nz.min()) if nz.size else None,
    }


def source_jacobian_ranks(
    params: StructuralParams,
    ctx: MomentContext,
    free_names: tuple[str, ...],
    m_obs: dict,
    provenance: dict | None = None,
    seed: int = 12345,
    step: float = JACOBIAN_STEP,
) -> dict:
    """Kaynağa özgü (J_UYAP / J_KAP / J_TOKİ) ve BİRLEŞİK Jacobian sayısal rank'ları.

    Her aile YALNIZCA o ailenin katkı verdiği (gözlenen) momentlerin satırlarını kullanır.
    Amaç: "hangi kaynak BAĞIMSIZ bir parametre yönü ekliyor?" (sadece "hangi kaynak daha
    çok satır ekledi?" değil). NOT: kaynağa özgü rank NEDENSEL ya da TEK-BAŞINA parametre
    kimliklendirmesi DEĞİLDİR; mevcut yapısal model altında yerel moment-duyarlılığı tanısıdır.
    """
    obs_keys = list(m_obs.keys()) if m_obs else None
    J, keys = moment_jacobian(params, ctx, free_names, seed=seed, step=step, moment_keys=obs_keys)
    prov = provenance or {}
    out: dict = {}
    for fam in ("uyap", "kap", "toki"):
        rows = [i for i, k in enumerate(keys) if prov.get(k) == fam]
        Jf = J[rows, :] if rows else np.zeros((0, len(free_names)))
        out[f"J_{fam.upper()}"] = _rank_and_svd(Jf)
    out["J_combined"] = _rank_and_svd(J)
    return out


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
    sample_sizes: dict | None = None,
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
    # GÖZLENEN ama mevcut yapısal modelde SİMÜLE KARŞILIĞI OLMAYAN momentler (ör. TOKİ
    # kohort momentleri): m_obs'ta VAR ama Jacobian'a GİRMEZ — çünkü simülatör bu
    # mekanizmayı (henüz) üretmez. Bu bir MODEL-eşleme boşluğudur (veri değil), dürüstçe raporlanır.
    observed_unmatched = sorted(k for k in (m_obs or {}) if k not in set(keys))

    report: dict = {
        "dataset": summary,
        "n_structural_parameters": dim,
        "n_observed_moments": n_moments,
        "free_parameters": list(free_names),
        "moment_keys": keys,
        "moment_provenance": provenance or {},
        "unavailable_moments": unavailable or [],
        "observed_without_simulated_counterpart": observed_unmatched,
        "sample_sizes": sample_sizes or {},
        "jacobian_step": step,
    }

    if n_moments == 0 or dim == 0:
        report.update(
            {
                "status": "NOT_IDENTIFIED",
                "rank": 0,
                "singular_values": [],
                "smallest_nonzero_singular_value": None,
                "condition_number": float("inf"),
                "weakly_identified_directions": [],
                "source_jacobians": {},
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
    nz = s[s > tol]
    smallest_nonzero_sv = float(nz.min()) if nz.size else None

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
            "smallest_nonzero_singular_value": smallest_nonzero_sv,
            "condition_number": cond,
            "weakly_identified_directions": weak_dirs,
            "source_jacobians": source_jacobian_ranks(
                params, ctx, free_names, m_obs or {}, provenance, seed=seed, step=step
            ),
            "prediction_mode": mode,
        }
    )
    return report


# --------------------------------------------------------------------------- #
# Snapshot karşılaştırması — identification-KATKI raporu (yeni batch etkisi)
# --------------------------------------------------------------------------- #
def snapshot_metrics(report: dict) -> dict:
    """Bir identification raporundan snapshot metriklerini çıkarır (önceki/sonraki kıyas)."""
    return {
        "n_structural_parameters": report.get("n_structural_parameters"),
        "n_observed_moments": report.get("n_observed_moments"),
        "available_moments": sorted((report.get("moment_provenance") or {}).keys()),
        "sample_sizes": report.get("sample_sizes", {}),
        "rank": report.get("rank"),
        "smallest_nonzero_singular_value": report.get("smallest_nonzero_singular_value"),
        "condition_number": report.get("condition_number"),
        "weak_directions": [
            wd.get("direction") for wd in report.get("weakly_identified_directions", [])
        ],
        "status": report.get("status"),
    }


def save_snapshot(report: dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot_metrics(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def load_snapshot(path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compare_snapshots(before: dict, after: dict) -> dict:
    """Önceki genuine snapshot ile mevcut arasındaki identification-KATKI değişimi.

    'Kaç satır eklendi?' değil, 'hangi moment açıldı / hangi yön güçlendi?' sorusuna yanıt.
    """
    b_moms = set(before.get("available_moments", []))
    a_moms = set(after.get("available_moments", []))
    ss_b = before.get("sample_sizes", {}) or {}
    ss_a = after.get("sample_sizes", {}) or {}
    increased = [
        {"moment": k, "before": int(ss_b.get(k, 0)), "after": int(ss_a.get(k, 0))}
        for k in sorted(set(ss_a) | set(ss_b))
        if int(ss_a.get(k, 0)) > int(ss_b.get(k, 0))
    ]
    return {
        "moments_newly_unlocked": sorted(a_moms - b_moms),
        "moments_sample_increased": increased,
        "rank": {"before": before.get("rank"), "after": after.get("rank")},
        "smallest_nonzero_singular_value": {
            "before": before.get("smallest_nonzero_singular_value"),
            "after": after.get("smallest_nonzero_singular_value"),
        },
        "condition_number": {
            "before": before.get("condition_number"),
            "after": after.get("condition_number"),
        },
        "weak_directions": {
            "before": before.get("weak_directions"),
            "after": after.get("weak_directions"),
        },
        "status": {"before": before.get("status"), "after": after.get("status")},
    }
