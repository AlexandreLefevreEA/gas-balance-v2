"""SeasonalNaive — the skill floor every model must beat.

Predicts each target date from the value one seasonal period earlier in the training
target (default 365 days), walking back in whole periods until a known value is found,
else the last training value. No features used.

ponytail: a hand-rolled seasonal-naive beats pulling statsforecast just for the floor.
AutoARIMA/ETS slot in behind this same registry when a classical model earns its keep.
"""

from __future__ import annotations

import pandas as pd

from gasbalance_ml.models.base import Model, register

_MAX_PERIODS = 50  # ponytail: >> any horizon; bounds the look-back loop


@register
class SeasonalNaive(Model):
    name = "seasonal_naive"

    def __init__(self, period_days: int = 365) -> None:
        self.period_days = period_days
        self._y: pd.Series = pd.Series(dtype=float)

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        del X  # baseline ignores features by design
        self._y = y.dropna().sort_index()

    def predict(self, X: pd.DataFrame) -> pd.Series:
        if self._y.empty:
            raise RuntimeError("SeasonalNaive.predict called before fit")
        last = float(self._y.iloc[-1])
        index = self._y.index
        period = pd.DateOffset(days=self.period_days)  # pandas-native; avoids np timedelta
        out: list[float] = []
        for d in X.index:
            value = last
            probe = d - period
            for _ in range(_MAX_PERIODS):
                if probe in index:
                    value = float(self._y.loc[probe])
                    break
                probe = probe - period
            out.append(value)
        return pd.Series(out, index=X.index, dtype=float)
