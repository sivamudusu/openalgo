import type { OptionData } from '@/types/option-chain'

/**
 * The values the option chain can show beside a strike.
 *
 * Shared by the right-rail option chain panel and the on-chart overlay so the
 * two cannot disagree about what a metric is called, how many decimals it
 * carries or which legs are missing a value.
 *
 * `dp` is per metric because the magnitudes are nothing alike. Gamma for an
 * index option is around 0.0019, so at the two decimals that suit delta and
 * theta every strike on the board would read 0.00.
 *
 * `symbol` is the notation the instrument is actually discussed in. Sigma for
 * implied volatility, and vega keeps a Latin V because it is not a Greek
 * letter at all, whatever the family is called.
 */
export const OPTION_CHAIN_METRICS = [
  { id: 'ltp', label: 'LTP', symbol: 'LTP', group: 'Price', dp: 2 },
  { id: 'oi', label: 'OI', symbol: 'OI', group: 'Price', dp: 0 },
  { id: 'volume', label: 'Volume', symbol: 'Vol', group: 'Price', dp: 0 },
  { id: 'iv', label: 'IV', symbol: 'σ', group: 'Greeks', dp: 1 },
  { id: 'delta', label: 'Delta', symbol: 'Δ', group: 'Greeks', dp: 2 },
  { id: 'gamma', label: 'Gamma', symbol: 'Γ', group: 'Greeks', dp: 4 },
  { id: 'theta', label: 'Theta', symbol: 'Θ', group: 'Greeks', dp: 2 },
  { id: 'vega', label: 'Vega', symbol: 'V', group: 'Greeks', dp: 2 },
] as const

export type OptionChainMetric = (typeof OPTION_CHAIN_METRICS)[number]['id']

/** The order the pickers list them in, so the groups stay together. */
export const OPTION_CHAIN_METRIC_GROUPS = ['Price', 'Greeks'] as const

/** Lakhs and crores: OI in raw units does not fit a narrow column. */
export function compactOptionChainNumber(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value === 0) return '-'
  if (value >= 1e7) return `${(value / 1e7).toFixed(2)}Cr`
  if (value >= 1e5) return `${(value / 1e5).toFixed(2)}L`
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`
  return String(Math.round(value))
}

/**
 * One leg's value for the selected metric, or undefined when there is none.
 *
 * Everything unavailable is absent rather than zero. A leg with no quote, or
 * one on an expired chain, cannot be inverted, and printing 0.00 would read as
 * a real measurement of zero volatility or zero sensitivity. That distinction
 * is the whole reason to show Greeks at all.
 */
export function optionChainMetricValue(
  leg: OptionData | null,
  metric: OptionChainMetric
): number | undefined {
  if (!leg) return undefined
  if (metric === 'ltp') return leg.ltp > 0 ? leg.ltp : undefined
  if (metric === 'oi') return leg.oi > 0 ? leg.oi : undefined
  if (metric === 'volume') return leg.volume > 0 ? leg.volume : undefined

  const value =
    metric === 'iv'
      ? leg.implied_volatility
      : metric === 'delta'
        ? leg.delta
        : metric === 'gamma'
          ? leg.gamma
          : metric === 'theta'
            ? leg.theta
            : leg.vega

  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

/** A leg's value as the table or the overlay prints it. */
export function formatOptionChainMetric(
  value: number | undefined,
  metric: OptionChainMetric
): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  if (metric === 'oi' || metric === 'volume') return compactOptionChainNumber(value)
  const dp = OPTION_CHAIN_METRICS.find((m) => m.id === metric)?.dp ?? 2
  return metric === 'iv' ? `${value.toFixed(dp)}%` : value.toFixed(dp)
}
