"""Metrikler + TCMB KFE kalibrasyonu.

Motor mikro düzeyde tahmin üretir; KFE (ekspertiz tabanlı, gerçeğe yakın)
agregada TREND çıpasıdır. Buradaki yardımcılar tahminleri ya bilinen bir
ortalamaya (etiketli çapa) ya da KFE büyümesine oturtur.
"""

from __future__ import annotations

import numpy as np


def mape(y_true, y_pred) -> float:
    """Ortalama mutlak yüzde hata (%)."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    mask = yt != 0
    if not mask.any():
        return float("nan")
    return float(np.mean(np.abs((yp[mask] - yt[mask]) / yt[mask])) * 100)


def median_ape(y_true, y_pred) -> float:
    """Medyan mutlak yüzde hata (%)."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    mask = yt != 0
    if not mask.any():
        return float("nan")
    return float(np.median(np.abs((yp[mask] - yt[mask]) / yt[mask])) * 100)


def scale_to_reference(estimates, target_mean: float) -> tuple[np.ndarray, float]:
    """Tahminleri, ortalaması ``target_mean`` olacak şekilde ölçekler.

    Etiketli bir çapa (ör. brokerdan/ekspertizden bilinen ortalama gerçekleşen
    fiyat) ile seviye yanlılığını düzeltmek için.
    """
    est = np.asarray(estimates, dtype=float)
    mean = est.mean()
    factor = float(target_mean) / mean if mean else 1.0
    return est * factor, factor


def align_growth_to_kfe(
    period_means: dict[str, float],
    kfe: dict[str, float],
    anchor: str,
) -> dict[str, float]:
    """Dönem ortalamalarını, ``anchor`` dönemine göre KFE büyümesine oturtur.

    Seviye ``anchor`` döneminden (veriden) alınır; diğer dönemler KFE endeks
    büyümesiyle ölçeklenir. Böylece ilan verisinin seviyesi korunurken trend
    KFE'ye (ekspertiz) sabitlenir.
    """
    if anchor not in kfe or not kfe.get(anchor):
        return dict(period_means)
    anchor_level = period_means.get(anchor)
    if anchor_level is None:
        return dict(period_means)
    out: dict[str, float] = {}
    for period, value in period_means.items():
        if period in kfe and kfe[anchor]:
            out[period] = anchor_level * (kfe[period] / kfe[anchor])
        else:
            out[period] = value
    return out


def period_means(df, period_col: str, value_col: str) -> dict[str, float]:
    """Dönem sütununa göre değer ortalamalarını {dönem: ortalama} döndürür."""
    grouped = df.groupby(period_col)[value_col].mean()
    return {str(period): float(value) for period, value in grouped.items()}


def load_kfe_from_db(session, series_code: str = "TP.KFE.TR") -> dict[str, float]:
    """evds_observations tablosundan KFE serisini {YYYY-MM: endeks} olarak okur.

    Faz 0'da çekilen GERÇEK KFE'yi kalibrasyon çapası olarak kullanmayı sağlar.
    """
    from sqlalchemy import select

    from ..db.models import EvdsObservation

    rows = session.scalars(
        select(EvdsObservation)
        .where(EvdsObservation.series_code == series_code)
        .order_by(EvdsObservation.obs_date)
    ).all()
    return {
        r.obs_date.strftime("%Y-%m"): float(r.value)
        for r in rows
        if r.value is not None
    }
