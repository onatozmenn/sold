"""SQLAlchemy 2.0 ORM modelleri (longitudinal ilan deposu + kalibrasyon verisi).

KVKK NOTU: ``listings`` tablosunda KİŞİSEL VERİ (ilan sahibi adı, telefon vb.)
TUTULMAZ; yalnızca taşınmazın nesnel nitelikleri saklanır. Konum, ORM
tarafında taşınabilirlik için lat/lon olarak tutulur; PostgreSQL şemasında bu
alanlardan PostGIS ``geom`` sütunu üretilir (bkz. schema.sql).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Listing(Base):
    """Kaynak sitedeki benzersiz ilan (mümkün olduğunca durağan alanlar)."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_listing_id", name="uq_listing_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024))
    listing_type: Mapped[str | None] = mapped_column(String(16))  # sale | rent

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    delisted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active")

    # --- taşınmaz nitelikleri (kişisel veri YOK) ---
    province: Mapped[str | None] = mapped_column(String(64))
    district: Mapped[str | None] = mapped_column(String(64))
    neighborhood: Mapped[str | None] = mapped_column(String(128))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    gross_m2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    net_m2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    room_count: Mapped[str | None] = mapped_column(String(16))
    building_age: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    heating: Mapped[str | None] = mapped_column(String(64))

    snapshots: Mapped[list["ListingSnapshot"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class ListingSnapshot(Base):
    """Her tarama turunda ilanın fiyat/durum anlık görüntüsü (zaman serisi)."""

    __tablename__ = "listing_snapshots"
    __table_args__ = (
        UniqueConstraint("listing_id", "captured_at", name="uq_snapshot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE")
    )
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    price: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="TRY")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    days_on_market: Mapped[int | None] = mapped_column(Integer)

    listing: Mapped["Listing"] = relationship(back_populates="snapshots")


class PriceChange(Base):
    """Türetilmiş: ilan fiyatındaki değişimler (asking-side pazarlık sinyali)."""

    __tablename__ = "price_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE")
    )
    changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    old_price: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    new_price: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    pct_change: Mapped[float] = mapped_column(Float, nullable=False)


