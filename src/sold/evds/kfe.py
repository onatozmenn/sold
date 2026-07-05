"""Faz 0: TCMB KFE (ekspertiz tabanlı konut değeri) verisini çeker.

Kalibrasyon çıpası: ilan verisinden üretilen 'realized' tahminleri agregada
bu ekspertiz-tabanlı endekse oturtmak için kullanılır.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from .client import EvdsClient
from .series import DEFAULT_KFE_SERIES, KFE_DATAGROUP

logger = logging.getLogger(__name__)


def discover_turkiye_kfe_codes(client: EvdsClient) -> dict[str, str]:
    """KFE veri grubundaki serileri keşfeder ({kod: ad}); bulunamazsa boş döner."""
    try:
        df = client.list_series(KFE_DATAGROUP)
    except Exception as exc:  # katalog uçları bazen değişebilir  # noqa: BLE001
        logger.warning("KFE seri kataloğu alınamadı: %s", exc)
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
        if code and code.lower() != "nan":
            out[code] = name
    return out


def fetch_kfe(
    client: EvdsClient,
    start_date: str | dt.date = "01-01-2010",
    end_date: str | dt.date | None = None,
    codes: list[str] | None = None,
    prefer_discovery: bool = True,
) -> pd.DataFrame:
    """KFE serilerini çeker.

    Sıra: (1) verilen ``codes``, (2) katalogdan keşif (Türkiye geneli öncelikli),
    (3) DEFAULT_KFE_SERIES. Böylece seri kodu değişse bile kod kırılmaz.
    """
    if end_date is None:
        end_date = dt.date.today()

    if codes is None:
        codes = []
        if prefer_discovery:
            discovered = discover_turkiye_kfe_codes(client)
            for code, name in discovered.items():
                upper = name.upper()
                if (
                    "TÜRKİYE" in upper
                    or "TURKIYE" in upper
                    or code.upper().endswith(".TR")
                ):
                    codes.append(code)
            if not codes and discovered:
                codes = list(discovered)[:5]
        if not codes:
            logger.info("Keşif başarısız; varsayılan seri kodları kullanılıyor.")
            codes = list(DEFAULT_KFE_SERIES)

    logger.info("KFE serileri çekiliyor: %s", codes)
    return client.get_series(codes, start_date, end_date)
