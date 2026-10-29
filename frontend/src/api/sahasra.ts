import { webClient } from './client'

export type SahasraUnderlying = 'BANKNIFTY' | 'NIFTY'

export interface SahasraLevels {
  underlying: SahasraUnderlying
  expiry: string
  atm_strike: number
  spot?: number | null
  support: number | null
  resistance: number | null
  pcr: number | null
  regime: 'bearish' | 'neutral' | 'bullish'
  atm_ce_symbol: string | null
  atm_pe_symbol: string | null
  atm_lotsize: number | null
  ladder: Array<{ strike: number; call_oi: number; put_oi: number }>
  mode: string
  state?:
    | 'breakout_up'
    | 'breakdown'
    | 'support_bounce'
    | 'inside_zone'
    | 'above_resistance'
    | 'below_support'
    | 'no_levels'
    | 'warming_up'
  side?: 'CE' | 'PE' | null
  regime_blocked?: boolean
  bar?: { ts: number; o: number; h: number; low: number; c: number; prev_close: number } | null
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
}
