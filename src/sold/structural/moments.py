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

from .auction import auction_moments, simulate_auctions, uyap_observed_moments
from .bargaining import simulate_negotiations
from .kap import kap_observed_moments
from .params import StructuralParams
from .toki import toki_composition_moments


@dataclass
class MomentContext:
    """SMM için dışsal (exogenous) bağlam — gözlem ile simülasyon AYNI girdileri kullanır."""

    auction_appraised: np.ndarray  # gerçek Q dizisi (muhammen_bedel = ekspertiz)
    auction_floors: np.ndarray     # gerçek yasal tabanlar
    kap_appraisal: np.ndarray      # KAP ekspertiz/emsal-ekspertiz referansları
    auction_floor_exact: np.ndarray | None = None  # yalnız True ise kabul eşiği gözlenmiştir
    auction_floor_upper: np.ndarray | None = None  # kısmî taban için gözlenen tamamlanmış-fiyat üst sınırı
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
        f"{prefix}_sd": float(lr.std()) if lr.size > 1 else float("nan"),
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
        floors = np.asarray(ctx.auction_floors, float)
        if ctx.auction_floor_exact is None:
            exact = np.ones(len(floors), dtype=bool)
        else:
            exact = np.asarray(ctx.auction_floor_exact, bool)
        upper = (
            np.asarray(ctx.auction_floor_upper, float)
            if ctx.auction_floor_upper is not None
            else np.full(len(floors), np.nan)
        )
        threshold_blocks = []
        midpoint_fractions = (np.arange(ctx.reps, dtype=float) + 0.5) / ctx.reps
        for lower, upper_bound, is_exact in zip(floors, upper, exact):
            if is_exact:
                threshold_blocks.append(np.full(ctx.reps, lower))
            elif np.isfinite(upper_bound) and upper_bound >= lower:
                # Exact floor interval-censored: lower <= floor <= observed winning bid.
                threshold_blocks.append(lower + midpoint_fractions * (upper_bound - lower))
            else:
                threshold_blocks.append(np.full(ctx.reps, np.nan))
        L = np.concatenate(threshold_blocks) if threshold_blocks else np.array([], dtype=float)
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
    floor_exact = np.array([], dtype=bool)
    floor_upper = np.array([], dtype=float)
    bidders = None
    if auctions is not None and len(auctions):
        q = pd.to_numeric(auctions["appraised_value"], errors="coerce")
        adf = auctions[q > 0]
        aQ = pd.to_numeric(adf["appraised_value"], errors="coerce").to_numpy(float)
        aF = pd.to_numeric(adf["legal_floor"], errors="coerce").to_numpy(float)
        floor_exact = adf["legal_floor_exact"].fillna(False).astype(bool).to_numpy()
        floor_upper = pd.to_numeric(adf["winning_bid"], errors="coerce").to_numpy(float)
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
        auction_floor_exact=floor_exact,
        auction_floor_upper=floor_upper,
        tightness=tightness,
        reps=reps,
        auction_bidders=bidders,
    )


def _add_moment(
    moments: dict,
    provenance: dict,
    unavailable: list,
    key: str,
    value: object,
    source: str,
    reason: str,
) -> None:
    if value is not None and np.isfinite(value):
        moments[key] = float(value)
        provenance[key] = source
    else:
        unavailable.append({"moment": key, "source": source, "reason": reason})


