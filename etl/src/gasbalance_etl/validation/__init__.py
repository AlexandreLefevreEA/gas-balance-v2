"""Pandera schemas — the data-trust layer. Nothing loads unless its schema passes.

`canonical.py` holds the shared canonical schema (universal invariants); per-source
modules (e.g. `ce.py`) add source-specific range/unit checks. See docs/data-contracts.md.
"""
