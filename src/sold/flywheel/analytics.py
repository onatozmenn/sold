"""Broker müzakere (negotiation) analitiği — NON-ML, anında değer üretir.

Aynı fonksiyon hem broker'ın KENDİ portföyü hem de agregat anonim BENCHMARK için
kullanılır; ``benchmark_comparison`` ikisini kıyaslar (data flywheel'in geri
verdiği "değer" budur: emlakçı closing girer, karşılığında analitik alır).

Metrikler: işlem sayısı, ilan→satış indirimi (medyan/ortalama), kapanış süresi,
fiyat-kesinti sayısı ve fiyat-kesinti durumuna göre indirim.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..groundtruth.loader import ARM_LENGTH

SALE_OUTCOME = "sold"


def _median(series: pd.Series | None) -> float:
    if series is None:
        return float("nan")
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.median()) if len(s) else float("nan")


def _mean(series: pd.Series | None) -> float:
    if series is None:
        return float("nan")
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.mean()) if len(s) else float("nan")


def _sold_arm_length(df: pd.DataFrame) -> pd.DataFrame:
    """İndirim analizine giren alt küme: satılmış + arm_length + geçerli fiyatlar."""
    if df.empty:
        return df
    m = df
    if "outcome" in m.columns:
        m = m[m["outcome"] == SALE_OUTCOME]
    if "sale_mode" in m.columns:
        m = m[m["sale_mode"].fillna(ARM_LENGTH).astype(str) == ARM_LENGTH]
    for col in ("sold_price", "last_asking_price"):
        if col in m.columns:
            m = m[pd.to_numeric(m[col], errors="coerce") > 0]
    return m.reset_index(drop=True)


def negotiation_analytics(df: pd.DataFrame) -> dict:
    """Verilen ilan-sonuç kümesinden müzakere istatistikleri (satış alt kümesi)."""
    total = int(len(df))
    outcome_counts = (
        df["outcome"].value_counts().to_dict() if "outcome" in df.columns else {}
    )
    sold = _sold_arm_length(df)
    n = int(len(sold))

    base = {
        "transaction_count": n,
        "total_outcomes": total,
        "outcome_counts": outcome_counts,
        "median_discount_pct": float("nan"),
        "mean_discount_pct": float("nan"),
        "median_days_to_close": float("nan"),
        "mean_days_to_close": float("nan"),
        "median_price_cuts": float("nan"),
        "mean_price_cuts": float("nan"),
        "discount_by_price_cut": {},
    }
    if n == 0:
        return base

    ask = pd.to_numeric(sold["last_asking_price"], errors="coerce").to_numpy(float)
    sale = pd.to_numeric(sold["sold_price"], errors="coerce").to_numpy(float)
    discount = (1.0 - sale / ask) * 100.0

    days = sold.get("days_to_close")
    if days is None or pd.to_numeric(days, errors="coerce").notna().sum() == 0:
        days = sold.get("days_on_market")
    cuts = pd.to_numeric(sold.get("price_cut_count"), errors="coerce")

    disc_by_cut: dict[str, dict] = {}
    if cuts is not None and cuts.notna().any():
        cut_vals = cuts.fillna(0).to_numpy()
        for key, mask in (("no_cut", cut_vals == 0), ("with_cut", cut_vals > 0)):
            if mask.any():
                disc_by_cut[key] = {
                    "count": int(mask.sum()),
                    "mean_discount_pct": float(np.mean(discount[mask])),
                }

    base.update(
        {
            "median_discount_pct": float(np.median(discount)),
            "mean_discount_pct": float(np.mean(discount)),
            "median_days_to_close": _median(days),
            "mean_days_to_close": _mean(days),
            "median_price_cuts": _median(cuts),
            "mean_price_cuts": _mean(cuts),
            "discount_by_price_cut": disc_by_cut,
        }
    )
    return base


def benchmark_comparison(
    broker_df: pd.DataFrame, benchmark_df: pd.DataFrame
) -> dict:
    """Broker performansı vs agregat anonim benchmark (aynı metrikler + delta).

    Mimari kanca: ``benchmark_df`` ileride tüm anonimleştirilmiş veri kümesi olur;
    ``broker_df`` ise tek bir broker'ın kayıtları. Şimdilik ikisi de aynı
    fonksiyondan geçer, böylece kıyas hazır durur.
    """
    b = negotiation_analytics(broker_df)
    a = negotiation_analytics(benchmark_df)
    delta: dict[str, float] = {}
    for k in ("median_discount_pct", "mean_discount_pct", "median_days_to_close"):
        bv, av = b.get(k), a.get(k)
        if bv == bv and av == av:  # ikisi de NaN değil
            delta[k] = round(float(bv) - float(av), 2)
    return {"broker": b, "benchmark": a, "delta": delta}
