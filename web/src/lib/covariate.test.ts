import { describe, expect, it } from 'vitest'
import type { Series } from '../api/types'
import { forecastCodesFor, isCovariate, isDriverCovariate, longTermCodesFor } from './covariate'

const mk = (code: string, over: Partial<Series> = {}): Series => ({
  code,
  name: code,
  category: null,
  sub_group: null,
  area: null,
  unit: '',
  source: 'test',
  is_derived: false,
  is_active: true,
  ...over,
})

describe('isCovariate', () => {
  it('treats KP./ECB./EQ. codes as covariates and CE./EU. as balance', () => {
    expect(isCovariate('KP.TEMP.DE')).toBe(true)
    expect(isCovariate('ECB.FX.USD')).toBe(true)
    expect(isCovariate('EQ.COAL.API2')).toBe(true)
    expect(isCovariate('CE.74.1')).toBe(false)
    expect(isCovariate('EU.DEMAND')).toBe(false)
  })
})

describe('isDriverCovariate', () => {
  it('keeps actual drivers, drops _forecast/_longterm helpers', () => {
    expect(isDriverCovariate(mk('KP.TEMP.DE', { category: 'temperature' }))).toBe(true)
    expect(
      isDriverCovariate(mk('KP.TEMPFC.DE.EC_46', { category: 'temperature_forecast' })),
    ).toBe(false)
    expect(isDriverCovariate(mk('KP.TEMPLT.DE.MEAN', { category: 'temperature_longterm' }))).toBe(
      false,
    )
  })
})

describe('forecastCodesFor', () => {
  const catalog = [
    mk('KP.TEMP.DE', { category: 'temperature', area: 'DE' }),
    mk('KP.TEMPFC.DE.EC_46', { category: 'temperature_forecast', area: 'DE', sub_group: 'EC_46' }),
    mk('KP.TEMPFC.DE.EC_AIFS_ENS', {
      category: 'temperature_forecast',
      area: 'DE',
      sub_group: 'EC_AIFS_ENS',
    }),
    mk('KP.TEMPFC.FR.EC_46', { category: 'temperature_forecast', area: 'FR', sub_group: 'EC_46' }),
    mk('KP.TEMPLT.DE.MEAN', { category: 'temperature_longterm', area: 'DE', sub_group: 'MEAN' }),
    mk('KP.TEMPLT.DE.REF_2016', {
      category: 'temperature_longterm',
      area: 'DE',
      sub_group: 'REF_2016',
    }),
  ]

  it('pairs a driver with its per-model forecast codes for the same area', () => {
    const de = mk('KP.TEMP.DE', { category: 'temperature', area: 'DE' })
    expect(forecastCodesFor(de, catalog)).toEqual([
      { code: 'KP.TEMPFC.DE.EC_46', label: 'EC_46' },
      { code: 'KP.TEMPFC.DE.EC_AIFS_ENS', label: 'EC_AIFS_ENS' },
    ])
  })

  it('pairs a driver with its long-term MEAN + REF-year codes for the same area', () => {
    const de = mk('KP.TEMP.DE', { category: 'temperature', area: 'DE' })
    expect(longTermCodesFor(de, catalog)).toEqual([
      { code: 'KP.TEMPLT.DE.MEAN', label: 'MEAN' },
      { code: 'KP.TEMPLT.DE.REF_2016', label: 'REF_2016' },
    ])
  })

  it('returns [] for a category with no mapped forecast/long-term family', () => {
    const fx = mk('ECB.FX.USD', { category: 'fx', area: null })
    expect(forecastCodesFor(fx, catalog)).toEqual([])
    expect(longTermCodesFor(fx, catalog)).toEqual([])
  })
})
