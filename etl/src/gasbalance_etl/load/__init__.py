"""Load validated canonical series into Postgres — idempotent upserts only.

Never DELETE/TRUNCATE: upsert overwrites a value for an existing `(series, date)` and
leaves every other row untouched. See `upsert.py`.
"""
