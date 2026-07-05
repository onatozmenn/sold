"""Siteye özgü tarama arayüzü.

Bir ``SiteAdapter`` üç şeyi tanımlar:
1. ``list_page_urls``  — taranacak arama/liste sayfalarının URL'leri (sayfalama),
2. ``extract_listing_urls`` — bir liste sayfasından ilan (detay) linkleri,
3. ``parse`` — bir detay sayfasından ``ListingRecord`` (RespectfulScraper'dan).

``fetch`` varsayılan olarak robots.txt + hız sınırı uygulayan HTTP ``get``'i
kullanır; yerel/özel kaynaklar bunu geçersiz kılabilir.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable

from ..base import RespectfulScraper


class SiteAdapter(RespectfulScraper):
    """Crawler'ın çağırdığı siteye özgü sözleşme."""

    @abstractmethod
    def list_page_urls(self) -> Iterable[str]:
        """Taranacak liste/arama sayfası URL'lerini üretir (sayfalama dahil)."""
        raise NotImplementedError

    @abstractmethod
    def extract_listing_urls(self, html: str, page_url: str) -> list[str]:
        """Bir liste sayfası HTML'inden ilan (detay) URL'lerini çıkarır."""
        raise NotImplementedError

    def fetch(self, url: str) -> str | None:
        """Varsayılan getirme: robots.txt + hız sınırı ile HTTP."""
        return self.get(url)
