"""Kabul edilebilir YAKIN-UYUM kümesi (admissible near-fit set, Θ_A) — bir tanım kümesi
YA DA güven bölgesi DEĞİL.

Bu nesne, SMM ölçütü en iyi gözlenen-moment uyumunun BELGELİ yakın-uyum toleransı içinde
kalan, ekonomik olarak KABUL EDİLEBİLİR yapısal parametre vektörlerinin kümesidir:

    Θ_A = { θ ∈ Θ : Q(θ) ≤ Q_min + tolerans }

ÖNEMLİ (terminoloji): Θ_A biçimsel olarak TAHMİN EDİLMİŞ bir ekonometrik ``identified
set`` DEĞİLdİr ve kimliklendirilmiş küme için bir ``confidence region`` / ``%95 aralık`` /
herhangi bir örnekleme-kapsama (coverage) iddiası DEĞİLdİr. Tolerans, örnekleme
belirsizliğine ya da nominal güven kapsamına kalibre EDİLMEMİŞ; belgeli bir SAYISAL/
DUYARLILIK kuralıdır.

``Q``, SMM hedefidir; adaylar arası ORTAK RASTGELE SAYILARLA (sabit ``sim_seed``)
değerlendirilir; böylece Q(θ) farkları simülasyon gürültüsünden değil θ'dan gelir.
Tolerans AÇIKÇA belgelenir (``identification_tolerance``) ve DUYARLILIK taramasından
geçirilir (``tolerance_sensitivity``) — gizlice dar bir zarf elde etmek için seçilmez.
Ekonomik olarak geçerli parametre SINIRLARI (``PARAM_BOUNDS``) ve tüm yapısal kısıtlar
korunur. Bölge YENİDEN-ÜRETİLEBİLİR biçimde örneklenir (``seed``). Hangi parametrelerin
yakın-uyum toleransı içinde GENİŞ (zayıf kısıtlı) yoksa DAR aralıklı olduğu raporlanır.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .moments import MomentContext, align, simulated_moments
from .params import DEFAULT_FREE, StructuralParams

# Gelecek-metodoloji notu (bu iterasyonda biçimsel prosedür EKLENMEZ):
FUTURE_METHODOLOGY_NOTE = (
    "A formally calibrated confidence region for a partially identified parameter set "
    "would require an inference procedure whose criterion cutoff accounts for sampling "
    "uncertainty; the current near-fit tolerance is a computational sensitivity rule, "
    "not such a cutoff."
)

# Ekonomik olarak geçerli parametre SINIRLARI (belgeli; log-primitifler makul aralıkta).
# Doğal parametre uzayında; tüm yapısal kısıtları korur (sigma>0, eta∈(0,1) vb.).
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "mu_b": (-0.40, 0.40),        # alıcı değeri ort. (log, fair value'ya göre)
    "sigma_b": (0.03, 0.60),      # alıcı değeri std (log) > 0
    "mu_s": (-0.40, 0.40),        # satıcı rezervasyon ort. (log)
    "sigma_s": (0.03, 0.60),      # satıcı rezervasyon std (log) > 0
    "eta": (0.05, 0.95),          # pazarlık gücü ∈ (0,1)
    "auction_shift": (-0.60, 0.20),  # icra açık artırma (zorunlu satış) kayması
    "kap_shift": (-0.40, 0.40),   # KAP kurumsal müzakere kayması
    "tightness_beta": (-0.30, 0.30),
    "arrival_rate": (1.0, 8.0),
    "asking_signal": (0.0, 1.0),
}

# AÇIK tolerans kuralı bileşenleri (gizli seçim YOK; duyarlılık testli). Bu bir SAYISAL/
# DUYARLILIK kuralıdır; örnekleme-kapsama (coverage) eşiği DEĞİLdİr.
TOLERANCE_ABS = 1e-4   # mutlak taban (Q_min≈0 iken dejenere kümeyi önler)
TOLERANCE_REL = 0.25   # Q_min'e göreli pay (%25)
WIDE_RANGE_FRACTION = 0.15  # aralık/bound-genişliği ≥ bu ise "near_fit_wide" (aksi "near_fit_tight")


def objective_value(params: StructuralParams, m_obs: dict, ctx: MomentContext, seed: int) -> float:
    """SMM hedefi Q(θ) = (m_obs−m_sim)′W(m_obs−m_sim). ORTAK rastgele sayılar (sabit seed)."""
    m_sim = simulated_moments(params, ctx, np.random.default_rng(seed))
    obs, sim, keys = align(m_obs, m_sim)
    if not keys:
        return float("inf")
    d = obs - sim
    W = np.diag(1.0 / (np.abs(obs) + 1e-3) ** 2)
    return float(d @ W @ d)


def identification_tolerance(q_min: float, rel: float = TOLERANCE_REL, absolute: float = TOLERANCE_ABS) -> float:
    """AÇIK tolerans kuralı: ``tol = max(absolute, rel·|Q_min|)``. Belgeli ve sabit."""
    return float(max(absolute, rel * abs(q_min)))


def _sample_candidates(free_names, bounds, n, rng) -> np.ndarray:
    lo = np.array([bounds[p][0] for p in free_names], dtype=float)
    hi = np.array([bounds[p][1] for p in free_names], dtype=float)
    return lo + (hi - lo) * rng.random((n, len(free_names)))


def _local_candidates(free_names, bounds, anchors, n, rng, scale_frac) -> np.ndarray:
    """Anchor'lar (en iyi adaylar) etrafında Gauss perturbasyonu — yassı yönleri keşfeder."""
    lo = np.array([bounds[p][0] for p in free_names], dtype=float)
    hi = np.array([bounds[p][1] for p in free_names], dtype=float)
    A = np.atleast_2d(np.asarray(anchors, dtype=float))
    idx = rng.integers(0, A.shape[0], n)
    scale = scale_frac * (hi - lo)
    loc = A[idx] + rng.normal(0.0, 1.0, (n, len(free_names))) * scale
    return np.clip(loc, lo, hi)


