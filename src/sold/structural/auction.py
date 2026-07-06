"""UYAP icra açık artırması — YAPISAL açık artırma verisi (düşük-ağırlıklı önsel DEĞİL).

Modelleme: her açık artırmada teklif verenlerin değerleri buyer-value dağılımından
çekilir (fair value yerine EKSPERTİZ değeri Q'ya çıpalı, zorunlu-satış kaymasıyla).
Açık artırma, en yüksek teklif YASAL TABAN'ı (legal floor) aşarsa satılır. Hem SATILAN
hem SATILMAYAN açık artırmalar toplanır (satış olasılığı bir momenttir). Teklif/artıran
sayısı gözlemlenebiliyorsa KORUNUR.

KRİTİK YASAL-TABAN DÜZELTMESİ (İİK): ``muhammen_bedel`` YAPISAL REZERV DEĞİLDİR;
EKSPERTİZ değeri Q'dur. Kabul tabanı kanunî kuraldır:

    legal_floor = max( 0.5·Q , priority_claims ) + realization_costs

bileşenler gözlemlendiğinde. Gözlemlenmezse taban KISMEN gözlemlidir (``legal_floor_exact
= False``) — UYDURULMAZ; gözlenen bileşenlerle bir ALT SINIR verilir (gözlenmeyen = 0
alınır, ama bu bir alt sınırdır ve exact=False ile işaretlenir). Alan semantiği KORUNUR:
parsel alanı, birim net ve birim brüt alan BİRBİRİNİN YERİNE GEÇİRİLMEZ.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .params import StructuralParams

# YAPISAL açık artırma gözlem şeması (muhammen_bedel = appraised_value Q; rezerv DEĞİL)
AUCTION_FIELDS = (
    "public_record_id",    # kamuya açık kayıt/dosya kimliği
    "auction_date",
    "province",
    "district",
    "property_type",
    "appraised_value",     # Q = muhammen_bedel (EKSPERTİZ — rezerv DEĞİL)
    "sold",                # bool: ihale gerçekleşti mi (SATILAN + SATILMAYAN toplanır)
    "winning_bid",         # satıldıysa kazanan teklif (unsold → None)
    "offer_count",         # verilen teklif sayısı (opsiyonel)
    "bidder_count",        # teklif veren sayısı (opsiyonel)
    "priority_claims",     # rüçhanlı alacaklar (opsiyonel)
    "realization_costs",   # paraya çevirme/paylaştırma masrafları (opsiyonel)
    "legal_floor",         # türetilmiş kanunî taban (kısmî olabilir)
    "legal_floor_exact",   # taban tam gözlemlendi mi (her iki bileşen mevcut)
    "parcel_area_m2",      # parsel yüzey alanı (birim alanı DEĞİL)
    "unit_net_m2",         # birim net alanı (parsel/brüt DEĞİL)
    "unit_gross_m2",       # birim brüt alanı (parsel/net DEĞİL)
    "source_audited",      # kayıt elle denetlendi mi
)


def legal_floor(
    appraised_value: float,
    priority_claims: float | None = None,
    realization_costs: float | None = None,
) -> tuple[float, bool]:
    """Kanunî kabul tabanını (İİK) ve TAM gözlemli olup olmadığını döndürür.

        legal_floor = max(0.5·Q, priority_claims) + realization_costs

    Q = ekspertiz değeri (muhammen_bedel) — rezerv OLARAK EŞİTLENMEZ. Her iki bileşen de
    gözlemliyse ``exact=True``. Bir bileşen eksikse UYDURULMAZ: gözlenmeyen 0 alınır (bu
    bir ALT SINIRdır), ``exact=False`` ve gözlenen bileşenler korunur.
    """
    Q = float(appraised_value)
    half = 0.5 * Q
    exact = priority_claims is not None and realization_costs is not None
    pc = float(priority_claims) if priority_claims is not None else 0.0
    rc = float(realization_costs) if realization_costs is not None else 0.0
    floor = max(half, pc) + rc
    return floor, exact


def _num(v: object) -> float | None:
    try:
        if v is None or v == "" or (isinstance(v, float) and v != v):
            return None
        f = float(v)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None


def normalize_auction(rec: dict) -> dict:
    """Ham açık artırma kaydını YAPISAL veri kümesine normalize eder.

    ``muhammen_bedel``/``appraised_value`` → Q (rezerv DEĞİL). ``legal_floor`` ve
    ``legal_floor_exact`` bileşenlerden türetilir (kısmî olabilir). ALAN SEMANTİĞİ
    KORUNUR: parsel / birim-net / birim-brüt alanı birbirinin yerine GEÇİRİLMEZ (her biri
    kendi kaynağından alınır, eksikse None). SATILAN + SATILMAYAN açık artmalar toplanır.
    """
    Q = rec.get("appraised_value", rec.get("muhammen_bedel"))
    if Q in (None, "", 0):
        raise ValueError("appraised_value (muhammen_bedel = ekspertiz Q) zorunlu.")
    Q = float(Q)
    pc = _num(rec.get("priority_claims", rec.get("ruchanli_alacaklar")))
    rc = _num(rec.get("realization_costs", rec.get("paraya_cevirme_masraflari")))
    floor, exact = legal_floor(Q, pc, rc)

    sold = rec.get("sold")
    if sold is None:
        result = str(rec.get("ihale_sonucu") or rec.get("result") or "").lower()
        bid = rec.get("winning_bid", rec.get("ihale_bedeli"))
        sold = bool(bid) and ("satılma" not in result)
    sold = bool(sold)
    winning = _num(rec.get("winning_bid", rec.get("ihale_bedeli")))
    offer = rec.get("offer_count", rec.get("teklif_sayisi"))
    bidder = rec.get("bidder_count", rec.get("katilimci_sayisi"))
    return {
        "public_record_id": rec.get("public_record_id", rec.get("dosya_no")),
        "auction_date": rec.get("auction_date", rec.get("ihale_tarihi")),
        "province": rec.get("province", rec.get("il")),
        "district": rec.get("district", rec.get("ilce")),
        "property_type": rec.get("property_type", rec.get("tasinmaz_turu")),
        "appraised_value": Q,
        "sold": sold,
        "winning_bid": winning if sold else None,
        "offer_count": int(offer) if offer not in (None, "") else None,
        "bidder_count": int(bidder) if bidder not in (None, "") else None,
        "priority_claims": pc,
        "realization_costs": rc,
        "legal_floor": floor,
        "legal_floor_exact": exact,
        # Alan semantiği: her biri kendi kaynağından; ASLA birbirinin yerine geçirilmez
        "parcel_area_m2": _num(rec.get("parcel_area_m2", rec.get("parsel_alani_m2"))),
        "unit_net_m2": _num(rec.get("unit_net_m2", rec.get("birim_net_m2"))),
        "unit_gross_m2": _num(rec.get("unit_gross_m2", rec.get("birim_brut_m2"))),
        "source_audited": bool(rec.get("source_audited", False)),
    }


def load_auctions(records: list[dict]) -> pd.DataFrame:
    """Yapısal açık artma kayıtlarını normalize edip DataFrame'e yükler."""
    rows = [normalize_auction(r) for r in records]
    return pd.DataFrame(rows, columns=list(AUCTION_FIELDS))


