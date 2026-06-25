"""Cross-series "balance identities hold" check (docs/data-contracts.md).

A derived entry may declare `check: zero_sum` with a `tolerance`: the composed
series is an accounting residual that must stay within +/-tolerance of zero (the
legacy identity: supply - demand - storage_withdrawal ~ 0). Raises ValueError on
breach, so the CLI marks the run failed and writes nothing.

Identity checks span multiple series rows (long format), so this is plain Python,
not a Pandera column check. Pure — no DB.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def check_identities(df: pd.DataFrame, entries: list[dict[str, Any]]) -> None:
    """Raise if any `check: zero_sum` derived series exceeds its tolerance."""
    for e in entries:
        if e.get("check") != "zero_sum":
            continue
        tol = float(e.get("tolerance", 0.0))
        vals = df.loc[df["series_id"] == e["code"], "value"]
        breaches = vals[vals.abs() > tol]
        if not breaches.empty:
            worst = float(breaches.abs().max())
            raise ValueError(
                f"identity '{e['code']}' (zero_sum) breached on {len(breaches)} date(s); "
                f"worst |residual|={worst:.4g} > tolerance={tol:g}"
            )
