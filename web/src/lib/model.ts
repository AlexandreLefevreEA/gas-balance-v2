// Model-page helpers: friendly model names + client-side date joins for the scatter/error views.
// Pure — unit-tested in model.test.ts.
import type { ForecastPoint } from '../api/types'

export type Tidy = { date: string; value: number }

const asc = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/** Strip a trailing `-YYYY-MM-DD` run stamp: "lightgbm-2026-06-29" → "lightgbm". */
export function friendlyModel(id: string): string {
  return id.replace(/-\d{4}-\d{2}-\d{2}$/, '')
}

/** Split one model's forecast points into one line per `made_on` vintage (the spaghetti plot). */
export function groupByVintage(points: readonly ForecastPoint[]): Map<string, Tidy[]> {
  const byRun = new Map<string, Tidy[]>()
  for (const p of points) {
    const run = p.made_on ?? ''
    const arr = byRun.get(run)
    const pt = { date: p.target_date, value: p.value }
    if (arr) arr.push(pt)
    else byRun.set(run, [pt])
  }
  for (const arr of byRun.values()) arr.sort((a, b) => asc(a.date, b.date))
  return byRun
}

/** forecast − actual, inner-joined on date (unmatched dates dropped). */
export function errorPoints(forecast: readonly Tidy[], actual: readonly Tidy[]): Tidy[] {
  const act = new Map(actual.map((p) => [p.date, p.value]))
  const out: Tidy[] = []
  for (const f of forecast) {
    const a = act.get(f.date)
    if (a !== undefined) out.push({ date: f.date, value: f.value - a })
  }
  return out
}

/** Inner-join two tidy series on date → `[a.value, b.value]` pairs (scatter data). */
export function joinOnDate(a: readonly Tidy[], b: readonly Tidy[]): [number, number][] {
  const bm = new Map(b.map((p) => [p.date, p.value]))
  const out: [number, number][] = []
  for (const p of a) {
    const bv = bm.get(p.date)
    if (bv !== undefined) out.push([p.value, bv])
  }
  return out
}
