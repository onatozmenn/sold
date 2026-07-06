"""TCMB ekspertiz TL/m² konut birim fiyatlarını (EVDS) çeker — GERÇEK fiyat seviyesi.

KFE fiyatların *değişimini* (endeks), bu modül *seviyesini* (TL/m²) verir. İl
bazında, çeyreklik, SPK lisanslı değerleme (ekspertiz) raporlarından. MEVA,
Endeksa ve TCMB'nin dayandığı türden gerçek veri.

Kaynak: EVDS veri grubu ``bie_birimfiyat`` (Konut Birim Fiyatları). Seriler
``TP.BIRIMFIYAT.{il}`` (İstanbul=IST, Ankara=ANK, İzmir=IZM; diğerleri tam ad).
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from .client import EvdsClient
from .series import UNIT_PRICE_DATAGROUP

logger = logging.getLogger(__name__)


def province_from_name(name: str | None) -> str | None:
    """'İstanbul Konut Birim Fiyatları' -> 'İstanbul'."""
    if not name:
        return None
    head = str(name).split(" Konut", 1)[0].strip()
    return head or None


def discover_unit_price_codes(
    client: EvdsClient, datagroup: str = UNIT_PRICE_DATAGROUP
) -> dict[str, str]:
    """Konut birim fiyat serilerini keşfeder ({kod: il adı})."""
    try:
        df = client.list_series(datagroup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Birim fiyat kataloğu alınamadı: %s", exc)
        return {}
    if df.empty:
        return {}

    code_col = next((c for c in df.columns if c.upper() == "SERIE_CODE"), None)
    name_col = next(
        (c for c in df.columns if "SERIE" in c.upper() and "NAME" in c.upper()),
        None,
    )
    if code_col is None:
        return {}

    out: dict[str, str] = {}
    for _, row in df.iterrows():
        code = str(row[code_col]).strip()
        raw = str(row[name_col]).strip() if name_col else code
        province = province_from_name(raw)
        if code and code.lower() != "nan" and province:
            out[code] = province
    return out


def _chunks(seq: list[str], size: int) -> list[list[str]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def fetch_unit_prices(
    client: EvdsClient,
    start_date: str | dt.date = "01-01-2013",
    end_date: str | dt.date | None = None,
    codes: list[str] | None = None,
    name_map: dict[str, str] | None = None,
    batch: int = 20,
) -> pd.DataFrame:
    """Birim fiyatları uzun formatta çeker: ``province, period, tl_m2``.

    ``codes`` verilmezse tüm iller keşfedilir. URL uzunluğu için partiler halinde
    çekilir.
    """
    if end_date is None:
        end_date = dt.date.today()
    if codes is None:
        discovered = discover_unit_price_codes(client)
        codes = list(discovered)
        name_map = discovered
    name_map = name_map or {}

    frames: list[pd.DataFrame] = []
    for chunk in _chunks(codes, batch):
        wide = client.get_series(chunk, start_date, end_date)
        if wide.empty or "date" not in wide.columns:
            continue
        value_cols = [c for c in chunk if c in wide.columns]
        if not value_cols:
            continue
        long = wide.melt(
            id_vars=["date"],
            value_vars=value_cols,
            var_name="series_code",
            value_name="tl_m2",
        ).dropna(subset=["tl_m2"])
        frames.append(long)

    if not frames:
        return pd.DataFrame(columns=["province", "period", "tl_m2"])

    out = pd.concat(frames, ignore_index=True)
    out["province"] = out["series_code"].map(lambda c: name_map.get(c, c))
    out["period"] = pd.to_datetime(out["date"]).dt.date
    out["tl_m2"] = pd.to_numeric(out["tl_m2"], errors="coerce").round(1)
    out = out.dropna(subset=["tl_m2"])
    return (
        out[["province", "period", "tl_m2"]]
        .sort_values(["province", "period"])
        .reset_index(drop=True)
    )


def latest_by_province(unit_prices: pd.DataFrame) -> dict[str, float]:
    """Her il için en güncel TL/m² değerini döndürür."""
    if unit_prices.empty:
        return {}
    latest = unit_prices.sort_values("period").groupby("province").tail(1)
    return {str(r["province"]): float(r["tl_m2"]) for _, r in latest.iterrows()}
