import { describe, expect, it } from 'vitest'
import { gasYear, gasYearLabel, meanSeries, toLineSeries, toSeasonalSeries } from './chart'

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

describe('gasYear', () => {
  it('assigns Oct–Dec to the start year and Jan–Sep to the prior year', () => {
    expect(gasYear('2023-10-01')).toBe(2023)
    expect(gasYear('2023-11-15')).toBe(2023)
    expect(gasYear('2024-01-01')).toBe(2023)
    expect(gasYear('2024-09-30')).toBe(2023)
    expect(gasYear('2024-10-01')).toBe(2024)
  })
})

describe('gasYearLabel', () => {
  it('formats as start/next two-digit year', () => {
    expect(gasYearLabel(2023)).toBe('2023/24')
    expect(gasYearLabel(2009)).toBe('2009/10')
  })
})

describe('toSeasonalSeries', () => {
  const pts = [
    { date: '2023-10-01', value: 1 }, // GY2023
    { date: '2024-01-01', value: 2 }, // GY2023
    { date: '2024-10-01', value: 5 }, // GY2024
  ]

  it('groups by gas year and overlays on the Oct→Sep reference axis', () => {
    const out = toSeasonalSeries(pts)
    expect(out.map((s) => s.name)).toEqual(['2023/24', '2024/25'])
    expect(out[0].data).toEqual([
      ['1999-10-01', 1],
      ['2000-01-01', 2],
    ])
    expect(out[1].data).toEqual([['1999-10-01', 5]])
  })

  it('cumulates within each gas year when cumulative=true', () => {
    const out = toSeasonalSeries(pts, true)
    expect(out[0].data).toEqual([
      ['1999-10-01', 1],
      ['2000-01-01', 3],
    ])
    expect(out[1].data).toEqual([['1999-10-01', 5]])
  })

  it('drops a leading gas year whose data starts after Jan 1 (absolute, dropIncomplete=true)', () => {
    const out = toSeasonalSeries(
      [
        { date: '2016-03-01', value: 1 }, // GY2015: starts Mar → missing Oct–Dec, incomplete
        { date: '2016-11-01', value: 2 }, // GY2016
        { date: '2017-01-01', value: 3 }, // GY2016
      ],
      false,
      true,
    )
    expect(out.map((s) => s.name)).toEqual(['2016/17'])
  })

  it('drops a gas year missing Oct 1 on the cumulative view (dropIncomplete=true)', () => {
    const out = toSeasonalSeries(
      [
        { date: '2015-11-01', value: 1 }, // GY2015: no Oct 1 → cumulative understated → dropped
        { date: '2016-10-01', value: 2 }, // GY2016: has Oct 1
        { date: '2017-01-01', value: 3 }, // GY2016
      ],
      true,
      true,
    )
    expect(out.map((s) => s.name)).toEqual(['2016/17'])
  })
})

describe('meanSeries', () => {
  it('averages point-wise across lines aligned on the same x', () => {
    const out = meanSeries([
      { name: 'a', data: [['2000-01-01', 2], ['2000-02-01', 4]] },
      { name: 'b', data: [['2000-01-01', 4], ['2000-02-01', 8]] },
    ])
    expect(out.data).toEqual([
      ['2000-01-01', 3],
      ['2000-02-01', 6],
    ])
  })
})
