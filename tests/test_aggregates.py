"""Eşlenmemiş TOPLU (cohort) gözlem soyutlaması testleri.

Metodolojik çekirdek: sunulan envanter ile kümülatif gerçekleşen satışlar FARKLI
popülasyonlardır → EŞLEŞTİRİLMEZ, closing indirimi HESAPLANMAZ, RealizedLabel'a
ZORLANMAZ. Kaynak: Park Mavera III (PMVR3) 31 Aralık 2019 açıklaması.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sold.db.models import Base
from sold.labels import (
    AggregateError,
    asking_to_closing_labels,
    load_aggregates,
    mine_aggregates,
    normalize_aggregate,
    persist_aggregates,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


# Park Mavera III — operatörün elle DENETLEDİĞİ oda-tipi strata (31 Aralık 2019 resmî açıklaması)
_OFFERED_STRATA = [
    {"room_type": "2+1", "count": 80, "total_price": 92375000.00, "total_gross_m2": 10779.01, "average_price": 1154687.50, "average_m2_price": 8569.90},
    {"room_type": "3+1", "count": 2, "total_price": 3144000.00, "total_gross_m2": 374.02, "average_price": 1572000.00, "average_m2_price": 8405.97},
    {"room_type": "4+1", "count": 2, "total_price": 2995000.00, "total_gross_m2": 414.28, "average_price": 1497500.00, "average_m2_price": 7229.41},
    {"room_type": "5+1", "count": 0, "total_price": 0.0, "total_gross_m2": 0.0, "average_price": 0.0, "average_m2_price": 0.0},
]
_REALIZED_STRATA = [
    {"room_type": "2+1", "count": 165, "total_price": 118418019.69, "total_gross_m2": 22088.31, "average_price": 717684.97, "average_m2_price": 5361.12},
    {"room_type": "3+1", "count": 78, "total_price": 78003128.35, "total_gross_m2": 14568.99, "average_price": 1000040.11, "average_m2_price": 5354.05},
    {"room_type": "4+1", "count": 24, "total_price": 28932145.55, "total_gross_m2": 5143.10, "average_price": 1205506.06, "average_m2_price": 5625.43},
    {"room_type": "5+1", "count": 120, "total_price": 160630903.93, "total_gross_m2": 27511.67, "average_price": 1338590.87, "average_m2_price": 5838.65},
]

# Park Mavera III — operatörün elle denetleyip çıkardığı YAPISAL açıklama (strata dahil)
_PMVR3 = {
    "kind": "project_disclosure",
    "project_id": "PMVR3",
    "as_of_date": "2019-12-31",
    "disclosure_title": "Projede Benzer Nitelikte Olan Bağımsız Bölümlerin Ortalama Satış Fiyatları (31 Aralık 2019)",
    "populations": [
        {"observation_role": "offered_inventory", "count": 84, "total_price": 98514000.00, "average_price": 1172785.71, "strata": _OFFERED_STRATA},
        {"observation_role": "cumulative_realized_sales", "count": 387, "total_price": 385984197.52, "average_price": 997375.19, "strata": _REALIZED_STRATA},
    ],
}


# ---- adapter: iki EŞLENMEMİŞ gözlem ---------------------------------------- #
def test_project_disclosure_emits_two_unpaired_observations():
    obs = mine_aggregates("toki", [_PMVR3])
    assert len(obs) == 2
    by_role = {o["observation_role"]: o for o in obs}
    assert set(by_role) == {"offered_inventory", "cumulative_realized_sales"}

    offered = by_role["offered_inventory"]
    realized = by_role["cumulative_realized_sales"]
    assert offered["count"] == 84
    assert offered["total_price"] == 98514000.00
    assert offered["average_price"] == 1172785.71
    assert realized["count"] == 387
    assert realized["total_price"] == 385984197.52
    assert realized["average_price"] == 997375.19

    for o in obs:
        assert o["domain"] == "toki"
        assert o["label_source"] == "toki"
        assert o["aggregation_level"] == "cohort"
        assert o["comparison_scope"] == "unpaired_aggregate"
        assert o["project_id"] == "PMVR3"
        assert o["as_of_date"] == dt.date(2019, 12, 31)


def test_no_synthetic_pair_or_closing_discount():
    """İki popülasyon reference→realized ÇİFTİ olarak birleştirilmez; indirim yok."""
    obs = mine_aggregates("toki", [_PMVR3])
    for o in obs:
        # Yapısal olarak paired alanlar YOKTUR → eşleştirme/indirim imkânsız.
        assert "realized_price" not in o
        assert "reference_price" not in o
        assert "sale_mechanism" not in o
    # 1.172.785,71 ile 997.375,19 arasında hiçbir yerde indirim hesaplanmaz:
    # bunlar iki ayrı gözlem kaydında, ayrı role ile durur.
    avgs = {o["observation_role"]: o["average_price"] for o in obs}
    assert avgs["offered_inventory"] != avgs["cumulative_realized_sales"]


def test_aggregates_excluded_from_asking_to_closing():
    """Toplu gözlemler asking→closing head'ine ASLA giremez (paired df'e dönüşemez)."""
    obs = mine_aggregates("toki", [_PMVR3])
    import pandas as pd

    df = pd.DataFrame(obs)
    # Toplu gözlem DataFrame'inde asking→closing filtresinin aradığı kolonlar yoktur.
    for col in ("reference_price_type", "sale_mechanism", "realized_price"):
        assert col not in df.columns
    # Paired etiket filtresi yalnızca RealizedLabel şemasına uygulanır; boş df boş döner.
    assert asking_to_closing_labels(pd.DataFrame()).empty


# ---- normalize doğrulama --------------------------------------------------- #
def _raw(**over):
    base = {
        "domain": "toki",
        "aggregation_level": "cohort",
        "comparison_scope": "unpaired_aggregate",
        "observation_role": "offered_inventory",
        "count": 84,
        "total_price": 98514000.00,
    }
    base.update(over)
    return base


def test_normalize_derives_average_when_absent():
    out = normalize_aggregate(_raw(count=4, total_price=1000.0))
    assert out["average_price"] == 250.0


def test_normalize_preserves_reported_average():
    out = normalize_aggregate(_raw(average_price=1172785.71))
    assert out["average_price"] == 1172785.71  # yeniden hesaplanıp üzerine yazılmaz


@pytest.mark.parametrize(
    "over",
    [
        {"domain": "xyz"},
        {"aggregation_level": "unit"},
        {"comparison_scope": "paired"},
        {"observation_role": "sold"},
        {"count": 0},
        {"total_price": 0},
    ],
)
def test_normalize_rejects_invalid(over):
    with pytest.raises(AggregateError):
        normalize_aggregate(_raw(**over))


def test_mine_aggregates_unknown_source():
    with pytest.raises(AggregateError):
        mine_aggregates("sahibinden", [_PMVR3])


# ---- strata: TAM normalize + tutarlılık (havuzlanmaz) ---------------------- #
def test_pmvr3_strata_normalized_complete():
    """Park Mavera III oda-tipi strata TAM ve normalize edilmiş korunur.

    Sadece 'strata korunabiliyor' demez; DENETLENMİŞ tüm değerleri (room_type, count,
    total_price, total_gross_m2, average_price, average_m2_price) ve strata↔popülasyon
    tutarlılığını (toplam sayı/bedel DEĞİŞMEDEN eşleşir) doğrular. EŞLEŞTİRME YOKTUR.
    """
    from sold.labels.aggregates import STRATUM_FIELDS

    obs = {o["observation_role"]: o for o in mine_aggregates("toki", [_PMVR3])}
    offered = obs["offered_inventory"]
    realized = obs["cumulative_realized_sales"]

    # (1) TAM normalize strata — her denetlenmiş alan aynen korunur
    assert offered["strata"] == _OFFERED_STRATA
    assert realized["strata"] == _REALIZED_STRATA

    # (2) Kanonik 6 alan + oda-tipi sırası (2+1/3+1/4+1/5+1)
    for strata in (offered["strata"], realized["strata"]):
        assert [s["room_type"] for s in strata] == ["2+1", "3+1", "4+1", "5+1"]
        for s in strata:
            assert set(s) == set(STRATUM_FIELDS)

    # (3) strata ↔ popülasyon tutarlılığı: toplamlar DEĞİŞMEDEN eşleşir
    assert sum(s["count"] for s in offered["strata"]) == 84
    assert sum(s["count"] for s in realized["strata"]) == 387
    assert abs(sum(s["total_price"] for s in offered["strata"]) - 98514000.00) < 0.01
    assert abs(sum(s["total_price"] for s in realized["strata"]) - 385984197.52) < 0.01
    assert offered["total_price"] == 98514000.00  # popülasyon toplamı DEĞİŞMEDİ
    assert realized["total_price"] == 385984197.52


# ---- kalıcılık (AYRI tablo) ------------------------------------------------ #
def test_persist_and_load_aggregates_roundtrip():
    session = _session()
    obs = mine_aggregates("toki", [_PMVR3])
    n = persist_aggregates(session, obs)
    session.commit()
    assert n == 2
    df = load_aggregates(session, domain="toki")
    assert len(df) == 2
    assert set(df["observation_role"]) == {"offered_inventory", "cumulative_realized_sales"}
    realized = df[df["observation_role"] == "cumulative_realized_sales"].iloc[0]
    assert realized["count"] == 387
    assert float(realized["total_price"]) == 385984197.52
    assert len(realized["strata"]) == 4  # strata JSON kolonu round-trip eder
    # rol filtresi
    only = load_aggregates(session, observation_role="offered_inventory")
    assert len(only) == 1
    assert only.iloc[0]["count"] == 84