def simulate_auctions(
    rng: np.random.Generator,
    appraised_values,
    floors,
    params: StructuralParams,
    tightness: float = 0.0,
    fixed_bidders=None,
) -> dict:
    """Verilen Q'lar ve yasal tabanlar için açık artırmaları simüle eder.

    Her açık artırmada teklif veren sayısı N~Poisson(arrival_rate) (gözlemli değilse);
    her teklif B_i = Q·exp(mu_b + auction_shift + tightness_beta·τ + ε). En yüksek teklif
    yasal tabanı aşarsa SATILIR; kazanan teklif = max teklif. Döndürür: sold (bool dizi),
    winning/Q oranı (satılanlar için).
    """
    Q = np.asarray(appraised_values, dtype=float)
    L = np.asarray(floors, dtype=float)
    n = len(Q)
    if fixed_bidders is not None:
        counts = np.asarray(fixed_bidders, dtype=int)
    else:
        counts = rng.poisson(max(params.arrival_rate, 1e-3), n)
    counts = np.maximum(counts, 0)
    sold = np.zeros(n, dtype=bool)
    win_over_Q = np.full(n, np.nan)
    mean = params.mu_b + params.auction_shift + params.tightness_beta * float(tightness)
    for i in range(n):
        k = int(counts[i])
        if k == 0:
            continue
        bids = Q[i] * np.exp(rng.normal(mean, params.sigma_b, k))
        top = float(bids.max())
        if top >= L[i]:
            sold[i] = True
            win_over_Q[i] = top / Q[i]
    return {"sold": sold, "win_over_appraisal": win_over_Q, "bidder_count": counts}


