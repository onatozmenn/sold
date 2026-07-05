"""Faz 4 testleri: ground-truth yükleme, DB kalıcılığı ve CV değerlendirmesi."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sold.db.models import Base
from sold.groundtruth import (
    load_frame_from_db,
    make_demo,
    persist_to_db,
    read_csv,
    to_feature_frame,
    write_template,
)
from sold.model.calibrate import period_means
from sold.model.evaluate import cross_validate


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_demo_to_feature_frame():
    df = make_demo(120, seed=1)
    assert {"asking_price", "sold_price", "district", "room_count"}.issubset(df.columns)

    frame = to_feature_frame(df)
    assert len(frame) == 120
    assert {"last_price", "true_realized_price", "room_count_num"}.issubset(frame.columns)
    assert (frame["true_realized_price"] > 0).all()
    assert (frame["last_price"] > 0).all()


def test_template_roundtrip(tmp_path):
    path = write_template(tmp_path / "template.csv")
    df = read_csv(path)
    assert {"district", "gross_m2", "asking_price", "sold_price"}.issubset(df.columns)


def test_read_csv_missing_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    pd.DataFrame({"district": ["x"]}).to_csv(bad, index=False)
    with pytest.raises(ValueError):
        read_csv(bad)


def test_persist_and_load_db():
    session = _session()
    df = make_demo(60, seed=2)
    added = persist_to_db(session, df, source="broker-test")
    session.commit()
    assert added == 60

    frame = load_frame_from_db(session)
    assert len(frame) == 60
    assert "true_realized_price" in frame.columns
    assert (frame["true_realized_price"] > 0).all()


def test_cross_validate_beats_naive():
    frame = to_feature_frame(make_demo(600, seed=5))
    result = cross_validate(frame, folds=4, seed=5)
    assert result["n"] == 600
    assert result["model_mape_mean"] < result["naive_mape_mean"]
    assert result["improvement_pct"] > 0


def test_period_means():
    df = pd.DataFrame({"period": ["2020", "2020", "2021"], "value": [10.0, 20.0, 30.0]})
    means = period_means(df, "period", "value")
    assert means["2020"] == pytest.approx(15.0)
    assert means["2021"] == pytest.approx(30.0)
