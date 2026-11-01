import { webClient } from './client'

export type SahasraExactUnderlying = 'BANKNIFTY' | 'NIFTY'
export type SahasraExactBias = 'bullish' | 'bearish' | 'mixed' | 'unset'
export type SahasraExactConfidence = 'low' | 'medium' | 'high'
export type SahasraExactSignalState =
  | 'breakout'
  | 'breakdown'
  | 'rejection'
  | 'bounce'
  | 'inside_zone'
  | 'above_resistance'
  | 'below_support'
  | 'no_levels'
  | 'warming_up'

export interface SahasraExactLevel {
  strike: number
  label: string
  tier: string
  score: number
  oi: number
  call_oi: number
  put_oi: number
  rejections: number
  tags: string[]
  distance_pct: number
}

export interface SahasraExactAutoCue {
  key: string
  label: string
  kind: string
  symbol: string
  exchange: string
  value: number | null
  prev_close: number | null
  change_pct: number | null
  status: 'available' | 'unavailable'
  source: string
  error: string | null
}

export interface SahasraExactManualContext {
  fed: '' | 'dovish' | 'neutral' | 'hawkish'
  inflation: '' | 'cool' | 'in_line' | 'hot'
  geopolitics: '' | 'risk_on' | 'neutral' | 'risk_off'
  india_yield: string
}

export interface SahasraExactContext {
  trade_date: string
  auto_cues: SahasraExactAutoCue[]
  manual: SahasraExactManualContext
  final_bias: SahasraExactBias
  confidence: SahasraExactConfidence
  notes: string
  captured_at: string | null
  updated_at: string | null
  saved: boolean
}

export interface SahasraExactLevels {
  underlying: SahasraExactUnderlying
  expiry: string
  spot: number | null
  atm_strike: number | null
  atm_ce_symbol: string | null
  atm_pe_symbol: string | null
  atm_lotsize: number | null
  pcr: number | null
  live_pcr?: number | null
  regime: 'bearish' | 'neutral' | 'bullish' | 'unknown'
  supports: SahasraExactLevel[]
  resistances: SahasraExactLevel[]
  primary_support: number | null
  temporary_support: number | null
  major_support: number | null
  primary_resistance: number | null
  major_resistance: number | null
  no_trade_zone: { low: number; high: number } | null
  oi_change?: Record<string, { ce: number; pe: number }>
  source: 'eod_snapshot' | 'live_provisional'
  snapshot_date: string | null
  snapshot_age_days: number | null
  snapshot_status: string
  coverage: string
  stale: boolean
  warning: string | null
  levels_warning: string | null
  mode: string
}

export interface SahasraExactSignal extends SahasraExactLevels {
  state: SahasraExactSignalState
  side: 'CE' | 'PE' | null
  confirmed: boolean
  confirmation: {
    candle_ok: boolean
    candle_text: string
    body_ratio: number
    close_position: number
    upper_wick_ratio: number
    lower_wick_ratio: number
    volume_ok: boolean | null
    volume_text: string
    confirmed: boolean
  } | null
  trade: {
    tradeable: boolean
    reason: string | null
    side: 'CE' | 'PE'
    symbol: string | null
    lotsize: number | null
    stop_level: number | null
    target_1: number | null
    target_2: number | null
    rr: number | null
  } | null
  bar: {
    ts: number
    o: number
    h: number
    low: number
    c: number
    v: number
    prev_close?: number
  } | null
  entry_window_open: boolean
  flat_reminder: boolean
  message: string | null
  bar_count?: number
}

export interface SahasraExactSnapshot {
  id: number
  underlying: SahasraExactUnderlying
  trade_date: string
  expiry: string
  pcr: number | null
  strikes_captured: number
  source: string
  status: string
  warning: string | null
  captured_at: string | null
}

export interface SahasraExactSnapshotStatus {
  snapshots: SahasraExactSnapshot[]
  scheduler: {
    enabled: boolean
    next_run: string
    timezone: string
    now: string
  }
  today: string
  underlyings: SahasraExactUnderlying[]
}

export const sahasraExactApi = {
  getLevels: async (
    underlying: SahasraExactUnderlying
  ): Promise<{ status: string; data: SahasraExactLevels; message?: string }> => {
    const response = await webClient.get('/sahasra-exact/api/levels', { params: { underlying } })
    return response.data
  },

  getSignal: async (
    underlying: SahasraExactUnderlying
  ): Promise<{ status: string; data: SahasraExactSignal; message?: string }> => {
    const response = await webClient.get('/sahasra-exact/api/signal', { params: { underlying } })
    return response.data
  },

  getSnapshotStatus: async (): Promise<{ status: string; data: SahasraExactSnapshotStatus }> => {
    const response = await webClient.get('/sahasra-exact/api/snapshot/status')
    return response.data
  },

  captureSnapshot: async (
    underlying?: SahasraExactUnderlying
  ): Promise<{ status: string; message?: string; source?: string; results?: unknown[] }> => {
    const response = await webClient.post('/sahasra-exact/api/snapshot', { underlying })
    return response.data
  },

  getContext: async (
    tradeDate: string
  ): Promise<{ status: string; data: SahasraExactContext; message?: string }> => {
    const response = await webClient.get('/sahasra-exact/api/context', {
      params: { date: tradeDate },
    })
    return response.data
  },

  saveContext: async (payload: {
    trade_date: string
    manual: SahasraExactManualContext
    final_bias: SahasraExactBias
    confidence: SahasraExactConfidence
    notes: string
  }): Promise<{ status: string; data: SahasraExactContext; message?: string }> => {
    const response = await webClient.post('/sahasra-exact/api/context', payload)
    return response.data
  },
}
