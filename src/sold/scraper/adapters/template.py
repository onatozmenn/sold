"""Gerçek bir site için SiteAdapter ŞABLONU.

Bu şablonu YALNIZCA ilgili sitenin Kullanım Koşulları (ToS) ve robots.txt
kuralları izin veriyorsa doldurun. Kişisel veri (ilan sahibi adı, telefon vb.)
KESİNLİKLE çıkarmayın — sadece taşınmazın nesnel nitelikleri.

Doldurulacak yerler ``NotImplementedError`` ile işaretlidir. Hazır olduğunda
``adapters/__init__.py`` içindeki ADAPTERS kayıt defterine ekleyin.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..base import ListingRecord
from ..parsing import to_int, to_number  # noqa: F401  (gerçek parse'ta işinize yarar)
from .base import SiteAdapter


class TemplateSiteAdapter(SiteAdapter):
    """Gerçek bir emlak sitesi için doldurulacak şablon."""

    source_name = "template"  # örn. sitenin kısa adı

    # Sayfalı arama URL'si (örnek — kendi hedef bölgenize göre uyarlayın).
    SEARCH_URL = "https://ORNEK-SITE.example/arama?il=istanbul&sayfa={page}"
    MAX_PAGES = 5

    def list_page_urls(self) -> Iterable[str]:
        for page in range(1, self.MAX_PAGES + 1):
            yield self.SEARCH_URL.format(page=page)

    def extract_listing_urls(self, html: str, page_url: str) -> list[str]:
        # TODO: Arama sonucu sayfasından ilan (detay) linklerini CSS seçici ile
        # çıkarın. Örn:
        #   soup = BeautifulSoup(html, "html.parser")
        #   return [urljoin(page_url, a["href"]) for a in soup.select(".ilan a")]
        raise NotImplementedError(
            "extract_listing_urls: liste sayfasından ilan linklerini çıkarın."
        )

    def parse(self, html: str, url: str | None = None) -> ListingRecord | None:
        # TODO: Detay sayfasından SADECE taşınmaz niteliklerini çıkarın
        # (m², oda, konum, fiyat). KİŞİSEL VERİ (ad/telefon) ÇIKARMAYIN.
        raise NotImplementedError(
            "parse: detay sayfasından ListingRecord üretin (kişisel veri hariç)."
        )
