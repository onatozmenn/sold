"""Saygılı (rate-limited, robots.txt uyumlu) temel scraper.

ÖNEMLİ HUKUKİ / ETİK NOTLAR
---------------------------
- Her sitenin Kullanım Koşulları (ToS) ve robots.txt kurallarına UYUN.
- KVKK: İlan sahibinin adı, telefonu vb. KİŞİSEL VERİ TOPLANMAZ/SAKLANMAZ.
  Yalnızca taşınmazın nesnel nitelikleri (m², oda, konum, fiyat) tutulur.
- Siteye yük bindirmeyin: düşük hız + jitter + robots.txt kontrolü.
- Bu sınıf yalnızca *altyapı* sağlar; siteye özgü ``parse`` metodunu, ilgili
  sitenin ToS'una uygun şekilde siz yazarsınız.
"""

from __future__ import annotations

import logging
import random
import time
import urllib.robotparser
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ListingRecord:
    """Bir tarama turunda bir ilandan çıkarılan (kişisel-veri-içermeyen) kayıt."""

    source: str
    source_listing_id: str
    price: float
    currency: str = "TRY"
    url: str | None = None
    listing_type: str | None = None  # sale | rent
    province: str | None = None
    district: str | None = None
    neighborhood: str | None = None
    lat: float | None = None
    lon: float | None = None
    gross_m2: float | None = None
    net_m2: float | None = None
    room_count: str | None = None
    building_age: int | None = None
    floor: int | None = None
    total_floors: int | None = None
    heating: str | None = None
    extra: dict = field(default_factory=dict)


class RespectfulScraper(ABC):
    """robots.txt + hız sınırı uygulayan temel sınıf."""

    source_name: str = "unknown"

    def __init__(
        self,
        user_agent: str | None = None,
        min_delay: float | None = None,
        max_delay: float | None = None,
    ) -> None:
        self.user_agent = user_agent or settings.scraper_user_agent
        self.min_delay = settings.scraper_min_delay if min_delay is None else min_delay
        self.max_delay = settings.scraper_max_delay if max_delay is None else max_delay
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._client = httpx.Client(
            timeout=30.0,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        )

    def __enter__(self) -> "RespectfulScraper":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots.get(base)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{base}/robots.txt")
            try:
                parser.read()
            except Exception as exc:  # robots.txt yoksa/erişilemezse  # noqa: BLE001
                logger.warning("robots.txt okunamadı (%s): %s", base, exc)
            self._robots[base] = parser
        return parser

    def can_fetch(self, url: str) -> bool:
        """robots.txt bu URL'yi bizim UA için serbest bırakıyor mu?"""
        return self._robots_for(url).can_fetch(self.user_agent, url)

    def _throttle(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def get(self, url: str) -> str | None:
        """robots.txt'e uyar, hız sınırı uygular ve sayfa HTML'ini döndürür."""
        if not self.can_fetch(url):
            logger.warning("robots.txt izin vermiyor, atlanıyor: %s", url)
            return None
        self._throttle()
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.text

    @abstractmethod
    def parse(self, html: str, url: str | None = None) -> ListingRecord | None:
        """Sayfa HTML'inden ListingRecord üretir (siteye özgü, ToS'a uygun)."""
        raise NotImplementedError
