import { webClient } from './client'

export interface LowIVConfig {
  underlying: string
  index_exchange: string
  fo_exchange: string
  strategy_tag: string
  iv_percentile_max: number
  debounce_ticks: number
  reentry_cooldown_min: number
  max_entry_hour: number
  exit_target_pct: number
  exit_stop_pct: number
  exit_max_hold_min: number
  mode: string
}

export interface LowIVSettings {
  iv_percentile_max: number
  debounce_ticks: number
  reentry_cooldown_min: number
  max_entry_hour: number
  profit_lock_enabled: boolean
  profit_lock_threshold: number
  exit_target_pct: number
  exit_stop_pct: number
  exit_max_hold_min: number
  premium_floor: number
  max_spread_points: number
  max_entries_per_day: number
  bb_enabled: boolean
  bb_period: number
  bb_std: number
  bb_bottom_pctb: number
  bb_touch_tol_pct: number
}

export interface LowIVChainLeg {
  symbol: string | null
  label: string
  ltp?: number
  lotsize?: number | null
  bid?: number
  ask?: number
  implied_volatility?: number
}

export interface LowIVLegSample {
  strike: number
  option_type: 'CE' | 'PE'
  iv: number
  bid?: number
  ask?: number
}

export interface LowIVChainRow {
  strike: number
  ce: LowIVChainLeg
  pe: LowIVChainLeg
}

export interface LowIVCandle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// Low-IV BANKNIFTY terminal API. Served by blueprints/low_iv.py under the root
// path (not /api/v1), so webClient (session + CSRF aware) is used.
export const lowIvApi = {
  getConfig: async (): Promise<{ status: string; data: LowIVConfig }> => {
    const response = await webClient.get('/lowiv/api/config')
    return response.data
  },

  getExpiry: async (): Promise<{ status: string; data: string[] }> => {
    const response = await webClient.get('/lowiv/api/expiry')
    return response.data
  },

  getStrikes: async (
    expiry: string,
    strikeCount = 7,
    withQuotes = false
  ): Promise<{
    status: string
    underlying_ltp?: number
    atm_strike?: number
    atm_iv?: number | null
    chain?: LowIVChainRow[]
    message?: string
  }> => {
    const response = await webClient.get('/lowiv/api/strikes', {
      params: { expiry, strike_count: strikeCount, quotes: withQuotes ? 'true' : 'false' },
    })
    return response.data
  },

  getHistory: async (
    symbol: string,
    exchange: string,
    interval: string,
    date?: string
  ): Promise<{
    status: string
    symbol: string
    exchange: string
    interval: string
    date: string | null
    candles: LowIVCandle[]
    message?: string
  }> => {
    const params: Record<string, string> = { symbol, exchange, interval }
    if (date) params.date = date
    const response = await webClient.get('/lowiv/api/history', { params })
    return response.data
  },

  evaluate: async (
    currentIv: number,
    ivHistory: number[],
    belowCount: number,
    closes: number[] = []
  ): Promise<{
    status: string
    data: {
      current_iv: number
      iv_percentile: number | null
      threshold: number
      samples_today?: number
      min_samples?: number
      fire: boolean
      reason: string
      iv?: { fire: boolean; reason: string }
      bb?: {
        pass: boolean
        reason: string
        at_bottom?: boolean | null
        rising?: boolean | null
        pct_b?: number
        prev_pct_b?: number
        basis?: number
        upper?: number
        lower?: number
      }
      settings?: LowIVSettings
    }
  }> => {
    const response = await webClient.post('/lowiv/api/signal', {
      current_iv: currentIv,
      iv_history: ivHistory,
      below_count: belowCount,
      closes,
    })
    return response.data
  },

  getSettings: async (): Promise<{ status: string; data: LowIVSettings }> => {
    const response = await webClient.get('/lowiv/api/settings')
    return response.data
  },

  saveSettings: async (
    settings: Partial<LowIVSettings>
  ): Promise<{ status: string; data?: LowIVSettings; message?: string; errors?: Record<string, string> }> => {
    const response = await webClient.post('/lowiv/api/settings', settings)
    return response.data
  },

  resetSettings: async (): Promise<{ status: string; data: LowIVSettings }> => {
    const response = await webClient.post('/lowiv/api/settings/reset', {})
    return response.data
  },

  recordIv: async (
    iv: number,
    quote?: { strike?: number | null; bid?: number; ask?: number; legs?: LowIVLegSample[] }
  ): Promise<{ status: string; data?: { samples_today: number }; message?: string }> => {
    const response = await webClient.post('/lowiv/api/iv', { iv, ...quote })
    return response.data
  },

  getIvSeries: async (): Promise<{ status: string; data: { ivs: number[]; count: number } }> => {
    const response = await webClient.get('/lowiv/api/iv/series')
    return response.data
  },

  getAutoState: async (
    symbol: string,
    exchange: string,
    product: string,
    extraSymbols: string[] = []
  ): Promise<{
    status: string
    data: {
      mode: string
      qty: number
      avg_price: number
      ltp: number
      realized_today: number
      realized_supported: boolean
    }
    message?: string
  }> => {
    const response = await webClient.get('/lowiv/api/auto_state', {
      params: { symbol, exchange, product, symbols: extraSymbols.join(',') },
    })
    const raw = response.data
    const pos = raw?.data?.position ?? {}
    return {
      status: raw?.status ?? 'error',
      message: raw?.message,
      data: {
        mode: raw?.data?.mode ?? '',
        qty: pos.qty ?? 0,
        avg_price: pos.avg_price ?? 0,
        ltp: pos.ltp ?? 0,
        realized_today: raw?.data?.realized_today ?? 0,
        realized_supported: raw?.data?.realized_supported ?? false,
      },
    }
  },

  placeOrder: async (req: {
    symbol: string
    exchange: string
    action: 'BUY' | 'SELL'
    product: string
    quantity: number
    ltp?: number
    bid?: number
    ask?: number
    strategy?: 'LowIV' | 'Sahasra' | 'Momentum'
  }): Promise<{ status: string; message?: string; data?: unknown }> => {
    const response = await webClient.post('/lowiv/api/order', req)
    return response.data
  },

  closeLeg: async (req: {
    symbol: string
    exchange: string
    action: 'BUY' | 'SELL'
    product: string
    quantity: number
    strategy?: 'LowIV' | 'Sahasra' | 'Momentum'
  }): Promise<{ status: string; message?: string }> => {
    const response = await webClient.post('/lowiv/api/close_leg', req)
    return response.data
  },

  cancelAll: async (): Promise<{ status: string; message?: string }> => {
    const response = await webClient.post('/lowiv/api/cancel_all', {})
    return response.data
  },
}