def auction_moments(sold, win_over_appraisal) -> dict:
    """UYAP momentleri: satış olasılığı + koşullu kazanan/ekspertiz oranı dağılımı."""
    sold = np.asarray(sold, dtype=bool)
    ratios = np.asarray(win_over_appraisal, dtype=float)
    ratios = ratios[~np.isnan(ratios)]
    sale_prob = float(sold.mean()) if sold.size else float("nan")
    return {
        "uyap_sale_prob": sale_prob,
        "uyap_win_over_appraisal_mean": float(ratios.mean()) if ratios.size else float("nan"),
        "uyap_win_over_appraisal_sd": float(ratios.std()) if ratios.size > 1 else 0.0,
    }


def uyap_observed_moments(auctions: pd.DataFrame, floor_band: float = 0.05) -> dict:
    """Gerçek UYAP veri kümesinden ZENGİN betimsel momentler (identification raporu için).

    Destekler: satış olasılığı; koşullu kazanan/ekspertiz oranı mean/var/quantiles;
    yasal tabana YAKIN kütle (kazanan/floor ≤ 1+band); teklif/artıran sayısı dağılımı
    (gözlemli olduğunda). UYDURMA YOK: eksik bileşenler sayıma katılmaz.
    """
    if auctions is None or len(auctions) == 0:
        return {"uyap_sale_prob": float("nan"), "uyap_n": 0}
    sold = auctions["sold"].astype(bool).to_numpy()
    Q = pd.to_numeric(auctions["appraised_value"], errors="coerce").to_numpy(float)
    win = pd.to_numeric(auctions["winning_bid"], errors="coerce").to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(sold & (Q > 0), win / Q, np.nan)
    r = ratio[np.isfinite(ratio)]
    out: dict = {
        "uyap_n": int(len(auctions)),
        "uyap_sale_prob": float(sold.mean()),
        "uyap_win_over_appraisal_mean": float(r.mean()) if r.size else float("nan"),
        "uyap_win_over_appraisal_var": float(r.var()) if r.size > 1 else float("nan"),
        "uyap_win_over_appraisal_q25": float(np.quantile(r, 0.25)) if r.size else float("nan"),
        "uyap_win_over_appraisal_q50": float(np.quantile(r, 0.50)) if r.size else float("nan"),
        "uyap_win_over_appraisal_q75": float(np.quantile(r, 0.75)) if r.size else float("nan"),
    }
    # Yasal tabana yakın kütle (kazanan teklif floor'un hemen üstünde mi)
    floor = pd.to_numeric(auctions["legal_floor"], errors="coerce").to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        near = np.where(sold & (floor > 0), win / floor, np.nan)
    near = near[np.isfinite(near)]
    if near.size:
        out["uyap_mass_near_floor"] = float(np.mean(near <= 1.0 + floor_band))
    # Teklif / artıran sayısı dağılımı (gözlemli olduğunda)
    for col, key in (("offer_count", "uyap_offer_count"), ("bidder_count", "uyap_bidder_count")):
        vals = pd.to_numeric(auctions[col], errors="coerce").dropna()
        out[f"{key}_observed"] = int(len(vals))
        if len(vals):
            out[f"{key}_mean"] = float(vals.mean())
    return out

