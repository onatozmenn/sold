"""Yerel örnek HTML üzerinden çalışan demo parser (hiçbir siteye istek atmaz).

Amaç: scraper -> pipeline hattını gerçek bir siteye dokunmadan uçtan uca
göstermek ve test etmek. Gerçek site parser'ları bu arayüzü örnek alır ve
ilgili sitenin ToS'una uygun biçimde AYRICA yazılır.
"""

from __future__ import annotations

from .base import ListingRecord, RespectfulScraper
from .parsing import parse_data_fields


class ExampleParser(RespectfulScraper):
    """Demo amaçlı, ``data-field="..."`` işaretli örnek HTML'i ayrıştırır."""

    source_name = "example"

    def parse(self, html: str, url: str | None = None) -> ListingRecord | None:
        return parse_data_fields(html, self.source_name, url)
