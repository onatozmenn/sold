"""İki aşamalı realized fiyat tahmin motoru.

Aşama 1 — Hedonik model: taşınmaz niteliklerinden log(ilan fiyatı) tahmini.
    Kalıntı (overprice_residual = log(son fiyat) − hedonik tahmin) ilanın
    göreli olarak ne kadar pahalı listelendiğini yakalar.

Aşama 2 — İndirim modeli: 'son ilan fiyatı → gerçekleşen' indirimini
    (sale-to-list) niteliklerden + time-on-market + toplam düşüş + kalıntıdan
    tahmin eder. Etiket olarak (sentetik ya da broker/ekspertiz kaynaklı)
    gerçekleşen fiyat gerekir.

Realized ≈ son_fiyat × (1 − d̂) × (1 − residual_bargain)
"""

from __future__ import annotations

import contextlib
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_selection import VarianceThreshold
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

HEDONIC_NUM = [
    "gross_m2",
    "building_age",
    "floor",
    "total_floors",
    "room_count_num",
    "lat",
    "lon",
]
HEDONIC_CAT = ["province", "district", "neighborhood", "heating", "listing_type"]

DISCOUNT_NUM = HEDONIC_NUM + [
    "days_on_market",
    "num_price_changes",
    "total_drop_pct",
    "overprice_residual",
]
DISCOUNT_CAT = HEDONIC_CAT


def make_regressor(prefer_lightgbm: bool = False):
    """Gradient boosting regresörü döndürür (varsayılan: sklearn HistGBR).

    LightGBM kuruluysa ve ``prefer_lightgbm=True`` ise onu kullanır; aksi halde
    ek yerel bağımlılık gerektirmeyen sklearn HistGradientBoostingRegressor.
    """
    if prefer_lightgbm:
        try:
            from lightgbm import LGBMRegressor

            return LGBMRegressor(
                n_estimators=500,
                learning_rate=0.05,
                num_leaves=31,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=0,
                verbose=-1,
            )
        except Exception:  # noqa: BLE001 — lightgbm yoksa sessizce sklearn'e düş
            pass
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(
        max_iter=400,
        learning_rate=0.06,
        l2_regularization=1.0,
        random_state=0,
    )


def _build_pipeline(num_cols: list[str], cat_cols: list[str], prefer_lightgbm: bool) -> Pipeline:
    # Sayısal: medyan doldur (tümü-NaN sütunları düşürür) + sıfır-varyans ele.
    # Böylece broker verisindeki boş lat/lon ve sabit sütunlar HistGBR'ı bozmaz.
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", keep_empty_features=False)),
            ("variance", VarianceThreshold(0.0)),
        ]
    )
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="constant", fill_value="NA")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    preprocessor = ColumnTransformer(
        [
            ("num", numeric, num_cols),
            ("cat", categorical, cat_cols),
        ],
        remainder="drop",
    )
    return Pipeline([("pre", preprocessor), ("reg", make_regressor(prefer_lightgbm))])


def _frame(df: pd.DataFrame, num_cols: list[str], cat_cols: list[str]) -> pd.DataFrame:
    X = df.reindex(columns=num_cols + cat_cols).copy()
    for col in num_cols:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    for col in cat_cols:
        X[col] = X[col].astype("object")
    return X


@contextlib.contextmanager
def _quiet():
    # Broker verisinde lat/lon tamamen boş olabilir; imputer bunları düşürür
    # ve beklenen bir uyarı basar (fit ve predict sırasında) — gürültüyü bastır.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Skipping features without any observed values",
            category=UserWarning,
        )
        yield


