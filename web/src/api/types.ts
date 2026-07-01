// Hand-written mirrors of the API JSON (api/src/gasbalance_api/schemas/*).
// ponytail: hand-typed while the surface is small; swap to openapi-typescript codegen when it grows.

export interface Series {
  code: string
  name: string
  category: string | null
  sub_group: string | null
  area: string | null
  unit: string
  source: string
  is_derived: boolean
  is_active: boolean
}

export interface Point {
  date: string // ISO YYYY-MM-DD
  value: number
}

export interface SeriesPoints {
  code: string
  points: Point[]
}

export interface ForecastPoint {
  target_date: string // ISO YYYY-MM-DD
  value: number
  model_run_id: string
  made_on: string | null // null when made_on=latest
}

export interface ForecastSeries {
  code: string
  scenario: string
  points: ForecastPoint[]
}

export interface Scenario {
  code: string
  description: string | null
  kind: string
  adjustments: unknown[] | null
  weather_years: string[] | null
  is_active: boolean
}

export interface MetricBucket {
  bucket: string // h1 | h2-7 | h8-30 | h31-90 | h91-365 | h366+
  n: number
  mae: number
  rmse: number
  bias: number
}

export interface MetricGroup {
  scenario: string
  model_run_id: string
  buckets: MetricBucket[]
}