class EvdsObservation(Base):
    """TCMB EVDS gözlemleri (kalibrasyon / ground truth)."""

    __tablename__ = "evds_observations"
    __table_args__ = (
        UniqueConstraint("series_code", "obs_date", name="uq_evds_obs"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_code: Mapped[str] = mapped_column(String(64), nullable=False)
    series_name: Mapped[str | None] = mapped_column(String(256))
    obs_date: Mapped[dt.date] = mapped_column(nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric(18, 4))


class TuikHouseSale(Base):
    """TÜİK konut satış adetleri (talep hacmi / likidite)."""

    __tablename__ = "tuik_house_sales"
    __table_args__ = (
        UniqueConstraint("province", "period", "sale_type", name="uq_tuik_sales"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    province: Mapped[str | None] = mapped_column(String(64))
    period: Mapped[dt.date] = mapped_column(nullable=False)
    sales_count: Mapped[int | None] = mapped_column(Integer)
    sale_type: Mapped[str | None] = mapped_column(String(64))


class CrawlRun(Base):
    """Bir tarama turunun künyesi (izleme / kalite takibi)."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    listings_seen: Mapped[int] = mapped_column(Integer, default=0)
    new_listings: Mapped[int] = mapped_column(Integer, default=0)
    price_changes: Mapped[int] = mapped_column(Integer, default=0)
    delisted: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    note: Mapped[str | None] = mapped_column(String(256))


class GroundTruthSale(Base):
    """Gerçek (broker/ekspertiz) gerçekleşen satış etiketi (Faz 4)."""

    __tablename__ = "ground_truth_sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str | None] = mapped_column(String(64))
    listing_type: Mapped[str | None] = mapped_column(String(16))
    province: Mapped[str | None] = mapped_column(String(64))
    district: Mapped[str | None] = mapped_column(String(64))
    neighborhood: Mapped[str | None] = mapped_column(String(128))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    gross_m2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    net_m2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    room_count: Mapped[str | None] = mapped_column(String(16))
    building_age: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    heating: Mapped[str | None] = mapped_column(String(64))
    asking_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    sold_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    days_on_market: Mapped[int | None] = mapped_column(Integer)
    sale_date: Mapped[dt.date | None] = mapped_column()
    # Etiket kanıtı (provenance): her etiket eşit güvenilir değildir.
    sale_mode: Mapped[str | None] = mapped_column(String(24))  # arm_length/auction/related_party/unknown
    label_source: Mapped[str | None] = mapped_column(String(32))  # broker_closing/deed_declared/...
    label_confidence: Mapped[str | None] = mapped_column(String(1))  # A/B/C
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ListingOutcome(Base):
    """Broker veri flywheel — bir ilanın YAŞAM DÖNGÜSÜ sonucu (yalnızca satış değil).

    SaleProbability tüm sonuçlardan; ClosingDiscount yalnızca ``outcome='sold'`` +
    ``sale_mode='arm_length'`` alt kümesinden eğitilir. Kapanış alanları
    (sold_price, sale_date, days_to_close) SADECE 'sold' sonucunda doldurulur.

    Kanıt (evidence) alanları şema için hazır; belge yükleme HENÜZ yok. Güven:
    broker'ın kendi beyanı varsayılan 'B'; bağımsız doğrulanırsa 'A'.
    """

    __tablename__ = "listing_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str | None] = mapped_column(String(64))  # broker kimliği/adı
    listing_ref: Mapped[str | None] = mapped_column(String(256))  # ilan URL/ID (opsiyonel)
    listing_type: Mapped[str | None] = mapped_column(String(16))
    # Konum + nitelik
    province: Mapped[str | None] = mapped_column(String(64))
    district: Mapped[str | None] = mapped_column(String(64))
    neighborhood: Mapped[str | None] = mapped_column(String(128))
    gross_m2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    net_m2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    room_count: Mapped[str | None] = mapped_column(String(16))
    building_age: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[int | None] = mapped_column(Integer)
    total_floors: Mapped[int | None] = mapped_column(Integer)
    heating: Mapped[str | None] = mapped_column(String(64))
    # Fiyat yaşam döngüsü
    initial_asking_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    last_asking_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    price_cut_count: Mapped[int | None] = mapped_column(Integer)
    listing_date: Mapped[dt.date | None] = mapped_column()
    days_on_market: Mapped[int | None] = mapped_column(Integer)
    # SONUÇ (yaşam döngüsü) — sold/withdrawn/expired/active/lost_to_other/unknown
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    # Kapanış — SADECE outcome='sold'
    sold_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    sale_date: Mapped[dt.date | None] = mapped_column()
    days_to_close: Mapped[int | None] = mapped_column(Integer)
    sale_mode: Mapped[str | None] = mapped_column(String(24))  # arm_length/auction/...
    # Provenance / kanıt
    label_source: Mapped[str | None] = mapped_column(String(32))
    label_confidence: Mapped[str | None] = mapped_column(String(1))  # A/B/C
    evidence_type: Mapped[str | None] = mapped_column(String(32))  # none/screenshot/contract/...
    evidence_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RealizedLabel(Base):
    """Birleşik, provenance-aware GERÇEKLEŞEN-fiyat etiketi (çok-domainli).

    Kamuya açık işlem domain'lerinden (UYAP icra, KAP kurumsal, TOKİ/GYO birincil)
    ve doğrudan closing gözleyen kaynaklardan (broker/seller) gelen etiketleri TEK
    kayıtta toplar — ama ``domain`` ve ``sale_mechanism`` AYRI tutulur. asking→closing
    ML head'i YALNIZCA doğrudan closing kaynaklarını (reference='asking', arm_length)
    görür; kamu domain'leri FairValue→realized kalibrasyonuna gider (asla karıştırılmaz).
    """

    __tablename__ = "realized_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(24), nullable=False)  # kaynak-domain: kap/uyap/toki/broker/consumer
    label_source: Mapped[str] = mapped_column(String(32), nullable=False)  # uyap/kap/toki/seller_self_reported/...
    sale_mechanism: Mapped[str] = mapped_column(String(40), nullable=False)  # auction/corporate_negotiated_non_related/corporate_related_party/public_auction/primary_market/arm_length
    reference_price_type: Mapped[str] = mapped_column(String(16), nullable=False)  # appraisal/prior_appraisal/reserve/asking/offered_avg/none
    reference_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    realized_price: Mapped[float] = mapped_column(Numeric(16, 2), nullable=False)
    related_party: Mapped[bool] = mapped_column(Boolean, default=False)
    value_method: Mapped[str | None] = mapped_column(String(32))  # negotiation/auction/administrative...
    # Taşınmaz nitelikleri
    province: Mapped[str | None] = mapped_column(String(64))
    district: Mapped[str | None] = mapped_column(String(64))
    property_type: Mapped[str | None] = mapped_column(String(32))
    gross_m2: Mapped[float | None] = mapped_column(Numeric(10, 2))
    transaction_date: Mapped[dt.date | None] = mapped_column()
    # Provenance
    label_confidence: Mapped[str | None] = mapped_column(String(1))  # A/B/C
    external_ref: Mapped[str | None] = mapped_column(String(256))  # kaynak id/URL
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AggregateObservation(Base):
    """Eşlenmemiş TOPLU (cohort) gözlem — paired ``RealizedLabel``'a ZORLANMAZ.

    Bazı resmî açıklamalar (ör. TOKİ/GYO proje "benzer nitelikteki bağımsız
    bölümlerin ortalama satış fiyatları") AYNI taşınmaz için reference→realized
    ÇİFTİ vermez; FARKLI popülasyonların toplu istatistiğini verir: (a) sunulan
    envanter ortalaması, (b) KÜMÜLATİF gerçekleşen satış ortalaması. Bu ikisi AYNI
    birimler DEĞİLDİR → reference→realized olarak EŞLEŞTİRİLEMEZ, aralarında closing
    indirimi HESAPLANMAZ. Bu tablo onları eşlenmemiş toplu gözlem olarak, kendi
    ``observation_role``'üyle saklar ve YAPISAL olarak ``realized_price``/
    ``reference_price`` alanı YOKTUR (asking→closing head'ine giremez).
    """

    __tablename__ = "aggregate_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(24), nullable=False)  # kaynak-domain: toki/...
    label_source: Mapped[str | None] = mapped_column(String(32))
    aggregation_level: Mapped[str] = mapped_column(String(24), nullable=False)  # cohort
    comparison_scope: Mapped[str] = mapped_column(String(32), nullable=False)  # unpaired_aggregate
    observation_role: Mapped[str] = mapped_column(String(32), nullable=False)  # offered_inventory/cumulative_realized_sales
    project_id: Mapped[str | None] = mapped_column(String(64))
    as_of_date: Mapped[dt.date | None] = mapped_column()
    count: Mapped[int | None] = mapped_column(Integer)
    total_price: Mapped[float | None] = mapped_column(Numeric(18, 2))
    average_price: Mapped[float | None] = mapped_column(Numeric(16, 2))
    strata: Mapped[list | None] = mapped_column(JSON)  # oda-tipi kırılımı, kaynaktaki gibi KORUNUR
    external_ref: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
