/** ECharts time-axis datum: [isoDate, value]. */
export type LinePoint = [string, number]

/**
 * Reshape tidy {date,value} points into ECharts line data, sorted ascending by date.
 * ISO YYYY-MM-DD sorts lexicographically == chronologically, so no Date parsing needed.
 */
export function toLineSeries(
  points: ReadonlyArray<{ date: string; value: number }>,
): LinePoint[] {
  return points
    .map((p): LinePoint => [p.date, p.value])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
}
