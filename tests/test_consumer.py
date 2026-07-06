"""Tüketici (öz-beyan) satış toplayıcı testleri.

Kapsam: KVKK reddi, zorunlu alanlar, SABİT provenance, milestone (asking→closing
head'ini 0'dan ≥1'e taşımak), kamu domainlerinin HARİÇ kalması, anlık analitik ve
dürüst segment benchmark (yeterli gözlem yoksa uydurma yok).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sold.consumer import (
    CONSUMER_CONFIDENCE,
    CONSUMER_DOMAIN,
    CONSUMER_LABEL_SOURCE,
    CONSUMER_SALE_MECHANISM,
    CONSUMER_REFERENCE_TYPE,
    ConsumerSaleError,
    load_consumer_sales,
    record_consumer_sale,
    sale_analytics,
    sale_as_dict,
    sale_label_dict,
    segment_benchmark,
    validate_consumer_sale,
)
from sold.db.models import Base
from sold.labels import asking_to_closing_labels, load_labels, normalize_label


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _valid() -> dict:
    return {
        "initial_asking_price": 4_000_000,
        "final_asking_price": 3_800_000,
        "closing_price": 3_500_000,
        "province": "İstanbul",
        "district": "Kadıköy",
        "property_type": "konut",
        "gross_m2": 110,
        "room_count": "2+1",
        "price_cut_count": 1,
        "listing_date": "2024-01-01",
        "closing_date": "2024-02-20",
    }


# ---- doğrulama + KVKK ------------------------------------------------------ #
def test_requires_final_asking_and_closing():
    with pytest.raises(ConsumerSaleError):
        validate_consumer_sale({"final_asking_price": 0, "closing_price": 100})
    with pytest.raises(ConsumerSaleError):
        validate_consumer_sale({"final_asking_price": 100})  # closing yok


def test_rejects_personal_data_keys():
    for key in ("name", "tckn", "phone", "adres", "tapu", "iban", "buyer", "satici"):
        raw = _valid()
        raw[key] = "x"
        with pytest.raises(ConsumerSaleError):
            validate_consumer_sale(raw)


def test_normalizes_and_derives_days_to_close():
    v = validate_consumer_sale(_valid())
    assert v["days_to_close"] == (dt.date(2024, 2, 20) - dt.date(2024, 1, 1)).days
    assert v["property_type"] == "konut"
    assert v["price_cut_count"] == 1


def test_property_type_defaults_to_konut():
    raw = _valid()
    raw.pop("property_type")
    assert validate_consumer_sale(raw)["property_type"] == "konut"


# ---- SABİT provenance + milestone (0 → 1) ---------------------------------- #
def test_label_has_fixed_consumer_provenance():
    label = normalize_label(sale_label_dict(validate_consumer_sale(_valid())))
    assert label["domain"] == CONSUMER_DOMAIN == "consumer"
    assert label["label_source"] == CONSUMER_LABEL_SOURCE == "seller_self_reported"
    assert label["sale_mechanism"] == CONSUMER_SALE_MECHANISM == "ordinary_resale"
    assert label["reference_price_type"] == CONSUMER_REFERENCE_TYPE == "asking"
    assert label["label_confidence"] == CONSUMER_CONFIDENCE == "B"
    # referans = SON ilan fiyatı, gerçekleşen = kapanış
    assert label["reference_price"] == 3_800_000
    assert label["realized_price"] == 3_500_000


def test_consumer_label_enters_asking_to_closing():
    label = normalize_label(sale_label_dict(validate_consumer_sale(_valid())))
    a2c = asking_to_closing_labels(pd.DataFrame([label]))
    assert len(a2c) == 1  # MİLESTONE: asking→closing head 0 → 1


def test_record_persists_sale_and_direct_label_end_to_end():
    with _session() as session:
        row = record_consumer_sale(session, _valid())
        session.commit()
        assert row.id is not None

        sales = load_consumer_sales(session)
        assert len(sales) == 1

        # ürünün KENDİ edinim yolundan head'e gerçek bir doğrudan etiket girdi
        a2c = asking_to_closing_labels(load_labels(session))
        assert len(a2c) == 1
        assert set(a2c["domain"]) == {"consumer"}
        assert set(a2c["label_source"]) == {"seller_self_reported"}


# ---- kamu domainleri HARİÇ kalır ------------------------------------------- #
def test_public_domains_still_excluded_from_head():
    uyap = normalize_label(
        {
            "domain": "uyap",
            "label_source": "uyap",
            "sale_mechanism": "auction",
            "reference_price_type": "appraisal",
            "reference_price": 4_500_000,
            "realized_price": 4_545_000,
        }
    )
    consumer = normalize_label(sale_label_dict(validate_consumer_sale(_valid())))
    a2c = asking_to_closing_labels(pd.DataFrame([uyap, consumer]))
    assert set(a2c["label_source"]) == {"seller_self_reported"}
    assert "uyap" not in set(a2c["label_source"])


# ---- anlık analitik -------------------------------------------------------- #
def test_sale_analytics_gaps_and_days():
    a = sale_analytics(validate_consumer_sale(_valid()))
    assert a["final_ask_to_close_gap_pct"] == pytest.approx(
        (1 - 3_500_000 / 3_800_000) * 100, abs=0.01
    )
    assert a["initial_ask_to_close_gap_pct"] == pytest.approx(
        (1 - 3_500_000 / 4_000_000) * 100, abs=0.01
    )
    assert a["days_to_close"] == 50
    assert a["price_cut_count"] == 1


# ---- segment benchmark: dürüst yetersizlik → yeterli ----------------------- #
def test_segment_benchmark_insufficient_is_honest():
    with _session() as session:
        row = record_consumer_sale(session, _valid())
        session.commit()
        bench = segment_benchmark(session, sale_as_dict(row), min_observations=3)
        assert bench["enough_observations"] is False
        assert bench["observations"] == 1
        assert "yeterli gözlem yok" in bench["message"]


def test_segment_benchmark_enough_returns_aggregates():
    with _session() as session:
        for _ in range(3):
            record_consumer_sale(session, _valid())
        session.commit()
        bench = segment_benchmark(
            session, {"province": "İstanbul", "property_type": "konut"}, min_observations=3
        )
        assert bench["enough_observations"] is True
        assert bench["observations"] == 3
        assert bench["median_final_ask_to_close_gap_pct"] is not None


def test_segment_benchmark_segments_by_province():
    with _session() as session:
        record_consumer_sale(session, _valid())  # İstanbul
        izmir = _valid()
        izmir["province"] = "İzmir"
        record_consumer_sale(session, izmir)
        session.commit()
        b_ist = segment_benchmark(
            session, {"province": "İstanbul", "property_type": "konut"}, min_observations=1
        )
        assert b_ist["observations"] == 1  # İzmir ayrı segmentte
