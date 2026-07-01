import { useState } from 'react'
import { TimeSeriesChart, type ChartSeries } from '../components/TimeSeriesChart'
import { useForecasts, useObservations, useSeries } from '../hooks/queries'
import { toLineSeries } from '../lib/chart'

export function SeriesExplorer() {
  const seriesQ = useSeries()
  const [code, setCode] = useState('')

  const codes = code ? [code] : []
  const obsQ = useObservations(codes)
  const fcQ = useForecasts(codes)

  const chartSeries: ChartSeries[] = []
  const actual = obsQ.data?.find((s) => s.code === code)
  if (actual) chartSeries.push({ name: 'actual', data: toLineSeries(actual.points) })
  for (const fs of fcQ.data ?? []) {
    chartSeries.push({
      name: fs.scenario,
      dashed: true,
      data: toLineSeries(fs.points.map((p) => ({ date: p.target_date, value: p.value }))),
    })
  }

  const loadingData = Boolean(code) && (obsQ.isLoading || fcQ.isLoading)
  const dataError = Boolean(code) && (obsQ.error || fcQ.error)

  return (
    <section>
      <h1>Series explorer</h1>

      {seriesQ.isLoading && <p>Loading series…</p>}
      {seriesQ.error && <p>Failed to load the series catalog.</p>}
      {seriesQ.data && (
        <select value={code} onChange={(e) => setCode(e.target.value)}>
          <option value="">Select a series…</option>
          {seriesQ.data.map((s) => (
            <option key={s.code} value={s.code}>
              {s.code} — {s.name}
            </option>
          ))}
        </select>
      )}

      {loadingData && <p>Loading data…</p>}
      {dataError && <p>Failed to load series data.</p>}
      {code && !loadingData && !dataError && chartSeries.length === 0 && <p>No data for {code}.</p>}
      {chartSeries.length > 0 && <TimeSeriesChart series={chartSeries} />}
    </section>
  )
}
