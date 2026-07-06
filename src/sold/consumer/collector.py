"""Tüketici (ev satmış kişi) öz-beyan satış toplayıcı — DOĞRUDAN asking→closing edinimi.

Projenin çözülmemiş çekirdek sorunu: sıradan ikinci-el konutta GERÇEK
``asking → closing`` etiketini edinmek (modelleme değil, ETİKET edinimi). Bu modül,
ürünün KENDİ edinim yolundan (tüketici formu) gelen satışları toplar ve her başarılı
satıştan provenance-aware DOĞRUDAN bir etiket türetir. Etiket zinciri SABİTTİR:

    domain               = consumer
    label_source         = seller_self_reported
    sale_mechanism       = ordinary_resale
    reference_price_type = asking   (referans = SON ilan fiyatı)
    label_confidence     = B        (öz-beyan; bağımsız doğrulama YOK → asla otomatik A)

Bu etiketler ``asking_to_closing_labels()``'e GİREBİLİR. Kamu (UYAP/KAP/TOKİ)
gözlemleri HARİÇ kalmaya devam eder (onlar FairValue kalibrasyonuna gider).

KVKK SINIRI: KİŞİSEL VERİ TOPLANMAZ. Ad, TCKN, tam adres, tapu, banka dekontu,
alıcı/satıcı kimliği gibi alanlar gelirse kayıt REDDEDİLİR (bkz.
``FORBIDDEN_PERSONAL_KEYS``). Konum en fazla ilçe düzeyindedir (mahalle/adres YOK).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ConsumerSale, RealizedLabel
from ..labels.registry import normalize_label

# SABİT tüketici öz-beyan provenance zinciri
CONSUMER_DOMAIN = "consumer"
CONSUMER_LABEL_SOURCE = "seller_self_reported"
CONSUMER_SALE_MECHANISM = "ordinary_resale"
CONSUMER_REFERENCE_TYPE = "asking"
CONSUMER_CONFIDENCE = "B"
DEFAULT_PROPERTY_TYPE = "konut"

# Toplanan alanlar (KVKK: yalnızca nesnel taşınmaz + fiyat/tarih; kişisel veri YOK)
COLLECTED_FIELDS = (
    "initial_asking_price",
    "final_asking_price",
    "closing_price",
    "listing_date",
    "closing_date",
    "province",
    "district",
    "property_type",
    "gross_m2",
    "room_count",
    "price_cut_count",
)

# KVKK SINIRI — bu alanlar ASLA toplanmaz; gelirse kayıt reddedilir (savunma amaçlı).
FORBIDDEN_PERSONAL_KEYS = frozenset(
    {
        "name", "full_name", "fullname", "ad", "adsoyad", "ad_soyad", "isim",
        "soyad", "surname",
        "tckn", "tc", "tc_kimlik", "tckimlik", "national_id", "identity", "kimlik",
        "phone", "telefon", "gsm", "mobile", "cep", "email", "e_posta", "eposta", "mail",
        "address", "adres", "full_address", "acik_adres", "tam_adres", "street", "sokak",
        "tapu", "deed", "title_deed", "tapu_no", "parcel",
        "iban", "bank", "banka", "bank_receipt", "dekont", "receipt",
        "buyer", "seller", "alici", "satici", "buyer_name", "seller_name",
        "satici_name", "alici_name",
    }
)


class ConsumerSaleError(ValueError):
    """Geçersiz tüketici satış kaydı (eksik zorunlu alan veya KVKK ihlali)."""


# --------------------------------------------------------------------------- #
# Küçük tip yardımcıları (NaN/boş -> None)
# --------------------------------------------------------------------------- #
def _f(v: object) -> float | None:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: object) -> int | None:
    f = _f(v)
    return int(f) if f is not None else None


def _s(v: object) -> str | None:
    if v is None or (isinstance(v, float) and v != v) or v == "":
        return None
    return str(v)


def _date(v: object) -> dt.date | None:
    if not v:
        return None
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Doğrulama (KVKK reddi + zorunlu alanlar)
# --------------------------------------------------------------------------- #
def _reject_personal_data(raw: dict) -> None:
    """Kişisel/gereksiz veri anahtarı varsa reddeder (KVKK sınırı)."""
    present = {str(k).strip().lower() for k in raw.keys()}
    hit = present & FORBIDDEN_PERSONAL_KEYS
    if hit:
        raise ConsumerSaleError(
            "KVKK: kişisel/gereksiz veri toplanmaz. Reddedilen alan(lar): "
            + ", ".join(sorted(hit))
        )


def validate_consumer_sale(raw: dict) -> dict:
    """Ham tüketici satışını doğrular/normalize eder.

    Zorunlu: ``final_asking_price`` > 0 ve ``closing_price`` > 0. ``days_to_close``
    verilmemişse ilan→kapanış tarihinden türetilir. ``property_type`` boşsa 'konut'.
    Kişisel veri anahtarı varsa ``ConsumerSaleError`` yükseltir.
    """
    _reject_personal_data(raw)

    final_ask = _f(raw.get("final_asking_price"))
    closing = _f(raw.get("closing_price"))
    if final_ask is None or final_ask <= 0:
        raise ConsumerSaleError("final_asking_price (son ilan fiyatı) zorunlu ve > 0 olmalı.")
    if closing is None or closing <= 0:
        raise ConsumerSaleError("closing_price (gerçekleşen satış fiyatı) zorunlu ve > 0 olmalı.")

    listing_date = _date(raw.get("listing_date"))
    closing_date = _date(raw.get("closing_date"))
    days_to_close = _i(raw.get("days_to_close"))
    if days_to_close is None and listing_date and closing_date:
        delta = (closing_date - listing_date).days
        days_to_close = delta if delta >= 0 else None

    return {
        "initial_asking_price": _f(raw.get("initial_asking_price")),
        "final_asking_price": final_ask,
        "closing_price": closing,
        "price_cut_count": _i(raw.get("price_cut_count")) or 0,
        "listing_date": listing_date,
        "closing_date": closing_date,
        "days_to_close": days_to_close,
        "province": _s(raw.get("province")),
        "district": _s(raw.get("district")),
        "property_type": _s(raw.get("property_type")) or DEFAULT_PROPERTY_TYPE,
        "gross_m2": _f(raw.get("gross_m2")),
        "room_count": _s(raw.get("room_count")),
        "domain": CONSUMER_DOMAIN,
        "label_source": CONSUMER_LABEL_SOURCE,
        "sale_mechanism": CONSUMER_SALE_MECHANISM,
        "reference_price_type": CONSUMER_REFERENCE_TYPE,
        "label_confidence": CONSUMER_CONFIDENCE,
    }


# --------------------------------------------------------------------------- #
# Etikete çevirme (SABİT provenance) + kalıcılık
# --------------------------------------------------------------------------- #
def sale_label_dict(sale: dict, external_ref: str | None = None) -> dict:
    """Doğrulanmış tüketici satışını registry etiket dict'ine çevirir.

    reference_price = SON ilan fiyatı (final_asking), realized_price = kapanış bedeli.
    Bu dict ``normalize_label``'dan geçince ``asking_to_closing_labels()``'e UYGUN olur.
    """
    return {
        "domain": CONSUMER_DOMAIN,
        "label_source": CONSUMER_LABEL_SOURCE,
        "sale_mechanism": CONSUMER_SALE_MECHANISM,
        "reference_price_type": CONSUMER_REFERENCE_TYPE,
        "reference_price": sale.get("final_asking_price"),
        "realized_price": sale.get("closing_price"),
        "related_party": False,
        "province": sale.get("province"),
        "district": sale.get("district"),
        "property_type": sale.get("property_type"),
        "gross_m2": sale.get("gross_m2"),
        "transaction_date": sale.get("closing_date"),
        "label_confidence": CONSUMER_CONFIDENCE,
        "external_ref": external_ref,
    }


def record_consumer_sale(session: Session, raw: dict) -> ConsumerSale:
    """Tüketici satışını doğrular, ``consumer_sales``'e yazar VE aynı transaction'da
    doğrudan bir ``RealizedLabel`` üretir.

    Böylece ``load_labels()`` → ``asking_to_closing_labels()`` bu etiketi otomatik
    görür (milestone: asking→closing head'ini 0'dan ≥1 gerçek etikete taşımak).
    ``ConsumerSale`` zengin ham alanları (ilk ilan, tarihler, fiyat-kesinti) analitik
    için tutar; ``RealizedLabel`` normalize edilmiş registry kaydıdır. İkisi tek
    transaction'da yazılır; ``external_ref`` ile ilişkilendirilir.
    """
    v = validate_consumer_sale(raw)
    row = ConsumerSale(
        initial_asking_price=v["initial_asking_price"],
        final_asking_price=v["final_asking_price"],
        closing_price=v["closing_price"],
        price_cut_count=v["price_cut_count"],
        listing_date=v["listing_date"],
        closing_date=v["closing_date"],
        days_to_close=v["days_to_close"],
        province=v["province"],
        district=v["district"],
        property_type=v["property_type"],
        gross_m2=v["gross_m2"],
        room_count=v["room_count"],
        domain=v["domain"],
        label_source=v["label_source"],
        sale_mechanism=v["sale_mechanism"],
        reference_price_type=v["reference_price_type"],
        label_confidence=v["label_confidence"],
    )
    session.add(row)
    session.flush()  # row.id erişimi için
    label = normalize_label(sale_label_dict(v, external_ref=f"consumer_sale:{row.id}"))
    session.add(RealizedLabel(**label))
    session.flush()
    return row


# --------------------------------------------------------------------------- #
# Okuma
# --------------------------------------------------------------------------- #
_CONSUMER_COLUMNS = [
    "id",
    "initial_asking_price",
    "final_asking_price",
    "closing_price",
    "price_cut_count",
    "listing_date",
    "closing_date",
    "days_to_close",
    "province",
    "district",
    "property_type",
    "gross_m2",
    "room_count",
    "label_source",
    "sale_mechanism",
    "label_confidence",
]


def sale_as_dict(row: ConsumerSale) -> dict:
    """Bir ``ConsumerSale`` ORM satırını analitiğe uygun düz dict'e çevirir."""
    return {
        "id": row.id,
        "initial_asking_price": _f(row.initial_asking_price),
        "final_asking_price": _f(row.final_asking_price),
        "closing_price": _f(row.closing_price),
        "price_cut_count": row.price_cut_count,
        "listing_date": row.listing_date,
        "closing_date": row.closing_date,
        "days_to_close": row.days_to_close,
        "province": row.province,
        "district": row.district,
        "property_type": row.property_type,
        "gross_m2": _f(row.gross_m2),
        "room_count": row.room_count,
        "label_source": row.label_source,
        "sale_mechanism": row.sale_mechanism,
        "label_confidence": row.label_confidence,
    }


def load_consumer_sales(session: Session) -> pd.DataFrame:
    """Tüm tüketici satışlarını DataFrame olarak yükler (analitik + benchmark)."""
    rows = session.scalars(select(ConsumerSale)).all()
    if not rows:
        return pd.DataFrame(columns=_CONSUMER_COLUMNS)
    return pd.DataFrame([sale_as_dict(r) for r in rows])
