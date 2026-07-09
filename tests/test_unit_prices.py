"""TCMB birim fiyat (TL/m²) modülü birim testleri (ağ erişimi olmadan)."""

from __future__ import annotations

import pandas as pd
import pytest

from sold.cli import _atomic_to_csv, _validate_refresh_frame

from sold.evds.unit_prices import (
    discover_unit_price_codes,
    fetch_unit_prices,
    latest_by_province,
    province_from_name,
)


class _FakeClient:
    def list_series(self, datagroup: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"SERIE_CODE": "TP.BIRIMFIYAT.TR", "SERIE_NAME": "Türkiye Konut Birim Fiyatları"},
                {"SERIE_CODE": "TP.BIRIMFIYAT.IST", "SERIE_NAME": "İstanbul Konut Birim Fiyatları"},
                {"SERIE_CODE": "TP.BIRIMFIYAT.ANK", "SERIE_NAME": "Ankara Konut Birim Fiyatları"},
            ]
        )

    def get_series(self, codes, start_date, end_date):  # noqa: ANN001
        data = {"date": [pd.Timestamp(2025, 10, 1), pd.Timestamp(2026, 1, 1)]}
        vals = {
            "TP.BIRIMFIYAT.TR": [45447.1, 48290.6],
            "TP.BIRIMFIYAT.IST": [74100.7, 79305.8],
            "TP.BIRIMFIYAT.ANK": [41280.9, 44035.5],
        }
        for c in codes:
            if c in vals:
                data[c] = vals[c]
        return pd.DataFrame(data)


def test_province_from_name():
    assert province_from_name("İstanbul Konut Birim Fiyatları") == "İstanbul"
    assert province_from_name("Türkiye Konut Birim Fiyatları") == "Türkiye"
    assert province_from_name("") is None
    assert province_from_name(None) is None


def test_discover_unit_price_codes():
    codes = discover_unit_price_codes(_FakeClient())
    assert codes == {
        "TP.BIRIMFIYAT.TR": "Türkiye",
        "TP.BIRIMFIYAT.IST": "İstanbul",
        "TP.BIRIMFIYAT.ANK": "Ankara",
    }


def test_fetch_unit_prices_long_format():
    df = fetch_unit_prices(_FakeClient(), start_date="01-01-2025", end_date="01-01-2026")
    assert list(df.columns) == ["province", "period", "tl_m2"]
    ist = df[(df["province"] == "İstanbul") & (df["period"] == pd.Timestamp(2026, 1, 1).date())]
    assert float(ist["tl_m2"].iloc[0]) == 79305.8
    # 3 il × 2 dönem = 6 satır
    assert len(df) == 6


def test_latest_by_province():
    df = pd.DataFrame(
        {
            "province": ["İstanbul", "İstanbul", "Ankara"],
            "period": ["2025-10-01", "2026-01-01", "2026-01-01"],
            "tl_m2": [74100.7, 79305.8, 44035.5],
        }
    )
    latest = latest_by_province(df)
    assert latest["İstanbul"] == 79305.8
    assert latest["Ankara"] == 44035.5


def test_fetch_empty_when_no_series():
    class _Empty:
        def list_series(self, datagroup):  # noqa: ANN001
            return pd.DataFrame()

        def get_series(self, codes, start_date, end_date):  # noqa: ANN001
            return pd.DataFrame()

    df = fetch_unit_prices(_Empty())
    assert df.empty
    assert list(df.columns) == ["province", "period", "tl_m2"]


def test_refresh_rejects_partial_scope_and_date_regression(tmp_path):
    output = tmp_path / "unit_prices.csv"
    existing = pd.DataFrame({
        "province": [f"P{i}" for i in range(77)],
        "period": ["2026-01-01"] * 77,
        "tl_m2": [100.0] * 77,
    })
    existing.to_csv(output, index=False)
    partial = pd.DataFrame({
        "province": ["P0"],
        "period": ["2026-04-01"],
        "tl_m2": [101.0],
    })
    stale = existing.copy()
    stale["period"] = "2025-10-01"

    with pytest.raises(ValueError, match="scope regressed"):
        _validate_refresh_frame(partial, output, scope_column="province", expected_scope=77)
    with pytest.raises(ValueError, match="period regressed"):
        _validate_refresh_frame(stale, output, scope_column="province", expected_scope=77)


def test_atomic_refresh_writes_parseable_csv(tmp_path):
    output = tmp_path / "unit_prices.csv"
    frame = pd.DataFrame({"province": ["Aydın"], "period": ["2026-01-01"], "tl_m2": [46080.6]})

    _atomic_to_csv(frame, output)

    assert pd.read_csv(output).to_dict("records") == frame.to_dict("records")
