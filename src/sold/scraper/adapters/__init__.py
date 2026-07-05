"""Siteye özgü tarama adapter'ları ve kayıt defteri (registry)."""

from __future__ import annotations

from .base import SiteAdapter
from .local_example import LocalExampleAdapter
from .template import TemplateSiteAdapter

# Yalnızca çalışır durumdaki adapter'lar kayıt defterinde yer alır.
# TemplateSiteAdapter bilinçli olarak KAYITLI DEĞİLDİR (doldurulması gerekir).
ADAPTERS: dict[str, type[SiteAdapter]] = {
    "local-example": LocalExampleAdapter,
}


def get_adapter(name: str, **kwargs) -> SiteAdapter:
    try:
        cls = ADAPTERS[name]
    except KeyError as exc:
        available = ", ".join(ADAPTERS) or "(yok)"
        raise KeyError(
            f"Bilinmeyen adapter '{name}'. Mevcut: {available}"
        ) from exc
    return cls(**kwargs)


__all__ = [
    "SiteAdapter",
    "LocalExampleAdapter",
    "TemplateSiteAdapter",
    "ADAPTERS",
    "get_adapter",
]
