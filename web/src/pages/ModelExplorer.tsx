import { Group, MultiSelect, NumberInput, SegmentedControl, Select, Table } from '@mantine/core'
import type { EChartsOption } from 'echarts'
import ReactECharts from 'echarts-for-react'
import { useMemo, useState } from 'react'
import { ScatterChart, type ScatterSeries } from '../components/ScatterChart'
import { type ChartSeries, TimeSeriesChart } from '../components/TimeSeriesChart'
import {
  useCovariates,
  useForecasts,
  useMetrics,
  useModelForecastLines,
  useObservations,
  useSeries,
  useVintages,
} from '../hooks/queries'
import { toLineSeries } from '../lib/chart'
import { isCovariate, isDriverCovariate } from '../lib/covariate'
import { errorPoints, friendlyModel, groupByVintage, joinOnDate, type Tidy } from '../lib/model'

const MEAN_COLORS = ['#c0392b', '#8e44ad', '#16a085', '#d35400', '#2c3e50'] // one per model
const SPREAD = { color: '#6b8fb5', opacity: 0.35, width: 1 } // faded vintage envelope
const BUCKETS = ['h1', 'h2-7', 'h8-30', 'h31-90', 'h91-365', 'h366+'] // horizon buckets (metrics API)
const MAX_VINTAGES = 20 // keep the spaghetti plot readable

type Metric = 'mae' | 'rmse' | 'bias' | 'skill'
const METRICS: { label: string; value: Metric }[] = [
  { label: 'MAE', value: 'mae' },
  { label: 'RMSE', value: 'rmse' },
  { label: 'Bias', value: 'bias' },
  { label: 'Skill', value: 'skill' },
]

const fmt = (v: number | undefined, metric: Metric): string =>
  v === undefined
    ? '—'
    : metric === 'skill'
      ? v.toFixed(3)
      : Math.abs(v) >= 100
        ? Math.round(v).toLocaleString('en-US')
        : v.toFixed(2)

