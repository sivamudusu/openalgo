/**
 * Manual ATM trading for the /trading signals rail.
 *
 * The trader picks BANKNIFTY or NIFTY, then buys the at-the-money call or put
 * as a market MIS order. The ATM leg comes from the same signal endpoint the
 * models read, so the strike follows spot between refreshes.
 *
 * Orders carry strategy=Manual so they are attributed separately from the
 * automated models. The rows below are a local reminder, not a book; the
 * Positions page remains the source of truth.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { lowIvApi } from '@/api/low-iv'
import { sahasraApi, type SahasraUnderlying } from '@/api/sahasra'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useMarketData } from '@/hooks/useMarketData'
import { showToast } from '@/utils/toast'
import { hintFor } from './LowIvSection'

type Side = 'CE' | 'PE'

interface ManualPosition {
  id: string
  symbol: string
  exchange: string
  underlying: SahasraUnderlying
  side: Side
  qty: number
  entry: number | null
  ts: number
}

const STORAGE_KEY = 'manual.positions.v1'
const UNDERLYINGS: SahasraUnderlying[] = ['BANKNIFTY', 'NIFTY']

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function loadPositions(): ManualPosition[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (row): row is ManualPosition =>
        !!row &&
        typeof row === 'object' &&
        typeof (row as ManualPosition).symbol === 'string' &&
        typeof (row as ManualPosition).qty === 'number'
    )
  } catch {
    return []
  }
}

const inr = (n: number) => n.toLocaleString('en-IN')

export function ManualSection() {
  const [underlying, setUnderlying] = useState<SahasraUnderlying>('BANKNIFTY')
  const [lots, setLots] = useState(1)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [positions, setPositions] = useState<ManualPosition[]>(loadPositions)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(positions))
    } catch {
      /* storage unavailable */
    }
  }, [positions])

  const signalQ = useQuery({
    queryKey: ['sahasra-signal', underlying],
    queryFn: () => sahasraApi.getSignal(underlying),
    refetchInterval: 30_000,
  })
  const data = signalQ.data?.data ?? null
  const lot = data?.atm_lotsize && data.atm_lotsize > 0 ? data.atm_lotsize : null
  const qty = lot ? lots * lot : 0

  const wsSymbols = useMemo(() => {
    const list = [
      { symbol: underlying, exchange: 'NSE_INDEX' },
      ...(data?.atm_ce_symbol ? [{ symbol: data.atm_ce_symbol, exchange: 'NFO' }] : []),
      ...(data?.atm_pe_symbol ? [{ symbol: data.atm_pe_symbol, exchange: 'NFO' }] : []),
      ...positions.map((pos) => ({ symbol: pos.symbol, exchange: pos.exchange })),
    ]
    return [...new Map(list.map((item) => [`${item.exchange}:${item.symbol}`, item])).values()]
  }, [underlying, data?.atm_ce_symbol, data?.atm_pe_symbol, positions])

  const { data: ticks } = useMarketData({ symbols: wsSymbols, mode: 'LTP' })
  const tickOf = (symbol: string | null, exchange: string) =>
    symbol ? ticks.get(`${exchange}:${symbol}`)?.data?.ltp : undefined

  const spot = tickOf(underlying, 'NSE_INDEX') ?? data?.spot ?? undefined
  const ceLtp = tickOf(data?.atm_ce_symbol ?? null, 'NFO')
  const peLtp = tickOf(data?.atm_pe_symbol ?? null, 'NFO')
  const sideSymbol = (side: Side) => (side === 'CE' ? data?.atm_ce_symbol : data?.atm_pe_symbol)
  const canBuy = (side: Side) => busy === null && lot !== null && sideSymbol(side) !== null

  const buy = async (side: Side) => {
    if (busy !== null) return
    const symbol = sideSymbol(side)
    if (!symbol || !lot) {
      setError('The ATM strike is still loading. Wait for the chain, then try again.')
      return
    }
    const ltp = side === 'CE' ? ceLtp : peLtp
    setBusy(side)
    setError(null)
    try {
      const res = await lowIvApi.placeOrder({
        symbol,
        exchange: 'NFO',
        action: 'BUY',
        product: 'MIS',
        quantity: qty,
        ...(ltp != null ? { ltp } : {}),
        strategy: 'Manual',
      })
      if (res.status === 'success') {
        setPositions((prev) => [
          ...prev,
          {
            id: newId(),
            symbol,
            exchange: 'NFO',
            underlying,
            side,
            qty,
            entry: ltp ?? null,
            ts: Date.now(),
          },
        ])
        showToast.success(`BUY ${symbol} ×${qty} placed`)
      } else {
        const message = res.message ?? 'The order was not accepted.'
        setError(message)
        showToast.error(message)
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : 'The order could not be placed.'
      setError(message)
      showToast.error(message)
    } finally {
      setBusy(null)
    }
  }

  const sell = async (pos: ManualPosition) => {
    if (busy !== null) return
    setBusy(pos.id)
    setError(null)
    try {
      const res = await lowIvApi.closeLeg({
        symbol: pos.symbol,
        exchange: pos.exchange,
        action: 'SELL',
        product: 'MIS',
        quantity: pos.qty,
        strategy: 'Manual',
      })
      if (res.status === 'success') {
        setPositions((prev) => prev.filter((row) => row.id !== pos.id))
        showToast.success(`SELL ${pos.symbol} ×${pos.qty} placed`)
      } else {
        const message = res.message ?? 'The exit was not accepted.'
        setError(message)
        showToast.error(message)
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : 'The exit could not be placed.'
      setError(message)
      showToast.error(message)
    } finally {
      setBusy(null)
    }
  }

  const clear = (id: string) => {
    setPositions((prev) => prev.filter((row) => row.id !== id))
  }

  const mode =
    data?.mode === 'analyze' ? 'Sandbox' : data?.mode === 'live' ? 'Live' : '—'

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">Order ticket</CardTitle>
          <Badge variant="outline">{mode}</Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          Buy the at-the-money call or put as a market MIS order.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          {UNDERLYINGS.map((name) => (
            <Button
              key={name}
              size="sm"
              variant={underlying === name ? 'default' : 'outline'}
              aria-pressed={underlying === name}
              disabled={busy !== null}
              onClick={() => setUnderlying(name)}
            >
              {name}
            </Button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md border p-2">
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span>ATM CE</span>
              <span>{ceLtp != null ? ceLtp.toFixed(2) : '—'}</span>
            </div>
            <div className="truncate font-mono text-xs" title={data?.atm_ce_symbol ?? undefined}>
              {data?.atm_ce_symbol ?? 'Waiting for the chain'}
            </div>
          </div>
          <div className="rounded-md border p-2">
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span>ATM PE</span>
              <span>{peLtp != null ? peLtp.toFixed(2) : '—'}</span>
            </div>
            <div className="truncate font-mono text-xs" title={data?.atm_pe_symbol ?? undefined}>
              {data?.atm_pe_symbol ?? 'Waiting for the chain'}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs">
          <span>
            Spot <span className="font-mono">{spot != null ? inr(Math.round(spot)) : '—'}</span>
          </span>
          <span>
            Expiry <span className="font-mono">{data?.expiry ?? '—'}</span>
          </span>
        </div>

        <div className="flex items-center gap-2">
          <Input
            type="number"
            min={1}
            max={20}
            value={lots}
            onChange={(e) => setLots(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
          />
          <span className="text-xs text-muted-foreground">
            {lot ? `lots × ${lot} = ${qty} qty` : 'lot size loading'}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <Button
            className="bg-emerald-600 text-white hover:bg-emerald-700"
            disabled={!canBuy('CE')}
            onClick={() => void buy('CE')}
          >
            {busy === 'CE' ? 'Buying…' : 'BUY ATM CE'}
          </Button>
          <Button
            className="bg-rose-600 text-white hover:bg-rose-700"
            disabled={!canBuy('PE')}
            onClick={() => void buy('PE')}
          >
            {busy === 'PE' ? 'Buying…' : 'BUY ATM PE'}
          </Button>
        </div>

        {signalQ.isError && !data && (
          <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
            <span>The ATM strike could not be read.</span>
            <button
              type="button"
              className="underline underline-offset-2"
              onClick={() => void signalQ.refetch()}
            >
              Retry
            </button>
          </div>
        )}

        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-800">
            <div>{error}</div>
            {hintFor(error) && <div className="mt-1">Fix: {hintFor(error)}</div>}
          </div>
        )}

        {positions.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-medium text-muted-foreground">Manual positions</div>
            {positions.map((pos) => {
              const ltp = tickOf(pos.symbol, pos.exchange)
              const pnl =
                pos.entry && pos.entry > 0 && ltp != null
                  ? ((ltp - pos.entry) / pos.entry) * 100
                  : null
              return (
                <div
                  key={pos.id}
                  className="flex items-center justify-between gap-2 rounded-md border p-2 text-xs"
                >
                  <div className="min-w-0">
                    <div className="truncate">
                      <span className="font-medium">
                        {pos.underlying} ATM {pos.side}
                      </span>{' '}
                      <span className="font-mono">{pos.symbol}</span>
                    </div>
                    <div className="text-muted-foreground">
                      ×{pos.qty}
                      {pos.entry != null ? ` · ref ${pos.entry.toFixed(2)}` : ''}
                      {ltp != null ? ` · LTP ${ltp.toFixed(2)}` : ''}
                      {pnl != null && (
                        <span className={pnl >= 0 ? 'text-green-600' : 'text-red-600'}>
                          {' '}
                          ({pnl >= 0 ? '+' : ''}
                          {pnl.toFixed(2)}%)
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy !== null}
                      onClick={() => void sell(pos)}
                    >
                      {busy === pos.id ? 'Selling…' : 'Sell'}
                    </Button>
                    <button
                      type="button"
                      className="text-muted-foreground underline underline-offset-2 disabled:opacity-50"
                      disabled={busy !== null}
                      title="Remove this row without placing an order"
                      onClick={() => clear(pos.id)}
                    >
                      clear
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        <p className="text-[11px] text-muted-foreground">
          The ATM strike follows spot and refreshes every 30 seconds. Orders follow the platform's
          Sandbox/Live mode. These rows are a local reminder; manage the actual position in
          Positions.
        </p>
      </CardContent>
    </Card>
  )
}
