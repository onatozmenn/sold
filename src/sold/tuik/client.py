"""TÜİK veri portalından (SDMX / toplu indirme) CSV yükleme.

TÜİK, Haziran 2026 itibarıyla SDMX web servisi ve toplu CSV/XML/JSON indirme
sunuyor (veriportali.tuik.gov.tr). En dayanıklı başlangıç yolu, portalden
indirilen ya da bağlantısı verilen CSV'yi DataFrame'e yüklemektir; tam SDMX
entegrasyonu sonraki adım.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

TUIK_PORTAL = "https://veriportali.tuik.gov.tr"


def load_csv(
    path_or_url: str,
    sep: str | None = None,
    encoding: str = "utf-8",
) -> pd.DataFrame:
    """Yerel yoldan veya URL'den CSV yükler.

    ``sep=None`` ile ayraç otomatik sezilir (python engine).
    """
    if path_or_url.startswith(("http://", "https://")):
        resp = httpx.get(path_or_url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        return pd.read_csv(
            io.BytesIO(resp.content), sep=sep, engine="python", encoding=encoding
        )
    return pd.read_csv(path_or_url, sep=sep, engine="python", encoding=encoding)
