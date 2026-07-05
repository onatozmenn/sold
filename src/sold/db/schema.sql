-- PostgreSQL + PostGIS şeması (üretim için kanonik DDL).
-- SQLite (geliştirme/test) için ORM create_all kullanılır; bkz. db/__init__.py.

CREATE EXTENSION IF NOT EXISTS postgis;

-- Benzersiz ilan (mümkün olduğunca durağan alanlar). KİŞİSEL VERİ TUTULMAZ.
CREATE TABLE IF NOT EXISTS listings (
    id                BIGSERIAL PRIMARY KEY,
    source            TEXT NOT NULL,
    source_listing_id TEXT NOT NULL,
    url               TEXT,
    listing_type      TEXT,
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    delisted_at       TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'active',
    province          TEXT,
    district          TEXT,
    neighborhood      TEXT,
    lat               DOUBLE PRECISION,
    lon               DOUBLE PRECISION,
    geom              geometry(Point, 4326) GENERATED ALWAYS AS
                          (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
    gross_m2          NUMERIC(10, 2),
    net_m2            NUMERIC(10, 2),
    room_count        TEXT,
    building_age      INTEGER,
    floor             INTEGER,
    total_floors      INTEGER,
    heating           TEXT,
    CONSTRAINT uq_listing_source UNIQUE (source, source_listing_id)
);

-- Zaman serisi: her tarama turunda fiyat/durum anlık görüntüsü.
CREATE TABLE IF NOT EXISTS listing_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    listing_id     BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    captured_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    price          NUMERIC(16, 2) NOT NULL,
    currency       TEXT NOT NULL DEFAULT 'TRY',
    is_active      BOOLEAN NOT NULL DEFAULT true,
    days_on_market INTEGER,
    CONSTRAINT uq_snapshot UNIQUE (listing_id, captured_at)
);

-- Türetilmiş: fiyat değişimleri (asking-side pazarlık sinyali).
CREATE TABLE IF NOT EXISTS price_changes (
    id          BIGSERIAL PRIMARY KEY,
    listing_id  BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    old_price   NUMERIC(16, 2) NOT NULL,
    new_price   NUMERIC(16, 2) NOT NULL,
    pct_change  DOUBLE PRECISION NOT NULL
);

-- TCMB EVDS gözlemleri (kalibrasyon / ground truth).
CREATE TABLE IF NOT EXISTS evds_observations (
    id          BIGSERIAL PRIMARY KEY,
    series_code TEXT NOT NULL,
    series_name TEXT,
    obs_date    DATE NOT NULL,
    value       NUMERIC(18, 4),
    CONSTRAINT uq_evds_obs UNIQUE (series_code, obs_date)
);

-- TÜİK konut satış adetleri (talep hacmi).
CREATE TABLE IF NOT EXISTS tuik_house_sales (
    id          BIGSERIAL PRIMARY KEY,
    province    TEXT,
    period      DATE NOT NULL,
    sales_count INTEGER,
    sale_type   TEXT,
    CONSTRAINT uq_tuik_sales UNIQUE (province, period, sale_type)
);

-- Tarama turu künyesi (izleme / kalite takibi).
CREATE TABLE IF NOT EXISTS crawl_runs (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    listings_seen INTEGER NOT NULL DEFAULT 0,
    new_listings  INTEGER NOT NULL DEFAULT 0,
    price_changes INTEGER NOT NULL DEFAULT 0,
    delisted      INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'ok',
    note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_listing ON listing_snapshots(listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_geom ON listings USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_crawl_runs_source ON crawl_runs(source, started_at);

-- Gerçek (broker/ekspertiz) gerçekleşen satış etiketleri (Faz 4).
CREATE TABLE IF NOT EXISTS ground_truth_sales (
    id             BIGSERIAL PRIMARY KEY,
    source         TEXT,
    listing_type   TEXT,
    province       TEXT,
    district       TEXT,
    neighborhood   TEXT,
    lat            DOUBLE PRECISION,
    lon            DOUBLE PRECISION,
    gross_m2       NUMERIC(10, 2),
    net_m2         NUMERIC(10, 2),
    room_count     TEXT,
    building_age   INTEGER,
    floor          INTEGER,
    total_floors   INTEGER,
    heating        TEXT,
    asking_price   NUMERIC(16, 2),
    sold_price     NUMERIC(16, 2),
    days_on_market INTEGER,
    sale_date      DATE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gt_district ON ground_truth_sales(district);
