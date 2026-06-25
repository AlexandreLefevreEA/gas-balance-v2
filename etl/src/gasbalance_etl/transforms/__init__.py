"""Shared mapping helpers + derived series (see etl/CLAUDE.md).

`compose.py` holds the series-composition primitive reused by connectors and the
derived stage; `derived.py` is the post-load stage that computes is_derived series
from already-loaded data and stores them in `observation`. See docs/adr/0007.
"""
