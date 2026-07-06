"""Broker veri flywheel: ilan sonucu toplama + non-ML müzakere analitiği.

Aynı akıştan iki etiket kümesi doğar:
- ClosingDiscount → yalnızca 'sold' + arm_length (``closing_discount_frame``)
- SaleProbability → tüm sonuçlar (``load_outcomes``; model ileride)
"""

from __future__ import annotations

from .analytics import benchmark_comparison, negotiation_analytics
from .pipeline import (
    EVIDENCE_TYPES,
    OUTCOMES,
    SALE_OUTCOME,
    OutcomeError,
    assign_confidence,
    closing_discount_frame,
    load_outcomes,
    record_outcome,
    validate_outcome,
)

__all__ = [
    "OUTCOMES",
    "SALE_OUTCOME",
    "EVIDENCE_TYPES",
    "OutcomeError",
    "assign_confidence",
    "validate_outcome",
    "record_outcome",
    "load_outcomes",
    "closing_discount_frame",
    "negotiation_analytics",
    "benchmark_comparison",
]
