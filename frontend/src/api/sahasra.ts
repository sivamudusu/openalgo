import { webClient } from './client'

export type SahasraUnderlying = 'BANKNIFTY' | 'NIFTY'

export interface SahasraLevels {
  underlying: SahasraUnderlying
  expiry: string
  atm_strike: number
  spot?: number | null
  support: number | null
  resistance: number | null
  /** The nearest wall on each side, and the one beyond it. */
  nearest_support?: number | null
  nearest_resistance?: number | null
  next_support?: number | null
  next_resistance?: number | null
  /** The strike whose weaker side carries the most interest. */
  magnet?: number | null
  magnet_balance?: number | null
  /** The stretch between the two walls, where the model does not trade. */
  no_trade_zone?: { low: number; high: number } | null
  pcr: number | null
  regime: 'bearish' | 'neutral' | 'bullish'
  atm_ce_symbol: string | null
  atm_pe_symbol: string | null
  atm_lotsize: number | null
  ladder: Array<{ strike: number; call_oi: number; put_oi: number }>
  mode: string
  warning?: string | null
  /**
   * What the market can do today, and how a move would behave: the expected
   * move from the at-the-money volatility, and the dealer gamma position with
   * the strike where it changes sign.
   */
  market_context?: MarketContext
  /** Whether the closed candle confirms the setup, and why. */
  confirmation?: SignalConfirmation
  state?:
    | 'breakout_up'
    | 'breakdown'
    | 'rejection'
    | 'support_bounce'
    | 'inside_zone'
    | 'above_resistance'
    | 'below_support'
    | 'no_levels'
    | 'warming_up'
  side?: 'CE' | 'PE' | null
  regime_blocked?: boolean
  bar?: { ts: number; o: number; h: number; l: number; c: number; prev_close: number } | null
}

/**
 * The expected move, the dealer gamma position and the flip strike.
 */
export interface MarketContext {
  expected_move: {
    atm_iv: number
    intraday: ExpectedMoveBand
    to_expiry: ExpectedMoveBand
  } | null
  atm_iv: number | null
  gamma_flip_strike: number | null
  dealer_regime: 'dampening' | 'amplifying'
  total_net_gex: number
  support_within_reach?: boolean | null
  resistance_within_reach?: boolean | null
}

/** A standard-deviation sized range around the underlying. */
export interface ExpectedMoveBand {
  sigma_move: number
  one_sigma_low: number
  one_sigma_high: number
  two_sigma_low: number
  two_sigma_high: number
}

/**
 * Whether the closed candle confirms the setup.
 *
 * The candle's own geometry carries the confirmation: a rejection is an upper
 * wick that took back the high, a breakout is a body that closed where it
 * broke.
 */
export interface SignalConfirmation {
  candle_ok: boolean
  candle_text: string
  body_ratio: number
  close_position: number
  upper_wick_ratio: number
  lower_wick_ratio: number
  /** Null when volume is unavailable, which does not fail the confirmation. */
  volume_ok: boolean | null
  volume_text: string
  confirmed: boolean
}

/**
 * The live record of the model's signals.
 *
 * A rate is only claimed once there are enough closed signals for it to mean
 * something. Below that the numbers are a count, and the panel says so.
 */
export interface SignalEvidence {
  underlying: SahasraUnderlying
  signals: number
  closed: number
  targets: number
  stops: number
  wins: number
  hit_rate: number | null
  target_rate: number | null
  evidence_target: number
  enough_evidence: boolean
}

// Sahasra OI-model monitoring. Served by blueprints/sahasra.py under the root
// path (not /api/v1), so webClient (session + CSRF aware) is used. The
// underlying defaults to BANKNIFTY so existing callers keep their behaviour.
export const sahasraApi = {
  getLevels: async (
    underlying: SahasraUnderlying = 'BANKNIFTY'
  ): Promise<{ status: string; data: SahasraLevels; message?: string }> => {
    const response = await webClient.get('/sahasra/api/levels', { params: { underlying } })
    return response.data
  },

  getSignal: async (
    underlying: SahasraUnderlying = 'BANKNIFTY'
  ): Promise<{ status: string; data: SahasraLevels; message?: string }> => {
    const response = await webClient.get('/sahasra/api/signal', { params: { underlying } })
    return response.data
  },

  /**
   * Record a signal the model produced, with the map that produced it.
   *
   * Best effort: a signal that cannot be written must not stop the trade.
   */
  recordSignal: async (payload: {
    underlying: SahasraUnderlying
    state: string
    side: 'CE' | 'PE'
    confirmed?: boolean
    level_map?: SahasraLevels | null
    market_context?: MarketContext | null
    entry_spot?: number | null
    entry_premium?: number | null
    quantity?: number | null
  }): Promise<{ status: string; data: { id: number | null; kept: boolean } }> => {
    const response = await webClient.post('/sahasra/api/signal-log', payload)
    return response.data
  },

  /** Record what a signal turned into. */
  closeSignal: async (payload: {
    id: number
    outcome: string
    pnl_pct?: number | null
  }): Promise<{ status: string; data: { kept: boolean } }> => {
    const response = await webClient.post('/sahasra/api/signal-log/close', payload)
    return response.data
  },

  /** The live record: how many signals, and how many reached their target. */
  getEvidence: async (
    underlying: SahasraUnderlying = 'BANKNIFTY'
  ): Promise<{ status: string; data: SignalEvidence }> => {
    const response = await webClient.get('/sahasra/api/evidence', { params: { underlying } })
    return response.data
  },
}
