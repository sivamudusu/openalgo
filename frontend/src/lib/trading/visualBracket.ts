/**
 * Visual chart order types and pure helpers.
 *
 * A visual order is staged on an index chart but executed in an option
 * contract. The index level is the trigger; the option is the position. These
 * helpers keep the stage, the overlay and the server's validation on the same
 * side of entry, with the same tick rounding.
 */

export type VisualBracketAction = 'BUY' | 'SELL'
export type VisualBracketOptionType = 'CE' | 'PE'
export type VisualBracketBias = 'bullish' | 'bearish'
export type VisualBracketStrikeSelection = 'ATM' | 'ITM1' | 'ITM2' | 'OTM1' | 'OTM2' | 'CUSTOM'
export type VisualBracketStatus =
  | 'staged'
  | 'placing'
  | 'working'
  | 'active'
  | 'exiting'
  | 'closed'
  | 'cancelled'
  | 'error'

/** A bracket drawn on the chart, either staged locally or returned by the server. */
export interface VisualBracketView {
  id?: number
  clientToken?: string
  /** The order source that owns this bracket: VisualBracket or Manual. */
  strategy?: string
  status: VisualBracketStatus
  underlying: string
  underlyingExchange: string
  foExchange: string
  action: VisualBracketAction
  optionType: VisualBracketOptionType
  bias: VisualBracketBias
  entryReference: number
  targetPrice: number
  stopPrice: number
  clickPrice: number
  tickSize: number
  lotSize: number
  lots: number
  quantity: number
  contractSymbol?: string
  contractExchange?: string
  expiry?: string
  strike?: number
  entryOrderId?: string | null
  entryFillPrice?: number | null
  exitKind?: string | null
  exitReason?: string | null
  errorMessage?: string | null
  /** Index points from entry to target and stop. */
  targetOffset: number
  stopOffset: number
  riskReward: number
}

export interface VisualBracketLevels {
  entryReference: number
  targetPrice: number
  stopPrice: number
}

/** The index underlyings the visual tool may trade, with their option exchange. */
export const SUPPORTED_VISUAL_UNDERLYINGS: Record<
  string,
  { indexExchange: string; foExchange: string }
> = {
  NIFTY: { indexExchange: 'NSE_INDEX', foExchange: 'NFO' },
  BANKNIFTY: { indexExchange: 'NSE_INDEX', foExchange: 'NFO' },
  FINNIFTY: { indexExchange: 'NSE_INDEX', foExchange: 'NFO' },
  MIDCPNIFTY: { indexExchange: 'NSE_INDEX', foExchange: 'NFO' },
  NIFTYNXT50: { indexExchange: 'NSE_INDEX', foExchange: 'NFO' },
  SENSEX: { indexExchange: 'BSE_INDEX', foExchange: 'BFO' },
  BANKEX: { indexExchange: 'BSE_INDEX', foExchange: 'BFO' },
}

const MIN_TICK = 0.0001

export function isVisualUnderlying(
  symbol: string | null | undefined,
  exchange: string | null | undefined
): boolean {
  const cfg = SUPPORTED_VISUAL_UNDERLYINGS[String(symbol ?? '').toUpperCase()]
  if (!cfg) return false
  return cfg.indexExchange === String(exchange ?? '').toUpperCase()
}

export function visualUnderlyingConfig(symbol: string | null | undefined) {
  return SUPPORTED_VISUAL_UNDERLYINGS[String(symbol ?? '').toUpperCase()] ?? null
}

export function biasFor(
  action: VisualBracketAction,
  optionType: VisualBracketOptionType
): VisualBracketBias {
  if (action === 'BUY') return optionType === 'CE' ? 'bullish' : 'bearish'
  return optionType === 'PE' ? 'bullish' : 'bearish'
}

function gapFor(tickSize: number | null | undefined): number {
  return tickSize && tickSize > 0 ? tickSize : MIN_TICK
}

export function roundToTick(price: number, tickSize: number | null | undefined): number {
  if (!tickSize || tickSize <= 0) return price
  return Number((Math.round(price / tickSize) * tickSize).toFixed(4))
}

/** Clamp a dragged target or stop so it cannot cross entry. Mirrors services/risk/bracket.py. */
export function clampVisualLevel(
  bias: VisualBracketBias,
  entry: number,
  value: number,
  moving: 'target' | 'stop',
  tickSize: number | null | undefined
): number {
  const gap = gapFor(tickSize)
  let price = Number.isFinite(value) && value > 0 ? value : entry

  if (moving === 'target') {
    price = bias === 'bullish' ? Math.max(price, entry + gap) : Math.min(price, entry - gap)
  } else {
    price = bias === 'bullish' ? Math.min(price, entry - gap) : Math.max(price, entry + gap)
  }

  price = roundToTick(price, tickSize)
  return price > 0 ? price : gap
}

export function validateVisualLevels(
  bias: VisualBracketBias,
  levels: VisualBracketLevels,
  tickSize: number | null | undefined
): string | null {
  const gap = gapFor(tickSize)
  if (!(levels.entryReference > 0) || !(levels.targetPrice > 0) || !(levels.stopPrice > 0))
    return 'Entry, target and stop must all be positive prices.'
  if (Math.abs(levels.targetPrice - levels.stopPrice) < gap)
    return 'Target and stop cannot be the same price.'
  if (bias === 'bullish') {
    if (levels.targetPrice <= levels.entryReference + gap)
      return 'Target must be above entry for a bullish trade.'
    if (levels.stopPrice >= levels.entryReference - gap)
      return 'Stop must be below entry for a bullish trade.'
  } else {
    if (levels.targetPrice >= levels.entryReference - gap)
      return 'Target must be below entry for a bearish trade.'
    if (levels.stopPrice <= levels.entryReference + gap)
      return 'Stop must be above entry for a bearish trade.'
  }
  return null
}

