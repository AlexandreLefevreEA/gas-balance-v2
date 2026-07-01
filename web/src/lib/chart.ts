/** ECharts time-axis datum: [isoDate, value]. */
export type LinePoint = [string, number]

/** A named line for the chart (one actual / forecast / gas-year series). */
export interface LineSeries {
  name: string
  data: LinePoint[]
}

type Tidy = { date: string; value: number }

const asc = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/**
 * Reshape tidy {date,value} points into ECharts line data, sorted ascending by date.
 * ISO YYYY-MM-DD sorts lexicographically == chronologically, so no Date parsing needed.
 */
export function toLineSeries(points: ReadonlyArray<Tidy>): LinePoint[] {
  return points.map((p): LinePoint => [p.date, p.value]).sort((a, b) => asc(a[0], b[0]))
}

/** EU gas year: starts Oct 1, named by its start year. 2024-01 → 2023 (the 2023/24 year). */
export function gasYear(iso: string): number {
  const y = Number(iso.slice(0, 4))
  const m = Number(iso.slice(5, 7))
  return m >= 10 ? y : y - 1
}

/** Human label for a gas year, e.g. 2023 → "2023/24". */
export function gasYearLabel(gy: number): string {
  return `${gy}/${String((gy + 1) % 100).padStart(2, '0')}`
}

/**
 * Project an ISO date onto one reference gas-year axis (Oct→Sep) so every gas year overlays
 * on the same seasonal x. Oct–Dec → 1999, Jan–Sep → 2000 (a leap year, so Feb 29 stays a
 * valid date); the two are consecutive so the year wraps correctly. Only months/days are shown.
 */
function seasonalDate(iso: string): string {
  const m = Number(iso.slice(5, 7))
  return `${m >= 10 ? 1999 : 2000}-${iso.slice(5)}`
}

/**
 * Split tidy points into one line per gas year, overlaid on the seasonal axis (Oct→Sep).
 * cumulative=true → running sum from Oct 1 within each gas year (e.g. cumulative flow).
 * dropIncomplete=true → skip a gas year that starts too late to be comparable: the cumulative
 * view needs the Oct 1 start (else the running sum is understated), while the absolute view only
 * needs the winter, so it tolerates data starting up to Jan 1.
 */
export function toSeasonalSeries(
  points: ReadonlyArray<Tidy>,
  cumulative = false,
  dropIncomplete = false,
): LineSeries[] {
  const byGy = new Map<number, Tidy[]>()
  for (const p of points) {
    const gy = gasYear(p.date)
    const arr = byGy.get(gy)
    if (arr) arr.push(p)
    else byGy.set(gy, [p])
  }
  return [...byGy.entries()]
    .sort((a, b) => a[0] - b[0])
    .flatMap(([gy, pts]) => {
      const sorted = [...pts].sort((a, b) => asc(a.date, b.date))
      // ponytail: literal Oct-1 check — a complete daily-flow year always has Oct 1; refine to a
      // few-day tolerance if a series legitimately skips Oct 1 (e.g. a weekend-only trading series).
      const cutoff = cumulative ? `${gy}-10-01` : `${gy + 1}-01-01`
      if (dropIncomplete && sorted[0].date > cutoff) return []
      let run = 0
      const data = sorted.map((p): LinePoint => {
        run += p.value
        return [seasonalDate(p.date), cumulative ? run : p.value]
      })
      return [{ name: gasYearLabel(gy), data }]
    })
}

/** Point-wise mean across lines that share an x axis (the seasonal gas-year lines). */
export function meanSeries(lines: ReadonlyArray<LineSeries>, name = 'mean'): LineSeries {
  const byX = new Map<string, number[]>()
  for (const line of lines) {
    for (const [x, y] of line.data) {
      const arr = byX.get(x)
      if (arr) arr.push(y)
      else byX.set(x, [y])
    }
  }
  const data: LinePoint[] = [...byX.entries()]
    .map(([x, ys]): LinePoint => [x, ys.reduce((a, b) => a + b, 0) / ys.length])
    .sort((a, b) => asc(a[0], b[0]))
  return { name, data }
}
