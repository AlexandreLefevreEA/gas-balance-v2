"""Fast history-projection models — no covariates, no learning.

These forecast a series from its own past, the way legacy did for most of the supply side
(`models/scenarios/forecast_scenario.py`): production, LNG sendout, linepack/imbalance,
capacity points and a couple of border points that aren't worth a learned model. They are
**weather-blind** (the same path under every scenario) and **NaN-safe** — an absent source
series yields an all-NaN forecast, surfaced downstream as a gap rather than a wrong number
(the user's rule: a missing series must show as NaN, never silently drop from a total).

`predict` reads only `X.index` (the horizon dates); `X`'s columns are ignored, so the same
`features.assemble` machinery is unnecessary — `forecast.forecast_static` builds the bare
future index for them.
"""

from __future__ import annotations

import logging

import pandas as pd

from gasbalance_ml.models.base import Model, register

log = logging.getLogger(__name__)


def _history(y: pd.Series) -> pd.Series:
    return y.dropna().sort_index()


@register
class AbsoluteZero(Model):
    """Future = 0 (legacy `AbsoluteModel(value=0)` for Linepack + CE Imbalance)."""

    name = "absolute_zero"

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        del y, X

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(0.0, index=X.index, dtype=float)


@register
class Ffill(Model):
    """Forward-fill the last observed value (legacy `FFILLModel`; capacity series)."""

    name = "ffill"

    def __init__(self) -> None:
        self._last = float("nan")

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        del X
        h = _history(y)
        self._last = float(h.iloc[-1]) if not h.empty else float("nan")

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(self._last, index=X.index, dtype=float)


@register
class AveragePlusOutage(Model):
    """Flat trailing-window mean (legacy `AveragePlusOutageModel`, production + a few imports).

    Legacy lifts past outage-suppressed lows to a 60-day rolling mean, takes the trailing 365d
    mean, then caps the forward level by the scheduled restricted capacity. v2 has **no gas
    outage / restricted-capacity feed** (confirmed absent), so we keep the trailing-mean level
    and drop the cap — warned once so the degradation isn't silent."""

    name = "average_plus_outage"
    _warned = False

    def __init__(self, window_days: int = 365) -> None:
        self.window_days = window_days
        self._level = float("nan")

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        del X
        h = _history(y)
        self._level = float(h.iloc[-self.window_days :].mean()) if not h.empty else float("nan")
        if not type(self)._warned:  # per-subclass: Azeri stays silent (it never had a cap)
            log.warning(
                "average_plus_outage: no gas outage/restricted-capacity feed in v2 -> "
                "trailing-%dd mean with no capacity cap (legacy parity dropped)",
                self.window_days,
            )
            type(self)._warned = True

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(self._level, index=X.index, dtype=float)


@register
class SeasonalMean(Model):
    """Future day = mean of historical values on that **calendar day** (month, day).

    Legacy had no LNG sendout model; the user chose a seasonal average for it. Keyed by
    (month, day) rather than day-of-year so it is leap-year-stable (Jun 1 is one bucket every
    year). Falls back to the overall mean for a calendar day unseen in history; all-NaN if the
    source series is absent."""

    name = "seasonal_mean"

    def __init__(self) -> None:
        self._by_md: dict[tuple[int, int], float] = {}
        self._overall = float("nan")

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        del X
        h = _history(y)
        if h.empty:
            self._by_md, self._overall = {}, float("nan")
            return
        grouped = h.groupby([h.index.month, h.index.day]).mean()
        self._by_md = {(int(m), int(d)): float(v) for (m, d), v in grouped.items()}
        self._overall = float(h.mean())

    def predict(self, X: pd.DataFrame) -> pd.Series:
        vals = [self._by_md.get((ts.month, ts.day), self._overall) for ts in X.index]
        return pd.Series(vals, index=X.index, dtype=float)


@register
class Azeri(AveragePlusOutage):
    """Trailing-730d mean (legacy `AzeriModel`). Legacy subtracts scraped AGSC outages; v2 has
    no such feed (confirmed absent) -> mean only. Same trailing-mean as `average_plus_outage`,
    longer default window and no cap-drop warning. All-NaN if the source is absent."""

    name = "azeri"
    _warned = True  # not production — no capacity-cap degradation to warn about

    def __init__(self, window_days: int = 730) -> None:
        super().__init__(window_days)


@register
class BoundedPersistence(Model):
    """Last value, floored at `floor` (legacy Kyustendil = Prophet logistic cap/floor).

    ponytail: a Prophet logistic-growth fit for one tiny saturating border point isn't worth the
    dependency or the runtime — last-value-with-a-floor keeps the non-negativity that mattered.
    Legacy's cap (cap=2, its own units) is dropped; add a cap back if a real ceiling is known.
    All-NaN if the source is absent."""

    name = "bounded_persistence"

    def __init__(self, floor: float = 0.0) -> None:
        self.floor = floor
        self._val = float("nan")

    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        del X
        h = _history(y)
        self._val = max(float(h.iloc[-1]), self.floor) if not h.empty else float("nan")

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(self._val, index=X.index, dtype=float)
