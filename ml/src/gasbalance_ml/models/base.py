"""Model interface + a name registry.

A `Model` is a thin fit/predict wrapper over an *already-assembled* feature matrix.
ALL leakage-safety lives in `features.assemble` (the train/predict cut), so a model
never reasons about time — it just maps X -> y. Models register by name so the
pipeline selects them from config, not a hardcoded loop (see ml/CLAUDE.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar

import pandas as pd


class Model(ABC):
    """Fit/predict over aligned (target, features). Time-agnostic by design."""

    name: ClassVar[str]  # registry key, set by each subclass

    @abstractmethod
    def fit(self, y: pd.Series, X: pd.DataFrame) -> None:
        """Fit on aligned target/features (same index)."""

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> pd.Series:
        """Predict for the rows of X; returns a Series indexed like X."""


# Values are callables (the model classes) so callers instantiate without mypy flagging
# abstract `Model` — `get(name)(**params)` returns a concrete model.
_REGISTRY: dict[str, Callable[..., Model]] = {}


def register(cls: type[Model]) -> type[Model]:
    """Class decorator — add a model to the registry under its `name`."""
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> Callable[..., Model]:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"unknown model '{name}'. Known: {known}") from None
