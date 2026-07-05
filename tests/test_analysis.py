"""Gerçek etiket analizi ve KFE-DB kalibrasyonu testleri (Faz 4+)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sold.db.models import Base, EvdsObservation
from sold.groundtruth.analyze import discount_summary
from sold.model.calibrate import load_kfe_from_db


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_discount_summary_overall_and_breakdowns():
    frame = pd.DataFrame(
        {
            "last_price": [1_000_000, 2_000_000, 3_000_000, 5_000_000],
            "true_realized_price": [900_000, 1_800_000, 2_850_000, 4_500_000],
            "district": ["Kadıköy", "Kadıköy", "Beşiktaş", "Beşiktaş"],
        }
    )
    summary = discount_summary(frame)
    assert summary["overall"]["count"] == 4
    assert summary["overall"]["mean_pct"] == pytest.approx(8.75)
    assert summary["overall"]["median_pct"] == pytest.approx(10.0)
    assert not summary["by_district"].empty
    assert not summary["by_price_band"].empty


def test_discount_summary_ignores_invalid_rows():
    frame = pd.DataFrame(
        {
            "last_price": [1_000_000, 0, None],
            "true_realized_price": [900_000, 500_000, 800_000],
            "district": ["A", "B", "C"],
        }
    )
    summary = discount_summary(frame)
    assert summary["overall"]["count"] == 1


def test_load_kfe_from_db():
    session = _session()
    session.add_all(
        [
            EvdsObservation(series_code="TP.KFE.TR", obs_date=dt.date(2026, 1, 1), value=211.73),
            EvdsObservation(series_code="TP.KFE.TR", obs_date=dt.date(2026, 2, 1), value=215.40),
            EvdsObservation(series_code="TP.KFE.TR10", obs_date=dt.date(2026, 1, 1), value=196.70),
        ]
    )
    session.commit()

    kfe = load_kfe_from_db(session, "TP.KFE.TR")
    assert kfe == {"2026-01": 211.73, "2026-02": 215.40}
