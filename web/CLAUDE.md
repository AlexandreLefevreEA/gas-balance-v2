# web/ — React + Vite dashboard

Simple, fast, reactive UI for gas-balance series & forecasts. Talks **only** to the
API over HTTP — no DB access, no business logic (ADR 0004).

## Layout (target)

```
src/
├── api/         # typed API client (one place that knows the endpoints)
├── components/  # presentational + chart components
├── pages/       # routed views
├── hooks/       # data-fetching / state hooks
└── lib/         # helpers, formatting
```

## Conventions

- TypeScript, strict mode. ESLint + `tsc --noEmit` must pass (CI runs both).
- All server state goes through `src/api/` — components never `fetch()` ad hoc.
- API base URL comes from `VITE_API_BASE_URL` (env; only `VITE_`-prefixed vars reach the browser).
- Charting library is chosen at the first chart (candidates: ECharts for large
  series, Recharts for simple). Keep bundle lean.
- Tests with **vitest**.

> Scaffold: app not generated yet. Initialise per the README, then build out `src/`.
