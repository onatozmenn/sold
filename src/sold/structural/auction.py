"""UYAP icra açık artırması — YAPISAL açık artırma verisi (düşük-ağırlıklı önsel DEĞİL).

Modelleme: her açık artırmada teklif verenlerin değerleri buyer-value dağılımından
çekilir (fair value yerine EKSPERTİZ değeri Q'ya çıpalı, zorunlu-satış kaymasıyla).
Açık artırma, en yüksek teklif YASAL TABAN'ı (legal floor) aşarsa satılır. Hem SATILAN
hem SATILMAYAN açık artırmalar toplanır (satış olasılığı bir momenttir). Teklif/artıran
sayısı gözlemlenebiliyorsa KORUNUR.

KRİTİK YASAL-TABAN DÜZELTMESİ (İİK): ``muhammen_bedel`` YAPISAL REZERV DEĞİLDİR;
EKSPERTİZ değeri Q'dur. Kabul tabanı kanunî kuraldır:

    legal_floor = max( 0.5·Q ,  priority_claims + realization_costs )

bileşenler gözlemlendiğinde. Gözlemlenmezse taban KISMEN gözlemlidir (``legal_floor_exact
= False``) — UYDURULMAZ; gözlenen bileşenlerle bir ALT SINIR verilir (gözlenmeyen = 0
alınır, ama bu bir alt sınırdır ve exact=False ile işaretlenir).
"""

from __future__ import annotations

import numpy as np

from .params import StructuralParams

# Açık artırma-model veri kümesi alanları (muhammen_bedel = appraised_value Q)
AUCTION_FIELDS = (
    "appraised_value",     # Q = muhammen_bedel (EKSPERTİZ — rezerv DEĞİL)
    "sold",                # bool: ihale gerçekleşti mi
    "winning_bid",         # satıldıysa kazanan teklif
    "bidder_count",        # gözlemlenebiliyorsa teklif veren sayısı
    "priority_claims",     # rüçhanlı alacaklar (opsiyonel)
    "realization_costs",   # paraya çevirme/paylaştırma masrafları (opsiyonel)
    "legal_floor",         # türetilmiş kanunî taban (kısmî olabilir)
    "legal_floor_exact",   # taban tam gözlemlendi mi (bileşenler mevcut)
    "province",
    "district",
)


def legal_floor(
    appraised_value: float,
    priority_claims: float | None = None,
    realization_costs: float | None = None,
) -> tuple[float, bool]:
    """Kanunî kabul tabanını (İİK) ve TAM gözlemli olup olmadığını döndürür.

    floor = max(0.5·Q, priority_claims + realization_costs). Q = ekspertiz değeri
    (muhammen_bedel) — rezerv OLARAK EŞİTLENMEZ. Her iki bileşen de gözlemliyse
    ``exact=True``; değilse gözlenmeyen bileşen 0 alınır (ALT SINIR) ve ``exact=False``
    (UYDURMA YOK — kısmen gözlemli). Q ayrı olarak korunur.
    """
    Q = float(appraised_value)
    half = 0.5 * Q
    if priority_claims is not None and realization_costs is not None:
        floor = max(half, float(priority_claims) + float(realization_costs))
        return floor, True
    pc = float(priority_claims) if priority_claims is not None else 0.0
    rc = float(realization_costs) if realization_costs is not None else 0.0
    return max(half, pc + rc), False


def normalize_auction(rec: dict) -> dict:
    """Ham açık artırma kaydını yapısal veri kümesine normalize eder.

    ``muhammen_bedel``/``appraised_value`` → Q (rezerv DEĞİL). ``legal_floor`` ve
    ``legal_floor_exact`` bileşenlerden türetilir (kısmî olabilir).
    """
    Q = rec.get("appraised_value", rec.get("muhammen_bedel"))
    if Q in (None, "", 0):
        raise ValueError("appraised_value (muhammen_bedel = ekspertiz Q) zorunlu.")
    Q = float(Q)
    pc = rec.get("priority_claims", rec.get("ruchanli_alacaklar"))
    rc = rec.get("realization_costs", rec.get("paraya_cevirme_masraflari"))
    pc = float(pc) if pc not in (None, "") else None
    rc = float(rc) if rc not in (None, "") else None
    floor, exact = legal_floor(Q, pc, rc)

    sold = rec.get("sold")
    if sold is None:
        result = str(rec.get("ihale_sonucu") or rec.get("result") or "").lower()
        bid = rec.get("winning_bid", rec.get("ihale_bedeli"))
        sold = bool(bid) and not ("satılma" in result)
    winning = rec.get("winning_bid", rec.get("ihale_bedeli"))
    winning = float(winning) if winning not in (None, "", 0) else None
    bidder = rec.get("bidder_count", rec.get("katilimci_sayisi"))
    bidder = int(bidder) if bidder not in (None, "") else None
    return {
        "appraised_value": Q,
        "sold": bool(sold),
        "winning_bid": winning if sold else None,
        "bidder_count": bidder,
        "priority_claims": pc,
        "realization_costs": rc,
        "legal_floor": floor,
        "legal_floor_exact": exact,
        "province": rec.get("province", rec.get("il")),
        "district": rec.get("district", rec.get("ilce")),
    }


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
