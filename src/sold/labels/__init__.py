"""Birleşik gerçekleşen-fiyat etiket registry'si + PublicLabelMiner.

Kamuya açık işlem domain'lerinden (UYAP/KAP/TOKİ) ve doğrudan closing gözleyen
kaynaklardan (broker/seller) etiketleri toplar; domain'leri AYRI tutar. Metodolojik
çekirdek: ``asking_to_closing_labels`` (yalnızca doğrudan closing) vs
``fair_value_labels`` (appraisal/reserve → realized).
"""

from __future__ import annotations

from .miner import (
    KAPAdapter,
    PublicLabelMiner,
    PublicSourceAdapter,
    TOKIAdapter,
    UYAPAdapter,
)
from .registry import (
    DIRECT_CLOSING_SOURCES,
    DOMAINS,
    OBSERVED_PUBLIC_SOURCES,
    REFERENCE_PRICE_TYPES,
    SALE_MECHANISMS,
    LabelError,
    asking_to_closing_labels,
    confidence_for,
    fair_value_labels,
    fair_value_strata,
    load_labels,
    normalize_label,
    persist_labels,
)

__all__ = [
    "DOMAINS",
    "SALE_MECHANISMS",
    "REFERENCE_PRICE_TYPES",
    "DIRECT_CLOSING_SOURCES",
    "OBSERVED_PUBLIC_SOURCES",
    "LabelError",
    "confidence_for",
    "normalize_label",
    "persist_labels",
    "load_labels",
    "asking_to_closing_labels",
    "fair_value_labels",
    "fair_value_strata",
    "PublicLabelMiner",
    "PublicSourceAdapter",
    "UYAPAdapter",
    "KAPAdapter",
    "TOKIAdapter",
]
