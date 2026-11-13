import { useCallback, useEffect, useMemo, useState } from 'react'
import { type VisualBracketApiRow, visualBracketApi } from '@/api/visualBracket'
import { useSocketContext } from '@/components/socket/SocketProvider'
import type { VisualBracketStatus, VisualBracketView } from '@/lib/trading/visualBracket'

function asStatus(value: string): VisualBracketStatus {
  switch (value) {
    case 'staged':
    case 'placing':
    case 'working':
    case 'active':
    case 'exiting':
    case 'closed':
    case 'cancelled':
    case 'error':
      return value
    default:
      return 'error'
  }
}

export function rowToView(row: VisualBracketApiRow): VisualBracketView {
  return {
    id: row.id,
    clientToken: row.client_token,
    strategy: row.strategy ?? 'VisualBracket',
    status: asStatus(row.status),
    underlying: row.underlying_symbol,
    underlyingExchange: row.underlying_exchange,
    foExchange: row.fo_exchange,
    action: row.action,
    optionType: row.option_type,
    bias: row.bias,
    entryReference: row.entry_reference,
    targetPrice: row.target_price,
    stopPrice: row.stop_price,
    clickPrice: row.click_price,
    tickSize: row.tick_size,
    lotSize: row.lot_size,
    lots: row.lot_size > 0 ? Math.max(1, Math.round(row.quantity / row.lot_size)) : row.quantity,
    quantity: row.quantity,
    contractSymbol: row.contract_symbol,
    contractExchange: row.contract_exchange,
    expiry: row.expiry,
    strike: row.strike,
    entryOrderId: row.entry_order_id,
    entryFillPrice: row.entry_fill_price,
    exitKind: row.exit_kind,
    exitReason: row.exit_reason,
    errorMessage: row.error_message,
    targetOffset: row.target_offset,
    stopOffset: row.stop_offset,
    riskReward: row.risk_reward,
  }
}

/**
 * Live visual brackets for the chart.
 *
 * The list is the authoritative restore source after a reload; the socket
 * carries the small status changes (placed, filled, target, exit) that happen
 * while the pane is open.
 */
export function useVisualBrackets(enabled: boolean) {
  const [rows, setRows] = useState<VisualBracketApiRow[]>([])
  const { socket } = useSocketContext()

  const refresh = useCallback(async () => {
    if (!enabled) return
    try {
      const next = await visualBracketApi.list()
      setRows(next)
    } catch {
      // The chart remains usable without the list; the next refresh retries.
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled) {
      setRows([])
      return
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 30000)
    return () => window.clearInterval(timer)
  }, [enabled, refresh])

  useEffect(() => {
    if (!socket || !enabled) return
    const onUpdate = (row: VisualBracketApiRow) => {
      if (!row || typeof row.id !== 'number') return
      setRows((prev) => {
        const index = prev.findIndex((item) => item.id === row.id)
        if (index === -1) return [...prev, row]
        const next = [...prev]
        next[index] = row
        return next
      })
    }
    socket.on('bracket_update', onUpdate)
    return () => {
      socket.off('bracket_update', onUpdate)
    }
  }, [socket, enabled])

  const brackets = useMemo(() => rows.map(rowToView), [rows])

  return { brackets, refresh }
}
