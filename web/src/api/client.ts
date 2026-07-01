// The one place that knows the API endpoints. Components never fetch() directly (web/CLAUDE.md).
import type { ForecastSeries, MetricGroup, Scenario, Series, SeriesPoints } from './types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

type Params = Record<string, string | undefined>

async function get<T>(path: string, params?: Params): Promise<T> {
  const url = new URL(path, BASE)
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== '') url.searchParams.set(k, v)
  }
  const res = await fetch(url)
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} — GET ${url.pathname}${url.search}`)
  }
  return res.json() as Promise<T>
}

export function getSeries(
  opts: { area?: string; category?: string; active?: boolean } = {},
): Promise<Series[]> {
  return get('/series', {
    area: opts.area,
    category: opts.category,
    active: opts.active === undefined ? undefined : String(opts.active),
  })
}

export function getObservations(codes: string[]): Promise<SeriesPoints[]> {
  return get('/observations', { codes: codes.join(',') })
}

export function getForecasts(
  codes: string[],
  opts: { scenario?: string; made_on?: string; models?: string } = {},
): Promise<ForecastSeries[]> {
  return get('/forecasts', {
    codes: codes.join(','),
    scenario: opts.scenario,
    made_on: opts.made_on ?? 'latest',
    models: opts.models,
  })
}

export function getCovariates(codes: string[]): Promise<SeriesPoints[]> {
  return get('/covariates', { codes: codes.join(',') })
}

// Covariate forecast: latest vintage per delivery hour, daily-mean, one series per model.
export function getForecastCovariates(codes: string[]): Promise<SeriesPoints[]> {
  return get('/forecast-covariates', { codes: codes.join(',') })
}

// Stubs for later slices (metrics view, scenario list).

export function getMetrics(
  code: string,
  opts: { scenario?: string; models?: string } = {},
): Promise<MetricGroup[]> {
  return get('/metrics', { code, scenario: opts.scenario, models: opts.models })
}

export function getScenarios(
  opts: { kind?: string; active?: boolean } = {},
): Promise<Scenario[]> {
  return get('/scenarios', {
    kind: opts.kind,
    active: opts.active === undefined ? undefined : String(opts.active),
  })
}
