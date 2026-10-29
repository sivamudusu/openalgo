import { scalpingApi } from '@/api/scalping'

/** Derivative segments the option chain service can resolve an underlying in. */
export const OPTION_SEGMENTS = ['NFO', 'BFO', 'CDS', 'MCX'] as const
export type OptionSegment = (typeof OPTION_SEGMENTS)[number]

export interface OptionChainIdentity {
  underlying: string
  exchange: OptionSegment
}

export interface OptionChainCandidates {
  segment: OptionSegment
  /**
   * The names to try against the segment's underlying list, most likely first.
   * A derivative chart's own symbol is the contract; its `name` is the
   * underlying. A cash or index chart is the other way round.
   */
  candidates: string[]
}

/** The chart facts this needs, kept structural so the module does not pull in the terminal. */
export interface ChartInstrument {
  symbol: string
  exchange: string
  name: string
  synthetic?: boolean
}

const SEGMENT_BY_EXCHANGE: Record<string, OptionSegment> = {
  NSE: 'NFO',
  NSE_INDEX: 'NFO',
  NFO: 'NFO',
  BSE: 'BFO',
  BSE_INDEX: 'BFO',
  BFO: 'BFO',
  CDS: 'CDS',
  MCX: 'MCX',
}

/**
 * Index charts carry the display name (`NIFTY 50`, `NIFTY BANK`) while the
 * option chain lists the trading symbol (`NIFTY`, `BANKNIFTY`). The master
 * contract is the source of truth; these aliases only bridge the display name
 * to it when the chart's own symbol does not already match.
 */
const INDEX_ALIASES: Record<string, string> = {
  NIFTY50: 'NIFTY',
  NIFTYBANK: 'BANKNIFTY',
  NIFTYFINSERVICE: 'FINNIFTY',
  NIFTYMIDSELECT: 'MIDCPNIFTY',
  NIFTYNEXT50: 'NIFTYNXT50',
  NIFTYNXT50: 'NIFTYNXT50',
  SENSEX: 'SENSEX',
  BANKEX: 'BANKEX',
}

function normalize(value: string): string {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '')
}

/**
 * The derivative segment and underlying names to try for a charted instrument.
 * Null means the chart's exchange has no option chain this platform serves.
 */
export function optionChainCandidates(
  instrument: ChartInstrument | null
): OptionChainCandidates | null {
  if (!instrument || instrument.synthetic) return null
  const exchange = instrument.exchange.trim().toUpperCase()
  const segment = SEGMENT_BY_EXCHANGE[exchange]
  if (!segment) return null

  const symbol = instrument.symbol.trim()
  if (!symbol) return null
  const name = instrument.name.trim()
  const derivativeChart = exchange === segment
  const candidates = derivativeChart ? [name, symbol] : [symbol, name]
  const unique = candidates.filter((value, index) => value && candidates.indexOf(value) === index)
  return unique.length > 0 ? { segment, candidates: unique } : null
}

/**
 * The list's own spelling of the underlying, or null when the charted
 * instrument has no chain. Exact match first, then a punctuation-insensitive
 * match, then the index display-name aliases.
 */
export function matchOptionUnderlying(
  underlyings: readonly string[],
  candidates: readonly string[]
): string | null {
  if (underlyings.length === 0) return null
  const byNormalized = new Map<string, string>()
  for (const underlying of underlyings) byNormalized.set(normalize(underlying), underlying)

  for (const candidate of candidates) {
    const trimmed = candidate.trim()
    if (!trimmed) continue
    if (underlyings.includes(trimmed)) return trimmed

    const normalized = normalize(trimmed)
    const direct = byNormalized.get(normalized)
    if (direct) return direct

    const alias = INDEX_ALIASES[normalized]
    if (!alias) continue
    const aliased = byNormalized.get(normalize(alias))
    if (aliased) return aliased
    if (underlyings.includes(alias)) return alias
  }
  return null
}

const UNDERLYINGS_TTL_MS = 5 * 60_000
const underlyingsCache = new Map<OptionSegment, { at: number; promise: Promise<string[]> }>()

/**
 * One request per segment per five minutes, shared by every pane. The promise
 * is cached rather than the resolved list so simultaneous panes wait on the
 * same call; a rejection is dropped so the next caller can retry.
 */
export function loadOptionUnderlyings(segment: OptionSegment): Promise<string[]> {
  const hit = underlyingsCache.get(segment)
  if (hit && Date.now() - hit.at < UNDERLYINGS_TTL_MS) return hit.promise

  const promise = scalpingApi
    .getAllUnderlyings(segment, 'options')
    .then((response) =>
      (response.data ?? []).filter(
        (name): name is string => typeof name === 'string' && name.trim().length > 0
      )
    )
  underlyingsCache.set(segment, { at: Date.now(), promise })
  promise.catch(() => {
    if (underlyingsCache.get(segment)?.promise === promise) underlyingsCache.delete(segment)
  })
  return promise
}

/** Test seam: clear the shared list so a test can observe the next request. */
export function resetOptionUnderlyingsCache(): void {
  underlyingsCache.clear()
}
