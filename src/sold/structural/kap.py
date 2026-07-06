"""KAP yapısal veri kümesi — ilişkisiz müzakereli GAYRİMENKUL satışları (moment kaynağı).

YALNIZCA savunulabilir bir müzakereli gayrimenkul-elden-çıkarma yolunu sağlayan
kayıtlar genişletilir:
- gayrimenkul elden çıkarma (real-estate disposal),
- ``related_party = false`` (ilişkili taraf DIŞLANIR),
- açıkça raporlandığında müzakereli satış yöntemi (``value_method`` korunur),
- gerçekleşen satış bedeli mevcut,
- ekspertiz VEYA önceki-ekspertiz referansı mevcut.

Gözlem momentleri ``log(sale_price / appraisal_value)`` üzerinedir (ortalama, varyans,
medyan, seçili kuantiller). Bu momentler pazarlık gücü (eta) ve KAP mekanizma kaymasını
ORTAK kalibre etmeye YARDIM edebilir. DİKKAT: "KAP eta VERİR" DEĞİL — eta, yapısal
ilkeler ve mekanizma varsayımlarıyla BİRLİKTE (SMM'de) tahmin edilir. Kurumsal müzakere
sıradan yeniden-satış GERÇEĞİ olarak alınmaz.

Epistemik katılık: referans türü belirtilmemiş, satış/ekspertiz eksik ya da ilişkili
taraf olan kayıtlar SESSİZCE DIŞLANIR (uydurulmaz).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Ekspertiz referans türleri (registry ile aynı anlam; yapısal moment için ayrı görünüm)
KAP_REFERENCE_TYPES = ("appraisal", "prior_appraisal")

# Yalnızca AÇIKÇA müzakereli satış yöntemini destekleyen değerler (müzakere-kalibrasyon alt kümesi).
# İlişkisiz olmak TEK BAŞINA müzakere ÇIKARIMI yaptırmaz.
_NEGOTIATED_TOKENS = ("negoti", "pazarl", "müzakere", "muzakere", "anlaşmal", "anlasmal")


def _is_negotiated(value_method: object) -> bool:
    vm = str(value_method or "").strip().lower()
    return bool(vm) and any(tok in vm for tok in _NEGOTIATED_TOKENS)


KAP_FIELDS = (
    "official_record_id",
    "source_record_ids",        # bağlı açıklama zinciri (TEK işlem; iki bağımsız satış DEĞİL)
    "appraisal_value",          # TL-normalize (ekspertiz referansı)
    "appraisal_value_original",
    "appraisal_currency",
    "appraisal_vat_basis",
    "reference_price_type",
    "appraisal_date",           # nullable
    "sale_price",               # TL-normalize (dökümanlı resmî TCMB kuruyla)
    "sale_price_original",
    "sale_currency",
    "sale_vat_basis",
    "exchange_rate",            # dökümanlı resmî TCMB kuru (yabancı para için)
    "exchange_rate_series",     # TCMB EVDS seri kodu (ör. TP.DK.USD.A.YTL)
    "conversion_date",          # dönüşüm tarihi (işlem tamamlanma tarihi)
    "sale_date",
    "province",
    "district",
    "property_type",
    "gross_m2",                 # nullable
    "value_method",
    "sale_mechanism",           # mekanizma sınırı (ör. corporate_negotiated_non_related)
    "negotiated",               # value_method AÇIKÇA müzakereyi destekliyor mu (kalibrasyon alt kümesi)
    "related_party",
    "related_party_basis",      # provenance (ör. official_old_form_relation_none)
    "source_audited",
)


def _num(v: object) -> float | None:
    try:
        if v is None or v == "" or (isinstance(v, float) and v != v):
            return None
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


# TL/TRY para-birimi jetonları (eski kayıtlar zaten TL: para birimi belirtilmemiş → TRY sayılır)
_TRY_TOKENS = ("try", "tl", "ytl", "₺")


def _is_try_currency(currency: object) -> bool:
    return str(currency if currency not in (None, "") else "TRY").strip().lower() in _TRY_TOKENS


def _to_try(amount: float | None, currency: object, rate: float | None) -> float | None:
    """Tutarı TL'ye normalize eder. TL: olduğu gibi. Yabancı para: DÖKÜMANLI resmî kur
    ŞARTtir (yoksa ``None`` — kur UYDURULMAZ). Böylece para-birimi normalizasyonu ancak
    resmî bir TCMB kuru veri içinde belgelenmişse yapılır."""
    if amount is None:
        return None
    if _is_try_currency(currency):
        return amount
    if rate is None or rate <= 0:
        return None  # yabancı para + belgelenmiş resmî kur yok → normalize edilemez (uydurma YOK)
    return amount * rate


def _vat_comparable(sale_basis: object, appraisal_basis: object) -> bool:
    """log(sale/appraisal) için iki taraf AYNI KDV bazında mı (ya da ikisi de belirtilmemiş
    → eski kayıt uyumu). Farklı bazlar (biri KDV dahil, diğeri hariç) KARŞILAŞTIRILAMAZ."""
    if sale_basis in (None, "") and appraisal_basis in (None, ""):
        return True
    return str(sale_basis or "").strip().lower() == str(appraisal_basis or "").strip().lower()


def normalize_kap_disposal(rec: dict) -> dict | None:
    """Müzakereli gayrimenkul-elden-çıkarma yolunu sağlayan kaydı normalize eder.

    Yolu sağlamayan (ilişkili taraf, eksik satış/ekspertiz, referans türü yok, taşınmaz
    türü yok, DÖKÜMANLI kuru olmayan yabancı para, KDV bazı karşılaştırılamaz) kayıtlar
    için ``None`` döner (dışlanır — uydurma YOK). Yabancı para satış bedeli yalnızca kayıt
    içinde belgelenmiş resmî TCMB kuruyla TL'ye çevrilir; oran log(sale/appraisal) AYNI KDV
    bazında hesaplanır.
    """
    related = bool(rec.get("related_party", rec.get("iliskili_taraf", False)))
    if related:
        return None  # ilişkili taraf → müzakereli-arm's-length yolu değil
    # --- Satış bedeli: dökümanlı resmî TCMB kuruyla TL'ye normalize (uydurma YOK) ---
    sale_original = _num(
        rec.get("sale_price_original", rec.get("sale_price", rec.get("toplam_satis_bedeli")))
    )
    sale_currency = rec.get("sale_currency")
    exchange_rate = _num(rec.get("exchange_rate"))
    sale = _to_try(sale_original, sale_currency, exchange_rate)
    # --- Ekspertiz: TL referans (yabancı ekspertiz + belgelenmiş kur yoksa dışlanır) ---
    appraisal_original = _num(
        rec.get(
            "appraisal_value_original",
            rec.get("appraisal_value", rec.get("degerleme_tutari", rec.get("prior_appraisal_value"))),
        )
    )
    appraisal_currency = rec.get("appraisal_currency")
    appraisal = _to_try(appraisal_original, appraisal_currency, _num(rec.get("appraisal_exchange_rate")))
    ref_type = str(rec.get("reference_price_type") or "").strip()
    ptype = rec.get("property_type", rec.get("tasinmaz_turu"))
    # --- KDV bazları karşılaştırılabilir olmalı (oran aynı baz üzerinde) ---
    if not _vat_comparable(rec.get("sale_vat_basis"), rec.get("appraisal_vat_basis")):
        return None
    if sale is None or appraisal is None or ref_type not in KAP_REFERENCE_TYPES or not ptype:
        return None
    value_method = rec.get("value_method", rec.get("deger_belirleme_yontemi"))
    return {
        "official_record_id": rec.get("official_record_id", rec.get("kap_id")),
        "source_record_ids": rec.get("source_record_ids"),
        "appraisal_value": appraisal,
        "appraisal_value_original": appraisal_original,
        "appraisal_currency": appraisal_currency,
        "appraisal_vat_basis": rec.get("appraisal_vat_basis"),
        "reference_price_type": ref_type,
        "appraisal_date": rec.get("appraisal_date"),
        "sale_price": sale,
        "sale_price_original": sale_original,
        "sale_currency": sale_currency,
        "sale_vat_basis": rec.get("sale_vat_basis"),
        "exchange_rate": exchange_rate,
        "exchange_rate_series": rec.get("exchange_rate_series"),
        "conversion_date": rec.get("conversion_date"),
        "sale_date": rec.get("sale_date", rec.get("islem_tarihi")),
        "province": rec.get("province", rec.get("il")),
        "district": rec.get("district", rec.get("ilce")),
        "property_type": ptype,
        "gross_m2": _num(rec.get("gross_m2", rec.get("brut_m2"))),
        "value_method": value_method,
        "sale_mechanism": rec.get("sale_mechanism"),
        "negotiated": _is_negotiated(value_method),
        "related_party": related,
        "related_party_basis": rec.get("related_party_basis"),
        "source_audited": bool(rec.get("source_audited", False)),
    }


def load_kap_disposals(records: list[dict]) -> pd.DataFrame:
    """Yolu sağlayan KAP kayıtlarını normalize edip DataFrame'e yükler (diğerlerini atlar)."""
    rows = [r for r in (normalize_kap_disposal(x) for x in records) if r is not None]
    return pd.DataFrame(rows, columns=list(KAP_FIELDS))


