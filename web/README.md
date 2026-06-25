# web

React + Vite (TypeScript) dashboard. Consumes the API; renders gas-balance series and
forecasts. See [`CLAUDE.md`](CLAUDE.md).

## Initialise the app (implementation step)

Rather than hand-maintain a half-scaffold, generate it with Vite, then keep the
`package.json` scripts below:

```bash
cd web
npm create vite@latest . -- --template react-ts
npm install
npm run dev
```

Point it at the API with `VITE_API_BASE_URL` (see `../.env.example`).

## Scripts (expected)

| Script | Does |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run build` | type-check + production build |
| `npm run lint` | ESLint |
| `npm test` | vitest |
