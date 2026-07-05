"""Gerçek etiketlerden 'sold-to-list' indirim analizi (Faz 4+).

Ekonomi hocasının asıl sorusu: ilan (asking) ile gerçekleşen (sold) fiyat
arasındaki fark ne? Bu modül, etiketli satışlardan bu gerçek indirimi
(1 − sold/asking) genel ve segment (ilçe, fiyat bandı) bazında çıkarır.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_PRICE_BANDS = [0, 1_500_000, 3_000_000, 6_000_000, np.inf]
DEFAULT_PRICE_LABELS = ["<1.5M", "1.5-3M", "3-6M", ">6M"]


def discount_summary(
    frame: pd.DataFrame,
    price_bands: list[float] | None = None,
    price_labels: list[str] | None = None,
) -> dict:
    """asking→sold indirim (%) dağılımını genel + ilçe + fiyat bandında özetler."""
    df = frame.dropna(subset=["last_price", "true_realized_price"]).copy()
    df = df[df["last_price"].astype(float) > 0]

    if df.empty:
        return {
            "overall": {
                "count": 0,
                "mean_pct": float("nan"),
                "median_pct": float("nan"),
                "p25_pct": float("nan"),
                "p75_pct": float("nan"),
            },
            "by_district": pd.DataFrame(),
            "by_price_band": pd.DataFrame(),
        }

    last = df["last_price"].astype(float)
    sold = df["true_realized_price"].astype(float)
    df["discount_pct"] = (1 - sold / last) * 100

    overall = {
        "count": int(len(df)),
        "mean_pct": float(df["discount_pct"].mean()),
        "median_pct": float(df["discount_pct"].median()),
        "p25_pct": float(df["discount_pct"].quantile(0.25)),
        "p75_pct": float(df["discount_pct"].quantile(0.75)),
    }

    by_district = pd.DataFrame()
    if "district" in df.columns and df["district"].notna().any():
        by_district = (
            df.groupby("district")["discount_pct"]
            .agg(count="count", mean="mean")
            .reset_index()
            .sort_values("mean", ascending=False)
            .reset_index(drop=True)
        )

    bands = price_bands or DEFAULT_PRICE_BANDS
    labels = price_labels or DEFAULT_PRICE_LABELS
    df["price_band"] = pd.cut(last, bins=bands, labels=labels)
    by_price_band = (
        df.groupby("price_band", observed=False)["discount_pct"]
        .agg(count="count", mean="mean")
        .reset_index()
    )

    return {
        "overall": overall,
        "by_district": by_district,
        "by_price_band": by_price_band,
    }
