"""Veritabanı: motor, oturum ve şema başlatma."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from .models import (
    Base,
    CrawlRun,
    EvdsObservation,
    GroundTruthSale,
    Listing,
    ListingSnapshot,
    PriceChange,
    TuikHouseSale,
)

logger = logging.getLogger(__name__)

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")

__all__ = [
    "Base",
    "Listing",
    "ListingSnapshot",
    "PriceChange",
    "EvdsObservation",
    "TuikHouseSale",
    "CrawlRun",
    "GroundTruthSale",
    "get_engine",
    "get_sessionmaker",
    "init_db",
]


def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or settings.database_url, future=True)


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine or get_engine(), class_=Session, expire_on_commit=False
    )


def init_db(engine: Engine | None = None) -> None:
    """Tabloları oluşturur.

    PostgreSQL'de PostGIS uzantısı + ``geom`` üretilmiş sütunu içeren
    ``schema.sql`` uygulanır. Diğer veritabanlarında (ör. SQLite) taşınabilir
    ORM tabloları kullanılır.
    """
    engine = engine or get_engine()
    if engine.dialect.name == "postgresql":
        sql = _SCHEMA_SQL.read_text(encoding="utf-8")
        with engine.begin() as conn:
            for statement in _split_sql(sql):
                conn.execute(text(statement))
        Base.metadata.create_all(engine)
        logger.info("PostgreSQL şeması uygulandı (PostGIS dahil).")
    else:
        Base.metadata.create_all(engine)
        logger.info("ORM tabloları oluşturuldu (%s).", engine.dialect.name)


def _split_sql(sql: str) -> list[str]:
    """Line comments removed before splitting the simple DDL statements."""
    uncommented = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [statement.strip() for statement in uncommented.split(";") if statement.strip()]
