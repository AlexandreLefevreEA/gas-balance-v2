import { Group, NumberInput, Select } from '@mantine/core'
import { useState } from 'react'
import { TimeSeriesChart, type ChartSeries } from '../components/TimeSeriesChart'
import { useForecasts, useObservations, useSeries } from '../hooks/queries'
import { gasYear, type LineSeries, meanSeries, toLineSeries, toSeasonalSeries } from '../lib/chart'

const SPREAD = { color: '#6b8fb5', opacity: 0.35, width: 1 } // muted weather-spread fan
const MEAN_COLORS = ['#c0392b', '#8e44ad', '#16a085', '#d35400', '#2c3e50'] // one per forecast year
const isRef = (scenario: string) => /^ref/i.test(scenario) // REF<weather-year> scenarios

// Legend order: custom scenarios, weather spread, means, gas years (descending), actual.
function rankName(n: string): number {
  if (/^Weather Spread/.test(n)) return 1
  if (/^mean\b/.test(n)) return 2
  if (/^\d{4}\/\d{2}$/.test(n)) return 3
  if (n === 'actual') return 4
  return 0 // custom scenarios
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
  const selected = seriesQ.data?.find((s) => s.code === code)

  const codes = code ? [code] : []
  const obsQ = useObservations(codes)
  const fcQ = useForecasts(codes)

  const isMcm = /\bmcm\b/i.test(selected?.unit ?? '')
  const round = (v: number) => (isMcm ? Math.round(v) : v)

  const actual = obsQ.data?.find((s) => s.code === code)
  const actualPoints = (actual?.points ?? []).map((p) => ({ date: p.date, value: round(p.value) }))
  const forecasts = (fcQ.data ?? []).map((fs) => ({
    scenario: fs.scenario,
    points: fs.points.map((p) => ({ date: p.target_date, value: round(p.value) })),
  }))
  const spread = forecasts.filter((f) => isRef(f.scenario))
  const custom = forecasts.filter((f) => !isRef(f.scenario))

  // Forecast gas years the spread covers, ascending. Hide the 2nd+ years' fan by default
  // (only the nearest forecast year's spread is shown until the user re-checks it).
  const forecastGys = [
    ...new Set(spread.flatMap((f) => f.points.map((p) => gasYear(p.date)))),
  ].sort((a, b) => a - b)
  const hiddenSpread: Record<string, boolean> = {}
  for (const gy of forecastGys.slice(1)) hiddenSpread[`Weather Spread ${gy + 1}`] = false

  // Absolute: actual (black) + weather-spread fan (faded, one legend entry) + custom (dashed).
  const absolute: ChartSeries[] = []
  if (actualPoints.length) {
    absolute.push({ name: 'actual', data: toLineSeries(actualPoints), color: '#000', width: 2, front: true })
  }
  for (const f of spread) {
    absolute.push({ name: 'Weather Spread', data: toLineSeries(f.points), ...SPREAD })
  }
  for (const f of custom) {
    absolute.push({ name: f.scenario, data: toLineSeries(f.points), dashed: true })
  }

  // Seasonal: historical actuals by gas year (latest = black + bold) + the weather-spread fan and
  // its mean, split per forecast year ("Weather Spread 2026" / "mean 2026"). Each forecast year is
  // seeded with that gas year's actuals, so on the cumulative chart the fan and mean continue from
  // the actual cumulative (actual Oct→now, then forecast) instead of restarting at 0.
  const seasonalCharts = (cumulative: boolean): ChartSeries[] => {
    const fan: ChartSeries[] = []
    const means: ChartSeries[] = []

    forecastGys.forEach((gy, k) => {
      const seed = actualPoints.filter((p) => gasYear(p.date) === gy)
      const lines: LineSeries[] = []
      for (const f of spread) {
        const fpts = f.points.filter((p) => gasYear(p.date) === gy)
        if (fpts.length === 0) continue
        const [line] = toSeasonalSeries([...seed, ...fpts], cumulative)
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

    // Keep only the most recent `historyYears` gas years (the latest, black, is always last).
    const years = toSeasonalSeries(actualPoints, cumulative, true)
    const yearLines: ChartSeries[] = years
      .map((s, i) => (i === years.length - 1 ? { ...s, color: '#000', width: 3, front: true } : s))
      .slice(-historyYears)
    return [...fan, ...yearLines, ...means]
  }

  const seasonal = seasonalCharts(false)
  const cumulative = seasonalCharts(true)

  const loadingData = Boolean(code) && (obsQ.isLoading || fcQ.isLoading)
  const dataError = Boolean(code) && (obsQ.error || fcQ.error)
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
            data={seriesQ.data.map((s) => ({ value: s.code, label: `${s.code} — ${s.name}` }))}
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
