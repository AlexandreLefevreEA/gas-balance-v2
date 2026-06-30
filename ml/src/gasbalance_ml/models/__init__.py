"""Model registry — importing this package registers the built-in models."""

from __future__ import annotations

from gasbalance_ml.models import baseline, lgbm, static  # noqa: F401  (side effect: registration)
from gasbalance_ml.models.base import Model, get, register

__all__ = ["Model", "get", "register"]
