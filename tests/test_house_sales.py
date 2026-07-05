"""TÜİK konut satış modülü birim testleri (ağ erişimi olmadan)."""

from __future__ import annotations

import pandas as pd

from sold.evds.house_sales import (
    _is_house_series,
    build_name_map,
    discover_house_sales_codes,
    fetch_house_sales,
    province_from_name,
    to_long,
)
from sold.evds.series import DEFAULT_HOUSE_SALES_SERIES


class _FakeClient:
    """list_series/get_series'i taklit eden sahte istemci (ağ yok)."""

    def __init__(self) -> None:
        self.captured_codes: list[str] | None = None

    def list_series(self, datagroup: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "SERIE_CODE": "TP.AKONUTSAT1.KTRTOPLAM",
                    "SERIE_NAME": "Türkiye_Konut_Toplam Satışlar",
                },
                {
                    "SERIE_CODE": "TP.AKONUTSAT1.KTR100",
                    "SERIE_NAME": "İstanbul_Konut_Toplam Satışlar",
                },
                {  # iş yeri serisi — konut değil, dışlanmalı
                    "SERIE_CODE": "TP.AKONUTSAT1.TRTOPLAM",
                    "SERIE_NAME": "Türkiye_İş Yeri_Toplam Satışlar",
                },
            ]
        )

    def get_series(self, codes, start_date, end_date):  # noqa: ANN001
        self.captured_codes = list(codes)
        return pd.DataFrame(
            {
                "date": [pd.Timestamp(2013, 1, 1), pd.Timestamp(2013, 2, 1)],
                "TP.AKONUTSAT1.KTRTOPLAM": [92594.0, 93757.0],
            }
        )


def test_is_house_series_filters_commercial():
    assert _is_house_series("TP.AKONUTSAT1.KTRTOPLAM")
    assert _is_house_series("TP.AKONUTSAT1.KTR100")
    assert not _is_house_series("TP.AKONUTSAT1.TRTOPLAM")  # iş yeri
    assert not _is_house_series("TP.AKONUTSAT1.TR100")


def test_province_from_name():
    assert province_from_name("İstanbul_Konut_Toplam Satışlar") == "İstanbul"
    assert province_from_name("Türkiye_Konut_Toplam Satışlar") == "Türkiye"
    assert province_from_name("") is None
    assert province_from_name(None) is None


def test_discover_house_sales_codes_excludes_commercial():
    codes = discover_house_sales_codes(_FakeClient())
    assert set(codes) == {"TP.AKONUTSAT1.KTRTOPLAM", "TP.AKONUTSAT1.KTR100"}
    assert "TP.AKONUTSAT1.TRTOPLAM" not in codes


def test_fetch_house_sales_uses_default_codes():
    client = _FakeClient()
    df = fetch_house_sales(client, start_date="01-01-2013", end_date="01-03-2013")
    assert client.captured_codes == list(DEFAULT_HOUSE_SALES_SERIES)
    assert "TP.AKONUTSAT1.KTRTOPLAM" in df.columns


def test_build_name_map_maps_codes_to_provinces():
    client = _FakeClient()
    name_map = build_name_map(client, ["TP.AKONUTSAT1.KTRTOPLAM", "TP.AKONUTSAT1.KTR100"])
    assert name_map["TP.AKONUTSAT1.KTRTOPLAM"] == "Türkiye"
    assert name_map["TP.AKONUTSAT1.KTR100"] == "İstanbul"


def test_to_long_shape_and_values():
    df_wide = pd.DataFrame(
        {
            "date": [pd.Timestamp(2013, 1, 1), pd.Timestamp(2013, 2, 1)],
            "TP.AKONUTSAT1.KTRTOPLAM": [92594.0, 93757.0],
            "TP.AKONUTSAT1.KTR100": [17700.0, float("nan")],  # NaN düşmeli
        }
    )
    name_map = {
        "TP.AKONUTSAT1.KTRTOPLAM": "Türkiye",
        "TP.AKONUTSAT1.KTR100": "İstanbul",
    }
    long_df = to_long(df_wide, name_map)

    assert list(long_df.columns) == ["province", "period", "sales_count", "sale_type"]
    assert len(long_df) == 3  # 2 Türkiye + 1 İstanbul (NaN satırı düştü)
    assert (long_df["sale_type"] == "toplam").all()
    tr = long_df[long_df["province"] == "Türkiye"].sort_values("period")
    assert tr["sales_count"].tolist() == [92594, 93757]
    assert long_df["period"].iloc[0] == pd.Timestamp(2013, 1, 1).date()


def test_to_long_empty_frame():
    empty = pd.DataFrame()
    out = to_long(empty, {})
    assert list(out.columns) == ["province", "period", "sales_count", "sale_type"]
    assert out.empty