class HedonicModel:
    """Taşınmaz niteliklerinden log(son ilan fiyatı) tahmin eder."""

    def __init__(self, prefer_lightgbm: bool = False) -> None:
        self.pipe = _build_pipeline(HEDONIC_NUM, HEDONIC_CAT, prefer_lightgbm)

    def fit(self, df: pd.DataFrame) -> "HedonicModel":
        X = _frame(df, HEDONIC_NUM, HEDONIC_CAT)
        y = np.log(df["last_price"].to_numpy(dtype=float))
        with _quiet():
            self.pipe.fit(X, y)
        return self

    def predict_log(self, df: pd.DataFrame) -> np.ndarray:
        with _quiet():
            return self.pipe.predict(_frame(df, HEDONIC_NUM, HEDONIC_CAT))

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return np.exp(self.predict_log(df))


class DiscountModel:
    """Son ilan fiyatından gerçekleşene indirimi (sale-to-list) tahmin eder."""

    def __init__(self, prefer_lightgbm: bool = False) -> None:
        self.pipe = _build_pipeline(DISCOUNT_NUM, DISCOUNT_CAT, prefer_lightgbm)

    def fit(self, df: pd.DataFrame, target: str = "discount") -> "DiscountModel":
        X = _frame(df, DISCOUNT_NUM, DISCOUNT_CAT)
        y = df[target].to_numpy(dtype=float)
        with _quiet():
            self.pipe.fit(X, y)
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        with _quiet():
            d = self.pipe.predict(_frame(df, DISCOUNT_NUM, DISCOUNT_CAT))
        return np.clip(d, -0.05, 0.5)


class RealizedValuator:
    """Hedonik + indirim modellerini birleştiren uçtan uca tahminci."""

    def __init__(
        self,
        hedonic: HedonicModel,
        discount: DiscountModel | None,
        residual_bargain: float = 0.0,
    ) -> None:
        self.hedonic = hedonic
        self.discount = discount
        self.residual_bargain = residual_bargain

    # ---- eğitim -------------------------------------------------------- #
    @classmethod
    def train(
        cls,
        df: pd.DataFrame,
        prefer_lightgbm: bool = False,
        residual_bargain: float = 0.0,
    ) -> "RealizedValuator":
        """Etiketli ('true_realized_price') veriyle iki aşamalı eğitim."""
        if "true_realized_price" not in df.columns:
            raise ValueError(
                "train(): 'true_realized_price' etiketi gerekli. "
                "Etiketsiz veri için train_hedonic_only kullanın."
            )
        frame = df.copy()
        hedonic = HedonicModel(prefer_lightgbm).fit(frame)
        frame["overprice_residual"] = np.log(
            frame["last_price"].to_numpy(dtype=float)
        ) - hedonic.predict_log(frame)
        frame["discount"] = np.clip(
            1
            - frame["true_realized_price"].to_numpy(dtype=float)
            / frame["last_price"].to_numpy(dtype=float),
            -0.05,
            0.5,
        )
        discount = DiscountModel(prefer_lightgbm).fit(frame)
        return cls(hedonic, discount, residual_bargain=residual_bargain)

    @classmethod
    def train_hedonic_only(
        cls,
        df: pd.DataFrame,
        prefer_lightgbm: bool = False,
        residual_bargain: float = 0.05,
    ) -> "RealizedValuator":
        """Etiketsiz veri için: yalnızca hedonik + sabit (kalibre) indirim."""
        hedonic = HedonicModel(prefer_lightgbm).fit(df.copy())
        return cls(hedonic, None, residual_bargain=residual_bargain)

    # ---- tahmin -------------------------------------------------------- #
    def estimate(self, df: pd.DataFrame) -> np.ndarray:
        frame = df.copy()
        frame["overprice_residual"] = np.log(
            frame["last_price"].to_numpy(dtype=float)
        ) - self.hedonic.predict_log(frame)
        if self.discount is not None:
            d_hat = self.discount.predict(frame)
        else:
            d_hat = np.zeros(len(frame))
        last = frame["last_price"].to_numpy(dtype=float)
        return last * (1 - d_hat) * (1 - self.residual_bargain)

    # ---- kalıcılık ----------------------------------------------------- #
    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "RealizedValuator":
        return joblib.load(Path(path))
