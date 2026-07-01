import type { EChartsOption } from 'echarts'
import ReactECharts from 'echarts-for-react'
import type { LineSeries } from '../lib/chart'

export interface ChartSeries extends LineSeries {
  dashed?: boolean
  color?: string
  opacity?: number
  width?: number
  front?: boolean // draw on top (the actual / latest gas year)
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const fmtNum = (v: number) => v.toLocaleString('en-US')

// Hierarchical labels for the synthetic gas-year axis: months when zoomed out, day numbers when
// zoomed in — and never the (fake 1999/2000) year, so zooming no longer repeats "May May Jun…".
const SEASONAL_AXIS_LABEL = {
  formatter: {
    year: '{MMM}',
    month: '{MMM}',
    day: '{d}',
    hour: '{HH}:{mm}',
    minute: '{HH}:{mm}',
    second: '{HH}:{mm}:{ss}',
    none: '{MMM} {d}',
  },
}

// Axis-trigger tooltip rows keep the original data item in `value` ([isoDate, y]).
type AxisRow = { marker: string; seriesName: string; value: [string, number] }

// Axis tooltip: month/day (seasonal) or full-date header. Series that share a name — the
// weather-spread fan — collapse into a single min–max row instead of N identical lines.
function makeTooltip(seasonal: boolean) {
  return (params: unknown): string => {
    const rows = params as AxisRow[]
    if (rows.length === 0) return ''
    const raw = String(rows[0].value[0])
    const [yy, mm, dd] = raw.split('-')
    let head = raw
    if (mm && seasonal) head = `${MONTHS[Number(mm) - 1]} ${Number(dd)}`
    else if (mm) head = `${yy}-${mm}-${dd}`

    const byName = new Map<string, { marker: string; vals: number[] }>()
    for (const r of rows) {
      const g = byName.get(r.seriesName)
      if (g) g.vals.push(r.value[1])
      else byName.set(r.seriesName, { marker: r.marker, vals: [r.value[1]] })
    }
    const body = [...byName.entries()]
      .map(([name, g]) =>
        g.vals.length === 1
          ? `${g.marker}${name}: ${fmtNum(g.vals[0])}`
          : `${g.marker}${name}: ${fmtNum(Math.min(...g.vals))}–${fmtNum(Math.max(...g.vals))}`,
      )
      .join('<br/>')
    return `${head}<br/>${body}`
  }
}

// ponytail: imports full echarts via echarts-for-react. Switch to core + manual registration
// if bundle size becomes a hard constraint (web/CLAUDE.md: keep bundle lean).
export function TimeSeriesChart({
  series,
  seasonal = false,
  height = 480,
  legendData,
  legendSelected,
}: {
  series: ChartSeries[]
  seasonal?: boolean
  height?: number
  legendData?: string[]
  legendSelected?: Record<string, boolean> // names default-unchecked (absent = shown)
}) {
  const option: EChartsOption = {
    tooltip: { trigger: 'axis', formatter: makeTooltip(seasonal) },
    // Legend at the very bottom, below the x-axis and zoom slider, so nothing overlaps.
    legend: { type: 'scroll', bottom: 8, data: legendData, selected: legendSelected },
    grid: { left: 72, right: 24, top: 24, bottom: 96 },
    xAxis: {
      type: 'time',
      axisLabel: seasonal ? SEASONAL_AXIS_LABEL : undefined, // month-only on the gas-year axis
    },
    yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => fmtNum(v) } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 44, height: 18 }],
    series: series.map((s) => ({
      name: s.name,
      type: 'line',
      showSymbol: false,
      data: s.data,
      z: s.front ? 5 : 2,
      itemStyle: s.color ? { color: s.color } : undefined,
      lineStyle: {
        type: s.dashed ? 'dashed' : 'solid',
        color: s.color,
        opacity: s.opacity,
        width: s.width,
      },
    })),
  }
  return <ReactECharts option={option} style={{ height }} notMerge />
}