def kap_observed_moments(kap: pd.DataFrame, negotiated_only: bool = True) -> dict:
    """log(sale_price/appraisal_value) momentleri: mean, sd (SMM eşlemesi) + betimsel.

    ``negotiated_only=True`` (varsayılan): yalnızca AÇIKÇA müzakereli satışlar (kalibrasyon
    alt kümesi). n<2 iken varyans/kuantiller UYDURULMAZ; NaN (mevcut değil) döner.
    Bu momentler eta'yı TEK BAŞINA tanımlamaz; pazarlık gücü + KAP mekanizma kaymasını
    ORTAK kalibre etmeye katkı verir.
    """
    if kap is None or len(kap) == 0:
        return {"kap_n": 0, "kap_log_ratio_mean": float("nan")}
    df = kap
    if negotiated_only and "negotiated" in df.columns:
        df = df[df["negotiated"].astype(bool)]
    if len(df) == 0:
        return {"kap_n": 0, "kap_log_ratio_mean": float("nan")}
    sale = pd.to_numeric(df["sale_price"], errors="coerce").to_numpy(float)
    appr = pd.to_numeric(df["appraisal_value"], errors="coerce").to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where((appr > 0) & (sale > 0), sale / appr, np.nan)
    r = ratio[np.isfinite(ratio) & (ratio > 0)]
    lr = np.log(r) if r.size else r
    out: dict = {"kap_n": int(len(df))}
    if lr.size >= 1:
        out["kap_log_ratio_mean"] = float(lr.mean())
    else:
        out["kap_log_ratio_mean"] = float("nan")
    # Varyans/medyan/kuantiller yalnızca örneklem DESTEKLERSE (n>=2); aksi halde NaN.
    if lr.size >= 2:
        out.update(
            {
                "kap_log_ratio_sd": float(lr.std()),
                "kap_log_ratio_var": float(lr.var()),
                "kap_log_ratio_median": float(np.median(lr)),
                "kap_log_ratio_q10": float(np.quantile(lr, 0.10)),
                "kap_log_ratio_q90": float(np.quantile(lr, 0.90)),
            }
        )
    return out
