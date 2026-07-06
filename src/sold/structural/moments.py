"""Gözlenen ve simüle momentler — SMM'nin eşleştirdiği istatistikler.

Gözlenen momentler (m_obs) gerçek kamu yapısal verisinden gelir:
- UYAP: satış olasılığı + koşullu kazanan/ekspertiz oranı dağılımı,
- KAP: ilişkisiz müzakereli satışta gerçekleşen/ekspertiz oranı momentleri,
- TOKİ: oda-tipi gerçekleşen kohort + kompozisyon momentleri (agregat).
TCMB ekspertiz-fiyat trendi/birim-fiyat çıpası ve TÜİK işlem-hacmi ``MomentContext``
içinde bağlam (sıkılık + fair value çıpası) olarak taşınır.

Simüle momentler (m_sim(θ)) aynı bağlamda (aynı Q'lar, aynı ekspertizler) yapısal
modeli çalıştırıp AYNI anahtarlı momentleri üretir. ``align`` yalnızca HER İKİSİNDE de
bulunan momentleri eşleştirir; böylece simülatör bir mekanizmayı henüz üretmiyorsa
(ör. TOKİ birincil-piyasa) o moment SMM hedefine girmez ama gözlemde raporlanır.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .auction import auction_moments, simulate_auctions
from .bargaining import simulate_negotiations
from .params import StructuralParams


@dataclass
class MomentContext:
    """SMM için dışsal (exogenous) bağlam — gözlem ile simülasyon AYNI girdileri kullanır."""

    auction_appraised: np.ndarray  # gerçek Q dizisi (muhammen_bedel = ekspertiz)
    auction_floors: np.ndarray     # gerçek yasal tabanlar
    kap_appraisal: np.ndarray      # KAP ekspertiz/emsal-ekspertiz referansları
    tightness: float = 0.0         # TÜİK hacminden piyasa sıkılığı
    reps: int = 200                # her gözlem için simülasyon tekrarı (moment stabilitesi)
    auction_bidders: np.ndarray | None = None  # gözlemli teklif sayısı (KORUNUR)


def _ratio_moments(values: np.ndarray, prefix: str) -> dict:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v) & (v > 0)]
    return {
        f"{prefix}_mean": float(v.mean()) if v.size else float("nan"),
        f"{prefix}_sd": float(v.std()) if v.size > 1 else 0.0,
    }


def _log_ratio_moments(ratio: np.ndarray, prefix: str) -> dict:
    """log(oran) momentleri (KAP: log(sale/appraisal)). mean + sd (SMM eşlemesi)."""
    r = np.asarray(ratio, dtype=float)
    r = r[np.isfinite(r) & (r > 0)]
    lr = np.log(r) if r.size else r
    return {
        f"{prefix}_mean": float(lr.mean()) if lr.size else float("nan"),
        f"{prefix}_sd": float(lr.std()) if lr.size > 1 else 0.0,
    }


def observed_moments(
    auctions: pd.DataFrame | None = None,
    kap_realized=None,
    kap_appraisal=None,
    toki_moments: dict | None = None,
) -> dict:
    """Gerçek yapısal veriden m_obs. Eksik kaynak momentleri atlanır (NaN)."""
    out: dict = {}
    if auctions is not None and len(auctions):
        sold = auctions["sold"].astype(bool).to_numpy()
        Q = pd.to_numeric(auctions["appraised_value"], errors="coerce").to_numpy(float)
        win = pd.to_numeric(auctions["winning_bid"], errors="coerce").to_numpy(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(sold & (Q > 0), win / Q, np.nan)
        out.update(auction_moments(sold, ratio))
    if kap_realized is not None and kap_appraisal is not None:
        r = np.asarray(kap_realized, dtype=float)
        a = np.asarray(kap_appraisal, dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(a > 0, r / a, np.nan)
        out.update(_log_ratio_moments(ratio, "kap_log_ratio"))
    if toki_moments:
        out.update(toki_moments)  # agregat kompozisyon (simülatör eşlemezse SMM'e girmez)
    return out


def simulated_moments(
    params: StructuralParams, ctx: MomentContext, rng: np.random.Generator
) -> dict:
    """Aynı bağlamda yapısal modeli çalıştırıp m_sim(θ) üretir."""
    out: dict = {}
    # --- UYAP açık artırmaları (gerçek Q + yasal taban üzerinde) ---
    if ctx.auction_appraised is not None and len(ctx.auction_appraised):
        Q = np.repeat(np.asarray(ctx.auction_appraised, float), ctx.reps)
        L = np.repeat(np.asarray(ctx.auction_floors, float), ctx.reps)
        bidders = (
            np.repeat(np.asarray(ctx.auction_bidders, int), ctx.reps)
            if ctx.auction_bidders is not None
            else None
        )
        sim = simulate_auctions(rng, Q, L, params, ctx.tightness, fixed_bidders=bidders)
        out.update(auction_moments(sim["sold"], sim["win_over_appraisal"]))
    # --- KAP müzakereli satışlar (kurumsal mekanizma kayması ile) ---
    if ctx.kap_appraisal is not None and len(ctx.kap_appraisal):
        V = np.repeat(np.asarray(ctx.kap_appraisal, float), ctx.reps)
        neg = simulate_negotiations(rng, V, params, len(V), ctx.tightness, mechanism="kap")
        traded = neg["traded"]
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(traded & (V > 0), neg["price"] / V, np.nan)
        out.update(_log_ratio_moments(ratio, "kap_log_ratio"))
    return out


def context_from_datasets(
    auctions: pd.DataFrame | None = None,
    kap: pd.DataFrame | None = None,
    tightness: float = 0.0,
    reps: int = 200,
) -> MomentContext:
    """Gerçek yapısal veri kümelerinden SMM bağlamı kurar (gözlem ile sim AYNI Q/ekspertiz).

    Açık artırma Q ve yasal tabanları (per-auction) ve KAP ekspertiz referansları alınır.
    Teklif sayısı TÜM satırlarda gözlemliyse ``auction_bidders`` olarak korunur; aksi halde
    None (Poisson varış). UYDURMA YOK.
    """
    aQ = np.array([], dtype=float)
    aF = np.array([], dtype=float)
    bidders = None
    if auctions is not None and len(auctions):
        q = pd.to_numeric(auctions["appraised_value"], errors="coerce")
        adf = auctions[q > 0]
        aQ = pd.to_numeric(adf["appraised_value"], errors="coerce").to_numpy(float)
        aF = pd.to_numeric(adf["legal_floor"], errors="coerce").to_numpy(float)
        bc = pd.to_numeric(adf["bidder_count"], errors="coerce")
        if len(adf) and bc.notna().all():
            bidders = bc.to_numpy(int)
    kV = np.array([], dtype=float)
    if kap is not None and len(kap):
        kV = pd.to_numeric(kap["appraisal_value"], errors="coerce").dropna().to_numpy(float)
    return MomentContext(
        auction_appraised=aQ,
        auction_floors=aF,
        kap_appraisal=kV,
        tightness=tightness,
        reps=reps,
        auction_bidders=bidders,
    )


def align(m_obs: dict, m_sim: dict) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Ortak, sonlu momentleri eşleştirir → (obs_vec, sim_vec, keys)."""
    keys = []
    for k in sorted(set(m_obs) & set(m_sim)):
        o, s = m_obs.get(k), m_sim.get(k)
        if o is None or s is None:
            continue
        if not (np.isfinite(o) and np.isfinite(s)):
            continue
        keys.append(k)
    obs = np.array([float(m_obs[k]) for k in keys], dtype=float)
    sim = np.array([float(m_sim[k]) for k in keys], dtype=float)
    return obs, sim, keys
