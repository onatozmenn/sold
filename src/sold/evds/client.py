"""TCMB Elektronik Veri Dağıtım Sistemi (EVDS) REST istemcisi.

API anahtarı: https://evds2.tcmb.gov.tr adresinden ücretsiz üyelik sonrası
Profil > "API Anahtarı" bölümünden alınır ve .env içine EVDS_API_KEY olarak
yazılır.

Not: EVDS uç noktaları parametreleri klasik ``?`` sorgu dizesi yerine yol
sonuna eklenmiş biçimde bekler; bu yüzden URL'ler elle kuruluyor.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Sequence

import httpx
import pandas as pd
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import settings

logger = logging.getLogger(__name__)

EVDS_HELP_URL = "https://evds3.tcmb.gov.tr"


class EvdsError(RuntimeError):
    """EVDS ile ilgili genel hata."""


class EvdsAuthError(EvdsError):
    """Eksik veya geçersiz API anahtarı."""


def _fmt_date(value: str | dt.date | dt.datetime) -> str:
    """EVDS'in beklediği DD-MM-YYYY biçimine çevirir."""
    if isinstance(value, str):
        return value  # zaten DD-MM-YYYY varsayılır
    return value.strftime("%d-%m-%Y")


def _parse_evds_date(raw: object) -> pd.Timestamp:
    """EVDS 'Tarih' alanını Timestamp'e çevirir.

    Desteklenen biçimler: "01-01-2024" (DD-MM-YYYY, yeni API), "2019" (yıllık),
    "2019-3"/"2019-03" (aylık), "2019-Q2" (çeyrek).
    """
    text = str(raw).strip()
    match = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        return pd.Timestamp(year, month, day)
    try:
        if "-" not in text:
            return pd.Timestamp(int(text), 1, 1)
        head, tail = text.split("-", 1)
        year = int(head)
        tail = tail.strip()
        if tail.upper().startswith("Q"):
            quarter = int(tail[1:])
            return pd.Timestamp(year, (quarter - 1) * 3 + 1, 1)
        return pd.Timestamp(year, int(tail), 1)
    except (ValueError, TypeError):
        return pd.to_datetime(text, errors="coerce", dayfirst=True)


def _parse_evds_number(raw: object) -> float:
    """Parse EVDS dot-decimal or Turkish grouped-decimal numeric values."""
    if raw is None:
        return float("nan")
    text = str(raw).strip()
    if not text:
        return float("nan")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return float("nan")


