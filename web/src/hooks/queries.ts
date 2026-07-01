// TanStack Query wrappers over the API client — components consume these, not the client directly.
import { useQuery } from '@tanstack/react-query'
import {
  getCovariates,
  getForecastCovariates,
  getForecasts,
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
