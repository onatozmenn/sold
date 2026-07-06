"""Birleşik gerçekleşen-fiyat etiket registry'si + PublicLabelMiner.

Kamuya açık işlem domain'lerinden (UYAP/KAP/TOKİ) ve doğrudan closing gözleyen
kaynaklardan (broker/seller) etiketleri toplar; domain'leri AYRI tutar. Metodolojik
çekirdek: ``asking_to_closing_labels`` (yalnızca doğrudan closing) vs
``fair_value_labels`` (appraisal/reserve → realized).
"""

from __future__ import annotations

from .aggregates import (
    AGGREGATE_COMPARED_FIELDS,
    AGGREGATE_PARSER_VERSION,
    AGGREGATION_LEVELS,
    COMPARISON_SCOPES,
    OBSERVATION_ROLES,
    AggregateError,
    ProjectDisclosureAdapter,
    aggregate_sources,
    load_aggregates,
    mine_aggregates,
    normalize_aggregate,
    persist_aggregates,
)
from .miner import (
    KAPAdapter,
    PublicLabelMiner,
    PublicSourceAdapter,
    TOKIAdapter,
    UYAPAdapter,
)
from .registry import (
    DIRECT_CLOSING_SOURCES,
    DIRECT_RESALE_MECHANISMS,
    DOMAINS,
    OBSERVED_PUBLIC_SOURCES,
    PARSER_VERSION,
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
    "PARSER_VERSION",
    "DIRECT_CLOSING_SOURCES",
    "DIRECT_RESALE_MECHANISMS",
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
    # Eşlenmemiş toplu (cohort) gözlem soyutlaması — RealizedLabel'dan AYRI
    "AGGREGATION_LEVELS",
    "COMPARISON_SCOPES",
    "OBSERVATION_ROLES",
    "AGGREGATE_PARSER_VERSION",
    "AGGREGATE_COMPARED_FIELDS",
    "AggregateError",
    "normalize_aggregate",
    "ProjectDisclosureAdapter",
    "aggregate_sources",
    "mine_aggregates",
    "persist_aggregates",
    "load_aggregates",
]
