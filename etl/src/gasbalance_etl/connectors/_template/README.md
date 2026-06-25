# _template — copy this to start a new connector

Copy this folder to `connectors/<source-name>/` and implement the contract
(see `../CLAUDE.md`). A finished connector contains:

```
<source-name>/
├── __init__.py        # registers the connector with the CLI
├── connector.py       # fetch(since) -> raw ;  to_canonical(raw) -> DataFrame
├── config.py          # secret/config NAMES, read via core.config (never hardcoded)
└── README.md          # what the source is, cadence, units, gotchas
```

Its Pandera schema goes in `../../validation/<source-name>.py`, and its contract test
(against a recorded fixture) in `etl/tests/`.

> This `_template/` holds docs only — no code — until the first real source is added.
