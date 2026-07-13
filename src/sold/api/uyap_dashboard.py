"""Privacy-safe UYAP auction dashboard data contract."""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict

from sqlalchemy import select

from ..db import get_engine, get_sessionmaker, init_db
from ..db.models import RealizedLabel


PROVINCE_CENTERS = (
    ("Adana", 37.0000, 35.3213),
    ("Adıyaman", 37.7648, 38.2786),
    ("Afyonkarahisar", 38.7507, 30.5567),
    ("Ağrı", 39.7191, 43.0503),
    ("Amasya", 40.6499, 35.8353),
    ("Ankara", 39.9334, 32.8597),
    ("Antalya", 36.8969, 30.7133),
    ("Artvin", 41.1828, 41.8183),
    ("Aydın", 37.8450, 27.8396),
    ("Balıkesir", 39.6484, 27.8826),
    ("Bilecik", 40.0567, 30.0665),
    ("Bingöl", 38.8853, 40.4983),
    ("Bitlis", 38.4006, 42.1095),
    ("Bolu", 40.7350, 31.6061),
    ("Burdur", 37.7203, 30.2908),
    ("Bursa", 40.1885, 29.0610),
    ("Çanakkale", 40.1553, 26.4142),
    ("Çankırı", 40.6013, 33.6134),
    ("Çorum", 40.5506, 34.9556),
    ("Denizli", 37.7765, 29.0864),
    ("Diyarbakır", 37.9144, 40.2306),
    ("Edirne", 41.6771, 26.5557),
    ("Elazığ", 38.6743, 39.2232),
    ("Erzincan", 39.7500, 39.4900),
    ("Erzurum", 39.9043, 41.2679),
    ("Eskişehir", 39.7667, 30.5256),
    ("Gaziantep", 37.0662, 37.3833),
    ("Giresun", 40.9128, 38.3895),
    ("Gümüşhane", 40.4603, 39.4814),
    ("Hakkari", 37.5744, 43.7408),
    ("Hatay", 36.2021, 36.1600),
    ("Isparta", 37.7648, 30.5566),
    ("Mersin", 36.8121, 34.6415),
    ("İstanbul", 41.0082, 28.9784),
    ("İzmir", 38.4237, 27.1428),
    ("Kars", 40.6013, 43.0975),
    ("Kastamonu", 41.3887, 33.7827),
    ("Kayseri", 38.7205, 35.4826),
    ("Kırklareli", 41.7351, 27.2252),
    ("Kırşehir", 39.1425, 34.1709),
    ("Kocaeli", 40.8533, 29.8815),
    ("Konya", 37.8746, 32.4932),
    ("Kütahya", 39.4242, 29.9833),
    ("Malatya", 38.3552, 38.3095),
    ("Manisa", 38.6140, 27.4296),
    ("Kahramanmaraş", 37.5753, 36.9371),
    ("Mardin", 37.3212, 40.7245),
    ("Muğla", 37.2153, 28.3636),
    ("Muş", 38.9462, 41.7539),
    ("Nevşehir", 38.6244, 34.7239),
    ("Niğde", 37.9698, 34.6766),
    ("Ordu", 40.9862, 37.8797),
    ("Rize", 41.0201, 40.5234),
    ("Sakarya", 40.7731, 30.3948),
    ("Samsun", 41.2867, 36.3300),
    ("Siirt", 37.9333, 41.9500),
    ("Sinop", 42.0231, 35.1531),
    ("Sivas", 39.7477, 37.0179),
    ("Tekirdağ", 40.9780, 27.5110),
    ("Tokat", 40.3167, 36.5500),
    ("Trabzon", 41.0027, 39.7168),
    ("Tunceli", 39.1079, 39.5401),
    ("Şanlıurfa", 37.1674, 38.7955),
    ("Uşak", 38.6823, 29.4082),
    ("Van", 38.4891, 43.4089),
    ("Yozgat", 39.8181, 34.8147),
    ("Zonguldak", 41.4564, 31.7987),
    ("Aksaray", 38.3687, 34.0370),
    ("Bayburt", 40.2552, 40.2249),
    ("Karaman", 37.1759, 33.2287),
    ("Kırıkkale", 39.8468, 33.5153),
    ("Batman", 37.8812, 41.1351),
    ("Şırnak", 37.4187, 42.4918),
    ("Bartın", 41.6344, 32.3375),
    ("Ardahan", 41.1105, 42.7022),
    ("Iğdır", 39.8880, 44.0048),
    ("Yalova", 40.6500, 29.2667),
    ("Karabük", 41.2061, 32.6204),
    ("Kilis", 36.7184, 37.1212),
    ("Osmaniye", 37.0742, 36.2478),
    ("Düzce", 40.8438, 31.1565),
)


def _median(values: list[float]) -> float | None:
    return round(float(statistics.median(values)), 2) if values else None


def _record(row: RealizedLabel) -> dict:
    reference = float(row.reference_price)
    realized = float(row.realized_price)
    ratio = realized / reference
    return {
        "id": int(row.id),
        "record_ref": str(row.external_ref),
        "province": row.province,
        "property_type": row.property_type or "belirsiz",
        "appraised_value": round(reference, 2),
        "auction_price": round(realized, 2),
        "ratio": round(ratio, 4),
        "difference_pct": round((ratio - 1.0) * 100.0, 2),
        "transaction_date": row.transaction_date.isoformat() if row.transaction_date else None,
    }


def dashboard_data() -> dict:
    engine = get_engine()
    init_db(engine)
    with get_sessionmaker(engine)() as session:
        rows = session.scalars(
            select(RealizedLabel)
            .where(
                RealizedLabel.domain == "uyap",
                RealizedLabel.reference_price > 0,
                RealizedLabel.realized_price > 0,
            )
            .order_by(RealizedLabel.province, RealizedLabel.id)
        ).all()

    records = [_record(row) for row in rows]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[str(record["province"])].append(record)

    provinces = []
    for name, latitude, longitude in PROVINCE_CENTERS:
        province_records = grouped.get(name, [])
        provinces.append({
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
            "count": len(province_records),
            "median_appraised": _median([record["appraised_value"] for record in province_records]),
            "median_auction": _median([record["auction_price"] for record in province_records]),
            "median_ratio": _median([record["ratio"] for record in province_records]),
            "property_types": dict(Counter(record["property_type"] for record in province_records)),
        })

    ratios = [record["ratio"] for record in records]
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "summary": {
            "record_count": len(records),
            "represented_provinces": sum(province["count"] > 0 for province in provinces),
            "province_count": len(provinces),
            "median_appraised": _median([record["appraised_value"] for record in records]),
            "median_auction": _median([record["auction_price"] for record in records]),
            "median_ratio": _median(ratios),
            "below_appraisal_pct": round(
                100.0 * sum(ratio < 1.0 for ratio in ratios) / len(ratios), 1
            ) if ratios else None,
        },
        "provinces": provinces,
        "records": records,
        "semantics": {
            "reference": "Muhammen/ekspertiz değeri",
            "realized": "Kesinleşmiş ihale bedeli",
            "scope": "UYAP icra ihalesi; normal piyasa asking→closing değildir.",
        },
    }