"""Broker veri flywheel testleri: sonuç toplama, güven, türetme, analitik."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sold.db.models import Base
from sold.flywheel import (
    OutcomeError,
    assign_confidence,
    benchmark_comparison,
    closing_discount_frame,
    load_outcomes,
    negotiation_analytics,
    record_outcome,
    validate_outcome,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


# ---- güven ataması --------------------------------------------------------- #
def test_broker_closing_not_auto_A():
    # Bu giriş yüzeyi öz-beyandır; istemci doğrulama iddiasıyla A alamaz.
    assert assign_confidence("broker_closing", evidence_verified=False) == "B"
    assert assign_confidence("broker_closing", evidence_verified=True) == "B"
    assert assign_confidence("manual", evidence_verified=False) == "B"
    assert assign_confidence("deed_declared", evidence_verified=False) == "C"
    assert assign_confidence("deed_declared", evidence_verified=True) == "C"


# ---- doğrulama ------------------------------------------------------------- #
def test_closing_fields_only_for_sold():
    v = validate_outcome(
        {"outcome": "withdrawn", "sold_price": 900000, "sale_date": "2026-01-01"}
    )
    assert v["outcome"] == "withdrawn"
    assert v["sold_price"] is None and v["sale_date"] is None
    assert v["label_confidence"] == "B"  # varsayılan öz-beyan


def test_sold_requires_price():
    with pytest.raises(OutcomeError):
        validate_outcome({"outcome": "sold"})


def test_invalid_outcome_rejected():
    with pytest.raises(OutcomeError):
        validate_outcome({"outcome": "banana"})


def test_sold_default_confidence_B_unless_verified():
    v = validate_outcome(
        {"outcome": "sold", "sold_price": 900000, "label_source": "broker_closing"}
    )
    assert v["label_confidence"] == "B"
    assert v["sale_mode"] == "arm_length"

    v2 = validate_outcome(
        {
            "outcome": "sold",
            "sold_price": 900000,
            "label_source": "broker_closing",
            "evidence_verified": True,
        }
    )
    assert v2["label_confidence"] == "B"
    assert v2["evidence_verified"] is False


# ---- kayıt + türetme ------------------------------------------------------- #
def test_record_load_and_closing_frame():
    session = _session()
    record_outcome(
        session,
        {
            "outcome": "sold",
            "province": "İstanbul",
            "district": "Kadıköy",
            "gross_m2": 120,
            "last_asking_price": 3_200_000,
            "sold_price": 2_900_000,
            "price_cut_count": 1,
            "days_to_close": 40,
            "sale_mode": "arm_length",
            "source": "broker-1",
        },
    )
    record_outcome(
        session,
        {
            "outcome": "sold",
            "province": "Ankara",
            "last_asking_price": 2_000_000,
            "sold_price": 1_000_000,
            "sale_mode": "auction",  # ihale → ClosingDiscount'a girmemeli
            "source": "broker-1",
        },
    )
    record_outcome(
        session,
        {"outcome": "withdrawn", "province": "İzmir", "last_asking_price": 1_500_000},
    )
    record_outcome(
        session,
        {"outcome": "active", "province": "Bursa", "last_asking_price": 1_000_000},
    )
    session.commit()

    df = load_outcomes(session)
    assert len(df) == 4
    assert set(df["outcome"]) == {"sold", "withdrawn", "active"}

    frame = closing_discount_frame(session)
    assert len(frame) == 1  # yalnızca arm_length sold
    assert (frame["true_realized_price"] > 0).all()
    assert "market_heat" in frame.columns


# ---- analitik -------------------------------------------------------------- #
def _sample_outcomes() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"outcome": "sold", "sale_mode": "arm_length", "last_asking_price": 1_000_000, "sold_price": 900_000, "price_cut_count": 0, "days_to_close": 30},
            {"outcome": "sold", "sale_mode": "arm_length", "last_asking_price": 2_000_000, "sold_price": 1_800_000, "price_cut_count": 2, "days_to_close": 90},
            {"outcome": "withdrawn", "sale_mode": None, "last_asking_price": 1_500_000, "sold_price": None, "price_cut_count": 1, "days_to_close": None},
            {"outcome": "sold", "sale_mode": "auction", "last_asking_price": 1_000_000, "sold_price": 500_000, "price_cut_count": 0, "days_to_close": 10},
        ]
    )


def test_negotiation_analytics():
    a = negotiation_analytics(_sample_outcomes())
    assert a["transaction_count"] == 2  # arm_length sold only (auction hariç)
    assert a["total_outcomes"] == 4
    assert a["outcome_counts"]["sold"] == 3
    assert a["outcome_counts"]["withdrawn"] == 1
    assert a["median_discount_pct"] == pytest.approx(10.0)
    assert a["mean_discount_pct"] == pytest.approx(10.0)
    assert a["median_days_to_close"] == pytest.approx(60.0)
    # fiyat-kesinti durumuna göre indirim
    assert a["discount_by_price_cut"]["no_cut"]["count"] == 1
    assert a["discount_by_price_cut"]["with_cut"]["count"] == 1


def test_negotiation_analytics_empty():
    a = negotiation_analytics(pd.DataFrame(columns=["outcome"]))
    assert a["transaction_count"] == 0
    assert a["median_discount_pct"] != a["median_discount_pct"]  # NaN


def test_benchmark_comparison():
    broker = pd.DataFrame(
        [{"outcome": "sold", "sale_mode": "arm_length", "last_asking_price": 1_000_000, "sold_price": 880_000, "price_cut_count": 1, "days_to_close": 50}]
    )
    bench = pd.DataFrame(
        [
            {"outcome": "sold", "sale_mode": "arm_length", "last_asking_price": 1_000_000, "sold_price": 920_000, "price_cut_count": 0, "days_to_close": 30},
            {"outcome": "sold", "sale_mode": "arm_length", "last_asking_price": 1_000_000, "sold_price": 900_000, "price_cut_count": 0, "days_to_close": 30},
        ]
    )
    cmp = benchmark_comparison(broker, bench)
    assert cmp["broker"]["mean_discount_pct"] == pytest.approx(12.0)
    assert cmp["benchmark"]["mean_discount_pct"] == pytest.approx(9.0)
    assert cmp["delta"]["mean_discount_pct"] == pytest.approx(3.0)
