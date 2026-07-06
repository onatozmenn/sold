"""Tüketici (öz-beyan) satış toplayıcı — sıradan konutta DOĞRUDAN asking→closing edinimi.

Projenin çekirdek çözülmemiş sorunu buradan çözülür: ürünün kendi edinim yolundan
(ev satmış kişilerin formu) provenance-aware doğrudan etiketler toplanır
(domain=consumer, seller_self_reported, ordinary_resale, asking, güven=B) ve bunlar
``asking_to_closing_labels()``'e girer. Kamu (UYAP/KAP/TOKİ) gözlemleri HARİÇ kalır.
"""

from __future__ import annotations

from .analytics import (
    MIN_SEGMENT_OBSERVATIONS,
    sale_analytics,
    segment_benchmark,
    segment_key,
)
from .collector import (
    COLLECTED_FIELDS,
    CONSUMER_CONFIDENCE,
    CONSUMER_DOMAIN,
    CONSUMER_LABEL_SOURCE,
    CONSUMER_REFERENCE_TYPE,
    CONSUMER_SALE_MECHANISM,
    FORBIDDEN_PERSONAL_KEYS,
    ConsumerSaleError,
    load_consumer_sales,
    record_consumer_sale,
    sale_as_dict,
    sale_label_dict,
    validate_consumer_sale,
)

__all__ = [
    "CONSUMER_DOMAIN",
    "CONSUMER_LABEL_SOURCE",
    "CONSUMER_SALE_MECHANISM",
    "CONSUMER_REFERENCE_TYPE",
    "CONSUMER_CONFIDENCE",
    "COLLECTED_FIELDS",
    "FORBIDDEN_PERSONAL_KEYS",
    "ConsumerSaleError",
    "validate_consumer_sale",
    "sale_label_dict",
    "record_consumer_sale",
    "sale_as_dict",
    "load_consumer_sales",
    "sale_analytics",
    "segment_benchmark",
    "segment_key",
    "MIN_SEGMENT_OBSERVATIONS",
]
