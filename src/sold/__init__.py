"""sold — ilan verisinden gerçekleşen (realized) konut fiyatı tahmini.

Türkiye'de bireysel işlem düzeyinde temiz 'gerçek satış fiyatı' verisi kamuya
açık değildir (tapu düşük beyanlı, MLS yok). Bu proje eksik katmanı *tahmin
eder*: ilanları zamanda izleyip (fiyat düşüşü + time-on-market) 'sold data'
proxy'si üretir ve bunu TCMB'nin ekspertiz-tabanlı Konut Fiyat Endeksi'ne (KFE)
kalibre eder.
"""

__version__ = "0.1.0"
