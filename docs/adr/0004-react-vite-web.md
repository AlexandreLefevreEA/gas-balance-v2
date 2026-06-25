# 0004. React + Vite for the web app

- Status: Accepted
- Date: 2026-06-25

## Context

The web app must be simple, fast, and reactive, and consume the API as a separate
service. Options weighed: SvelteKit (least boilerplate, very fast), React + Vite
(largest ecosystem/hiring pool), Streamlit (Python, fastest to build but blurs the
API/web split and is less reactive).

## Decision

**React + Vite** (TypeScript). The API/web separation is kept strict — the web app
talks only to `api/` over HTTP.

## Consequences

- Easy: deep ecosystem, charting libraries, hiring, long-term support.
- Easy: Vite dev loop is fast; clear separation from the backend.
- Give up: a little more boilerplate than Svelte; not Python-native like Streamlit.
- Charting library chosen at first chart (candidates: ECharts for large series, Recharts for simple).

## Trigger to revisit

Only if the dashboard stays trivially small and a Python-native tool would save real
effort, or if bundle size becomes a hard constraint that favours Svelte.
