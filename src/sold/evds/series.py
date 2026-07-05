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
