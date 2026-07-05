"""TÜİK Konut Satış İstatistiklerini (EVDS üzerinden) çeker.

Talep/likidite sinyali: aylık satış adetleri (Türkiye + iller). KFE fiyat
endeksini tamamlar — fiyat *seviyesi* KFE'den, *hacim/talep* buradan gelir.

Kaynak: TÜİK, EVDS veri grubu ``bie_akonutsat1`` (Toplam Satışlar). Seriler
``TP.AKONUTSAT1.KTR{il}`` biçimindedir; ``K`` öneki konutu, öneksiz ``TR...``
iş yerini gösterir.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from .client import EvdsClient
from .series import DEFAULT_HOUSE_SALES_SERIES, HOUSE_SALES_DATAGROUP

logger = logging.getLogger(__name__)


def _is_house_series(code: str) -> bool:
    """Sadece konut serileri (``...KTR...``); iş yeri (``...TR...``) hariç."""
    tail = code.split(".")[-1] if "." in code else code
    return tail.upper().startswith("KTR")


def discover_house_sales_codes(
    client: EvdsClient, datagroup: str = HOUSE_SALES_DATAGROUP
) -> dict[str, str]:
    """Konut satış serilerini keşfeder ({kod: ad}); bulunamazsa boş döner."""
    try:
        df = client.list_series(datagroup)
    except Exception as exc:  # katalog uçları değişebilir  # noqa: BLE001
        logger.warning("Konut satış seri kataloğu alınamadı: %s", exc)
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
        name = str(row[name_col]).strip() if name_col else code
        if code and code.lower() != "nan" and _is_house_series(code):
            out[code] = name
    return out


def province_from_name(name: str | None) -> str | None:
    """'İstanbul_Konut_Toplam Satışlar' -> 'İstanbul'."""
    if not name:
        return None
    head = str(name).split("_", 1)[0].strip()
    return head or None


def fetch_house_sales(
    client: EvdsClient,
    start_date: str | dt.date = "01-01-2013",
    end_date: str | dt.date | None = None,
    codes: list[str] | None = None,
) -> pd.DataFrame:
    """Konut satış serilerini tarih aralığında çeker (geniş format: date + kod sütunları).

    ``codes`` verilmezse Türkiye geneli + büyük iller (DEFAULT_HOUSE_SALES_SERIES)
    çekilir. Tüm iller için ``discover_house_sales_codes`` çıktısını verin.
    """
    if end_date is None:
        end_date = dt.date.today()
    if codes is None:
        codes = list(DEFAULT_HOUSE_SALES_SERIES)

    logger.info("Konut satış serileri çekiliyor: %s", codes)
    return client.get_series(codes, start_date, end_date)


def build_name_map(client: EvdsClient, codes: list[str]) -> dict[str, str]:
    """Kod -> il adı eşlemesi (önce katalog keşfi, sonra varsayılan adlar)."""
    discovered = discover_house_sales_codes(client)
    name_map: dict[str, str] = {}
    for code in codes:
        raw = discovered.get(code) or DEFAULT_HOUSE_SALES_SERIES.get(code)
        province = province_from_name(raw)
        name_map[code] = province or code
    return name_map


def to_long(
    df_wide: pd.DataFrame,
    name_map: dict[str, str],
    sale_type: str = "toplam",
) -> pd.DataFrame:
    """Geniş formatı (date + kod sütunları) uzun forma çevirir.

    Dönen sütunlar: ``province, period, sales_count, sale_type``.
    """
    if df_wide.empty or "date" not in df_wide.columns:
        return pd.DataFrame(columns=["province", "period", "sales_count", "sale_type"])

    value_cols = [c for c in df_wide.columns if c not in ("date", "Tarih")]
    long_df = df_wide.melt(
        id_vars=["date"],
        value_vars=value_cols,
        var_name="series_code",
        value_name="sales_count",
    ).dropna(subset=["sales_count"])

    long_df["province"] = long_df["series_code"].map(lambda c: name_map.get(c, c))
    long_df["period"] = pd.to_datetime(long_df["date"]).dt.date
    long_df["sale_type"] = sale_type
    long_df["sales_count"] = long_df["sales_count"].astype(float).round().astype("Int64")
    return long_df[["province", "period", "sales_count", "sale_type"]].reset_index(
        drop=True
    )
