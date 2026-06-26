"""ECB euro FX reference-rate connector — daily reference rates (foreign per EUR) per currency.

Source: the European Central Bank's public history file
`https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip` — a ZIP holding one wide CSV
(`Date` + one column per currency, history back to 1999). Each cell is the ECB daily reference
rate as **units of foreign currency per 1 EUR** (e.g. the `USD` column ~1.08 means 1 EUR =
1.08 USD). A handful of these are **price/supply covariates** for the gas balance: USD (LNG /
oil pricing), GBP (the UK NBP hub), NOK (Norwegian pipeline supply). One series per currency,
code `ECB.FX.<currency>`; `group = "fx"`, `sub_group = "spot"`.

Direction is left as published (foreign per EUR) — the connector only fetches + maps +
validates; inversion to EUR-per-foreign is a downstream `ml/` concern. The curated currencies
live in `settings/ecb_fx.yaml`.

Storage: a daily series → the `covariate` table via the `load` hook (ADR 0008), each rate keyed
by a midnight-UTC `ts` (same sink as the Kpler daily covariates). Validated by `fx_schema`.

Refresh: **full refresh**. The hist file *is* the entire 1999→today history in one small
download, and the covariate upsert is idempotent (PK `(series_id, ts)`), so a run re-loads the
whole history cheaply — no incremental window to manage.

Auth: **none** — the file is public; `fetch()` is a single GET (no async fan-out needed).
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import zipfile
from typing import TYPE_CHECKING, Any

import httpx
import pandas as pd

from gasbalance_etl.settings import load_series_dict
from gasbalance_etl.validation.fx import fx_schema

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# --- connector interface (read by the CLI registry) -------------------------
source = "ecb_fx"
schema = fx_schema

_ZIP_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"


def _code(currency: str) -> str:
    """Series code, e.g. ECB.FX.USD / ECB.FX.GBP."""
    return f"ECB.FX.{currency}"


def series_dict() -> list[dict[str, Any]]:
    """Curated dictionary = the currency YAML -> one daily FX series per currency."""
    return [
        {
            "code": _code(e["currency"]),
            "name": f"EUR/{e['currency']} reference rate",
            "group": "fx",
            "sub_group": "spot",
            "area": None,  # an FX rate is global, not a balance area (canonical `area` is nullable)
            "unit": f"{e['currency']}/EUR",  # foreign per EUR, as the ECB publishes it
            "currency": e["currency"],  # used by to_canonical merge + fetch filter
        }
        for e in load_series_dict(source)
    ]


def load(session: Session, df: pd.DataFrame, run_id: int, code_to_id: dict[str, int]) -> int:
    """Route this connector's daily rows to the `covariate` table (ADR 0008).

    Imported lazily so importing the connector (for the registry) stays DB-free.
    """
    from gasbalance_etl.load.upsert import upsert_covariates

    return upsert_covariates(session, df, run_id, code_to_id)


def fetch(since: dt.date | None = None) -> pd.DataFrame:
    """Full refresh: download the ECB hist ZIP and parse the curated currencies.

    `since` (framework contract) is ignored — the hist file is the whole 1999→today history in
    one small public download, and the covariate upsert is idempotent, so we re-load it all
    rather than manage an incremental window. Returns raw `[date, currency, value]` rows.
    """
    del since
    wanted = {e["currency"] for e in load_series_dict(source)}
    log.info("ecb_fx: downloading ECB hist zip, keeping %s", sorted(wanted))
    resp = httpx.get(_ZIP_URL, timeout=180.0, follow_redirects=True)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("utf-8")
    return _parse(text, wanted)


def _parse(text: str, currencies: set[str]) -> pd.DataFrame:
    """Parse the wide ECB CSV into long `[date, currency, value]`, keeping `currencies`.

    Pure (no network) so the contract test can exercise it directly. The CSV has a trailing
    comma (→ an `Unnamed` column we drop) and blanks / `N/A` for currencies not quoted on a
    day (→ coerced to NaN and dropped). Dates are `YYYY-MM-DD`.
    """
    df = pd.read_csv(io.StringIO(text))
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    long = df.melt(id_vars="Date", var_name="currency", value_name="value")
    long = long[long["currency"].isin(currencies)].copy()
    long["value"] = pd.to_numeric(long["value"], errors="coerce")  # "N/A" / blank -> NaN
    long = long.dropna(subset=["value"])
    long["date"] = pd.to_datetime(long["Date"])  # 'YYYY-MM-DD' -> naive datetime64
    return long[["date", "currency", "value"]]


def to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    """Map (date, currency) rate rows to canonical series; drop unknown currencies / nulls."""
    out_cols = ["date", "series_id", "name", "group", "sub_group", "area", "value", "source"]
    meta = pd.DataFrame(
        [
            {
                "currency": e["currency"],
                "series_id": e["code"],
                "name": e["name"],
                "group": e["group"],
                "sub_group": e["sub_group"],
                "area": e["area"],
            }
            for e in series_dict()
        ]
    )
    df = raw.copy()
    df = df[df["value"].notna()]
    df = df.merge(meta, on="currency", how="inner")  # currencies not in the dictionary drop out
    if df.empty:
        return pd.DataFrame(columns=out_cols)
    df["value"] = df["value"].astype(float)
    df["source"] = source
    return df[out_cols]