def build_observed_moments(
    uyap: pd.DataFrame | None = None,
    kap: pd.DataFrame | None = None,
    toki_result: dict | None = None,
) -> dict:
    """GERÇEK denetlenmiş veriden m_obs + moment PROVENANCE + unavailable (neden).

    Hesaplanamayan moment (yetersiz gözlem) UYDURULMAZ; ``unavailable`` listesine nedeniyle
    yazılır. Böylece ``m_obs`` gerçek veriye dinamik uyar ve identification raporu hangi
    kaynak ailesinin hangi momente katkı verdiğini gösterebilir.
    """
    moments: dict = {}
    provenance: dict = {}
    unavailable: list = []
    ineligible: list = []
    sample_sizes: dict = {}

    # --- UYAP ---
    # DİKKAT: uyap_sale_prob SMM'den KALDIRILDI. Gerçek e-Satış üst-düzey taksonomisi
    # (Satıldı / Birinci Alıcıya Süre Verildi / Malın Satışının Düşmesi / İhale Sonucu
    # Girilmemiştir) KARŞILAŞTIRILABİLİR bir negatif açık-artırma ticaret sınıfını AYIRAMIYOR
    # (geri-çekilme/iptal/uzlaşma-bekleyen/eksik sonuç ≠ piyasa no-trade). Bu yüzden
    # sale_prob savunulabilir bir popülasyon momenti DEĞİLdİr → ineligible (uydurulmaz).
    if uyap is not None and len(uyap):
        um = uyap_observed_moments(uyap)
        n = int(len(uyap))
        n_sold = int(uyap["sold"].astype(bool).sum())
        sample_sizes["uyap_win_over_appraisal_mean"] = n_sold
        sample_sizes["uyap_win_over_appraisal_sd"] = n_sold
        _sp = um.get("uyap_sale_prob")
        ineligible.append({
            "moment": "uyap_sale_prob", "source": "uyap",
            "observed_value": float(_sp) if _sp is not None and np.isfinite(_sp) else None,
            "reason": "public UYAP outcome taxonomy does not currently identify a comparable negative auction trade class",
        })
        # KOŞULLU-TİCARET momentleri: winning_bid/appraised_value | GÖZLENEN TAMAMLANMIŞ satış
        # (koşulsuz açık-artırma piyasa momenti OLARAK yorumlanmaz)
        _add_moment(moments, provenance, unavailable, "uyap_win_over_appraisal_mean",
                    um.get("uyap_win_over_appraisal_mean"), "uyap", f"needs>=1 sold, have {n_sold}")
        var = um.get("uyap_win_over_appraisal_var")
        sd = float(np.sqrt(var)) if (var is not None and np.isfinite(var) and n_sold >= 2) else None
        _add_moment(moments, provenance, unavailable, "uyap_win_over_appraisal_sd",
                    sd, "uyap", f"needs>=2 sold, have {n_sold}")
    else:
        for k in ("uyap_win_over_appraisal_mean", "uyap_win_over_appraisal_sd"):
            sample_sizes[k] = 0
            unavailable.append({"moment": k, "source": "uyap", "reason": "no_uyap_observations"})

    # --- KAP (negotiated-calibration subset) ---
    if kap is not None and len(kap):
        km = kap_observed_moments(kap, negotiated_only=True)
        kn = int(km.get("kap_n", 0))
        sample_sizes["kap_log_ratio_mean"] = kn
        sample_sizes["kap_log_ratio_sd"] = kn
        _add_moment(moments, provenance, unavailable, "kap_log_ratio_mean",
                    km.get("kap_log_ratio_mean"), "kap", f"needs>=1 negotiated, have {kn}")
        _add_moment(moments, provenance, unavailable, "kap_log_ratio_sd",
                    km.get("kap_log_ratio_sd"), "kap", f"needs>=2 negotiated, have {kn}")
    else:
        for k in ("kap_log_ratio_mean", "kap_log_ratio_sd"):
            sample_sizes[k] = 0
            unavailable.append({"moment": k, "source": "kap", "reason": "no_kap_observations"})

    # --- TOKİ agregat kohortlar ---
    n_cohorts = int(len(toki_result.get("cohorts", []))) if toki_result else 0
    sample_sizes["toki_cohort_moments"] = n_cohorts
    if toki_result and toki_result.get("cohorts"):
        for k, v in toki_composition_moments(toki_result["cohorts"]).items():
            if isinstance(v, (int, float)) and np.isfinite(v):
                moments[k] = float(v)
                provenance[k] = "toki"
                sample_sizes[k] = n_cohorts
    else:
        unavailable.append({
            "moment": "toki_cohort_moments", "source": "toki",
            "reason": "no_derivable_period_cohorts (needs >=2 consecutive consistent disclosures)",
        })

    return {
        "moments": moments,
        "provenance": provenance,
        "unavailable": unavailable,
        "ineligible": ineligible,
        "sample_sizes": sample_sizes,
        "moment_semantics": {
            "uyap_win_over_appraisal_mean": "winning_bid/appraised_value | observed completed auction sale (conditional-on-trade, NOT unconditional)",
            "uyap_win_over_appraisal_sd": "winning_bid/appraised_value | observed completed auction sale (conditional-on-trade, NOT unconditional)",
        },
    }


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
