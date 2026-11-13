import { webClient } from './client'

export interface VisualBracketApiRow {
  id: number
  client_token: string
  mode: string
  strategy?: string
  status: string
  underlying_symbol: string
  underlying_exchange: string
  fo_exchange: string
  contract_symbol: string
  contract_exchange: string
  option_type: 'CE' | 'PE'
  strike: number
  expiry: string
  lot_size: number
  tick_size: number
  freeze_qty: number | null
  action: 'BUY' | 'SELL'
  product: string
  quantity: number
  entry_order_type: string
  entry_limit_price: number | null
  entry_order_id: string | null
  entry_fill_price: number | null
  execution_mode: string
  bias: 'bullish' | 'bearish'
  click_price: number
  entry_reference: number
  target_price: number
  stop_price: number
  target_offset: number
  stop_offset: number
  risk_reward: number
  exit_kind: string | null
  exit_order_id: string | null
  exit_reason: string | null
  error_message: string | null
  filled_at: string | null
  closed_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface VisualBracketPlaceRequest {
  underlying: string
  index_exchange: string
  contract_symbol: string
  contract_exchange: string
  option_type: 'CE' | 'PE'
  action: 'BUY' | 'SELL'
  product: string
  quantity: number
  lots: number
  click_price: number
  entry_reference: number
  target_price: number
  stop_price: number
  execution_mode: 'SYNTHETIC_UNDERLYING_TRIGGER'
  entry_order_type: 'MARKET'
  expiry: string
  strike: number
  strike_selection: string
  client_token: string
  strategy?: 'Manual' | 'VisualBracket'
}

interface ApiResponse<T> {
  status: 'success' | 'error'
  data?: T
  message?: string
}

export const visualBracketApi = {
  async place(request: VisualBracketPlaceRequest): Promise<VisualBracketApiRow> {
    const response = await webClient.post<ApiResponse<VisualBracketApiRow>>(
      '/bracket/api/place',
      request
    )
    if (response.data.status !== 'success' || !response.data.data) {
      throw new Error(response.data.message || 'The visual order could not be placed.')
    }
    return response.data.data
  },

  async modify(
    id: number,
    levels: { entry_reference: number; target_price: number; stop_price: number }
  ): Promise<VisualBracketApiRow> {
    const response = await webClient.post<ApiResponse<VisualBracketApiRow>>('/bracket/api/modify', {
      id,
      ...levels,
    })
    if (response.data.status !== 'success' || !response.data.data) {
      throw new Error(response.data.message || 'The visual order could not be changed.')
    }
    return response.data.data
  },

  async cancel(id: number): Promise<VisualBracketApiRow> {
    const response = await webClient.post<ApiResponse<VisualBracketApiRow>>('/bracket/api/cancel', {
      id,
    })
    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'The visual order could not be cancelled.')
    }
    return response.data.data as VisualBracketApiRow
  },

  async exit(id: number): Promise<VisualBracketApiRow> {
    const response = await webClient.post<ApiResponse<VisualBracketApiRow>>('/bracket/api/exit', {
      id,
    })
    if (response.data.status !== 'success') {
      throw new Error(response.data.message || 'The position could not be exited.')
    }
    return response.data.data as VisualBracketApiRow
  },

  async list(): Promise<VisualBracketApiRow[]> {
    const response = await webClient.get<ApiResponse<VisualBracketApiRow[]>>('/bracket/api/list')
    return response.data.data ?? []
  },
}
