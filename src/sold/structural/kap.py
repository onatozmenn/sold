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

KAP_FIELDS = (
    "official_record_id",
    "appraisal_value",
    "reference_price_type",
    "appraisal_date",     # nullable
    "sale_price",
    "sale_date",
    "province",
    "district",
    "property_type",
    "gross_m2",            # nullable
    "value_method",
    "related_party",
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


def normalize_kap_disposal(rec: dict) -> dict | None:
    """Müzakereli gayrimenkul-elden-çıkarma yolunu sağlayan kaydı normalize eder.

    Yolu sağlamayan (ilişkili taraf, eksik satış/ekspertiz, referans türü yok, taşınmaz
    türü yok) kayıtlar için ``None`` döner (dışlanır — uydurma YOK).
    """
    related = bool(rec.get("related_party", rec.get("iliskili_taraf", False)))
    if related:
        return None  # ilişkili taraf → müzakereli-arm's-length yolu değil
    sale = _num(rec.get("sale_price", rec.get("toplam_satis_bedeli")))
    appraisal = _num(
        rec.get(
            "appraisal_value",
            rec.get("degerleme_tutari", rec.get("prior_appraisal_value")),
        )
    )
    ref_type = str(rec.get("reference_price_type") or "").strip()
    ptype = rec.get("property_type", rec.get("tasinmaz_turu"))
    if sale is None or appraisal is None or ref_type not in KAP_REFERENCE_TYPES or not ptype:
        return None
    return {
        "official_record_id": rec.get("official_record_id", rec.get("kap_id")),
        "appraisal_value": appraisal,
        "reference_price_type": ref_type,
        "appraisal_date": rec.get("appraisal_date"),
        "sale_price": sale,
        "sale_date": rec.get("sale_date", rec.get("islem_tarihi")),
        "province": rec.get("province", rec.get("il")),
        "district": rec.get("district", rec.get("ilce")),
        "property_type": ptype,
        "gross_m2": _num(rec.get("gross_m2", rec.get("brut_m2"))),
        "value_method": rec.get("value_method", rec.get("deger_belirleme_yontemi")),
        "related_party": related,
        "source_audited": bool(rec.get("source_audited", False)),
    }


def load_kap_disposals(records: list[dict]) -> pd.DataFrame:
    """Yolu sağlayan KAP kayıtlarını normalize edip DataFrame'e yükler (diğerlerini atlar)."""
    rows = [r for r in (normalize_kap_disposal(x) for x in records) if r is not None]
    return pd.DataFrame(rows, columns=list(KAP_FIELDS))


def kap_observed_moments(kap: pd.DataFrame) -> dict:
    """log(sale_price/appraisal_value) momentleri: mean, sd (SMM eşlemesi) + betimsel."""
    if kap is None or len(kap) == 0:
        return {"kap_n": 0, "kap_log_ratio_mean": float("nan")}
    sale = pd.to_numeric(kap["sale_price"], errors="coerce").to_numpy(float)
    appr = pd.to_numeric(kap["appraisal_value"], errors="coerce").to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where((appr > 0) & (sale > 0), sale / appr, np.nan)
    r = ratio[np.isfinite(ratio) & (ratio > 0)]
    lr = np.log(r) if r.size else r
    out: dict = {"kap_n": int(len(kap))}
    if lr.size:
        out.update(
            {
                "kap_log_ratio_mean": float(lr.mean()),
                "kap_log_ratio_sd": float(lr.std()) if lr.size > 1 else 0.0,
                "kap_log_ratio_var": float(lr.var()) if lr.size > 1 else 0.0,
                "kap_log_ratio_median": float(np.median(lr)),
                "kap_log_ratio_q10": float(np.quantile(lr, 0.10)),
                "kap_log_ratio_q90": float(np.quantile(lr, 0.90)),
            }
        )
    else:
        out["kap_log_ratio_mean"] = float("nan")
    return out
