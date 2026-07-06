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

    ``disclosures``: her biri ``{as_of_date, project_id?, table_semantics?,
    strata:[{room_type, cum_count, cum_total}]}``. Farklama YALNIZCA şu koşullarda:
    AYNI proje + AYNI oda-tipi stratum + AYNI kümülatif-tablo semantiği + kümülatif
    sayım ≥ önceki + kümülatif toplam ≥ önceki. Kümülatif değerler düşerse, tarihsel
    tanımlar/tablo semantiği değişirse ya da bir stratum beklenmedik biçimde
    KAYBOLURSA ``revision_detected=True`` ve o geçiş için kohort OTOMATİK TÜRETİLMEZ
    (property-level pair veya asking→closing indirimi ASLA üretilmez).
    """
    cohorts: list[dict] = []
    revisions: list[dict] = []
    reconciliation: list[dict] = []
    revision_detected = False

    # Farklama yalnızca AYNI proje içinde (farklı projeler karıştırılmaz)
    projects: dict[str, list[dict]] = {}
    for d in disclosures:
        if _date(d.get("as_of_date")) is None:
            continue
        pid = str(d.get("project_id") or d.get("proje") or "")
        projects.setdefault(pid, []).append(d)

    for pid, items in projects.items():
        ordered = sorted(items, key=lambda d: _date(d.get("as_of_date")))
        for prev, cur in zip(ordered, ordered[1:]):
            d0, d1 = _date(prev.get("as_of_date")), _date(cur.get("as_of_date"))
            sem0 = prev.get("table_semantics")
            sem1 = cur.get("table_semantics")
            if sem0 is not None and sem1 is not None and sem0 != sem1:
                # Tablo semantiği değişti → tarihsel tanım farkı, farklama yapılamaz
                revision_detected = True
                revisions.append(
                    {"project_id": pid, "period_start": str(d0), "period_end": str(d1),
                     "reason": "table_semantics_changed"}
                )
                continue
            m0, m1 = _strata_map(prev), _strata_map(cur)
            # Stratum kaybı guard'ı: öncekinde olup şimdikinde OLMAYAN oda-tipi
            for rt in m0.keys() - m1.keys():
                revision_detected = True
                revisions.append(
                    {"project_id": pid, "period_start": str(d0), "period_end": str(d1),
                     "room_type": rt, "reason": "stratum_disappeared"}
                )
            for rt, cur_s in m1.items():
                if rt not in m0:
                    continue  # yeni stratum → önceki kümülatif yok, farklanamaz
                prev_s = m0[rt]
                dcount = cur_s["cum_count"] - prev_s["cum_count"]
                dtotal = cur_s["cum_total"] - prev_s["cum_total"]
                if dcount < 0 or dtotal < 0:
                    # REVİZYON guard: kümülatif azaldı → tutarsız, farklama UYDURULMAZ
                    revision_detected = True
                    revisions.append(
                        {"project_id": pid, "period_start": str(d0), "period_end": str(d1),
                         "room_type": rt, "delta_count": dcount, "delta_total": dtotal,
                         "reason": "cumulative_decreased"}
                    )
                    continue
                if dcount == 0:
                    if dtotal != 0:  # sayım sabit ama tutar değişti → reconciliation
                        reconciliation.append(
                            {"project_id": pid, "period_end": str(d1), "room_type": rt,
                             "note": "count_unchanged_total_moved", "delta_total": dtotal}
                        )
                    continue
                cohorts.append(
                    {
                        "project_id": pid,
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
        "revision_detected": revision_detected,
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
