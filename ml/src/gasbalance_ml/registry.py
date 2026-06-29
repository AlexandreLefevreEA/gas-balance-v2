"""The series -> best-model registry: the `select` step's output, the `forecast` step's input.

A plain YAML map keyed by series code — diffable, committable, human-auditable (so a reviewer
sees which model serves each series without querying anything). One entry per series:

  CE.54:
    area: DE
    model: lightgbm
    params: {n_estimators: 437, learning_rate: 0.041, ...}
    skill: 0.62              # walk-forward skill vs seasonal_naive (>0 = beat the floor)
    model_run_id: "a1b2c3"   # selection-time MLflow run id (the forecast PK component)
    selected_on: "2026-06-29"
    n_origins: 24

`select` decides the model per series (data-driven); the registry just persists that decision
so `forecast` never re-backtests. Re-run `select` periodically to refresh it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# ml/experiments/series_models.yaml (registry.py is at ml/src/gasbalance_ml/registry.py).
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "experiments" / "series_models.yaml"


def load_registry(path: Path = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
    """Read the registry; an absent file is an empty registry (select hasn't run yet)."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML map of series, got {type(data).__name__}")
    return data


def save_registry(registry: dict[str, dict[str, Any]], path: Path = DEFAULT_PATH) -> None:
    """Write the registry (creating `experiments/` if needed); sorted for stable diffs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(registry, sort_keys=True, default_flow_style=False), encoding="utf-8"
    )
