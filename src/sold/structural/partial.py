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
    expected_keys = {
        key for key in m_obs
        if key.startswith(("uyap_win_over_appraisal_", "kap_log_ratio_"))
    }
    if not expected_keys or set(keys) != expected_keys:
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
    descent_starts: int = 6,
    descent_iters: int = 80,
    refine_rounds: int = 3,
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
    lo_b = np.array([bounds[p][0] for p in free_names], dtype=float)
    hi_b = np.array([bounds[p][1] for p in free_names], dtype=float)

    def _eval(matrix: np.ndarray) -> np.ndarray:
        out = np.empty(matrix.shape[0], dtype=float)
        for i in range(matrix.shape[0]):
            upd = {name: float(matrix[i, j]) for j, name in enumerate(free_names)}
            out[i] = objective_value(replace(base, **upd), m_obs, ctx, sim_seed)
        return out

    def _obj_vec(x: np.ndarray) -> float:
        xc = np.clip(np.asarray(x, dtype=float), lo_b, hi_b)  # kutu-kırpma (bounds korunur)
        upd = {name: float(xc[j]) for j, name in enumerate(free_names)}
        return objective_value(replace(base, **upd), m_obs, ctx, sim_seed)

    # 1) KÜRESEL uniform tarama (yeniden-üretilebilir).
    n_global = max(int(n_candidates // 3), 1)
    glob = _sample_candidates(free_names, bounds, n_global, rng)
    obj_glob = _eval(glob)

    # 2) En iyi birkaç global noktadan NELDER-MEAD inişi → DOĞRU Q_min (AYNI objektif + CRN).
    #    Küçük bütçede de gerçek minimumu bulup Q_min'i BÜTÇEYE-KARARLI kılar (yalnızca sayısal
    #    arama iyileştirmesi; tolerans/bound/kriter DEĞİŞMEZ).
    from .smm import nelder_mead

    anchors = []
    for idx in np.argsort(obj_glob)[: min(descent_starts, n_global)]:
        xb, _fb, _ = nelder_mead(_obj_vec, glob[idx], max_iter=descent_iters)
        anchors.append(np.clip(xb, lo_b, hi_b))
    anchor_arr = np.atleast_2d(np.array(anchors)) if anchors else glob[np.argsort(obj_glob)[:1]]

    # 3) YİNELEMELİ yerel örnekleme: anchor'lar + o ana dek kabul edilebilir noktalar etrafında
    #    yoğunlaş (yassı null-space yönlerini yeniden-üretilebilir biçimde doldurur).
    cand_parts = [glob, anchor_arr]
    obj_parts = [obj_glob, _eval(anchor_arr)]
    n_local = max(int(n_candidates - n_global), 0)
    rounds = max(int(refine_rounds), 1)
    per_round = max(n_local // rounds, 1)
    cur_anchors = anchor_arr
    for round_index in range(rounds):
        scale_frac = 0.12 * (0.35 ** round_index)
        loc = _local_candidates(free_names, bounds, cur_anchors, per_round, rng, scale_frac=scale_frac)
        loc_obj = _eval(loc)
        cand_parts.append(loc)
        obj_parts.append(loc_obj)
        cand_so = np.vstack(cand_parts)
        obj_so = np.concatenate(obj_parts)
        fin_so = obj_so[np.isfinite(obj_so)]
        q_so = float(fin_so.min()) if fin_so.size else float("inf")
        t_so = identification_tolerance(q_so, rel, absolute)
        adm_so = cand_so[np.isfinite(obj_so) & (obj_so <= q_so + t_so)]
        cur_anchors = np.vstack([anchor_arr, adm_so]) if adm_so.size else anchor_arr

    cand = np.vstack(cand_parts)
    objs = np.concatenate(obj_parts)
    finite = objs[np.isfinite(objs)]
    if not finite.size:
        raise ValueError(
            "No finite SMM objective is available; audited evidence and simulated moments are required"
        )
    incumbent = cand[int(np.nanargmin(objs))]
    profile_parts = []
    for parameter_index in range(len(free_names)):
        for value in np.linspace(lo_b[parameter_index], hi_b[parameter_index], 9):
            probe = incumbent.copy()
            probe[parameter_index] = value
            profile_parts.append(probe)
    if profile_parts:
        profile_candidates = np.asarray(profile_parts, dtype=float)
        cand = np.vstack([cand, profile_candidates])
        objs = np.concatenate([objs, _eval(profile_candidates)])
        finite = objs[np.isfinite(objs)]
    q_min = float(finite.min())
    tol = identification_tolerance(q_min, rel, absolute)
    mask = np.isfinite(objs) & (objs <= q_min + tol)
    adm = cand[mask]
    if not adm.size:
        raise ValueError("No admissible near-fit structural parameter configuration was found")
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
        with np.errstate(invalid="ignore", divide="ignore"):
            C = np.corrcoef(adm, rowvar=False)  # sabit parametre → NaN (aşağıda atlanır)
        for a in range(len(free_names)):
            for b in range(a + 1, len(free_names)):
                c = float(C[a, b])
                if np.isfinite(c):
                    correlations[f"{free_names[a]}|{free_names[b]}"] = c

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


def cumulative_near_fit_experiment(
    m_obs: dict,
    ctx: MomentContext,
    free_names: tuple[str, ...] = DEFAULT_FREE,
    budgets=(750, 1500, 3000),
    bounds: dict | None = None,
    seed: int = 12345,
    sim_seed: int = 12345,
    start: StructuralParams | None = None,
    rel: float = TOLERANCE_REL,
    absolute: float = TOLERANCE_ABS,
    descent_starts: int = 6,
    descent_iters: int = 80,
    local_scale: float = 0.12,
    reeval_tol: float = 1e-9,
) -> dict:
    """KÜMÜLATİF (iç-içe, INCUMBENT-koruyan) yakın-uyum arama deneyi — SAYISAL diagnostik.

    Üretim ``admissible_near_fit_set`` tanımını DEĞİŞTİRMEZ. Tek ``rng`` ile ARTIMLI aday
    havuzu kurar: ``pool(b_k) ⊇ pool(b_{k-1})``; her aday ORTAK rastgele sayılarla
    (``sim_seed``) BİR KEZ değerlendirilir ve havuza EKLENİR (asla çıkarılmaz), böylece
    global incumbent her bütçede KORUNUR ve ``cumulative_best_objective`` MONOTON AZALMAYAN
    olur (sayısal eşitlik toleransı dahilinde). Tüm bütçeler ORTAK bir ``Q_ref``/``tol_ref``
    altında yeniden değerlendirilir (hareketli eşik YOK). Yalnızca sayısal arama-kalitesi
    ölçer; ekonometrik metodolojiyi DEĞİŞTİRMEZ.
    """
    bounds = bounds or PARAM_BOUNDS
    base = start or StructuralParams()
    names = tuple(free_names)
    d = len(names)
    lo_b = np.array([bounds[p][0] for p in names], dtype=float)
    hi_b = np.array([bounds[p][1] for p in names], dtype=float)
    rng = np.random.default_rng(seed)
    from .smm import nelder_mead

    def _q(x) -> float:
        xc = np.clip(np.asarray(x, dtype=float), lo_b, hi_b)
        upd = {name: float(xc[j]) for j, name in enumerate(names)}
        return objective_value(replace(base, **upd), m_obs, ctx, sim_seed)

    pool_x = np.empty((0, d))
    pool_q = np.empty((0,))
    snapshots: list[tuple] = []  # (budget, X_copy, Q_copy, new_added)
    prev_b = 0
    for b in budgets:
        n_new = max(int(b) - prev_b, 0)
        parts = []
        if n_new > 0:
            n_glob = max(n_new // 2, 1)
            parts.append(lo_b + (hi_b - lo_b) * rng.random((n_glob, d)))
            n_loc = n_new - n_glob
            if n_loc > 0:
                if pool_x.shape[0]:
                    q0 = float(pool_q.min())
                    t0 = identification_tolerance(q0, rel, absolute)
                    anc = pool_x[pool_q <= q0 + t0]
                    if anc.shape[0] == 0:
                        anc = pool_x[np.argsort(pool_q)[: min(descent_starts, pool_x.shape[0])]]
                else:
                    anc = parts[0][:1]
                idx = rng.integers(0, anc.shape[0], n_loc)
                loc = anc[idx] + rng.normal(0.0, 1.0, (n_loc, d)) * (local_scale * (hi_b - lo_b))
                parts.append(np.clip(loc, lo_b, hi_b))
        new_base = np.vstack(parts) if parts else np.empty((0, d))
        new_base_q = np.array([_q(x) for x in new_base]) if new_base.shape[0] else np.empty((0,))
        # NELDER-MEAD inişi: (havuz + yeni) en iyi noktalarından (pool_q önceden hesaplı → tekrar YOK)
        cx = np.vstack([pool_x, new_base]) if pool_x.shape[0] else new_base
        cq = np.concatenate([pool_q, new_base_q]) if pool_q.shape[0] else new_base_q
        desc_x = np.empty((0, d))
        desc_q = np.empty((0,))
        if cx.shape[0]:
            starts = cx[np.argsort(cq)[: min(descent_starts, cx.shape[0])]]
            dl = [np.clip(nelder_mead(_q, s, max_iter=descent_iters)[0], lo_b, hi_b) for s in starts]
            desc_x = np.array(dl)
            desc_q = np.array([_q(x) for x in desc_x])
        add_x = np.vstack([a for a in (new_base, desc_x) if a.shape[0]]) if (new_base.shape[0] or desc_x.shape[0]) else np.empty((0, d))
        add_q = np.concatenate([a for a in (new_base_q, desc_q) if a.size]) if (new_base_q.size or desc_q.size) else np.empty((0,))
        pool_x = np.vstack([pool_x, add_x]) if pool_x.shape[0] else add_x
        pool_q = np.concatenate([pool_q, add_q]) if pool_q.shape[0] else add_q
        snapshots.append((int(b), pool_x.copy(), pool_q.copy(), int(add_x.shape[0])))
        prev_b = int(b)

    cum_best = [float(q.min()) for (_b, _x, q, _n) in snapshots]
    Q_ref = float(min(cum_best))               # tüm kümülatif deney boyunca keşfedilen minimum
    tol_ref = identification_tolerance(Q_ref, rel, absolute)
    # incumbent DETERMİNİSTİK-objektif yeniden-üretilebilirlik kontrolü (aynı θ, aynı CRN → aynı Q)
    _bb, fX, fQ, _n = snapshots[-1]
    bi = int(np.argmin(fQ))
    best_x = fX[bi]
    reeval_delta = float(abs(_q(best_x) - float(fQ[bi])))
    best_theta = {name: float(best_x[j]) for j, name in enumerate(names)}

    def _uniq(X: np.ndarray) -> int:
        return int(np.unique(np.round(X, 4), axis=0).shape[0]) if X.shape[0] else 0

    rows: list[dict] = []
    for (bb, X, Q, nadd) in snapshots:
        adm = X[Q <= Q_ref + tol_ref]                      # ORTAK eşik altında
        params = [replace(base, **{name: float(adm[r, j]) for j, name in enumerate(names)})
                  for r in range(adm.shape[0])]
        ranges = {
            name: ([round(float(adm[:, j].min()), 3), round(float(adm[:, j].max()), 3)]
                   if adm.shape[0] else [None, None])
            for j, name in enumerate(names)
        }
        q_own = float(Q.min())                              # bu bütçenin KÜMÜLATİF minimumu (monoton)
        t_own = identification_tolerance(q_own, rel, absolute)
        prod_count = int((Q <= q_own + t_own).sum())        # üretim-tarzı (kendi hareketli eşiği)
        rows.append({
            "budget": bb,
            "new_candidates_added": nadd,
            "cumulative_candidate_count": int(X.shape[0]),
            "cumulative_unique_candidate_count": _uniq(X),
            "cumulative_best_objective": round(q_own, 6),
            "production_near_fit_count": prod_count,
            "common_threshold_stability_near_fit_count": int(adm.shape[0]),
            "common_threshold_param_ranges": ranges,
            "eta_range": ranges["eta"],
            "admissible_params": params,
        })
    return {
        "budgets": [int(b) for b in budgets],
        "Q_ref": Q_ref,
        "tol_ref": tol_ref,
        "cumulative_best_objective": [round(c, 6) for c in cum_best],
        "best_theta": best_theta,
        "incumbent_reeval_delta": reeval_delta,
        "deterministic_objective_reproducible": bool(reeval_delta <= reeval_tol),
        "rows": rows,
    }
