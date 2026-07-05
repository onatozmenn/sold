"""Talep (market heat) özelliği birim testleri."""

from __future__ import annotations

import pandas as pd

from sold.features.demand import (
    HeatIndex,
    build_heat_table,
    load_heat_index,
    load_house_sales,
)


def _sample_sales() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "province": ["İstanbul"] * 3 + ["Türkiye"] * 3,
            "period": pd.to_datetime(
                ["2020-01-01", "2020-02-01", "2020-03-01"] * 2
            ),
            "sales_count": [100, 200, 300, 1000, 2000, 3000],
            "sale_type": ["toplam"] * 6,
        }
    )


def test_build_heat_table_normalizes_by_province_median():
    table = build_heat_table(_sample_sales())
    ist = table[table["province"] == "İstanbul"].set_index("ym")["market_heat"]
    # İstanbul medyanı 200 → [0.5, 1.0, 1.5]
    assert ist["2020-02"] == 1.0
    assert ist["2020-03"] == 1.5
    assert ist["2020-01"] == 0.5


def test_heat_index_lookup_and_national_fallback():
    idx = HeatIndex(build_heat_table(_sample_sales()))
    assert idx.get("İstanbul", "2020-03") == 1.5
    assert idx.get("İstanbul", "2020-02") == 1.0
    # Bilinmeyen il -> Türkiye geneli (2020-03 için 1.5)
    assert idx.get("Kayseri", "2020-03") == 1.5
    # Bilinmeyen ay -> varsayılan 1.0
    assert idx.get("İstanbul", "1999-01") == 1.0
    assert idx.get(None, None) == 1.0


def test_heat_index_attach_by_province_and_date():
    idx = HeatIndex(build_heat_table(_sample_sales()))
    df = pd.DataFrame(
        {
            "province": ["İstanbul", "Kayseri", "İstanbul"],
            "sale_date": ["2020-03-15", "2020-03-20", None],
        }
    )
    out = idx.attach(df)
    assert out["market_heat"].tolist() == [1.5, 1.5, 1.0]


def test_attach_without_columns_defaults_to_one():
    idx = HeatIndex(build_heat_table(_sample_sales()))
    out = idx.attach(pd.DataFrame({"x": [1, 2]}))
    assert out["market_heat"].tolist() == [1.0, 1.0]


def test_load_house_sales_missing_file(tmp_path):
    assert load_house_sales(tmp_path / "yok.csv").empty


def test_load_heat_index_from_csv(tmp_path):
    path = tmp_path / "hs.csv"
    _sample_sales().to_csv(path, index=False)
    idx = load_heat_index(path)
    assert idx.get("İstanbul", "2020-03") == 1.5


def test_empty_index_returns_default():
    idx = load_heat_index("__yok__.csv")
    assert idx.get("İstanbul", "2020-03") == 1.0
