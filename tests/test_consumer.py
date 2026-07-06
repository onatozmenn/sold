"""Tüketici (öz-beyan) satış toplayıcı + KALİTE KAPISI testleri.

Kapsam: KVKK reddi, YAPISAL red (fiyat≤0 / closing<listing), inceleme bayrakları
(aşırı oran, final>initial, hızlı kapanış, duplicate — hiçbiri RED değil), köken
ayrımı (test/demo a2c'ye girmez, genuine sayısını şişirmez), gizlilik-korumalı
parmak izi, ayrı sayımlar ve dürüst segment benchmark.

TERMİNOLOJİ: bir E2E testinin a2c'yi 0→1 taşıması YOLU kanıtlar; GERÇEK dünya
etiketini DEĞİL. Bu yüzden pipeline testleri origin=test_fixture kullanır ve genuine
sayısı 0 kalır; yalnızca origin=consumer_submission genuine sayılır.
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
    CONSUMER_REFERENCE_TYPE,
    CONSUMER_SALE_MECHANISM,
    FLAG_DUPLICATE,
    FLAG_EXTREME_RATIO,
    FLAG_FAST_CLOSE,
    FLAG_FINAL_ABOVE_INITIAL,
    ORIGIN_CONSUMER_SUBMISSION,
    ORIGIN_TEST_FIXTURE,
    QUALITY_ACCEPTED,
    QUALITY_FLAGGED,
    QUALITY_REJECTED,
    ConsumerSaleError,
    assess_quality,
    direct_label_counts,
    fingerprint,
    load_consumer_sales,
    quality_flags,
    record_consumer_sale,
    sale_analytics,
    sale_as_dict,
    sale_label_dict,
    segment_benchmark,
    structural_rejection_reason,
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


# ---- YAPISAL RED (yalnızca yapısal olarak imkânsız) ------------------------ #
def test_structural_reject_nonpositive_price():
    assert structural_rejection_reason({"final_asking_price": -1, "closing_price": 10})
    raw = _valid()
    raw["closing_price"] = 0
    with pytest.raises(ConsumerSaleError):
        validate_consumer_sale(raw)


def test_structural_reject_closing_before_listing():
    raw = _valid()
    raw["listing_date"] = "2024-03-01"
    raw["closing_date"] = "2024-01-01"  # kapanış ilandan ÖNCE → yapısal imkânsız
    with pytest.raises(ConsumerSaleError):
        validate_consumer_sale(raw)


# ---- olağandışı oranlar RED DEĞİL, yalnızca BAYRAK ------------------------- #
def test_closing_above_asking_is_not_rejected():
    raw = _valid()
    raw["closing_price"] = 3_900_000  # ilan 3.8M'nin ÜSTÜNDE kapandı (kızgın piyasa)
    v = validate_consumer_sale(raw)  # RED YOK
    status, _ = assess_quality(v)
    assert status != QUALITY_REJECTED


def test_extreme_close_to_ask_ratio_flagged_not_rejected():
    raw = _valid()
    raw["closing_price"] = 5_200_000  # 3.8M'nin çok üstünde → aşırı oran
    v = validate_consumer_sale(raw)
    status, flags = assess_quality(v)
    assert status == QUALITY_FLAGGED
    assert FLAG_EXTREME_RATIO in flags


def test_final_above_initial_flagged_not_rejected():
    raw = _valid()
    raw["initial_asking_price"] = 3_000_000
    raw["final_asking_price"] = 3_500_000  # ilan fiyatı ARTTI (>%10)
    raw["closing_price"] = 3_400_000
    v = validate_consumer_sale(raw)
    status, flags = assess_quality(v)
    assert status == QUALITY_FLAGGED
    assert FLAG_FINAL_ABOVE_INITIAL in flags


def test_suspiciously_fast_close_flagged():
    raw = _valid()
    raw["listing_date"] = "2024-01-01"
    raw["closing_date"] = "2024-01-02"  # 1 gün → şüpheli hızlı
    v = validate_consumer_sale(raw)
    _, flags = assess_quality(v)
    assert FLAG_FAST_CLOSE in flags


def test_quality_flags_preserve_original_values():
    raw = _valid()
    raw["closing_price"] = 5_200_000
    v = validate_consumer_sale(raw)
    # bayrak konsa da orijinal öz-beyan değeri KORUNUR (yeniden yazılmaz)
    assert v["closing_price"] == 5_200_000
    assert quality_flags(v)  # boş değil


# ---- SABİT provenance ------------------------------------------------------ #
def test_label_has_fixed_consumer_provenance():
    label = normalize_label(sale_label_dict(validate_consumer_sale(_valid())))
    assert label["domain"] == CONSUMER_DOMAIN == "consumer"
    assert label["label_source"] == CONSUMER_LABEL_SOURCE == "seller_self_reported"
    assert label["sale_mechanism"] == CONSUMER_SALE_MECHANISM == "ordinary_resale"
    assert label["reference_price_type"] == CONSUMER_REFERENCE_TYPE == "asking"
    assert label["label_confidence"] == CONSUMER_CONFIDENCE == "B"
    assert label["reference_price"] == 3_800_000  # SON ilan fiyatı
    assert label["realized_price"] == 3_500_000  # kapanış


# ---- KÖKEN KAPISI: test/demo a2c'ye girmez, genuine'i şişirmez ------------- #
def test_test_fixture_excluded_from_a2c_by_default():
    label = normalize_label(
        sale_label_dict(validate_consumer_sale(_valid()), origin=ORIGIN_TEST_FIXTURE)
    )
    df = pd.DataFrame([label])
    assert len(asking_to_closing_labels(df)) == 0  # varsayılan: test HARİÇ
    assert len(asking_to_closing_labels(df, include_non_production=True)) == 1  # opt-in


def test_genuine_submission_enters_a2c():
    label = normalize_label(
        sale_label_dict(
            validate_consumer_sale(_valid()), origin=ORIGIN_CONSUMER_SUBMISSION
        )
    )
    assert len(asking_to_closing_labels(pd.DataFrame([label]))) == 1


def test_e2e_path_via_fixture_does_not_inflate_genuine():
    """E2E YOLU kanıtlar (fixture) ama genuine sayısını ŞİŞİRMEZ."""
    with _session() as session:
        record_consumer_sale(session, _valid(), origin=ORIGIN_TEST_FIXTURE)
        session.commit()
        labels = load_labels(session)
        # varsayılan a2c: fixture HARİÇ → 0 (yol include_non_production ile kanıtlanır)
        assert len(asking_to_closing_labels(labels)) == 0
        assert len(asking_to_closing_labels(labels, include_non_production=True)) == 1
        counts = direct_label_counts(session)
        assert counts["genuine_accepted"] == 0  # GERÇEK etiket YOK
        assert counts["test_demo"] == 1


def test_genuine_submission_counts_as_one():
    with _session() as session:
        record_consumer_sale(session, _valid(), origin=ORIGIN_CONSUMER_SUBMISSION)
        record_consumer_sale(session, _valid(), origin=ORIGIN_TEST_FIXTURE)
        session.commit()
        counts = direct_label_counts(session)
        assert counts["genuine_accepted"] == 1  # yalnızca consumer_submission
        assert counts["test_demo"] == 1
        assert counts["asking_to_closing_default"] == 1  # yalnızca genuine accepted


def test_flagged_genuine_stays_out_of_a2c():
    raw = _valid()
    raw["closing_price"] = 5_200_000  # aşırı oran → flagged
    with _session() as session:
        row = record_consumer_sale(session, raw, origin=ORIGIN_CONSUMER_SUBMISSION)
        session.commit()
        assert row.quality_status == QUALITY_FLAGGED
        counts = direct_label_counts(session)
        assert counts["genuine_accepted"] == 0  # flagged accepted DEĞİL
        assert counts["genuine_flagged"] == 1
        assert len(asking_to_closing_labels(load_labels(session))) == 0


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


# ---- gizlilik-korumalı parmak izi + duplicate ------------------------------ #
def test_fingerprint_is_deterministic_hex_and_non_identifying():
    v = validate_consumer_sale(_valid())
    fp = fingerprint(v)
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)
    assert fingerprint(v) == fp  # deterministik
    other = validate_consumer_sale({**_valid(), "province": "İzmir"})
    assert fingerprint(other) != fp  # farklı segment → farklı iz
    # ham fiyat/kişisel değer parmak izinde AÇIKÇA görünmez (tek yönlü hash)
    assert "3500000" not in fp and "Kadıköy" not in fp


def test_duplicate_candidate_flagged_on_repeat():
    with _session() as session:
        first = record_consumer_sale(session, _valid(), origin=ORIGIN_TEST_FIXTURE)
        session.commit()
        assert FLAG_DUPLICATE not in list(first.quality_flags or [])
        second = record_consumer_sale(session, _valid(), origin=ORIGIN_TEST_FIXTURE)
        session.commit()
        assert second.quality_status == QUALITY_FLAGGED
        assert FLAG_DUPLICATE in list(second.quality_flags or [])


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


# ---- segment benchmark: dürüst + test/demo hariç --------------------------- #
def test_segment_benchmark_insufficient_is_honest():
    with _session() as session:
        row = record_consumer_sale(session, _valid(), origin=ORIGIN_CONSUMER_SUBMISSION)
        session.commit()
        bench = segment_benchmark(session, sale_as_dict(row), min_observations=3)
        assert bench["enough_observations"] is False
        assert bench["observations"] == 1
        assert "yeterli gözlem yok" in bench["message"]


def test_segment_benchmark_excludes_test_fixtures():
    with _session() as session:
        # 3 fixture → benchmark GÖRMEZ (üretim değil); 1 üretim gözlemi kalır
        for _ in range(3):
            record_consumer_sale(session, _valid(), origin=ORIGIN_TEST_FIXTURE)
        record_consumer_sale(session, _valid(), origin=ORIGIN_CONSUMER_SUBMISSION)
        session.commit()
        bench = segment_benchmark(
            session, {"province": "İstanbul", "property_type": "konut"}, min_observations=3
        )
        assert bench["observations"] == 1  # yalnızca üretim gözlemi sayılır
        assert bench["enough_observations"] is False


def test_segment_benchmark_enough_returns_aggregates():
    with _session() as session:
        for _ in range(3):
            record_consumer_sale(session, _valid(), origin=ORIGIN_CONSUMER_SUBMISSION)
        session.commit()
        bench = segment_benchmark(
            session, {"province": "İstanbul", "property_type": "konut"}, min_observations=3
        )
        assert bench["enough_observations"] is True
        assert bench["observations"] == 3
        assert bench["median_final_ask_to_close_gap_pct"] is not None
