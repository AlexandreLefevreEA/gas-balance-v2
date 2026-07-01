import type { EChartsOption } from 'echarts'
import ReactECharts from 'echarts-for-react'
import type { LinePoint } from '../lib/chart'

export interface ChartSeries {
  name: string
  data: LinePoint[]
  dashed?: boolean
}

// ponytail: imports full echarts via echarts-for-react. Switch to core + manual registration
// if bundle size becomes a hard constraint (web/CLAUDE.md: keep bundle lean).
export function TimeSeriesChart({ series }: { series: ChartSeries[] }) {
  const option: EChartsOption = {
    tooltip: { trigger: 'axis' },
    legend: {},
    grid: { left: 56, right: 24, top: 40, bottom: 40 },
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true },
    series: series.map((s) => ({
      name: s.name,
      type: 'line',
      showSymbol: false,
      data: s.data,
      lineStyle: s.dashed ? { type: 'dashed' } : undefined,
    })),
  }
  return <ReactECharts option={option} style={{ height: 480 }} notMerge />
}
