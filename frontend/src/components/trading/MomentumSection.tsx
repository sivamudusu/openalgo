/**
 * Momentum Scalping — explicitly an EXPERIMENT, not the YouTuber's model.
 *
 * What it does: buys a 15m bar that closes inside the S–R range in the bar's
 * own direction (CE on up closes, PE on down closes), still regime-gated.
 * That is plain intraday momentum — his stated rule for the range is "no
 * trading zone", and this section deliberately does the opposite to test
 * whether the backtest's 94.7% (n=19, one bank-friendly regime) was signal
 * or luck. Exits are premium-based exactly as backtested (T+4 / S-12 /
 * 15m), not structural. Judge it on live hit rate over 50+ trades.
 *
 * The same experiment runs on BANKNIFTY and NIFTY. Each variant keeps its own
 * localStorage keys and strategy tag, so both auto loops can hold a position
 * at once and their P&L is attributed separately. BANKNIFTY deliberately keeps
 * the original unsuffixed keys: renaming them would orphan a position already
 * open when this build lands.
 */

import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { sahasraApi, type SahasraUnderlying } from '@/api/sahasra'
import { lowIvApi, type LowIVStrategyTag } from '@/api/low-iv'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { useMarketData } from '@/hooks/useMarketData'
import { showToast } from '@/utils/toast'
import { hintFor } from './LowIvSection'
import { peerHoldsSymbol } from './strategyPeers'

const ENTRY_OPEN_MIN = 9 * 60 + 15
const ENTRY_CLOSE_MIN = 14 * 60 + 30
const COOLDOWN_MIN = 30

type MomentumVariant = 'BANKNIFTY' | 'NIFTY'

const VARIANTS = {
  BANKNIFTY: {
    underlying: 'BANKNIFTY',
    storage: 'momentum',
    strategy: 'Momentum',
    label: 'Momentum',
  },
  NIFTY: {
    underlying: 'NIFTY',
    storage: 'momentum.nifty',
    strategy: 'MomentumNifty',
    label: 'Momentum NIFTY',
  },
} as const satisfies Record<
  MomentumVariant,
  { underlying: SahasraUnderlying; storage: string; strategy: LowIVStrategyTag; label: string }
>

interface MomPos {
  symbol: string
  exchange: string
  product: string
  qty: number
  entry: number
  entryTs: number
  side: 'CE' | 'PE'
  targetPct: number
  stopPct: number
  maxHoldMin: number
  estimated: boolean
}

function todayStr(): string {
  return new Date().toLocaleDateString('en-CA')
}

function loadJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function istMinutes(): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Kolkata',
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  }).formatToParts(new Date())
  const h = Number(parts.find((p) => p.type === 'hour')?.value ?? 0)
  const m = Number(parts.find((p) => p.type === 'minute')?.value ?? 0)
  return h * 60 + m
}

