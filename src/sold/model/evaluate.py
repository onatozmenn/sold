"""k-fold çapraz doğrulama (Faz 4).

Gerçek ground-truth seti genelde küçüktür; tek bir train/test bölmesi
gürültülüdür. k-fold CV, model ve naive baseline'ın doğruluğunu güven aralığıyla
(ortalama ± std) verir.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .calibrate import mape, median_ape
from .estimator import RealizedValuator


def cross_validate(
    frame: pd.DataFrame,
    folds: int = 5,
    seed: int = 0,
    prefer_lightgbm: bool = False,
) -> dict:
    """Etiketli çerçeve üzerinde k-fold CV yürütür; model vs naive metrikleri döndürür."""
    from sklearn.model_selection import KFold

    if "true_realized_price" not in frame.columns:
        raise ValueError("cross_validate: 'true_realized_price' etiketi gerekli.")

    frame = frame.reset_index(drop=True)
    splitter = KFold(n_splits=folds, shuffle=True, random_state=seed)

    model_mape, naive_mape = [], []
    model_med, naive_med = [], []

    for train_idx, test_idx in splitter.split(frame):
        train = frame.iloc[train_idx]
        test = frame.iloc[test_idx]

        valuator = RealizedValuator.train(train, prefer_lightgbm=prefer_lightgbm)
        estimate = valuator.estimate(test)

        y_true = test["true_realized_price"].to_numpy(float)
        naive = test["last_price"].to_numpy(float)

        model_mape.append(mape(y_true, estimate))
        naive_mape.append(mape(y_true, naive))
        model_med.append(median_ape(y_true, estimate))
        naive_med.append(median_ape(y_true, naive))

    naive_mean = float(np.mean(naive_mape))
    model_mean = float(np.mean(model_mape))
    improvement = (naive_mean - model_mean) / naive_mean * 100 if naive_mean else 0.0

    return {
        "folds": folds,
        "n": int(len(frame)),
        "model_mape_mean": model_mean,
        "model_mape_std": float(np.std(model_mape)),
        "naive_mape_mean": naive_mean,
        "naive_mape_std": float(np.std(naive_mape)),
        "model_medape_mean": float(np.mean(model_med)),
        "naive_medape_mean": float(np.mean(naive_med)),
        "improvement_pct": float(improvement),
    }
