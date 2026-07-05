"""Ortak ayrıştırma yardımcıları (TR sayı biçimi + data-field tabanlı çıkarım).

``data-field="..."`` sözleşmesi hem demo parser'ında hem de local-example
adapter'ında kullanılır; böylece kod tekrarı olmaz (DRY).
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import ListingRecord


def to_number(text: str | None) -> float | None:
    """'2.500.000 TL' veya '40,9876' gibi TR biçimli metni sayıya çevirir."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", text)
    if not cleaned:
        return None
    # TR biçimi: '.' binlik ayracı, ',' ondalık ayracı
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def to_int(text: str | None) -> int | None:
    value = to_number(text)
    return int(value) if value is not None else None


def parse_data_fields(
    html: str, source: str, url: str | None = None
) -> ListingRecord | None:
    """``data-field`` işaretli bir sayfadan (kişisel-veri-içermeyen) kayıt üretir."""
    soup = BeautifulSoup(html, "html.parser")

    def field(name: str) -> str | None:
        element = soup.select_one(f'[data-field="{name}"]')
        return element.get_text(strip=True) if element else None

    listing_id = field("id")
    price = to_number(field("price"))
    if not listing_id or price is None:
        return None

    return ListingRecord(
        source=source,
        source_listing_id=str(listing_id),
        price=price,
        currency=field("currency") or "TRY",
        url=url,
        listing_type=field("listing_type"),
        province=field("province"),
        district=field("district"),
        neighborhood=field("neighborhood"),
        lat=to_number(field("lat")),
        lon=to_number(field("lon")),
        gross_m2=to_number(field("gross_m2")),
        room_count=field("room_count"),
        building_age=to_int(field("building_age")),
        floor=to_int(field("floor")),
        heating=field("heating"),
    )
