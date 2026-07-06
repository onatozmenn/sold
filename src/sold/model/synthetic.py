"""Sentetik konut piyası üreteci — GERÇEK TCMB ekspertiz TL/m²'ye kalibre.

Gerçek tek-tek ilan/satış verisi Türkiye'de yasal olarak halka açık DEĞİLDİR
(yalnızca kazımayla, o da ToS/yasa dışı). Bu yüzden per-listing veriyi simüle
ederiz; ANCAK fiyat SEVİYELERİ uydurma değildir: her ilin taban TL/m² değeri
TCMB'nin ekspertiz tabanlı GERÇEK birim fiyatlarından gelir (EVDS bie_birimfiyat →
datasets/unit_prices.csv). Yani seviyeler + iller arası fark + (KFE ile) trend
GERÇEK; yalnızca her dairenin bireysel sapması ve gizli ``true_realized_price``
simüledir. MEVA/Endeksa/TCMB de ground-truth olarak ekspertize dayanır.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

UNIT_PRICES_CSV = Path("datasets/unit_prices.csv")

# Gerçek TCMB ekspertiz TL/m² (2026-Q1, EVDS bie_birimfiyat). datasets/unit_prices.csv
# mevcutsa CANLI değerler kullanılır; yoksa bu snapshot'a (hermetik test) düşülür.
REAL_PPM2_SNAPSHOT: dict[str, float] = {
    "İstanbul": 79306, "Muğla": 79110, "Antalya": 54443, "İzmir": 52456,
    "Balıkesir": 45904, "Ankara": 44036, "Kocaeli": 42338, "Denizli": 40428,
    "Manisa": 39555, "Sakarya": 39348, "Bursa": 39327, "Adana": 38369,
    "Eskişehir": 37465, "Mersin": 37068, "Konya": 34796, "Trabzon": 34866,
    "Samsun": 34374, "Diyarbakır": 34277, "Gaziantep": 31170, "Kayseri": 27879,
    "Hatay": 26923, "Erzurum": 26245, "Şanlıurfa": 25448, "Malatya": 24070,
}

# İl piyasa ağırlıkları (kabaca işlem hacmi payı) — örnekleme dağılımı için.
MARKET_WEIGHTS: dict[str, float] = {
    "İstanbul": 20, "Ankara": 10, "İzmir": 8, "Bursa": 6, "Antalya": 6,
    "Kocaeli": 4, "Adana": 4, "Konya": 4, "Sakarya": 3, "Mersin": 3,
    "Gaziantep": 3, "Muğla": 3, "Denizli": 3, "Manisa": 3, "Balıkesir": 3,
    "Kayseri": 3, "Samsun": 2, "Eskişehir": 2, "Trabzon": 2, "Hatay": 2,
    "Diyarbakır": 2, "Şanlıurfa": 2, "Malatya": 2, "Erzurum": 2,
}


def load_province_ppm2(path: Path = UNIT_PRICES_CSV) -> dict[str, float]:
    """İl -> gerçek TL/m² (en güncel). CSV yoksa/bozuksa baked snapshot'a düşer."""
    try:
        if path.exists():
            df = pd.read_csv(path)
            if not df.empty and {"province", "period", "tl_m2"}.issubset(df.columns):
                latest = df.sort_values("period").groupby("province").tail(1)
                live = {
                    str(r["province"]): float(r["tl_m2"]) for _, r in latest.iterrows()
                }
                out = {p: live[p] for p in MARKET_WEIGHTS if p in live}
                if out:
                    return out
    except Exception:  # noqa: BLE001 — dosya bozuksa yedeğe düş
        pass
    return {p: v for p, v in REAL_PPM2_SNAPSHOT.items() if p in MARKET_WEIGHTS}


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
    """Gözlemlenebilir özellikler + gizli ``true_realized_price`` üretir.

    Taban TL/m² GERÇEK (il bazlı TCMB ekspertiz); iller piyasa ağırlığıyla
    örneklenir, il içi ilçe farkı simüle bir çarpanla eklenir.
    """
    rng = np.random.default_rng(seed)

    ppm2_map = load_province_ppm2()
    provinces = np.array(list(ppm2_map))
    weights = np.array([MARKET_WEIGHTS.get(p, 1.0) for p in provinces], dtype=float)
    weights = weights / weights.sum()
    province = rng.choice(provinces, size=n, p=weights)
    province_ppm2 = np.array([ppm2_map[p] for p in province])

    # İl içi ilçe/bölge farkı — gerçek ilçe TL/m² kamuya açık olmadığından simüle.
    zone = rng.integers(1, 7, n)
    district = np.array([f"{province[i]}-B{zone[i]}" for i in range(n)])
    base_ppm2 = province_ppm2 * np.exp(rng.normal(0.0, 0.16, n))
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

    # Piyasa sıcaklığı (talep): >1 hareketli, <1 durgun. Gerçekte TÜİK konut
    # satış hacminden (market_heat) gelir; sentetikte gerçekçi dağılımla üretip
    # gerçekleşen fiyatı/likiditeyi etkilemesine izin veririz (model öğrensin).
    market_heat = np.exp(rng.normal(0.0, 0.18, n))

    # Satıcılar gerçek değerin üstünde fiyat koyar (aspirasyonel markup).
    markup = rng.uniform(0.05, 0.28, n)
    initial_price = true_value * (1 + markup)

    # Yüksek markup -> daha uzun time-on-market; sıcak piyasa -> daha kısa.
    days_on_market = (
        (rng.exponential(35, n) + 250 * markup) / np.clip(market_heat, 0.6, 1.6)
    ).astype(int)
    num_price_changes = rng.poisson(np.clip(days_on_market / 45.0, 0, None)).astype(int)
    observed_drop = np.minimum(markup * rng.uniform(0.2, 0.7, n), 0.22)
    observed_drop = np.where(num_price_changes > 0, observed_drop, 0.0)
    last_price = initial_price * (1 - observed_drop)

    # Gerçekleşen satış ~ gerçek değer çevresinde küçük pazarlık gürültüsü;
    # sıcak piyasa (market_heat>1) satıcı lehine → gerçekleşen daha yüksek (az indirim).
    realized = true_value * np.exp(
        rng.normal(-0.015, 0.03, n) + 0.06 * (market_heat - 1.0)
    )
    is_delisted = rng.random(n) < 0.80
    total_drop_pct = (last_price - initial_price) / initial_price * 100.0

    return pd.DataFrame(
        {
            "source": "synthetic",
            "source_listing_id": [f"SYN-{i:05d}" for i in range(n)],
            "listing_type": "sale",
            "province": province,
            "district": district,
            "neighborhood": neighborhood,
            "lat": np.nan,
            "lon": np.nan,
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
            "market_heat": market_heat.round(4),
            "total_drop_pct": total_drop_pct,
            "is_delisted": is_delisted,
            "true_realized_price": realized.round(0),
        }
    )
