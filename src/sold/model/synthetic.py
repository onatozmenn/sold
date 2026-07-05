"""Sentetik konut piyasası üreteci (motoru doğrulamak için).

Gerçek satış fiyatı verisi kıt olduğundan, tahmincinin doğruluğunu ölçmenin
bilimsel yolu, GERÇEĞİN BİLİNDİĞİ sentetik bir piyasa kurmaktır. Burada her ilan
için gizli bir ``true_realized_price`` üretilir; gözlemlenebilir sinyaller
(ilan fiyatları, time-on-market, fiyat düşüşleri) bundan türetilir. Motor daha
sonra sadece gözlemlenebilir sinyallerden ``true_realized_price``'ı geri
kurmaya çalışır.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# İllustratif ilçe bazlı TL/m² taban değerleri (gerçek değil, göstermelik).
DISTRICT_PPM2 = {
    "Kadıköy": 92000.0,
    "Beşiktaş": 125000.0,
    "Üsküdar": 82000.0,
    "Ataşehir": 88000.0,
    "Bağcılar": 46000.0,
    "Esenyurt": 39000.0,
}

HEATING_OPTIONS = np.array(
    ["Kombi (Doğalgaz)", "Merkezi (Pay Ölçer)", "Klima", "Yok"]
)
HEATING_FACTOR = {
    "Kombi (Doğalgaz)": 1.00,
    "Merkezi (Pay Ölçer)": 1.03,
    "Klima": 0.98,
    "Yok": 0.93,
}


def generate_market(n: int = 2500, seed: int = 42) -> pd.DataFrame:
    """Gözlemlenebilir özellikler + gizli ``true_realized_price`` üretir."""
    rng = np.random.default_rng(seed)

    district_names = np.array(list(DISTRICT_PPM2))
    district = district_names[rng.integers(0, len(district_names), n)]
    base_ppm2 = np.array([DISTRICT_PPM2[d] for d in district])
    neighborhood = np.array(
        [f"{district[i]}-M{rng.integers(1, 6)}" for i in range(n)]
    )

    gross_m2 = rng.uniform(55, 210, n)
    building_age = rng.integers(0, 40, n)
    floor = rng.integers(0, 18, n)
    total_floors = floor + rng.integers(1, 9, n)
    room_count_num = np.clip(np.round(gross_m2 / 38.0), 1, 6)
    heating = HEATING_OPTIONS[rng.integers(0, len(HEATING_OPTIONS), n)]
    heat_factor = np.array([HEATING_FACTOR[h] for h in heating])

    age_factor = np.clip(1 - 0.006 * building_age, 0.70, 1.0)
    floor_factor = np.clip(1 + 0.004 * floor - 0.02 * (floor == 0), 0.95, 1.10)
    noise = np.exp(rng.normal(0.0, 0.12, n))

    true_value = gross_m2 * base_ppm2 * age_factor * floor_factor * heat_factor * noise

    # Satıcılar gerçek değerin üstünde fiyat koyar (aspirasyonel markup).
    markup = rng.uniform(0.05, 0.28, n)
    initial_price = true_value * (1 + markup)

    # Yüksek markup -> daha uzun time-on-market ve daha çok/derin indirim.
    days_on_market = (rng.exponential(35, n) + 250 * markup).astype(int)
    num_price_changes = rng.poisson(np.clip(days_on_market / 45.0, 0, None)).astype(int)
    observed_drop = np.minimum(markup * rng.uniform(0.2, 0.7, n), 0.22)
    observed_drop = np.where(num_price_changes > 0, observed_drop, 0.0)
    last_price = initial_price * (1 - observed_drop)

    # Gerçekleşen satış ~ gerçek değer çevresinde küçük pazarlık gürültüsü.
    realized = true_value * np.exp(rng.normal(-0.015, 0.03, n))
    is_delisted = rng.random(n) < 0.80
    total_drop_pct = (last_price - initial_price) / initial_price * 100.0

    return pd.DataFrame(
        {
            "source": "synthetic",
            "source_listing_id": [f"SYN-{i:05d}" for i in range(n)],
            "listing_type": "sale",
            "province": "İstanbul",
            "district": district,
            "neighborhood": neighborhood,
            "lat": 41.0 + rng.normal(0, 0.05, n),
            "lon": 29.0 + rng.normal(0, 0.06, n),
            "gross_m2": gross_m2.round(0),
            "net_m2": (gross_m2 * 0.85).round(0),
            "room_count_num": room_count_num,
            "building_age": building_age,
            "floor": floor,
            "total_floors": total_floors,
            "heating": heating,
            "initial_price": initial_price.round(0),
            "last_price": last_price.round(0),
            "num_snapshots": num_price_changes + 1,
            "num_price_changes": num_price_changes,
            "days_on_market": days_on_market,
            "total_drop_pct": total_drop_pct,
            "is_delisted": is_delisted,
            "true_realized_price": realized.round(0),
        }
    )