export function riskReward(entry: number, target: number, stop: number): number {
  const risk = Math.abs(entry - stop)
  const reward = Math.abs(target - entry)
  return risk > 0 ? reward / risk : 0
}

/** Nearest listed strike to a price. */
export function nearestStrike(price: number, strikes: readonly number[]): number | null {
  if (!strikes.length || !Number.isFinite(price)) return null
  return strikes.reduce((best, strike) =>
    Math.abs(strike - price) < Math.abs(best - price) ? strike : best
  )
}

/**
 * Offset from ATM using the same CE/PE ITM/OTM rule as the backend.
 *
 * CE ITM moves down the ladder, CE OTM moves up; PE is the mirror.
 */
export function strikeForSelection(
  strikes: readonly number[],
  atm: number | null,
  selection: VisualBracketStrikeSelection,
  optionType: VisualBracketOptionType
): number | null {
  if (!strikes.length) return null
  if (selection === 'CUSTOM') return null
  const anchor = atm ?? strikes[Math.floor(strikes.length / 2)]
  if (anchor === null) return null
  const index = strikes.indexOf(anchor)
  if (index < 0) return null
  if (selection === 'ATM') return anchor

  const isItm = selection.startsWith('ITM')
  const steps = Number(selection.slice(-1)) || 1
  const down = (optionType === 'CE' && isItm) || (optionType === 'PE' && !isItm)
  const target = index + (down ? -steps : steps)
  return target >= 0 && target < strikes.length ? strikes[target] : null
}

/** A sensible first target and stop: two strike steps out and one against. */
export function defaultLevels(
  bias: VisualBracketBias,
  entry: number,
  strikeStep: number
): VisualBracketLevels {
  const step = strikeStep > 0 ? strikeStep : 50
  const targetDistance = step * 2
  const stopDistance = step
  return {
    entryReference: entry,
    targetPrice: bias === 'bullish' ? entry + targetDistance : entry - targetDistance,
    stopPrice: bias === 'bullish' ? entry - stopDistance : entry + stopDistance,
  }
}

/** The smallest listed strike gap, used when the signal has no usable levels. */
export function strikeStepFromLadder(
  ladder: ReadonlyArray<{ strike: number }> | null | undefined
): number {
  if (!ladder?.length) return 50
  const strikes = [...new Set(ladder.map((row) => row.strike))]
    .filter((strike) => Number.isFinite(strike) && strike > 0)
    .sort((a, b) => a - b)
  const gaps = strikes
    .slice(1)
    .map((strike, index) => strike - strikes[index])
    .filter((gap) => gap > 0)
  return gaps.length ? Math.min(...gaps) : 50
}

/**
 * First target and stop for a manual ATM order.
 *
 * The signal's OI levels are the trader's own levels, so they win when they sit
 * on the correct side of entry. Otherwise the bracket falls back to the same
 * two-to-one strike-step default the visual tool uses.
 */
export function manualBracketLevels(
  bias: VisualBracketBias,
  entry: number,
  support: number | null | undefined,
  resistance: number | null | undefined,
  ladder: ReadonlyArray<{ strike: number }> | null | undefined,
  tickSize = 0.05
): VisualBracketLevels {
  const fallback = defaultLevels(bias, entry, strikeStepFromLadder(ladder))
  const candidate: VisualBracketLevels = {
    entryReference: roundToTick(entry, tickSize),
    targetPrice: roundToTick(
      bias === 'bullish' ? (resistance ?? fallback.targetPrice) : (support ?? fallback.targetPrice),
      tickSize
    ),
    stopPrice: roundToTick(
      bias === 'bullish' ? (support ?? fallback.stopPrice) : (resistance ?? fallback.stopPrice),
      tickSize
    ),
  }
  if (!validateVisualLevels(bias, candidate, tickSize)) return candidate
  return {
    entryReference: roundToTick(entry, tickSize),
    targetPrice: roundToTick(fallback.targetPrice, tickSize),
    stopPrice: roundToTick(fallback.stopPrice, tickSize),
  }
}

/** Apply dragged levels to a bracket view and recompute the derived fields. */
export function withVisualLevels(
  view: VisualBracketView,
  levels: VisualBracketLevels
): VisualBracketView {
  return {
    ...view,
    entryReference: levels.entryReference,
    targetPrice: levels.targetPrice,
    stopPrice: levels.stopPrice,
    targetOffset: Math.abs(levels.targetPrice - levels.entryReference),
    stopOffset: Math.abs(levels.entryReference - levels.stopPrice),
    riskReward: riskReward(levels.entryReference, levels.targetPrice, levels.stopPrice),
  }
}

export function estimateCurrencyPnl(
  delta: number | null | undefined,
  indexMove: number,
  quantity: number,
  action: VisualBracketAction = 'BUY'
): number | null {
  if (delta == null || !Number.isFinite(delta) || !Number.isFinite(indexMove)) return null
  const positionDelta = (action === 'BUY' ? 1 : -1) * delta
  return positionDelta * indexMove * quantity
}

export function statusLabel(status: VisualBracketStatus): string {
  switch (status) {
    case 'staged':
      return 'Staged'
    case 'placing':
      return 'Placing'
    case 'working':
      return 'Placed'
    case 'active':
      return 'Active'
    case 'exiting':
      return 'Exiting'
    case 'closed':
      return 'Closed'
    case 'cancelled':
      return 'Cancelled'
    case 'error':
      return 'Needs attention'
  }
}
