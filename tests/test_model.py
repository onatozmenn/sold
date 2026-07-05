"""Faz 2-3 testleri: özellik üretimi, sentetik motor ve kalibrasyon."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sold.db.models import Base
from sold.features.build import build_feature_frame, parse_room_count
from sold.model.calibrate import align_growth_to_kfe, mape, median_ape, scale_to_reference
from sold.model.estimator import RealizedValuator
from sold.model.synthetic import generate_market
from sold.scraper.adapters.local_example import LocalExampleAdapter
from sold.scraper.crawler import crawl_once


def test_parse_room_count():
    assert parse_room_count("2+1") == 3.0
    assert parse_room_count("3+1") == 4.0
    assert parse_room_count("1+0") == 1.0
    assert parse_room_count("Stüdyo") == 1.0
    assert math.isnan(parse_room_count(None))


def test_metrics_and_scaling():
    assert mape([100, 200], [110, 190]) == pytest.approx(7.5)
    assert median_ape([100, 200], [110, 190]) == pytest.approx(7.5)
    est, factor = scale_to_reference([1, 2, 3], target_mean=4)
    assert factor == pytest.approx(2.0)
    assert list(est) == [2.0, 4.0, 6.0]


def test_align_growth_to_kfe():
    means = {"2020": 100.0, "2021": 150.0}
    kfe = {"2020": 100.0, "2021": 120.0}
    out = align_growth_to_kfe(means, kfe, anchor="2020")
    assert out["2020"] == pytest.approx(100.0)
    assert out["2021"] == pytest.approx(120.0)


def test_synthetic_market_shape():
    df = generate_market(300, seed=1)
    assert len(df) == 300
    assert "true_realized_price" in df.columns
    assert (df["initial_price"] > 0).all()
    assert (df["true_realized_price"] > 0).all()
    # ilk ilan fiyatı, gerçek değerin üstünde (aspirasyonel markup) olmalı
    assert df["initial_price"].mean() > df["true_realized_price"].mean()


def test_valuator_beats_naive_baseline():
    df = generate_market(1600, seed=7)
    rng = np.random.default_rng(7)
    is_train = rng.random(len(df)) < 0.75
    train, test = df[is_train], df[~is_train]

    valuator = RealizedValuator.train(train)
    estimate = valuator.estimate(test)

    y_true = test["true_realized_price"].to_numpy(float)
    naive_last = test["last_price"].to_numpy(float)

    model_mape = mape(y_true, estimate)
    naive_mape = mape(y_true, naive_last)

    # Model, ham 'son ilan fiyatı' baseline'ından daha iyi olmalı
    assert model_mape < naive_mape
    assert model_mape < 12.0


def test_valuator_save_load(tmp_path):
    df = generate_market(400, seed=3)
    valuator = RealizedValuator.train(df)
    path = valuator.save(tmp_path / "valuator.joblib")
    loaded = RealizedValuator.load(path)
    a = valuator.estimate(df.head(10))
    b = loaded.estimate(df.head(10))
    assert np.allclose(a, b)


def _db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_feature_frame_from_db():
    session = _db_session()
    with LocalExampleAdapter(path="samples/site/day1") as day1:
        crawl_once(session, day1, captured_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    session.commit()

    df = build_feature_frame(session)
    assert len(df) == 3
    assert {"last_price", "gross_m2", "district", "total_drop_pct"}.issubset(df.columns)
    assert set(df["source_listing_id"]) == {"DEMO-1", "DEMO-2", "DEMO-3"}
