"""Gerçek gerçekleşen-satış etiketlerini yükleme/üretme/kaydetme (Faz 4).

Bir broker ya da SPK lisanslı değerleme (ekspertiz) firmasından gelen küçük ama
GERÇEK bir 'ilan fiyatı + gerçekleşen satış' seti, indirim modelini gerçek
etiketlerle eğitmenin ve motoru güvenilir biçimde doğrulamanın anahtarıdır.

Beklenen CSV şeması için ``GT_TEMPLATE_COLUMNS`` / ``write_template`` bakınız.
Gerçek broker verisi elde edilene kadar ``make_demo`` ile gerçekçi bir demo set
üretilebilir (sentetik piyasadan türetilir; longitudinal sinyaller içermez —
tıpkı gerçek bir broker dökümü gibi).
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import GroundTruthSale
from ..features.build import FEATURE_COLUMNS, parse_room_count

GT_REQUIRED = ["district", "gross_m2", "asking_price", "sold_price"]

GT_TEMPLATE_COLUMNS = [
    "source",
    "listing_type",
    "province",
    "district",
    "neighborhood",
    "gross_m2",
    "net_m2",
    "room_count",
    "building_age",
    "floor",
    "total_floors",
    "heating",
    "asking_price",
    "sold_price",
    "days_on_market",
    "sale_date",
]


# --------------------------------------------------------------------------- #
# Küçük tip yardımcıları (NaN -> None)
# --------------------------------------------------------------------------- #
def _num(value: object) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _str(value: object) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return str(value)


def _date(value: object) -> dt.date | None:
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == "":
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# CSV okuma / şablon / demo
# --------------------------------------------------------------------------- #
def read_csv(path: str | Path) -> pd.DataFrame:
    """Broker CSV'sini okur ve zorunlu sütunları doğrular."""
    df = pd.read_csv(path)
    missing = [c for c in GT_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(
            f"Eksik zorunlu sütun(lar): {missing}. Şablon için `sold gt template`."
        )
    return df


def write_template(path: str | Path) -> Path:
    """Beklenen sütunları içeren örnek bir CSV şablonu yazar."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    example = {
        "source": "broker-x",
        "listing_type": "sale",
        "province": "İstanbul",
        "district": "Kadıköy",
        "neighborhood": "Caferağa",
        "gross_m2": 120,
        "net_m2": 100,
        "room_count": "2+1",
        "building_age": 5,
        "floor": 3,
        "total_floors": 6,
        "heating": "Kombi (Doğalgaz)",
        "asking_price": 3200000,
        "sold_price": 3000000,
        "days_on_market": 40,
        "sale_date": "2026-03-15",
    }
    pd.DataFrame([example], columns=GT_TEMPLATE_COLUMNS).to_csv(path, index=False)
    return path


def make_demo(n: int = 500, seed: int = 123) -> pd.DataFrame:
    """Gerçekçi bir DEMO ground-truth seti (broker dökümü taklidi) üretir."""
    from ..model.synthetic import generate_market

    market = generate_market(n, seed)

    def room_label(value: float) -> str:
        rooms = int(value)
        return f"{rooms - 1}+1" if rooms >= 2 else "1+0"

    return pd.DataFrame(
        {
            "source": "broker-demo",
            "listing_type": market["listing_type"],
            "province": market["province"],
            "district": market["district"],
            "neighborhood": market["neighborhood"],
            "gross_m2": market["gross_m2"].astype(int),
            "net_m2": market["net_m2"].astype(int),
            "room_count": market["room_count_num"].map(room_label),
            "building_age": market["building_age"],
            "floor": market["floor"],
            "total_floors": market["total_floors"],
            "heating": market["heating"],
            "asking_price": market["last_price"].astype(int),
            "sold_price": market["true_realized_price"].astype(int),
            "days_on_market": market["days_on_market"],
        }
    )


# --------------------------------------------------------------------------- #
# Özellik çerçevesine dönüştürme (model eğitimi/değerlendirmesi için)
# --------------------------------------------------------------------------- #
def to_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Broker şemasındaki DataFrame'i model özellik çerçevesine çevirir.

    Broker verisinde longitudinal sinyaller (fiyat düşüş sayısı, toplam düşüş)
    yoktur; bunlar nötr (0) atanır. 'asking_price' son ilan fiyatı, 'sold_price'
    ise etiket (true_realized_price) olur.
    """
    n = len(df)

    def col(name: str, default: object = None) -> pd.Series:
        if name in df.columns:
            return df[name].reset_index(drop=True)
        return pd.Series([default] * n)

    frame = pd.DataFrame(
        {
            "source": col("source", "ground-truth"),
            "source_listing_id": [f"GT-{i:05d}" for i in range(n)],
            "listing_type": col("listing_type", "sale"),
            "province": col("province"),
            "district": col("district"),
            "neighborhood": col("neighborhood"),
            "lat": pd.to_numeric(col("lat"), errors="coerce"),
            "lon": pd.to_numeric(col("lon"), errors="coerce"),
            "gross_m2": pd.to_numeric(col("gross_m2"), errors="coerce"),
            "net_m2": pd.to_numeric(col("net_m2"), errors="coerce"),
            "room_count_num": col("room_count").map(parse_room_count),
            "building_age": pd.to_numeric(col("building_age"), errors="coerce"),
            "floor": pd.to_numeric(col("floor"), errors="coerce"),
            "total_floors": pd.to_numeric(col("total_floors"), errors="coerce"),
            "heating": col("heating"),
            "initial_price": pd.to_numeric(col("asking_price"), errors="coerce"),
            "last_price": pd.to_numeric(col("asking_price"), errors="coerce"),
            "num_snapshots": 1,
            "num_price_changes": 0,
            "days_on_market": pd.to_numeric(col("days_on_market"), errors="coerce"),
            "total_drop_pct": 0.0,
            "is_delisted": True,
            "true_realized_price": pd.to_numeric(col("sold_price"), errors="coerce"),
        }
    )
    # Talep sinyali (market heat): gerçek TÜİK konut satış hacminden, il + satış
    # ayına göre. sale_date/il yoksa 1.0'a (nötr) düşer.
    from ..features.demand import load_heat_index

    heat_df = pd.DataFrame({"province": col("province"), "sale_date": col("sale_date")})
    frame["market_heat"] = load_heat_index().attach(heat_df)["market_heat"].to_numpy()
    return frame.dropna(subset=["last_price", "true_realized_price"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Veritabanı kalıcılığı
# --------------------------------------------------------------------------- #
def persist_to_db(session: Session, df: pd.DataFrame, source: str | None = None) -> int:
    """Broker şemasındaki satırları ground_truth_sales tablosuna ekler."""
    count = 0
    for _, row in df.iterrows():
        session.add(
            GroundTruthSale(
                source=source or _str(row.get("source")) or "ground-truth",
                listing_type=_str(row.get("listing_type")),
                province=_str(row.get("province")),
                district=_str(row.get("district")),
                neighborhood=_str(row.get("neighborhood")),
                lat=_num(row.get("lat")),
                lon=_num(row.get("lon")),
                gross_m2=_num(row.get("gross_m2")),
                net_m2=_num(row.get("net_m2")),
                room_count=_str(row.get("room_count")),
                building_age=_int(row.get("building_age")),
                floor=_int(row.get("floor")),
                total_floors=_int(row.get("total_floors")),
                heating=_str(row.get("heating")),
                asking_price=_num(row.get("asking_price")),
                sold_price=_num(row.get("sold_price")),
                days_on_market=_int(row.get("days_on_market")),
                sale_date=_date(row.get("sale_date")),
            )
        )
        count += 1
    session.flush()
    return count


def load_frame_from_db(session: Session) -> pd.DataFrame:
    """ground_truth_sales tablosunu model özellik çerçevesine çevirir."""
    rows = session.scalars(select(GroundTruthSale)).all()
    if not rows:
        return pd.DataFrame(columns=[*FEATURE_COLUMNS, "true_realized_price"])
    broker = pd.DataFrame(
        [
            {
                "source": r.source,
                "listing_type": r.listing_type,
                "province": r.province,
                "district": r.district,
                "neighborhood": r.neighborhood,
                "lat": r.lat,
                "lon": r.lon,
                "gross_m2": _num(r.gross_m2),
                "net_m2": _num(r.net_m2),
                "room_count": r.room_count,
                "building_age": r.building_age,
                "floor": r.floor,
                "total_floors": r.total_floors,
                "heating": r.heating,
                "asking_price": _num(r.asking_price),
                "sold_price": _num(r.sold_price),
                "days_on_market": r.days_on_market,
            }
            for r in rows
        ]
    )
    return to_feature_frame(broker)