export function MomentumSection({ variant = 'BANKNIFTY' }: { variant?: MomentumVariant }) {
  const { underlying, storage, strategy, label } = VARIANTS[variant]
  const [lots, setLots] = useState(1)
  const [busy, setBusy] = useState(false)
  const [orderError, setOrderError] = useState<string | null>(null)
  const [autoMode, setAutoMode] = useState(() => {
    try {
      return localStorage.getItem(`${storage}.autoMode`) === '1'
    } catch {
      return false
    }
  })
  const [autoPos, setAutoPos] = useState<MomPos | null>(() =>
    loadJSON<MomPos | null>(`${storage}.autoPos`, null)
  )
  const [lastAutoExitTs, setLastAutoExitTs] = useState<number>(() =>
    Number(loadJSON(`${storage}.lastExit`, 0))
  )
  const [entriesCount, setEntriesCount] = useState(() =>
    loadJSON<{ date: string; count: number }>(`${storage}.entries`, { date: '', count: 0 })
  )
  const [targetPct, setTargetPct] = useState(() => Number(loadJSON(`${storage}.target`, 4)))
  const [stopPct, setStopPct] = useState(() => Number(loadJSON(`${storage}.stop`, 12)))
  const [maxHoldMin, setMaxHoldMin] = useState(() => Number(loadJSON(`${storage}.hold`, 15)))
  const [maxEntries, setMaxEntries] = useState(() =>
    Number(loadJSON(`${storage}.maxEntries`, 5))
  )
  const [autoBook, setAutoBook] = useState<{
    qty: number
    avg_price: number
    ltp: number
  } | null>(null)
  const firingRef = useRef(false)
  const closingRef = useRef(false)
  const lastCloseAttemptRef = useRef(0)

  useEffect(() => {
    try {
      localStorage.setItem(`${storage}.autoMode`, autoMode ? '1' : '0')
    } catch {
      /* storage unavailable */
    }
  }, [autoMode, storage])
  useEffect(() => {
    try {
      if (autoPos) localStorage.setItem(`${storage}.autoPos`, JSON.stringify(autoPos))
      else localStorage.removeItem(`${storage}.autoPos`)
    } catch {
      /* storage unavailable */
    }
  }, [autoPos, storage])
  useEffect(() => {
    try {
      localStorage.setItem(`${storage}.lastExit`, String(lastAutoExitTs))
      localStorage.setItem(
        `${storage}.entries`,
        JSON.stringify(entriesCount.date === todayStr() ? entriesCount : { date: todayStr(), count: 0 })
      )
    } catch {
      /* storage unavailable */
    }
  }, [lastAutoExitTs, entriesCount, storage])
  useEffect(() => {
    try {
      localStorage.setItem(`${storage}.target`, String(targetPct))
      localStorage.setItem(`${storage}.stop`, String(stopPct))
      localStorage.setItem(`${storage}.hold`, String(maxHoldMin))
      localStorage.setItem(`${storage}.maxEntries`, String(maxEntries))
    } catch {
      /* storage unavailable */
    }
  }, [targetPct, stopPct, maxHoldMin, maxEntries, storage])

  const todayEntries = entriesCount.date === todayStr() ? entriesCount.count : 0
  const recordEntry = () => {
    setEntriesCount((prev) => {
      const base = prev.date === todayStr() ? prev : { date: todayStr(), count: 0 }
      return { date: base.date, count: base.count + 1 }
    })
  }

  const signalQ = useQuery({
    queryKey: ['sahasra-signal', underlying],
    queryFn: () => sahasraApi.getSignal(underlying),
    refetchInterval: 30000,
  })
  const data = signalQ.data?.data ?? null

  const wsSymbols = [
    ...(data?.atm_ce_symbol ? [{ symbol: data.atm_ce_symbol, exchange: 'NFO' }] : []),
    ...(data?.atm_pe_symbol ? [{ symbol: data.atm_pe_symbol, exchange: 'NFO' }] : []),
  ]
  const { data: ticks } = useMarketData({ symbols: wsSymbols, mode: 'LTP' })
  const tickOf = (symbol: string | null) =>
    symbol ? ticks.get(`NFO:${symbol}`)?.data?.ltp : undefined

  const refreshBook = useCallback(async (sym: string) => {
    try {
      const res = await lowIvApi.getAutoState(sym, 'NFO', 'MIS', [])
      if (res.status === 'success')
        setAutoBook({ qty: res.data.qty, avg_price: res.data.avg_price, ltp: res.data.ltp })
    } catch {
      /* keep last book */
    }
  }, [])
  useEffect(() => {
    const sym = autoPos?.symbol ?? (data?.side === 'CE' ? data?.atm_ce_symbol : data?.atm_pe_symbol) ?? null
    if (!sym || (!autoMode && !autoPos)) return
    refreshBook(sym)
    const t = setInterval(() => refreshBook(sym), autoPos?.estimated ? 5000 : 15000)
    return () => clearInterval(t)
  }, [autoPos?.symbol, autoPos?.estimated, autoMode, autoPos, data, refreshBook])

  useEffect(() => {
    if (!autoPos || !autoBook) return
    if (autoBook.qty === 0) {
      if (autoPos.estimated && Date.now() - autoPos.entryTs < 60000) return
      setAutoPos(null)
      setLastAutoExitTs(Date.now())
      showToast.info(`${label} position is flat in the book — tracking cleared, no exit sent`)
      return
    }
    const updates: Partial<MomPos> = {}
    if (autoBook.qty !== autoPos.qty) updates.qty = autoBook.qty
    if (autoPos.estimated && autoBook.avg_price > 0) {
      updates.entry = autoBook.avg_price
      updates.estimated = false
    }
    if (Object.keys(updates).length > 0) setAutoPos({ ...autoPos, ...updates })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoBook])

  // Auto entry: inside-zone bar direction only — the experiment.
  useEffect(() => {
    if (!autoMode || autoPos || !data || firingRef.current) return
    if (data.state !== 'inside_zone' || !data.side) return
    const side = data.side
    if ((data.regime === 'bearish' && side === 'CE') || (data.regime === 'bullish' && side === 'PE'))
      return
    if (maxEntries > 0 && todayEntries >= maxEntries) {
      setOrderError(`${label} blocked: day cap ${todayEntries}/${maxEntries} reached`)
      return
    }
    if (autoBook && autoBook.qty !== 0) return
    const nowMin = istMinutes()
    if (nowMin < ENTRY_OPEN_MIN || nowMin >= ENTRY_CLOSE_MIN) return
    if (Date.now() - lastAutoExitTs < COOLDOWN_MIN * 60000) return
    const sym = side === 'CE' ? data.atm_ce_symbol : data.atm_pe_symbol
    if (!sym) return
    if (peerHoldsSymbol(sym, `${storage}.autoPos`)) return // a peer loop holds this leg
    const lot = data.atm_lotsize && data.atm_lotsize > 0 ? data.atm_lotsize : 30
    const qty = lots * lot
    const entryLtp = tickOf(sym)
    if (!entryLtp) {
      setOrderError(`${label} blocked: no live price for the ATM leg yet`)
      return
    }
    firingRef.current = true
    setBusy(true)
    lowIvApi
      .placeOrder({ symbol: sym, exchange: 'NFO', action: 'BUY', product: 'MIS', quantity: qty, ltp: entryLtp, strategy })
      .then((res) => {
        if (res.status === 'success') {
          setAutoPos({
            symbol: sym, exchange: 'NFO', product: 'MIS', qty, entry: entryLtp,
            entryTs: Date.now(), side, targetPct, stopPct, maxHoldMin, estimated: true,
          })
          recordEntry()
          setOrderError(null)
          showToast.success(`${label} BUY ${sym} x${qty} (fill confirming…)`)
          refreshBook(sym)
        } else {
          setOrderError(`${label} order rejected: ${res.message ?? 'unknown reason'}`)
        }
      })
      .catch((e) => {
        setOrderError(`${label} order failed: ${e instanceof Error ? e.message : 'unknown error'}`)
      })
      .finally(() => {
        firingRef.current = false
        setBusy(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.state, autoMode, autoPos, data, lastAutoExitTs, todayEntries])

  // Premium exits exactly as backtested: T+target / S-stop / max-hold.
  useEffect(() => {
    if (!autoPos || closingRef.current || autoPos.estimated) return
    const ltp = tickOf(autoPos.symbol) ?? autoBook?.ltp ?? undefined
    if (!ltp) return
    const pnl = ((ltp - autoPos.entry) / autoPos.entry) * 100
    const heldMin = (Date.now() - autoPos.entryTs) / 60000
    const reason =
      pnl >= autoPos.targetPct
        ? `target +${autoPos.targetPct}%`
        : pnl <= -autoPos.stopPct
          ? `stop -${autoPos.stopPct}%`
          : heldMin >= autoPos.maxHoldMin
            ? `max-hold ${autoPos.maxHoldMin}m`
            : null
    if (!reason) return
    if (autoBook && autoBook.qty < 0) {
      setOrderError(`${label} exit blocked: book shows a short leg — close it manually`)
      return
    }
    if (autoBook && autoBook.qty === 0) return
    const exitQty = autoBook && autoBook.qty > 0 ? Math.min(autoPos.qty, autoBook.qty) : autoPos.qty
    if (exitQty <= 0) return
    if (Date.now() - lastCloseAttemptRef.current < 5000) return
    lastCloseAttemptRef.current = Date.now()
    closingRef.current = true
    setBusy(true)
    lowIvApi
      .closeLeg({
        symbol: autoPos.symbol, exchange: autoPos.exchange, action: 'SELL',
        product: autoPos.product, quantity: exitQty, strategy,
      })
      .then((res) => {
        if (res.status === 'success') {
          showToast.success(`${label} exit (${reason}) ${autoPos.symbol} @ ${ltp.toFixed(2)}`)
          setAutoPos(null)
          setLastAutoExitTs(Date.now())
          setOrderError(null)
        } else {
          setOrderError(`${label} exit rejected (${reason}): ${res.message ?? 'unknown reason'}`)
        }
      })
      .catch((e) => {
        setOrderError(`${label} exit failed (${reason}): ${e instanceof Error ? e.message : 'unknown error'}`)
      })
      .finally(() => {
        closingRef.current = false
        setBusy(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticks, autoPos])

  const autoLtp = autoPos ? (tickOf(autoPos.symbol) ?? autoBook?.ltp) : undefined
  const autoPnl = autoPos && autoLtp ? ((autoLtp - autoPos.entry) / autoPos.entry) * 100 : null
  const orderHint = hintFor(orderError)
  const momentumState = data?.state === 'inside_zone' && data.side

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{data?.mode === 'analyze' ? 'Sandbox' : 'Live'}</Badge>
        {data?.regime && <Badge variant="secondary">{data.regime}</Badge>}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Manual</span>
          <Switch checked={autoMode} onCheckedChange={setAutoMode} />
          <span className="text-xs text-muted-foreground">Auto</span>
        </div>
      </div>

      <p className="text-sm text-muted-foreground">
        {momentumState
          ? `Inside-zone ${data.side} momentum setup (bar closed ${data.side === 'CE' ? 'up' : 'down'})`
          : `No momentum setup — state: ${data?.state ?? 'loading…'}`}
      </p>

      {autoMode ? (
        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">
            Auto ON {busy ? '(working…)' : '(watching inside bars)'}.
            {autoPos ? (
              <span>
                {' '}Holding <span className="font-mono">{autoPos.symbol}</span> x{autoPos.qty} @{' '}
                {autoPos.estimated ? 'fill confirming…' : autoPos.entry.toFixed(2)}
                {autoPnl != null && !autoPos.estimated && (
                  <span className={autoPnl >= 0 ? 'text-green-600' : 'text-red-600'}>
                    {' '}({autoPnl >= 0 ? '+' : ''}{autoPnl.toFixed(2)}%)
                  </span>
                )}
                <span className="text-muted-foreground">
                  {' '}· exits T+{autoPos.targetPct}% / S-{autoPos.stopPct}% / {autoPos.maxHoldMin}m
                </span>
              </span>
            ) : (
              <span className="text-muted-foreground"> Flat — waiting for an inside-zone close.</span>
            )}
            <span className="text-muted-foreground"> · entries today {todayEntries}/{maxEntries}</span>
          </div>
          <div className="mt-2 grid grid-cols-4 gap-2 text-xs">
            <label>
              Target %<Input type="number" step="0.5" value={targetPct} onChange={(e) => setTargetPct(Number(e.target.value) || 4)} />
            </label>
            <label>
              Stop %<Input type="number" step="0.5" value={stopPct} onChange={(e) => setStopPct(Number(e.target.value) || 12)} />
            </label>
            <label>
              Hold (m)<Input type="number" value={maxHoldMin} onChange={(e) => setMaxHoldMin(Number(e.target.value) || 15)} />
            </label>
            <label>
              Max/day<Input type="number" value={maxEntries} onChange={(e) => setMaxEntries(Number(e.target.value) || 5)} />
            </label>
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs">
            <Input
              type="number"
              min={1}
              max={20}
              value={lots}
              onChange={(e) => setLots(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
            />
            <span className="text-muted-foreground">lots{data?.atm_lotsize ? ` × ${data.atm_lotsize}` : ''}</span>
          </div>
        </div>
      ) : (
        autoPos && (
          <div className="rounded-md border p-3 text-sm">
            Auto is OFF but a {label} position is still open:{' '}
            <span className="font-mono">{autoPos.symbol}</span> — turn Auto back on to resume exit
            monitoring.
          </div>
        )
      )}

      {orderError && (
        <div className="rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-800">
          <div className="font-medium">{orderError}</div>
          {orderHint && <div className="mt-1">Fix: {orderHint}</div>}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        Experiment, not his model — he calls the range a no-trade zone. Backtest: 19 trades,
        94.7% WR, one regime. This section re-proves or disproves it live.
      </p>
    </div>
  )
}
