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

- TypeScript, strict mode. **oxlint** (`npm run lint`) + `tsc -b --noEmit` must pass (CI runs both).
- All server state goes through `src/api/` (typed client) — components consume the **TanStack Query**
  hooks in `src/hooks/`, never `fetch()` ad hoc.
- API base URL comes from `VITE_API_BASE_URL`. Vite reads the **repo-root `.env`** (`envDir: '..'`),
  and only `VITE_`-prefixed vars reach the browser.
- Routing: **React Router**. Charting: **ECharts** via `echarts-for-react` (chosen for large gas
  series). Keep bundle lean — the first cut imports full echarts (`ponytail:` split when it bites).
- Tests with **vitest** (`vitest run` in CI; tests co-located as `*.test.ts`).

> Deferred: openapi-typescript codegen (hand-written types in `src/api/types.ts` for now) and
> TanStack Table (for the balance grid) land with the feature pages that need them.
