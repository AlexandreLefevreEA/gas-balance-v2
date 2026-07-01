import { describe, expect, it } from 'vitest'
import { errorPoints, friendlyModel, groupByVintage, joinOnDate } from './model'

describe('model helpers', () => {
  it('friendlyModel strips the run-date stamp', () => {
    expect(friendlyModel('lightgbm-2026-06-29')).toBe('lightgbm')
    expect(friendlyModel('seasonal_naive-2026-06-29')).toBe('seasonal_naive')
    expect(friendlyModel('a1b2c3')).toBe('a1b2c3') // no stamp → unchanged
  })

  it('groupByVintage splits one model into one sorted line per made_on', () => {
    const pts = [
      { target_date: '2026-01-02', value: 2, model_run_id: 'm', made_on: 'r1' },
      { target_date: '2026-01-01', value: 1, model_run_id: 'm', made_on: 'r1' },
      { target_date: '2026-01-01', value: 9, model_run_id: 'm', made_on: 'r2' },
    ]
    const g = groupByVintage(pts)
    expect([...g.keys()].sort()).toEqual(['r1', 'r2'])
    expect(g.get('r1')).toEqual([
      { date: '2026-01-01', value: 1 },
      { date: '2026-01-02', value: 2 },
    ])
  })

  it('errorPoints = forecast − actual, dropping non-matching dates', () => {
    const fc = [
      { date: 'd1', value: 5 },
      { date: 'd3', value: 9 },
    ]
    const act = [
      { date: 'd1', value: 4 },
      { date: 'd2', value: 20 },
    ]
    expect(errorPoints(fc, act)).toEqual([{ date: 'd1', value: 1 }]) // d3 has no actual
  })

  it('joinOnDate inner-joins to [a, b] pairs', () => {
    const cov = [
      { date: 'd1', value: 10 },
      { date: 'd2', value: 20 },
    ]
    const fc = [
      { date: 'd1', value: 5 },
      { date: 'd3', value: 9 },
    ]
    expect(joinOnDate(cov, fc)).toEqual([[10, 5]]) // only d1 overlaps
  })
})
