import { Group, NumberInput, Select } from '@mantine/core'
import { useState } from 'react'
import { TimeSeriesChart, type ChartSeries } from '../components/TimeSeriesChart'
import {
  useCovariates,
  useForecastCovariates,
  useForecasts,
  useObservations,
  useSeries,
} from '../hooks/queries'
import { gasYear, type LineSeries, meanSeries, toLineSeries, toSeasonalSeries } from '../lib/chart'
import { forecastCodesFor, isCovariate, isDriverCovariate, longTermCodesFor } from '../lib/covariate'

const SPREAD = { color: '#6b8fb5', opacity: 0.35, width: 1 } // muted weather-spread / climatology fan
const MEAN_COLORS = ['#c0392b', '#8e44ad', '#16a085', '#d35400', '#2c3e50'] // one per forecast year/model
const CLIMO_MEAN = '#16a085' // long-term climatology "normal" (MEAN) line
const isRef = (scenario: string) => /^ref/i.test(scenario) // REF<weather-year> scenarios

// Legend order: model lines, spread fans, means, gas years (desc), actual.
function rankName(n: string): number {
  if (/^Weather Spread/.test(n) || /^Climatology$/.test(n)) return 1
  if (/^mean\b/.test(n) || /^Climatology mean$/.test(n)) return 2
  if (/^\d{4}\/\d{2}$/.test(n)) return 3
  if (n === 'actual') return 4
  return 0 // custom scenarios / covariate near-term model lines
}
const yearDigits = (n: string) => Number(n.replace(/\D/g, '').slice(-4))
function legendOrder(series: ChartSeries[]): string[] {
  return [...new Set(series.map((s) => s.name))].sort((a, b) => {
    const ra = rankName(a)
    const rb = rankName(b)
    if (ra !== rb) return ra - rb
    if (ra === 3) return b.localeCompare(a) // gas years descending
    if (ra === 1 || ra === 2) return yearDigits(a) - yearDigits(b) // forecast years ascending
    return 0 // keep scenario order
  })
}

