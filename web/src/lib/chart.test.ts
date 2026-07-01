import { describe, expect, it } from 'vitest'
import { toLineSeries } from './chart'

describe('toLineSeries', () => {
  it('maps to [date, value] pairs sorted ascending by date', () => {
    const out = toLineSeries([
      { date: '2024-03-01', value: 2 },
      { date: '2024-01-01', value: 1 },
      { date: '2024-02-01', value: 3 },
    ])
    expect(out).toEqual([
      ['2024-01-01', 1],
      ['2024-02-01', 3],
      ['2024-03-01', 2],
    ])
  })

  it('returns [] for empty input', () => {
    expect(toLineSeries([])).toEqual([])
  })
})
