"""EVDS istemcisi birim testleri (ağ erişimi olmadan)."""

from __future__ import annotations

import pandas as pd
import pytest

from sold.evds.client import EvdsAuthError, EvdsClient, _parse_evds_date


def test_parse_evds_date_variants():
    assert _parse_evds_date("2019") == pd.Timestamp(2019, 1, 1)
    assert _parse_evds_date("2019-3") == pd.Timestamp(2019, 3, 1)
    assert _parse_evds_date("2019-03") == pd.Timestamp(2019, 3, 1)
    assert _parse_evds_date("2020-Q2") == pd.Timestamp(2020, 4, 1)


def test_get_series_parses_underscored_keys(monkeypatch):
    client = EvdsClient(api_key="dummy")
    fake_response = {
        "items": [
            {"Tarih": "2020-1", "TP_KFE01": "100.5", "UNIXTIME": {"$numberLong": "1"}},
            {"Tarih": "2020-2", "TP_KFE01": "101.2", "UNIXTIME": {"$numberLong": "2"}},
        ]
    }
    monkeypatch.setattr(client, "_get_json", lambda url: fake_response)

    df = client.get_series("TP.KFE01", "01-01-2020", "01-03-2020")

    assert "TP.KFE01" in df.columns  # nokta geri eşlendi
    assert df["TP.KFE01"].tolist() == [100.5, 101.2]
    assert df["date"].tolist() == [pd.Timestamp(2020, 1, 1), pd.Timestamp(2020, 2, 1)]


def test_get_series_long_format(monkeypatch):
    client = EvdsClient(api_key="dummy")
    fake_response = {
        "items": [
            {"Tarih": "2020-1", "TP_KFE01": "100.5"},
            {"Tarih": "2020-2", "TP_KFE01": None},
        ]
    }
    monkeypatch.setattr(client, "_get_json", lambda url: fake_response)

    df = client.get_series("TP.KFE01", "01-01-2020", "01-03-2020", long=True)

    assert list(df.columns) == ["date", "Tarih", "series_code", "value"]
    assert len(df) == 1  # None satırı düşer
    assert df.iloc[0]["series_code"] == "TP.KFE01"
    assert df.iloc[0]["value"] == 100.5


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(
        "sold.evds.client.settings.evds_api_key", None, raising=False
    )
    with pytest.raises(EvdsAuthError):
        EvdsClient(api_key=None)
