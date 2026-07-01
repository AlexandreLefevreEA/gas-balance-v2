// Covariate driver logic: tell a covariate from a balance series, and pair a driver's
// actual code with its forecast codes (one per model). Pure — unit-tested in covariate.test.ts.
import type { Series } from '../api/types'

// ponytail: prefix proxy — `/series` has no covariate flag, and `category` collides
// (`demand`/`price` are shared by balance `CE.*` and covariate `KP.*`). Prefix disambiguates.
const COVARIATE_PREFIXES = ['KP.', 'ECB.', 'EQ.']

export function isCovariate(code: string): boolean {
  return COVARIATE_PREFIXES.some((p) => code.startsWith(p))
}

// A driver's actual category -> its forecast category (same `area`, one row per model).
// ponytail: clean `_forecast` families first; add price/carbon/coal (PFC / GASFC / CURVE /
// COALFC infixes, non-uniform categories) to the map when those drivers are wanted.
const FORECAST_CATEGORY: Record<string, string> = {
  temperature: 'temperature_forecast',
  demand: 'demand_forecast',
  generation: 'generation_forecast',
  availability: 'availability_forecast',
}

// A driver's actual category -> its long-term climatology category (forward-looking MEAN +
// REF weather years, e.g. KP.TEMPLT.<area>.<MODEL>). Same `area`, one row per model.
const LONGTERM_CATEGORY: Record<string, string> = {
  temperature: 'temperature_longterm',
  demand: 'demand_longterm',
  generation: 'generation_longterm',
}

// Non-driver covariate categories: reachable through their driver, hidden from the picker.
const NON_DRIVER = /(_forecast|_longterm)$/

export function isDriverCovariate(s: Series): boolean {
  return isCovariate(s.code) && !(s.category != null && NON_DRIVER.test(s.category))
}

// Related series (one per model) in `category`, same `area`; label = the model token.
function relatedCodes(
  selected: Series,
  catalog: readonly Series[],
  category: string | undefined,
): { code: string; label: string }[] {
  if (!category) return []
  return catalog
    .filter((s) => s.category === category && s.area === selected.area)
    .map((s) => ({ code: s.code, label: s.sub_group ?? s.code.split('.').pop() ?? s.code }))
}

/** Near-term forecast codes (one per model) for a covariate driver. */
export function forecastCodesFor(
  selected: Series,
  catalog: readonly Series[],
): { code: string; label: string }[] {
  return relatedCodes(selected, catalog, selected.category ? FORECAST_CATEGORY[selected.category] : undefined)
}

/** Long-term climatology codes (MEAN + REF weather years) for a covariate driver. */
export function longTermCodesFor(
  selected: Series,
  catalog: readonly Series[],
): { code: string; label: string }[] {
  return relatedCodes(selected, catalog, selected.category ? LONGTERM_CATEGORY[selected.category] : undefined)
}
