"""Gerçek (broker/ekspertiz) gerçekleşen-satış etiketleri — Faz 4."""

from .analyze import discount_summary
from .loader import (
    GT_REQUIRED,
    GT_TEMPLATE_COLUMNS,
    load_frame_from_db,
    make_demo,
    persist_to_db,
    read_csv,
    to_feature_frame,
    write_template,
)

__all__ = [
    "GT_REQUIRED",
    "GT_TEMPLATE_COLUMNS",
    "read_csv",
    "to_feature_frame",
    "make_demo",
    "write_template",
    "persist_to_db",
    "load_frame_from_db",
    "discount_summary",
]
