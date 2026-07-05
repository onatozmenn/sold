"""TÜİK konut satış hacminden 'market heat' (talep) özelliği üretir.

market_heat[il, ay] = o ilin o aydaki satış adedi / ilin uzun dönem medyanı.
~1.0 normal, >1 piyasa hareketli (talep yüksek → daha az indirim, hızlı satış),
<1 durgun (daha çok pazarlık). İl bazında normalize edildiği için İstanbul ile
Antalya kıyaslanabilir. İl bulunamazsa Türkiye geneli, o da yoksa 1.0'a düşer.

Kaynak: datasets/house_sales.csv (province, period, sales_count, sale_type) —
haftalık Action ile TÜİK'ten otomatik güncellenir.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

DEFAULT_HOUSE_SALES_CSV = Path("datasets/house_sales.csv")
_NATIONAL = {"Türkiye", "Turkiye"}


def load_house_sales(path: str | Path = DEFAULT_HOUSE_SALES_CSV) -> pd.DataFrame:
    """house_sales.csv'yi okur; yoksa/boşsa boş çerçeve döner (hata vermez)."""
    path = Path(path)
    cols = ["province", "period", "sales_count", "sale_type"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path)
    if df.empty or "period" not in df.columns:
        return pd.DataFrame(columns=cols)
    df["period"] = pd.to_datetime(df["period"], errors="coerce")
    df["sales_count"] = pd.to_numeric(df["sales_count"], errors="coerce")
    return df.dropna(subset=["period", "sales_count"]).reset_index(drop=True)


def build_heat_table(sales: pd.DataFrame, sale_type: str = "toplam") -> pd.DataFrame:
    """(province, ym, market_heat) tablosu. ``ym`` = 'YYYY-MM'."""
    empty = pd.DataFrame(columns=["province", "ym", "market_heat"])
    if sales.empty:
        return empty
    df = sales
    if "sale_type" in df.columns:
        sub = df[df["sale_type"] == sale_type]
        df = sub if not sub.empty else df
    df = df.copy()
    med = df.groupby("province")["sales_count"].transform("median")
    df["market_heat"] = (df["sales_count"] / med).where(med > 0, 1.0)
    df["ym"] = pd.to_datetime(df["period"]).dt.strftime("%Y-%m")
    return df[["province", "ym", "market_heat"]].reset_index(drop=True)


class HeatIndex:
    """(il, ay) -> market_heat araması; il/ay yoksa Türkiye geneli → 1.0."""

    def __init__(self, table: pd.DataFrame) -> None:
        self._lut: dict[tuple[str, str], float] = {}
        self._national: dict[str, float] = {}
        for _, r in table.iterrows():
            province, ym, heat = str(r["province"]), str(r["ym"]), float(r["market_heat"])
            self._lut[(province, ym)] = heat
            if province in _NATIONAL:
                self._national[ym] = heat

    def get(self, province: object, ym: object, default: float = 1.0) -> float:
        if province is not None and ym is not None and not pd.isna(province):
            hit = self._lut.get((str(province), str(ym)))
            if hit is not None:
                return hit
            nat = self._national.get(str(ym))
            if nat is not None:
                return nat
        return default

    def attach(
        self,
        df: pd.DataFrame,
        province_col: str = "province",
        date_col: str = "sale_date",
        out_col: str = "market_heat",
        default: float = 1.0,
    ) -> pd.DataFrame:
        """``out_col`` sütununu (il + satış ayına göre) ekler; kopya döndürür."""
        df = df.copy()
        n = len(df)
        prov = (
            df[province_col] if province_col in df.columns else pd.Series([None] * n)
        ).reset_index(drop=True)
        if date_col in df.columns:
            ym = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m")
            ym = ym.reset_index(drop=True)
        else:
            ym = pd.Series([None] * n)
        df[out_col] = [self.get(p, m, default) for p, m in zip(prov, ym)]
        return df


@lru_cache(maxsize=8)
def _load_cached(path_str: str, sale_type: str) -> HeatIndex:
    return HeatIndex(build_heat_table(load_house_sales(path_str), sale_type))


def load_heat_index(
    path: str | Path = DEFAULT_HOUSE_SALES_CSV, sale_type: str = "toplam"
) -> HeatIndex:
    """market_heat araması yükler (dosya yoksa hepsi 1.0 döndüren boş indeks)."""
    return _load_cached(str(path), sale_type)
