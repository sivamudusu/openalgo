/**
 * Manual ATM trading for the /trading signals rail.
 *
 * The trader picks BANKNIFTY or NIFTY, then buys the at-the-money call or put
 * as a market MIS order. The order is a visual bracket underneath: its target
 * and stop are index levels, the server watches the index, and the same
 * draggable lines appear on the index chart.
 *
 * Orders carry strategy=Manual, separate from the visual tool's own orders, so
 * their P&L is attributed apart. The rows below come from the bracket list, so
 * they survive a reload and are the same state the server is watching.
 */

import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { sahasraApi, type SahasraUnderlying } from '@/api/sahasra'
import { visualBracketApi } from '@/api/visualBracket'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useMarketData } from '@/hooks/useMarketData'
import { useVisualBrackets } from '@/hooks/useVisualBrackets'
import {
  biasFor,
  manualBracketLevels,
  statusLabel,
  type VisualBracketView,
} from '@/lib/trading/visualBracket'
import { showToast } from '@/utils/toast'

type Side = 'CE' | 'PE'

const UNDERLYINGS: SahasraUnderlying[] = ['BANKNIFTY', 'NIFTY']
const INDEX_EXCHANGE = 'NSE_INDEX'
const FO_EXCHANGE = 'NFO'

function newClientToken(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

const inr = (n: number) => n.toLocaleString('en-IN')

export function ManualSection({
  ordersAllowed = true,
  onShowChart,
}: {
  ordersAllowed?: boolean
  onShowChart?: (symbol: string, exchange: string) => void
}) {
  const [underlying, setUnderlying] = useState<SahasraUnderlying>('BANKNIFTY')
  const [lots, setLots] = useState(1)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const signalQ = useQuery({
    queryKey: ['sahasra-signal', underlying],
    queryFn: () => sahasraApi.getSignal(underlying),
    refetchInterval: 30_000,
  })
  const data = signalQ.data?.data ?? null
  const lot = data?.atm_lotsize && data.atm_lotsize > 0 ? data.atm_lotsize : null
  const qty = lot ? lots * lot : 0

  const { brackets, refresh } = useVisualBrackets(true)
  const manualBrackets = useMemo(
    () =>
      brackets
        .filter((bracket) => bracket.strategy === 'Manual')
        .sort((a, b) => (b.id ?? 0) - (a.id ?? 0)),
    [brackets]
  )

  const wsSymbols = useMemo(() => {
    const list = [
      { symbol: underlying, exchange: INDEX_EXCHANGE },
      ...(data?.atm_ce_symbol ? [{ symbol: data.atm_ce_symbol, exchange: FO_EXCHANGE }] : []),
      ...(data?.atm_pe_symbol ? [{ symbol: data.atm_pe_symbol, exchange: FO_EXCHANGE }] : []),
      ...manualBrackets.flatMap((bracket) =>
        bracket.contractSymbol && bracket.contractExchange
          ? [{ symbol: bracket.contractSymbol, exchange: bracket.contractExchange }]
          : []
      ),
    ]
    return [...new Map(list.map((item) => [`${item.exchange}:${item.symbol}`, item])).values()]
  }, [underlying, data?.atm_ce_symbol, data?.atm_pe_symbol, manualBrackets])

  const { data: ticks } = useMarketData({ symbols: wsSymbols, mode: 'LTP' })
  const tickOf = (symbol: string | null | undefined, exchange: string) =>
    symbol ? ticks.get(`${exchange}:${symbol}`)?.data?.ltp : undefined

  const spot = tickOf(underlying, INDEX_EXCHANGE) ?? data?.spot ?? undefined
  const ceLtp = tickOf(data?.atm_ce_symbol, FO_EXCHANGE)
  const peLtp = tickOf(data?.atm_pe_symbol, FO_EXCHANGE)
  const sideSymbol = (side: Side) => (side === 'CE' ? data?.atm_ce_symbol : data?.atm_pe_symbol)
  const canBuy = (side: Side) =>
    ordersAllowed &&
    busy === null &&
    lot !== null &&
    spot != null &&
    data?.expiry != null &&
    data?.atm_strike != null &&
    sideSymbol(side) != null

  const buy = async (side: Side) => {
    if (busy !== null) return
    if (!ordersAllowed) {
      setError('New manual orders are paused on this device by your administrator.')
      return
    }
    const symbol = sideSymbol(side)
    if (!symbol || !lot || !data?.expiry || data.atm_strike == null || spot == null) {
      setError('The ATM strike is still loading. Wait for the chain, then try again.')
      return
    }
    const levels = manualBracketLevels(
      biasFor('BUY', side),
      spot,
      data.support,
      data.resistance,
      data.ladder
    )
    setBusy(side)
    setError(null)
    try {
      const row = await visualBracketApi.place({
        underlying,
        index_exchange: INDEX_EXCHANGE,
        contract_symbol: symbol,
        contract_exchange: FO_EXCHANGE,
        option_type: side,
        action: 'BUY',
        product: 'MIS',
        quantity: qty,
        lots,
        click_price: spot,
        entry_reference: levels.entryReference,
        target_price: levels.targetPrice,
        stop_price: levels.stopPrice,
        execution_mode: 'SYNTHETIC_UNDERLYING_TRIGGER',
        entry_order_type: 'MARKET',
        expiry: data.expiry,
        strike: data.atm_strike,
        strike_selection: 'CUSTOM',
        client_token: newClientToken(),
        strategy: 'Manual',
      })
      showToast.success(`BUY ${row.contract_symbol} ×${row.quantity} placed`)
      await refresh()
    } catch (e) {
      const message = e instanceof Error ? e.message : 'The order could not be placed.'
      setError(message)
      showToast.error(message)
    } finally {
      setBusy(null)
    }
  }

  const exitBracket = async (bracket: VisualBracketView) => {
    if (busy !== null || bracket.id == null) return
    setBusy(`exit:${bracket.id}`)
    setError(null)
    try {
      await visualBracketApi.exit(bracket.id)
      showToast.info('Exit requested. The position will be squared off at market.')
      await refresh()
    } catch (e) {
      const message = e instanceof Error ? e.message : 'The position could not be exited.'
      setError(message)
      showToast.error(message)
    } finally {
      setBusy(null)
    }
  }

  const cancelBracket = async (bracket: VisualBracketView) => {
    if (busy !== null || bracket.id == null) return
    setBusy(`cancel:${bracket.id}`)
    setError(null)
    try {
      await visualBracketApi.cancel(bracket.id)
      showToast.success('Manual order cancelled.')
      await refresh()
    } catch (e) {
      const message = e instanceof Error ? e.message : 'The order could not be cancelled.'
      setError(message)
      showToast.error(message)
    } finally {
      setBusy(null)
    }
  }

  const mode = data?.mode === 'analyze' ? 'Sandbox' : data?.mode === 'live' ? 'Live' : '—'

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">Order ticket</CardTitle>
          <Badge variant="outline">{mode}</Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          Buy the at-the-money call or put. A target and stop are set on the index and watched by
          the server.
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

        {!ordersAllowed && (
          <div className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900">
            New manual orders are paused on this device by your administrator. Open positions can
            still be exited.
          </div>
        )}

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
            {error}
          </div>
        )}

        {manualBrackets.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-medium text-muted-foreground">Manual orders</div>
            {manualBrackets.map((bracket) => {
              const ltp = tickOf(bracket.contractSymbol, bracket.contractExchange ?? FO_EXCHANGE)
              const entry = bracket.entryFillPrice
              const pnlPct =
                entry && entry > 0 && ltp != null ? ((ltp - entry) / entry) * 100 : null
              const pnlAbs = entry != null && ltp != null ? (ltp - entry) * bracket.quantity : null
              return (
                <div
                  key={bracket.id ?? bracket.clientToken}
                  className="space-y-2 rounded-md border p-2 text-xs"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate">
                        <span className="font-medium">
                          {bracket.underlying} ATM {bracket.optionType}
                        </span>{' '}
                        <span className="font-mono">{bracket.contractSymbol ?? '—'}</span>
                      </div>
                      <div className="text-muted-foreground">
                        {statusLabel(bracket.status)} · ×{bracket.quantity}
                        {entry != null ? ` · fill ${entry.toFixed(2)}` : ''}
                        {ltp != null ? ` · LTP ${ltp.toFixed(2)}` : ''}
                        {pnlPct != null && (
                          <span className={pnlPct >= 0 ? 'text-green-600' : 'text-red-600'}>
                            {' '}
                            ({pnlPct >= 0 ? '+' : ''}
                            {pnlPct.toFixed(2)}%
                            {pnlAbs != null
                              ? ` · ${pnlAbs >= 0 ? '+' : '-'}${inr(Math.abs(Math.round(pnlAbs)))}`
                              : ''}
                            )
                          </span>
                        )}
                      </div>
                      <div className="text-muted-foreground">
                        Index {inr(Math.round(bracket.entryReference))} · Target{' '}
                        {inr(Math.round(bracket.targetPrice))} (+
                        {inr(Math.round(bracket.targetOffset))}) · Stop{' '}
                        {inr(Math.round(bracket.stopPrice))} (-
                        {inr(Math.round(bracket.stopOffset))})
                      </div>
                      {bracket.errorMessage && (
                        <div className="mt-1 text-red-700">{bracket.errorMessage}</div>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      {onShowChart && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 px-2 text-[11px]"
                          onClick={() =>
                            onShowChart(bracket.underlying, bracket.underlyingExchange)
                          }
                        >
                          Show chart
                        </Button>
                      )}
                      {bracket.status === 'active' && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 px-2 text-[11px]"
                          disabled={busy !== null}
                          onClick={() => void exitBracket(bracket)}
                        >
                          {busy === `exit:${bracket.id}` ? 'Exiting…' : 'Exit'}
                        </Button>
                      )}
                      {bracket.status === 'working' && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 px-2 text-[11px]"
                          disabled={busy !== null}
                          onClick={() => void cancelBracket(bracket)}
                        >
                          {busy === `cancel:${bracket.id}` ? 'Cancelling…' : 'Cancel'}
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}

        <p className="text-[11px] text-muted-foreground">
          The ATM strike follows spot and refreshes every 30 seconds. Target and stop are index
          levels; drag them on the index chart. Orders follow the platform's Sandbox/Live mode.
        </p>
      </CardContent>
    </Card>
  )
}