@dataclass
class AdmissibleNearFitResult:
    free_names: tuple
    best_objective: float
    tolerance: float
    tolerance_rule: str
    n_candidates: int
    n_admissible: int
    admissible_params: list          # list[StructuralParams] (Θ_A üyeleri)
    best_params: StructuralParams
    param_ranges: dict               # param -> {min,max,width,bound_width,range_fraction,classification}
    correlations: dict               # "pi|pj" -> corr
    bounds: dict

    def wide_parameters(self) -> list[str]:
        """Yakın-uyum toleransı içinde GENİŞ aralıklı (zayıf kısıtlı) parametreler."""
        return [p for p, r in self.param_ranges.items() if r["classification"] == "near_fit_wide"]

    def tight_parameters(self) -> list[str]:
        """Yakın-uyum toleransı içinde DAR aralıklı (sıkı kısıtlı) parametreler."""
        return [p for p, r in self.param_ranges.items() if r["classification"] == "near_fit_tight"]


def admissible_near_fit_set(
    m_obs: dict,
    ctx: MomentContext,
    free_names: tuple[str, ...] = DEFAULT_FREE,
    bounds: dict | None = None,
    n_candidates: int = 4000,
    seed: int = 12345,
    sim_seed: int = 12345,
    start: StructuralParams | None = None,
    rel: float = TOLERANCE_REL,
    absolute: float = TOLERANCE_ABS,
    wide_range_fraction: float = WIDE_RANGE_FRACTION,
) -> AdmissibleNearFitResult:
    """Θ_A = {θ : Q(θ) ≤ Q_min + tol} kabul edilebilir YAKIN-UYUM kümesini kurar.

    Bu bir biçimsel ``identified set`` / ``confidence region`` DEĞİLdİr (bkz. modül
    başlığı). Doğal parametre uzayında ``bounds`` içinden ``seed`` ile YENİDEN-ÜRETİLEBİLİR
    örnekler; her adayı ORTAK rastgele sayılarla (``sim_seed``) değerlendirir;
    ``identification_tolerance`` (SAYISAL/DUYARLILIK kuralı) ile kabul eder. Parametre
    aralıklarını, korelasyon/ödünleşim tanılarını ve her parametrenin yakın-uyum içinde
    GENİŞ (zayıf kısıtlı) mi yoksa DAR aralıklı mı olduğunu raporlar.
    """
    bounds = bounds or PARAM_BOUNDS
    base = start or StructuralParams()
    rng = np.random.default_rng(seed)

    def _eval(matrix: np.ndarray) -> np.ndarray:
        out = np.empty(matrix.shape[0], dtype=float)
        for i in range(matrix.shape[0]):
            upd = {name: float(matrix[i, j]) for j, name in enumerate(free_names)}
            out[i] = objective_value(replace(base, **upd), m_obs, ctx, sim_seed)
        return out

    # 1) KÜRESEL uniform tarama (yeniden-üretilebilir); 2) en iyi anchor'lar etrafında
    #    YEREL inceltme — yassı (null-space) yönleri yoğunca örnekler. Tek seed sürer.
    n_global = max(int(n_candidates // 2), 1)
    n_local = max(int(n_candidates - n_global), 0)
    glob = _sample_candidates(free_names, bounds, n_global, rng)
    obj_glob = _eval(glob)
    k = min(12, n_global)
    anchors = glob[np.argsort(obj_glob)[:k]]
    if n_local:
        loc = _local_candidates(free_names, bounds, anchors, n_local, rng, scale_frac=0.20)
        cand = np.vstack([glob, loc])
        objs = np.concatenate([obj_glob, _eval(loc)])
    else:
        cand, objs = glob, obj_glob

    finite = objs[np.isfinite(objs)]
    q_min = float(finite.min()) if finite.size else float("inf")
    tol = identification_tolerance(q_min, rel, absolute)
    mask = np.isfinite(objs) & (objs <= q_min + tol)
    adm = cand[mask]
    admissible_params = [
        replace(base, **{name: float(adm[r, j]) for j, name in enumerate(free_names)})
        for r in range(adm.shape[0])
    ]
    best_i = int(np.argmin(objs))
    best_params = replace(base, **{name: float(cand[best_i, j]) for j, name in enumerate(free_names)})

    param_ranges: dict = {}
    for j, name in enumerate(free_names):
        col = adm[:, j] if adm.shape[0] else np.array([getattr(best_params, name)])
        lo_b, hi_b = bounds[name]
        width = float(col.max() - col.min())
        bound_width = float(hi_b - lo_b)
        frac = float(width / bound_width) if bound_width > 0 else 0.0
        param_ranges[name] = {
            "min": float(col.min()),
            "max": float(col.max()),
            "width": width,
            "bound_width": bound_width,
            "range_fraction": frac,
            "classification": "near_fit_tight" if frac < wide_range_fraction else "near_fit_wide",
        }

    correlations: dict = {}
    if adm.shape[0] >= 3:
        C = np.corrcoef(adm, rowvar=False)
        for a in range(len(free_names)):
            for b in range(a + 1, len(free_names)):
                correlations[f"{free_names[a]}|{free_names[b]}"] = float(C[a, b])

    rule = f"tol = max({absolute:g}, {rel:g}·|Q_min|)"
    return AdmissibleNearFitResult(
        free_names=tuple(free_names),
        best_objective=q_min,
        tolerance=tol,
        tolerance_rule=rule,
        n_candidates=int(cand.shape[0]),
        n_admissible=int(adm.shape[0]),
        admissible_params=admissible_params,
        best_params=best_params,
        param_ranges=param_ranges,
        correlations=correlations,
        bounds={p: bounds[p] for p in free_names},
    )


def tolerance_sensitivity(
    m_obs: dict,
    ctx: MomentContext,
    free_names: tuple[str, ...] = DEFAULT_FREE,
    rel_grid=(0.10, 0.25, 0.50, 1.00),
    **kwargs,
) -> list[dict]:
    """Tolerans DUYARLILIK testi: ``rel`` çarpanını gezdirip kabul edilebilir küme boyutu
    ile parametre aralıklarının toleransla nasıl büyüdüğünü gösterir. Böylece tolerans
    gizlice dar aralık için SEÇİLMEDİĞİ açıkça görülür."""
    rows: list[dict] = []
    for rel in rel_grid:
        res = admissible_near_fit_set(m_obs, ctx, free_names=free_names, rel=rel, **kwargs)
        rows.append({
            "rel": float(rel),
            "tolerance": res.tolerance,
            "n_admissible": res.n_admissible,
            "eta_width": res.param_ranges.get("eta", {}).get("width"),
            "wide_parameters": res.wide_parameters(),
        })
    return rows
