"""Curated series-dictionary loader (hierarchical YAML, ported from legacy).

`load_series_dict("ce")` reads `ce.yaml` (sat next to this file) into a list of
plain dicts. The dictionary is the curation knob: which series a source ingests,
plus the metadata that materialises each `series` row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DIR = Path(__file__).parent


def load_series_dict(source: str) -> list[dict[str, Any]]:
    """Read the curated series dictionary for `source` from `<source>.yaml`."""
    path = _DIR / f"{source}.yaml"
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or []
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected a YAML list of series, got {type(data).__name__}")
    return data
