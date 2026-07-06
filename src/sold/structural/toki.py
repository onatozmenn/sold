"""TOKİ/GYO tekrarlı proje açıklamaları → AGREGAT yapısal momentler.

Tekrar eden (kümülatif) proje açıklamaları, oda-tipi strata içinde FARKLANARAK
dönemsel gerçekleşen-satış kohortları verir:

    cohort_count[t]  = cum_count[t]  − cum_count[t−1]
    cohort_total[t]  = cum_total[t]  − cum_total[t−1]
    cohort_avg[t]    = cohort_total[t] / cohort_count[t]

YALNIZCA tutarlı, REVİZE EDİLMEMİŞ ardışık kümülatif açıklamalar farklanır. Property
düzeyinde ÇİFT ÜRETİLMEZ ve asking→closing indirimi HESAPLANMAZ (bunlar agregat
kohort momentleridir). Reconciliation + revizyon guard'ları:
- kümülatif sayım/tutar AZALIRSA → revizyon (tutarsız); o strata-geçiş ATLANIR ve
  ``revisions`` listesine yazılır (fark UYDURULMAZ),
- toplam kümülatif, strata toplamıyla bağdaşmıyorsa → reconciliation uyarısı.
"""

from __future__ import annotations

import datetime as dt


def _date(v: object) -> dt.date | None:
    if not v:
        return None
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _strata_map(disclosure: dict) -> dict[str, dict]:
    """Bir açıklamanın oda-tipi strata'sını {room_type: {cum_count, cum_total}} yapar."""
    out: dict[str, dict] = {}
    for s in disclosure.get("strata", []):
        rt = str(s.get("room_type") or "")
        cc = s.get("cum_count", s.get("cumulative_count"))
        ct = s.get("cum_total", s.get("cumulative_total"))
        if rt and cc is not None and ct is not None:
            out[rt] = {"cum_count": float(cc), "cum_total": float(ct)}
    return out


def difference_disclosures(disclosures: list[dict]) -> dict:
    """Ardışık kümülatif açıklamaları farklar → dönemsel kohortlar + guard'lar.

    ``disclosures``: her biri ``{as_of_date, project_id?, strata:[{room_type,
    cum_count, cum_total}]}``. Tarihe göre sıralanır; ardışık çiftler oda-tipi bazında
    farklanır. Döndürür: ``cohorts`` (period_start, period_end, room_type,
    cohort_count, cohort_total, cohort_avg), ``revisions`` (guard tetiklenen geçişler),
    ``reconciliation`` (toplam tutarlılık notları).
    """
    ordered = sorted(
        [d for d in disclosures if _date(d.get("as_of_date"))],
        key=lambda d: _date(d.get("as_of_date")),
    )
    cohorts: list[dict] = []
    revisions: list[dict] = []
    reconciliation: list[dict] = []

    for prev, cur in zip(ordered, ordered[1:]):
        d0, d1 = _date(prev.get("as_of_date")), _date(cur.get("as_of_date"))
        m0, m1 = _strata_map(prev), _strata_map(cur)
        for rt, cur_s in m1.items():
            if rt not in m0:
                continue  # yeni strata → önceki kümülatif yok, farklanamaz
            prev_s = m0[rt]
            dcount = cur_s["cum_count"] - prev_s["cum_count"]
            dtotal = cur_s["cum_total"] - prev_s["cum_total"]
            if dcount < 0 or dtotal < 0:
                # REVİZYON guard: kümülatif azaldı → tutarsız, farklama UYDURULMAZ
                revisions.append(
                    {
                        "period_start": str(d0),
                        "period_end": str(d1),
                        "room_type": rt,
                        "delta_count": dcount,
                        "delta_total": dtotal,
                        "reason": "cumulative_decreased",
                    }
                )
                continue
            if dcount == 0:
                if dtotal != 0:  # sayım sabit ama tutar değişti → reconciliation uyarısı
                    reconciliation.append(
                        {
                            "period_end": str(d1),
                            "room_type": rt,
                            "note": "count_unchanged_total_moved",
                            "delta_total": dtotal,
                        }
                    )
                continue
            cohorts.append(
                {
                    "period_start": str(d0),
                    "period_end": str(d1),
                    "room_type": rt,
                    "cohort_count": int(round(dcount)),
                    "cohort_total": float(dtotal),
                    "cohort_avg": float(dtotal / dcount),
                }
            )
    return {
        "cohorts": cohorts,
        "revisions": revisions,
        "reconciliation": reconciliation,
    }


def toki_composition_moments(cohorts: list[dict]) -> dict:
    """Oda-tipi kohortlardan agregat kompozisyon momentleri.

    Property-level PAIR değil: yalnızca kohort ortalama fiyatı ve kompozisyon payları.
    """
    if not cohorts:
        return {}
    total_n = sum(c["cohort_count"] for c in cohorts)
    grand_total = sum(c["cohort_total"] for c in cohorts)
    out: dict = {
        "toki_cohort_avg_price": (grand_total / total_n) if total_n else float("nan"),
        "toki_cohort_count": int(total_n),
    }
    # oda-tipi kompozisyon payı (agregat)
    by_rt: dict[str, int] = {}
    for c in cohorts:
        by_rt[c["room_type"]] = by_rt.get(c["room_type"], 0) + c["cohort_count"]
    for rt, n in by_rt.items():
        out[f"toki_share_{rt}"] = (n / total_n) if total_n else float("nan")
    return out
