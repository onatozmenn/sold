"""Yerel dosyalardan okuyan örnek adapter (HİÇBİR siteye istek atmaz).

Bir dizini (ör. ``samples/site/day1``) "site" gibi ele alır: ``list.html``
içindeki ``listing-*.html`` linklerini izler. Böylece crawler + pipeline +
zamanlayıcı, gerçek bir siteye dokunmadan uçtan uca çalıştırılabilir/test edilir.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from bs4 import BeautifulSoup

from ..base import ListingRecord
from ..parsing import parse_data_fields
from .base import SiteAdapter


class LocalExampleAdapter(SiteAdapter):
    """Yerel bir dizini demo "site" olarak tarar."""

    source_name = "local-example"

    def __init__(self, path: str | Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.path = Path(path)

    def list_page_urls(self) -> Iterable[str]:
        return [str(self.path / "list.html")]

    def extract_listing_urls(self, html: str, page_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        base = Path(page_url).parent
        urls: list[str] = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if href.endswith(".html") and "listing" in href:
                urls.append(str(base / href))
        return urls

    def fetch(self, url: str) -> str | None:
        # Yerel dosya; HTTP/robots.txt uygulanmaz.
        path = Path(url)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def parse(self, html: str, url: str | None = None) -> ListingRecord | None:
        return parse_data_fields(html, self.source_name, url)
