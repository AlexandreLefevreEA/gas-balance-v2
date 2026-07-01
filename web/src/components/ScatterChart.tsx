import type { EChartsOption } from 'echarts'
import ReactECharts from 'echarts-for-react'

export interface ScatterSeries {
  name: string
  color?: string
  points: [number, number][] // [x, y]
}

const fmtNum = (v: number) =>
  Math.abs(v) >= 100 ? Math.round(v).toLocaleString('en-US') : Number(v.toFixed(2)).toString()

// Value-vs-value scatter (forecast/error vs a covariate). Sibling of TimeSeriesChart, which
// assumes a time x-axis; here both axes are numeric, so it's a separate component, not a prop.
export function ScatterChart({
  series,
  xName,
  yName,
  height = 420,
  zeroLine = false,
}: {
  series: ScatterSeries[]
  xName: string
  yName: string
  height?: number
  zeroLine?: boolean // draw y=0 (error scatters)
}) {
  const option: EChartsOption = {
    tooltip: {
      trigger: 'item',
      formatter: (p: unknown) => {
        const it = p as { seriesName: string; value: [number, number] }
        return `${it.seriesName}<br/>${xName}: ${fmtNum(it.value[0])}<br/>${yName}: ${fmtNum(it.value[1])}`
      },
    },
    legend: { type: 'scroll', bottom: 8, data: series.map((s) => s.name) },
    grid: { left: 72, right: 24, top: 24, bottom: 64 },
    xAxis: {
      type: 'value',
      scale: true,
      name: xName,
      nameLocation: 'middle',
      nameGap: 30,
      axisLabel: { formatter: (v: number) => fmtNum(v) },
    },
    yAxis: { type: 'value', scale: true, name: yName, axisLabel: { formatter: (v: number) => fmtNum(v) } },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0 },
      { type: 'inside', yAxisIndex: 0 },
    ],
    series: series.map((s, i) => ({
      name: s.name,
      type: 'scatter',
      symbolSize: 6,
      itemStyle: { color: s.color, opacity: 0.6 },
      data: s.points,
      // one markLine only (on the first series) so y=0 isn't drawn N times
      markLine:
        zeroLine && i === 0
          ? { silent: true, symbol: 'none', lineStyle: { color: '#888', type: 'dashed' }, data: [{ yAxis: 0 }] }
          : undefined,
    })),
  }
  return <ReactECharts option={option} style={{ height }} notMerge />
}