export function SeriesExplorer() {
  const seriesQ = useSeries()
  const [code, setCode] = useState('')
  const [historyYears, setHistoryYears] = useState(5)
  const catalog = seriesQ.data ?? []
  const selected = catalog.find((s) => s.code === code)

  // Covariates read from different tables (covariate / forecast_covariate) and pair their actual
  // with per-model forecast codes; balance series keep the observations + weather-scenario path.
  const isCov = Boolean(code) && isCovariate(code)
  const codes = code ? [code] : []
  const balanceCodes = isCov ? [] : codes
  const forecastPairs = isCov && selected ? forecastCodesFor(selected, catalog) : []
  const longTermPairs = isCov && selected ? longTermCodesFor(selected, catalog) : []
  // Actual + long-term climatology both live in the `covariate` table — one fetch.
  const covCodes = isCov ? [code, ...longTermPairs.map((p) => p.code)] : []
  const fcCovCodes = forecastPairs.map((p) => p.code)

  const obsQ = useObservations(balanceCodes)
  const fcQ = useForecasts(balanceCodes)
  const covQ = useCovariates(covCodes)
  const fcCovQ = useForecastCovariates(fcCovCodes)

  const isMcm = /\bmcm\b/i.test(selected?.unit ?? '')
  const round = (v: number) => (isMcm ? Math.round(v) : v)

  const actual = (isCov ? covQ.data : obsQ.data)?.find((s) => s.code === code)
  const actualPoints = (actual?.points ?? []).map((p) => ({ date: p.date, value: round(p.value) }))

  // Historical actuals by gas year (latest = black + bold), kept to the most recent `historyYears`.
  // Shared by the balance and covariate seasonal builders.
  const historyYearLines = (cumulative: boolean): ChartSeries[] => {
    const years = toSeasonalSeries(actualPoints, cumulative, true)
    return years
      .map((s, i) => (i === years.length - 1 ? { ...s, color: '#000', width: 3, front: true } : s))
      .slice(-historyYears)
  }

  let absolute: ChartSeries[] = []
  let seasonal: ChartSeries[] = []
  let cumulative: ChartSeries[] = []
  const hiddenSpread: Record<string, boolean> = {}

  if (isCov) {
    // Covariate overlays: actual (black), near-term forecast per model (latest run, dashed), and the
    // long-term climatology — MEAN ("normal", solid) + REF weather-year fan (faded envelope).
    const near = (fcCovQ.data ?? []).map((s) => ({
      label: forecastPairs.find((p) => p.code === s.code)?.label ?? s.code,
      points: s.points.map((p) => ({ date: p.date, value: round(p.value) })),
    }))
    const longTerm = longTermPairs.map((p) => ({
      label: p.label,
      isMean: p.label === 'MEAN',
      points: (covQ.data?.find((s) => s.code === p.code)?.points ?? []).map((pt) => ({
        date: pt.date,
        value: round(pt.value),
      })),
    }))
    const ltMean = longTerm.find((m) => m.isMean)
    const ltRefs = longTerm.filter((m) => !m.isMean)

    absolute = []
    if (actualPoints.length) {
      absolute.push({ name: 'actual', data: toLineSeries(actualPoints), color: '#000', width: 2, front: true })
    }
    near.forEach((m, k) => {
      absolute.push({ name: m.label, data: toLineSeries(m.points), color: MEAN_COLORS[k % MEAN_COLORS.length], dashed: true, front: true })
    })
    for (const r of ltRefs) {
      absolute.push({ name: 'Climatology', data: toLineSeries(r.points), ...SPREAD })
    }
    if (ltMean) {
      absolute.push({ name: 'Climatology mean', data: toLineSeries(ltMean.points), color: CLIMO_MEAN, width: 2, dashed: true })
    }

    // The single fullest gas-year cycle of a forward series, projected onto the seasonal axis
    // (climatology is periodic, so one cycle avoids drawing ~2 near-duplicate years).
    const fullestCycle = (points: { date: string; value: number }[], cml: boolean) =>
      toSeasonalSeries(points, cml).reduce<LineSeries | undefined>(
        (best, l) => (l.data.length > (best?.data.length ?? 0) ? l : best),
        undefined,
      )

    const covSeasonal = (cml: boolean): ChartSeries[] => {
      const lines: ChartSeries[] = []
      // Near-term forecast: seed with the gas year's actuals so the cumulative line continues.
      near.forEach((m, k) => {
        for (const gy of [...new Set(m.points.map((p) => gasYear(p.date)))]) {
          const seed = actualPoints.filter((p) => gasYear(p.date) === gy)
          const fpts = m.points.filter((p) => gasYear(p.date) === gy)
          const [line] = toSeasonalSeries([...seed, ...fpts], cml)
          if (line) {
            lines.push({ name: m.label, data: line.data, color: MEAN_COLORS[k % MEAN_COLORS.length], width: 2, dashed: true, front: true })
          }
        }
      })
      // Long-term climatology: one full cycle each — REF fan + the MEAN line.
      for (const r of ltRefs) {
        const line = fullestCycle(r.points, cml)
        if (line) lines.push({ name: 'Climatology', data: line.data, ...SPREAD })
      }
      if (ltMean) {
        const line = fullestCycle(ltMean.points, cml)
        if (line) lines.push({ name: 'Climatology mean', data: line.data, color: CLIMO_MEAN, width: 2, dashed: true, front: true })
      }
      return lines
    }
    seasonal = [...historyYearLines(false), ...covSeasonal(false)]
    cumulative = [...historyYearLines(true), ...covSeasonal(true)]
  } else {
    // Balance: actual (black) + weather-spread fan (faded, one legend entry) + custom (dashed).
    const forecasts = (fcQ.data ?? []).map((fs) => ({
      scenario: fs.scenario,
      points: fs.points.map((p) => ({ date: p.target_date, value: round(p.value) })),
    }))
    const spread = forecasts.filter((f) => isRef(f.scenario))
    const custom = forecasts.filter((f) => !isRef(f.scenario))

    // Forecast gas years the spread covers, ascending. Hide the 2nd+ years' fan by default.
    const forecastGys = [
      ...new Set(spread.flatMap((f) => f.points.map((p) => gasYear(p.date)))),
    ].sort((a, b) => a - b)
    for (const gy of forecastGys.slice(1)) hiddenSpread[`Weather Spread ${gy + 1}`] = false

    absolute = []
    if (actualPoints.length) {
      absolute.push({ name: 'actual', data: toLineSeries(actualPoints), color: '#000', width: 2, front: true })
    }
    for (const f of spread) {
      absolute.push({ name: 'Weather Spread', data: toLineSeries(f.points), ...SPREAD })
    }
    for (const f of custom) {
      absolute.push({ name: f.scenario, data: toLineSeries(f.points), dashed: true })
    }

    // Seasonal: historical actuals by gas year + the weather-spread fan and its mean, split per
    // forecast year, each seeded with that gas year's actuals (so the cumulative fan/mean continue).
    const seasonalCharts = (cml: boolean): ChartSeries[] => {
      const fan: ChartSeries[] = []
      const means: ChartSeries[] = []

      forecastGys.forEach((gy, k) => {
        const seed = actualPoints.filter((p) => gasYear(p.date) === gy)
        const lines: LineSeries[] = []
        for (const f of spread) {
          const fpts = f.points.filter((p) => gasYear(p.date) === gy)
          if (fpts.length === 0) continue
          const [line] = toSeasonalSeries([...seed, ...fpts], cml)
          if (line) lines.push(line)
        }
        if (lines.length === 0) return
        const yr = gy + 1
        for (const line of lines) fan.push({ name: `Weather Spread ${yr}`, data: line.data, ...SPREAD })
        means.push({
          ...meanSeries(lines, `mean ${yr}`),
          color: MEAN_COLORS[k % MEAN_COLORS.length],
          width: 2.5,
          dashed: true,
          front: true,
        })
      })

      return [...fan, ...historyYearLines(cml), ...means]
    }

    seasonal = seasonalCharts(false)
    cumulative = seasonalCharts(true)
  }

  const loadingData =
    Boolean(code) &&
    (isCov ? covQ.isLoading || fcCovQ.isLoading : obsQ.isLoading || fcQ.isLoading)
  const dataError =
    Boolean(code) && (isCov ? covQ.error || fcCovQ.error : obsQ.error || fcQ.error)
  const hasData = absolute.length > 0

  return (
    <section>
      <h1>Series explorer</h1>

      {seriesQ.isLoading && <p>Loading series…</p>}
      {seriesQ.error && <p>Failed to load the series catalog.</p>}
      {seriesQ.data && (
        <Group align="flex-end" gap="sm">
          <Select
            label="Series"
            // Covariate forecast/climatology helper codes are reachable through their driver — hide them.
            data={catalog
              .filter((s) => !isCovariate(s.code) || isDriverCovariate(s))
              .map((s) => ({ value: s.code, label: `${s.code} — ${s.name}` }))}
            value={code}
            onChange={(v) => setCode(v ?? '')}
            searchable
            clearable
            limit={100}
            nothingFoundMessage="No match"
            placeholder="Search series…"
            maxDropdownHeight={320}
            w={420}
          />
          <NumberInput
            label="History years"
            value={historyYears}
            onChange={(v) => {
              if (typeof v === 'number') setHistoryYears(v)
            }}
            min={1}
            max={30}
            w={140}
          />
          {selected && <span>({selected.unit})</span>}
        </Group>
      )}

      {loadingData && <p>Loading data…</p>}
      {dataError && <p>Failed to load series data.</p>}
      {code && !loadingData && !dataError && !hasData && <p>No data for {code}.</p>}

      {hasData && (
        <>
          <h2>Absolute</h2>
          <TimeSeriesChart series={absolute} legendData={legendOrder(absolute)} />
          {seasonal.length > 0 && (
            <>
              <h2>Seasonal (gas year)</h2>
              <TimeSeriesChart
                series={seasonal}
                seasonal
                legendData={legendOrder(seasonal)}
                legendSelected={hiddenSpread}
              />
              <h2>Seasonal cumulative (gas year)</h2>
              <TimeSeriesChart
                series={cumulative}
                seasonal
                legendData={legendOrder(cumulative)}
                legendSelected={hiddenSpread}
              />
            </>
          )}
        </>
      )}
    </section>
  )
}
