// TanStack Query wrappers over the API client — components consume these, not the client directly.
import { useQueries, useQuery } from '@tanstack/react-query'
import {
  getCovariates,
  getForecastCovariates,
  getForecasts,
  getMetrics,
  getObservations,
  getSeries,
} from '../api/client'

export function useSeries() {
  return useQuery({ queryKey: ['series'], queryFn: () => getSeries({ active: true }) })
}

export function useObservations(codes: string[]) {
  return useQuery({
    queryKey: ['observations', codes],
    queryFn: () => getObservations(codes),
    enabled: codes.length > 0,
  })
}

export function useForecasts(codes: string[]) {
  return useQuery({
    queryKey: ['forecasts', 'latest', codes],
    queryFn: () => getForecasts(codes, { made_on: 'latest' }),
    enabled: codes.length > 0,
  })
}

export function useCovariates(codes: string[]) {
  return useQuery({
    queryKey: ['covariates', codes],
    queryFn: () => getCovariates(codes),
    enabled: codes.length > 0,
  })
}

export function useForecastCovariates(codes: string[]) {
  return useQuery({
    queryKey: ['forecast-covariates', codes],
    queryFn: () => getForecastCovariates(codes),
    enabled: codes.length > 0,
  })
}

// --- Model explorer ---

// Error metrics for a series (all scenarios × models). Also the model/scenario discovery source.
export function useMetrics(code: string) {
  return useQuery({
    queryKey: ['metrics', code],
    queryFn: () => getMetrics(code),
    enabled: code.length > 0,
  })
}

// One "latest forecast per target date" line per model. made_on='latest' collapses across models
// (DISTINCT ON), so we filter to a single model_run_id per query — one query per selected model.
export function useModelForecastLines(code: string, scenario: string, models: string[]) {
  return useQueries({
    queries: models.map((m) => ({
      queryKey: ['forecasts', 'latest', code, scenario, m],
      queryFn: () => getForecasts([code], { scenario, made_on: 'latest', models: m }),
      enabled: code.length > 0 && scenario.length > 0,
    })),
  })
}

// Every vintage of one model (the spaghetti plot); bounded by `from` to cap payload.
export function useVintages(code: string, scenario: string, model: string, from: string) {
  return useQuery({
    queryKey: ['forecasts', 'all', code, scenario, model, from],
    queryFn: () => getForecasts([code], { scenario, made_on: 'all', models: model, from }),
    enabled: code.length > 0 && scenario.length > 0 && model.length > 0,
  })
}