export function ModelExplorer() {
  const seriesQ = useSeries()
  const catalog = seriesQ.data ?? []

  const [code, setCode] = useState('')
  const [scenario, setScenario] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [vintageModel, setVintageModel] = useState('')
  const [cov, setCov] = useState('')
  const [historyYears, setHistoryYears] = useState(3)
  const [metric, setMetric] = useState<Metric>('mae')

  const selected = catalog.find((s) => s.code === code)
  const covOptions = catalog.filter(isDriverCovariate)

  // Discovery = metrics ∪ latest forecasts, so a model/scenario with forecasts but no *realized*
  // error yet (all future-dated) still appears. Metrics alone misses forward-only backfills.
  const metricsQ = useMetrics(code)
  const catalogQ = useForecasts(code ? [code] : [])
  const groups = metricsQ.data ?? []
  const fcCatalog = catalogQ.data ?? []
  const allScenarios = [
    ...new Set([...groups.map((g) => g.scenario), ...fcCatalog.map((s) => s.scenario)]),
  ].sort()
  const allModels = [
    ...new Set([
      ...groups.map((g) => g.model_run_id),
      ...fcCatalog.flatMap((s) => s.points.map((p) => p.model_run_id)),
    ]),
  ]

  // Effective selection: user's choice if still valid, else a sensible default (no effects, no stale).
  const effScenario =
    scenario && allScenarios.includes(scenario)
      ? scenario
      : allScenarios.includes('MEAN')
        ? 'MEAN'
        : (allScenarios[0] ?? '')
  const effModels = models.length ? models.filter((m) => allModels.includes(m)) : allModels
  const effVintageModel =
    vintageModel && effModels.includes(vintageModel) ? vintageModel : (effModels[0] ?? '')
  const defaultCov = selected ? `KP.TEMP.${selected.area}` : ''
  const effCov =
    cov && covOptions.some((s) => s.code === cov)
      ? cov
      : covOptions.some((s) => s.code === defaultCov)
        ? defaultCov
        : ''

  const from = useMemo(() => {
    const d = new Date()
    d.setFullYear(d.getFullYear() - historyYears)
    return d.toISOString().slice(0, 10)
  }, [historyYears])

  const obsQ = useObservations(code ? [code] : [])
  const covQ = useCovariates(effCov ? [effCov] : [])
  const lineResults = useModelForecastLines(code, effScenario, effModels)
  const vintageQ = useVintages(code, effScenario, effVintageModel, from)

  const modelColor = new Map(effModels.map((m, i) => [m, MEAN_COLORS[i % MEAN_COLORS.length]]))
  const actualPoints: Tidy[] = obsQ.data?.find((s) => s.code === code)?.points ?? []
  const covPoints: Tidy[] = covQ.data?.find((s) => s.code === effCov)?.points ?? []

  // The latest-vintage forecast line for one model (one query per model, index-aligned to effModels).
  const forecastLine = (m: string): Tidy[] => {
    const fs = lineResults[effModels.indexOf(m)]?.data?.[0]
    return (fs?.points ?? []).map((p) => ({ date: p.target_date, value: p.value }))
  }

  const unit = selected?.unit ?? ''
  const covUnit = covOptions.find((s) => s.code === effCov)?.unit ?? ''

  // 1. Forecast: actual + one latest line per model.
  const lineChart: ChartSeries[] = []
  if (actualPoints.length) {
    lineChart.push({ name: 'actual', data: toLineSeries(actualPoints), color: '#000', width: 2, front: true })
  }
  for (const m of effModels) {
    const pts = forecastLine(m)
    if (pts.length) lineChart.push({ name: friendlyModel(m), data: toLineSeries(pts), color: modelColor.get(m) })
  }

  // 2. Vintages: every run of the chosen model, most-recent MAX_VINTAGES, latest run highlighted.
  const byRun = groupByVintage(vintageQ.data?.[0]?.points ?? [])
  const shownRuns = [...byRun.keys()].sort().slice(-MAX_VINTAGES)
  const vintageChart: ChartSeries[] = []
  if (actualPoints.length) {
    vintageChart.push({ name: 'actual', data: toLineSeries(actualPoints), color: '#000', width: 2, front: true })
  }
  shownRuns.forEach((r, k) => {
    const latest = k === shownRuns.length - 1
    vintageChart.push({
      name: latest ? `latest run (${r})` : 'vintages',
      data: toLineSeries(byRun.get(r) ?? []),
      ...(latest ? { color: MEAN_COLORS[0], width: 2, front: true } : SPREAD),
    })
  })

  // 3. Error: metrics for the chosen scenario × selected models; skill vs the seasonal_naive floor.
  const errGroups = groups.filter((g) => g.scenario === effScenario && effModels.includes(g.model_run_id))
  const baseline = groups.find((g) => g.scenario === effScenario && /seasonal_naive/i.test(g.model_run_id))
  const cell = (modelRunId: string, bucket: string): number | undefined => {
    const g = errGroups.find((x) => x.model_run_id === modelRunId)
    const mb = g?.buckets.find((x) => x.bucket === bucket)
    if (!mb) return undefined
    if (metric === 'skill') {
      const base = baseline?.buckets.find((x) => x.bucket === bucket)
      return base && base.mae ? 1 - mb.mae / base.mae : undefined
    }
    return mb[metric]
  }
  const barOption: EChartsOption = {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 8, data: errGroups.map((g) => friendlyModel(g.model_run_id)) },
    grid: { left: 64, right: 24, top: 24, bottom: 56 },
    xAxis: { type: 'category', data: BUCKETS },
    yAxis: { type: 'value', scale: true },
    series: errGroups.map((g) => ({
      name: friendlyModel(g.model_run_id),
      type: 'bar',
      itemStyle: { color: modelColor.get(g.model_run_id) },
      data: BUCKETS.map((b) => cell(g.model_run_id, b) ?? null),
    })),
  }

  // 4 & 5. Scatters vs the chosen covariate (inner-joined on date), one series per model.
  const fcScatter: ScatterSeries[] = effModels
    .map((m) => ({ name: friendlyModel(m), color: modelColor.get(m), points: joinOnDate(covPoints, forecastLine(m)) }))
    .filter((s) => s.points.length)
  const errScatter: ScatterSeries[] = effModels
    .map((m) => ({
      name: friendlyModel(m),
      color: modelColor.get(m),
      points: joinOnDate(covPoints, errorPoints(forecastLine(m), actualPoints)),
    }))
    .filter((s) => s.points.length)

  return (
    <section>
      <h1>Model explorer</h1>

      {seriesQ.isLoading && <p>Loading series…</p>}
      {seriesQ.error && <p>Failed to load the series catalog.</p>}
      {seriesQ.data && (
        <Group align="flex-end" gap="sm">
          <Select
            label="Series"
            data={catalog
              .filter((s) => !isCovariate(s.code))
              .map((s) => ({ value: s.code, label: `${s.code} — ${s.name}` }))}
            value={code}
            onChange={(v) => setCode(v ?? '')}
            searchable
            clearable
            limit={100}
            nothingFoundMessage="No match"
            placeholder="Search series…"
            maxDropdownHeight={320}
            w={360}
          />
          <Select
            label="Scenario"
            data={allScenarios}
            value={effScenario}
            onChange={(v) => setScenario(v ?? '')}
            disabled={!allScenarios.length}
            w={160}
          />
          <MultiSelect
            label="Models"
            data={allModels.map((m) => ({ value: m, label: friendlyModel(m) }))}
            value={effModels}
            onChange={setModels}
            disabled={!allModels.length}
            clearable={false}
            w={280}
          />
          <NumberInput
            label="History years"
            value={historyYears}
            onChange={(v) => {
              if (typeof v === 'number') setHistoryYears(v)
            }}
            min={1}
            max={30}
            w={130}
          />
        </Group>
      )}

      {code && (metricsQ.isLoading || catalogQ.isLoading) && !allModels.length && <p>Loading models…</p>}
      {code && !metricsQ.isLoading && !catalogQ.isLoading && !allModels.length && (
        <p>No forecasts found for {code}.</p>
      )}

      {effModels.length > 0 && (
        <>
          <h2>Forecast {unit && `(${unit})`}</h2>
          <TimeSeriesChart series={lineChart} legendData={lineChart.map((s) => s.name)} />

          <h2>Vintage runs</h2>
          <Select
            label="Model"
            data={effModels.map((m) => ({ value: m, label: friendlyModel(m) }))}
            value={effVintageModel}
            onChange={(v) => setVintageModel(v ?? '')}
            w={280}
            mb="xs"
          />
          {vintageChart.length > 1 ? (
            <TimeSeriesChart series={vintageChart} legendData={[...new Set(vintageChart.map((s) => s.name))]} />
          ) : (
            <p>No vintages in the last {historyYears} year(s) for this model.</p>
          )}

          <h2>Model error</h2>
          <SegmentedControl
            data={METRICS}
            value={metric}
            onChange={(v) => setMetric(v as Metric)}
            mb="xs"
          />
          {errGroups.length ? (
            <>
              <Table striped withTableBorder mb="md">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Model</Table.Th>
                    {BUCKETS.map((b) => (
                      <Table.Th key={b}>{b}</Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {errGroups.map((g) => (
                    <Table.Tr key={g.model_run_id}>
                      <Table.Td>{friendlyModel(g.model_run_id)}</Table.Td>
                      {BUCKETS.map((b) => (
                        <Table.Td key={b}>{fmt(cell(g.model_run_id, b), metric)}</Table.Td>
                      ))}
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              <ReactECharts option={barOption} style={{ height: 360 }} notMerge />
            </>
          ) : (
            <p>
              No realized errors for {effScenario} yet — a forecast is scored only once its target
              date has an actual observation. These forecasts are still future-dated.
            </p>
          )}

          <h2>Scatter vs covariate</h2>
          <Select
            label="Covariate"
            data={covOptions.map((s) => ({ value: s.code, label: `${s.code} — ${s.name}` }))}
            value={effCov}
            onChange={(v) => setCov(v ?? '')}
            searchable
            clearable
            w={340}
            mb="sm"
          />

          <h3>Forecast vs {effCov || 'covariate'}</h3>
          {effCov && fcScatter.length ? (
            <ScatterChart series={fcScatter} xName={`${effCov}${covUnit ? ` (${covUnit})` : ''}`} yName={`forecast${unit ? ` (${unit})` : ''}`} />
          ) : (
            <p>Pick a covariate that overlaps the forecast dates.</p>
          )}

          <h3>Error vs {effCov || 'covariate'}</h3>
          {effCov && errScatter.length ? (
            <ScatterChart series={errScatter} xName={`${effCov}${covUnit ? ` (${covUnit})` : ''}`} yName={`error${unit ? ` (${unit})` : ''}`} zeroLine />
          ) : (
            <p>Needs forecast, actual and covariate to overlap on date.</p>
          )}
        </>
      )}
    </section>
  )
}
