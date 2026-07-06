"""Gerçek-veri değerleme (RealValuator) birim testleri — mock yok."""

from __future__ import annotations

import pandas as pd
import pytest

from sold.features.demand import HeatIndex
from sold.model.valuation import (
    DEFAULT_DISCOUNT,
    RealValuator,
    effective_discount,
)

_EMPTY_HEAT = HeatIndex(pd.DataFrame(columns=["province", "ym", "market_heat"]))


def _rv() -> RealValuator:
    return RealValuator(
        ppm2_map={"İstanbul": 80000.0, "Ankara": 44000.0}, heat_index=_EMPTY_HEAT
    )


def test_effective_discount_published_base():
    assert effective_discount("İstanbul", 1.0) == 0.10
    assert effective_discount("Ankara", 1.0) == 0.05
    assert effective_discount("İzmir", 1.0) == 0.08
    assert effective_discount("Bilinmeyenİl", 1.0) == DEFAULT_DISCOUNT


def test_effective_discount_demand_adjustment_and_bounds():
    # Soğuk piyasa (heat<1) → daha çok pazarlık; sıcak (heat>1) → daha az.
    assert effective_discount("İstanbul", 0.5) > 0.10
    assert effective_discount("İstanbul", 1.5) < 0.10
    # Sınırlar [0.02, 0.20] içinde.
    assert 0.02 <= effective_discount("Ankara", 0.5) <= 0.20
    assert 0.02 <= effective_discount("İstanbul", 1.5) <= 0.20
    # NaN talep → taban paya düşer.
    assert effective_discount("İstanbul", float("nan")) == 0.10


def test_estimate_applies_discount_to_asking():
    df = pd.DataFrame([{"province": "İstanbul", "last_price": 3_200_000.0}])
    sold = _rv().estimate(df)[0]
    assert sold == pytest.approx(3_200_000.0 * 0.90)  # %10 pazarlık


def test_estimate_uses_market_heat_column():
    df = pd.DataFrame(
        [{"province": "İstanbul", "last_price": 1_000_000.0, "market_heat": 0.5}]
    )
    sold = _rv().estimate(df)[0]
    # heat 0.5 → indirim 0.10×(2−0.5)=0.15 → satış 850k
    assert sold == pytest.approx(850_000.0)


def test_market_value_from_real_tl_m2():
    rv = _rv()
    assert rv.market_value("İstanbul", 100) == pytest.approx(8_000_000.0)
    assert rv.market_value("Ankara", 120) == pytest.approx(5_280_000.0)
    assert rv.market_value("İstanbul", None) is None
    assert rv.market_value("BilinmeyenİL", 100) is None