class EvdsClient:
    """TCMB EVDS REST istemcisi (seri verisi + katalog uçları)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or settings.evds_api_key
        if not self.api_key:
            raise EvdsAuthError(
                "EVDS API anahtarı bulunamadı. .env dosyasına EVDS_API_KEY ekleyin. "
                f"Ücretsiz anahtar: {EVDS_HELP_URL}"
            )
        self.base_url = (base_url or settings.evds_base_url).rstrip("/")
        # Güvenlik: httpx istek loglaması istek URL'sini yazabilir → sustur.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        # EVDS3: API anahtarı HTTP header'da gönderilir (URL'de değil).
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "sold/0.1 (+research)", "key": self.api_key},
            follow_redirects=True,
        )

    def __enter__(self) -> "EvdsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _redact(self, text: str) -> str:
        """API anahtarını hata/çıktı metinlerinden temizler (sızıntı önleme)."""
        if self.api_key and self.api_key in text:
            return text.replace(self.api_key, "***")
        return text

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _raw_get(self, url: str) -> httpx.Response:
        return self._client.get(url)

    def _get_json(self, url: str) -> dict | list:
        # httpx istisnaları istek URL'sini (dolayısıyla anahtarı) içerebilir;
        # bu yüzden tüm hata metinleri _redact ile temizlenir ve zincir (from
        # None) kesilir ki traceback URL'yi göstermesin.
        try:
            resp = self._raw_get(url)
        except httpx.HTTPError as exc:
            raise EvdsError(f"EVDS isteği başarısız: {self._redact(str(exc))}") from None
        if resp.status_code in (401, 403):
            raise EvdsAuthError(
                "EVDS anahtarı reddedildi (401/403). Anahtarınızı kontrol edin."
            )
        if resp.status_code >= 400:
            raise EvdsError(f"EVDS HTTP {resp.status_code} döndü.")
        try:
            return resp.json()
        except ValueError:
            raise EvdsError(
                f"EVDS beklenmeyen (JSON olmayan) yanıt: {self._redact(resp.text[:200])!r}"
            ) from None

    # ------------------------------------------------------------------ #
    # Katalog uçları
    # ------------------------------------------------------------------ #
    def list_datagroups(self, mode: int = 0) -> pd.DataFrame:
        """Tüm veri gruplarını listeler."""
        url = f"{self.base_url}/datagroups/mode={mode}&code=&type=json"
        data = self._get_json(url)
        return _to_frame(data)

    def list_series(self, datagroup_code: str) -> pd.DataFrame:
        """Bir veri grubundaki (ör. 'bie_kfe') tüm serileri listeler."""
        url = f"{self.base_url}/serieList/type=json&code={datagroup_code}"
        data = self._get_json(url)
        return _to_frame(data)

    def search_series(self, datagroup_code: str, keyword: str) -> pd.DataFrame:
        """Veri grubu içindeki serileri isme göre filtreler."""
        df = self.list_series(datagroup_code)
        if df.empty:
            return df
        name_col = next(
            (c for c in df.columns if "SERIE" in c.upper() and "NAME" in c.upper()),
            None,
        )
        if name_col is None:
            return df
        mask = df[name_col].astype(str).str.contains(keyword, case=False, na=False)
        return df[mask].reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # Seri verisi
    # ------------------------------------------------------------------ #
    def get_series(
        self,
        codes: str | Sequence[str],
        start_date: str | dt.date,
        end_date: str | dt.date,
        frequency: int | None = None,
        aggregation: str | None = None,
        long: bool = False,
    ) -> pd.DataFrame:
        """Bir veya birden çok seriyi tarih aralığında çeker.

        ``long=True`` verilirse (series_code, value) uzun formatında döner;
        aksi halde her seri ayrı bir sütundur (geniş format).
        """
        if isinstance(codes, str):
            codes = [codes]
        codes = list(codes)

        url = (
            f"{self.base_url}/series={'-'.join(codes)}"
            f"&startDate={_fmt_date(start_date)}&endDate={_fmt_date(end_date)}"
            f"&type=json"
        )
        if frequency is not None:
            url += f"&frequency={frequency}"
        if aggregation is not None:
            url += f"&aggregationTypes={aggregation}"

        data = self._get_json(url)
        items = data.get("items", []) if isinstance(data, dict) else data
        df = pd.DataFrame(items)
        if df.empty:
            return df

        if "Tarih" in df.columns:
            df["date"] = df["Tarih"].map(_parse_evds_date)

        # EVDS JSON'da kod içindeki '.' -> '_' olur. İstenen kodlara geri eşle.
        rename = {code.replace(".", "_"): code for code in codes}
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        for code in codes:
            if code in df.columns:
                df[code] = df[code].map(_parse_evds_number)

        keep = [c for c in ("date", "Tarih") if c in df.columns]
        keep += [c for c in codes if c in df.columns]
        df = df[keep]
        if "date" in df.columns:
            df = df.sort_values("date").reset_index(drop=True)

        if long:
            value_cols = [c for c in codes if c in df.columns]
            id_cols = [c for c in ("date", "Tarih") if c in df.columns]
            df = (
                df.melt(
                    id_vars=id_cols,
                    value_vars=value_cols,
                    var_name="series_code",
                    value_name="value",
                )
                .dropna(subset=["value"])
                .reset_index(drop=True)
            )
        return df


def _to_frame(data: dict | list) -> pd.DataFrame:
    """EVDS katalog yanıtını (list veya {'items': [...]}) DataFrame'e çevirir."""
    if isinstance(data, list):
        return pd.DataFrame(data)
    if isinstance(data, dict):
        return pd.DataFrame(data.get("items", [data]))
    return pd.DataFrame()
