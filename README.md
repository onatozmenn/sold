# sold — Gerçekleşen (Realized) Konut Fiyatı Tahmini

> İlan ("intended"/asking) fiyatından, **gerçekleşen** (realized/transaction)
> satış fiyatının bir **proxy'sini** üretmek — ABD'deki MLS "sold data"
> katmanının Türkiye'de eksik olan karşılığı.

## Problem

Türkiye'de bireysel işlem düzeyinde temiz "gerçek satış fiyatı" verisi kamuya
açık **değildir**:

- **Tapu** kayıtlarında beyan edilen bedeller sistematik olarak düşüktür (harç).
- **MLS** benzeri, satılan fiyatı şeffaf paylaşan bir sistem yoktur.
- İlan siteleri yalnızca **asking (istenen)** fiyatı tutar.

Sonuç: değerleme modelleri (AVM) ilan fiyatıyla eğitildiğinde yukarı yanlı olur.

## Yaklaşım

Eksik veriyi **çekmek** yerine **tahmin ederiz**:

1. **Longitudinal ilan takibi** — ilanları her gün izleyip fiyat düşüşlerini ve
   *time-on-market*'i kaydederiz; ilan kaybolunca "delisted" işaretleriz
   (gerçekleşen fiyata en yakın gözlemlenebilir sinyal).
2. **Kalibrasyon** — TCMB'nin **ekspertiz tabanlı** Konut Fiyat Endeksi'ne (KFE)
   oturtarak agregada tutarlılık sağlarız.
3. **Modelleme** (sonraki faz) — hedonik fiyat + indirim (sale-to-list) modeli.

## Kurulum

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"          # üretimde ayrıca: .[postgres]  ve  .[model]
Copy-Item .env.example .env      # sonra .env içine EVDS_API_KEY yazın
```

## Hızlı başlangıç

```powershell
# 1) Testler (ağ/anahtar gerektirmez)
pytest

# 2) Scraper -> pipeline hattını yerel örnekle çalıştır (siteye istek atmaz)
sold scrape-demo

# 2b) Faz 1: iki günlük yerel "site" ile longitudinal taramayı gör (siteye istek atmaz)
sold crawl run --path samples/site/day1 --date 2026-01-01
sold crawl run --path samples/site/day2 --date 2026-01-15
sold crawl stats

# 3) Faz 2-3: realized fiyat tahmin motorunu sentetik veride doğrula (.[model] gerekir)
sold model demo

# 4) Faz 4: gerçek etiket akışı (demo) — k-fold CV ile doğrula
sold gt demo --out data/gt.csv
sold gt import --csv data/gt.csv
sold gt analyze
sold model evaluate --source gt --folds 5

# 3) Faz 0: TCMB KFE verisini çek (EVDS_API_KEY gerekir)
sold evds kfe --start 01-01-2015 --out data/kfe.csv
#   alternatif:  python scripts/fetch_kfe.py

# Kesin/güncel KFE seri kodlarını doğrula:
sold evds series bie_kfe

# Veritabanını hazırla (SQLite varsayılan; Postgres için DATABASE_URL ayarla)
sold db init

# 5) Değerleme servisi (web arayüzü + API) — .[api] gerekir
sold serve   # sonra tarayıcıda http://127.0.0.1:8000
```

## Yol haritası (fazlar)

| Faz | İçerik | Durum |
|----|--------|-------|
| 0 | TCMB EVDS (KFE) + TÜİK satış adetleri + veri modeli | ✅ bu repo |
| 1 | Saygılı crawler + adapter + zamanlayıcı; longitudinal biriktirme | ✅ bu repo |
| 2 | Time-on-market + fiyat düşüş eğrisi (asking-side indirim) | ✅ bu repo |
| 3 | İndirim modeli + KFE kalibrasyonu → realized tahmin motoru | ✅ bu repo |
| 4 | Broker/ekspertiz ile küçük ground-truth doğrulaması | ✅ bu repo |

## Proje yapısı

```
src/sold/
  config.py            # ayarlar (.env)
  evds/                # TCMB EVDS istemcisi + KFE (Faz 0)
  tuik/                # TÜİK CSV/SDMX yükleyici
  db/                  # SQLAlchemy modelleri + PostGIS şeması
  scraper/             # saygılı temel + longitudinal pipeline (Faz 1)
  cli.py               # `sold` komutları
scripts/fetch_kfe.py   # Faz 0 tek-tık script
samples/               # demo HTML fixture (siteye istek atmaz)
tests/                 # ağ gerektirmeyen birim/uçtan uca testler
```

## Hukuki / etik notlar (önemli)

- **KVKK:** Kişisel veri (ilan sahibi adı, telefon vb.) **toplanmaz/saklanmaz**;
  yalnızca taşınmazın nesnel nitelikleri tutulur.
- **ToS / robots.txt:** Her sitenin kullanım koşullarına ve `robots.txt`
  kurallarına uyulur; scraper hız sınırı + jitter uygular. Siteye özgü
  `parse` metodunu, ilgili sitenin izinleri çerçevesinde **siz** yazarsınız.
- **Amaç:** Ürünün konumu **değerleme doğruluğu / şeffaf fiyat tahmini**dir;
  vergi denetimi/teşhir değildir (bkz. GİB "MEVA" projesi ayrı bir bağlamdır).
