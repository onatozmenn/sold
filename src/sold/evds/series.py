"""TCMB Konut Fiyat Endeksi (KFE) seri tanımları.

DİKKAT: EVDS seri kodları, özellikle metodoloji revizyonlarında değişebilir.
Üretimde kesin/güncel kodları ``EvdsClient.list_series(KFE_DATAGROUP)`` ile
DOĞRULAYIN; aşağıdaki sabitler yalnızca makul varsayılanlardır.
"""

from __future__ import annotations

# EVDS veri grubu kodu — Konut Fiyat Endeksi
KFE_DATAGROUP = "bie_kfe"

# Sık atıfta bulunulan Türkiye geneli seriler (list_series ile doğrulayın).
DEFAULT_KFE_SERIES: dict[str, str] = {
    "TP.KFE01": "Konut Fiyat Endeksi (Türkiye)",
    "TP.KFE02": "Yeni Konutların Fiyat Endeksi (Türkiye)",
}

# EVDS veri grubu — TÜİK Konut ve İş Yeri Satış İstatistikleri (Toplam Satışlar).
# İlgili gruplar: bie_akonutsat1 (Toplam), 2 (İpotekli), 3 (İlk el), 4 (İkinci el).
HOUSE_SALES_DATAGROUP = "bie_akonutsat1"

# Konut satış serileri (K öneki = konut; öneksiz TR... = iş yeri). Aylık, adet.
# İl kodları TÜİK IBBS: İstanbul=100, Ankara=510, İzmir=310, Bursa=411, Antalya=611.
# Kesin/güncel kodlar için: EvdsClient.list_series(HOUSE_SALES_DATAGROUP).
DEFAULT_HOUSE_SALES_SERIES: dict[str, str] = {
    "TP.AKONUTSAT1.KTRTOPLAM": "Türkiye Konut Toplam Satış",
    "TP.AKONUTSAT1.KTR100": "İstanbul Konut Toplam Satış",
    "TP.AKONUTSAT1.KTR510": "Ankara Konut Toplam Satış",
    "TP.AKONUTSAT1.KTR310": "İzmir Konut Toplam Satış",
    "TP.AKONUTSAT1.KTR411": "Bursa Konut Toplam Satış",
    "TP.AKONUTSAT1.KTR611": "Antalya Konut Toplam Satış",
}

# EVDS veri grubu — TCMB ekspertiz TL/m² konut birim fiyatları (il bazında, GERÇEK,
# çeyreklik). Seriler TP.BIRIMFIYAT.{il} (İstanbul=IST, Ankara=ANK, İzmir=IZM;
# diğerleri tam il adı). Kodlar keşifle alınır (discover_unit_price_codes).
UNIT_PRICE_DATAGROUP = "bie_birimfiyat"
