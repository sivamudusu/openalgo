/**
 * Sahasra OI-model section for the /trading sidebar signals panel.
 *
 * Monitoring + trading, his way:
 * - Levels: OI support/resistance + PCR regime from the live chain.
 * - Entries (auto): 15m breakout above R -> BUY ATM CE, breakdown below S ->
 *   BUY ATM PE, support tag-and-hold -> BUY ATM CE; regime-gated (bearish
 *   blocks CE, bullish blocks PE), one position, cooldown, day cap.
 * - Exits (auto): TARGET = next OI level in the trade's direction (his
 *   measured-move style); SL = structural reclaim (15m state back inside
 *   the range / against the trade); safety rails clearly labeled as ours:
 *   premium catastrophic stop % + max-hold minutes.
 * Everything goes through the shared NFO order path, so Sandbox/Live follows
 * the global mode. Book reconciliation (never exit what the book says is
 * flat) mirrors the Low-IV section.
 *
 * The same rules run on BANKNIFTY and NIFTY. Each variant keeps its own
 * localStorage keys and strategy tag, so both auto loops can hold a position
 * at once and their P&L is attributed separately. BANKNIFTY deliberately keeps
 * the original unsuffixed keys: renaming them would orphan a position already
 * open when this build lands.
 */

import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { sahasraApi, type SahasraLevels, type SahasraUnderlying } from '@/api/sahasra'
import { lowIvApi, type LowIVStrategyTag } from '@/api/low-iv'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { useMarketData } from '@/hooks/useMarketData'
import { showToast } from '@/utils/toast'
import { hintFor } from './LowIvSection'
import { peerHoldsSymbol } from './strategyPeers'

type SahasraVariant = 'BANKNIFTY' | 'NIFTY'

const VARIANTS = {
  BANKNIFTY: {
    underlying: 'BANKNIFTY',
    storage: 'sahasra',
    strategy: 'Sahasra',
    label: 'Sahasra',
  },
  NIFTY: {
    underlying: 'NIFTY',
    storage: 'sahasra.nifty',
    strategy: 'SahasraNifty',
    label: 'Sahasra NIFTY',
  },
} as const satisfies Record<
  SahasraVariant,
  { underlying: SahasraUnderlying; storage: string; strategy: LowIVStrategyTag; label: string }
>

const STATE_TEXT: Record<string, string> = {
  breakout_up: 'Breakout above resistance — long CE per the plan',
  breakdown: 'Breakdown below support — long PE per the plan',
  support_bounce: 'Tagged support and held — bounce long setup',
  inside_zone: 'Inside the S–R range — no-trade zone, wait for the break',
  above_resistance: 'Holding above resistance — breakout already in motion',
  below_support: 'Holding below support — breakdown already in motion',
  no_levels: 'No readable OI levels right now (magnet-dominated chain)',
  warming_up: 'Waiting for two closed 15m bars',
}

// Fixed session rules (his plan trades 09:15–14:30; exits manage after).
const ENTRY_OPEN_MIN = 9 * 60 + 15
const ENTRY_CLOSE_MIN = 14 * 60 + 30
const COOLDOWN_MIN = 30

interface SahPos {
  symbol: string
  exchange: string
  product: string
  qty: number
  entry: number
  entryTs: number
  entrySpot: number
  side: 'CE' | 'PE'
  targetSpot: number | null
  entryState: string
  estimated: boolean
}

interface EntriesLog {
  date: string
  count: number
  symbols: string[]
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

/** Next OI level in the trade's direction (his measured-move target). */
function nextLevel(
  ladder: SahasraLevels['ladder'],
  fromSpot: number,
  side: 'CE' | 'PE'
): number | null {
  const strikes = [...ladder].sort((a, b) => a.strike - b.strike)
  if (side === 'CE') {
    const above = strikes.filter((r) => r.strike > fromSpot)
    if (above.length === 0) return null
    return above.reduce((a, b) => (b.call_oi > a.call_oi ? b : a)).strike
  }
  const below = strikes.filter((r) => r.strike < fromSpot)
  if (below.length === 0) return null
  return below.reduce((a, b) => (b.put_oi > a.put_oi ? b : a)).strike
}

export function SahasraSection({ variant = 'BANKNIFTY' }: { variant?: SahasraVariant }) {
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
  const [autoPos, setAutoPos] = useState<SahPos | null>(() =>
    loadJSON<SahPos | null>(`${storage}.autoPos`, null)
  )
  const [lastAutoExitTs, setLastAutoExitTs] = useState<number>(() =>
    Number(loadJSON(`${storage}.lastExit`, 0))
  )
  const [entriesLog, setEntriesLog] = useState<EntriesLog>(() =>
    loadJSON<EntriesLog>(`${storage}.entries`, { date: todayStr(), count: 0, symbols: [] })
  )
  const [maxEntries, setMaxEntries] = useState(() =>
    Number(loadJSON(`${storage}.maxEntries`, 3))
  )
  const [maxHoldMin, setMaxHoldMin] = useState(() =>
    Number(loadJSON(`${storage}.maxHold`, 30))
  )
  const [premStopPct, setPremStopPct] = useState(() =>
    Number(loadJSON(`${storage}.premStop`, 15))
  )
  const [autoBook, setAutoBook] = useState<{
    qty: number
    avg_price: number
    ltp: number
    realized_today: number
    realized_supported: boolean
    mode: string
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
    } catch {
      /* storage unavailable */
    }
  }, [lastAutoExitTs, storage])
  useEffect(() => {
    try {
      localStorage.setItem(`${storage}.entries`, JSON.stringify(entriesLog))
    } catch {
      /* storage unavailable */
    }
  }, [entriesLog, storage])
  useEffect(() => {
    try {
      localStorage.setItem(`${storage}.maxEntries`, String(maxEntries))
      localStorage.setItem(`${storage}.maxHold`, String(maxHoldMin))
      localStorage.setItem(`${storage}.premStop`, String(premStopPct))
    } catch {
      /* storage unavailable */
    }
  }, [maxEntries, maxHoldMin, premStopPct, storage])

  const recordEntry = (sym: string) => {
    setEntriesLog((prev) => {
      const base = prev.date === todayStr() ? prev : { date: todayStr(), count: 0, symbols: [] }
      return {
        date: base.date,
        count: base.count + 1,
        symbols: base.symbols.includes(sym) ? base.symbols : [...base.symbols, sym],
      }
    })
  }

  const levelsQ = useQuery({
    queryKey: ['sahasra-levels', underlying],
    queryFn: () => sahasraApi.getLevels(underlying),
    refetchInterval: 30000,
  })
  const signalQ = useQuery({
    queryKey: ['sahasra-signal', underlying],
    queryFn: () => sahasraApi.getSignal(underlying),
    refetchInterval: 30000,
  })
  const data = signalQ.data?.data ?? levelsQ.data?.data ?? null

  const wsSymbols = [
    { symbol: underlying, exchange: 'NSE_INDEX' },
    ...(data?.atm_ce_symbol ? [{ symbol: data.atm_ce_symbol, exchange: 'NFO' }] : []),
    ...(data?.atm_pe_symbol ? [{ symbol: data.atm_pe_symbol, exchange: 'NFO' }] : []),
  ]
  const { data: ticks } = useMarketData({ symbols: wsSymbols, mode: 'LTP' })
  const tickOf = (symbol: string | null, exchange: string) =>
    symbol ? ticks.get(`${exchange}:${symbol}`)?.data?.ltp : undefined
  const spotLtp = tickOf(underlying, 'NSE_INDEX') ?? data?.spot ?? undefined

  // Book reconciliation (same contract as Low-IV: never exit flat legs).
  const refreshBook = useCallback(
    async (sym: string) => {
      try {
        const res = await lowIvApi.getAutoState(sym, 'NFO', 'MIS', entriesLog.symbols)
        if (res.status === 'success') setAutoBook(res.data)
      } catch {
        /* keep last book; local tracking is the fallback */
      }
    },
    [entriesLog.symbols]
  )
  useEffect(() => {
    const sym = autoPos?.symbol ?? data?.atm_ce_symbol ?? null
    if (!sym || (!autoMode && !autoPos)) return
    refreshBook(sym)
    const t = setInterval(() => refreshBook(sym), autoPos?.estimated ? 5000 : 15000)
    return () => clearInterval(t)
  }, [autoPos?.symbol, autoPos?.estimated, autoMode, autoPos, data?.atm_ce_symbol, refreshBook])

  useEffect(() => {
    if (!autoPos || !autoBook) return
    if (autoBook.qty === 0) {
      if (autoPos.estimated && Date.now() - autoPos.entryTs < 60000) return
      setAutoPos(null)
      setLastAutoExitTs(Date.now())
      showToast.info(`${label} position is flat in the book — tracking cleared, no exit sent`)
      return
    }
    const updates: Partial<SahPos> = {}
    if (autoBook.qty !== autoPos.qty) updates.qty = autoBook.qty
    if (autoPos.estimated && autoBook.avg_price > 0) {
      updates.entry = autoBook.avg_price
      updates.estimated = false
    }
    if (Object.keys(updates).length > 0) setAutoPos({ ...autoPos, ...updates })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoBook])

  // Auto entry on 15m-bar state.
  useEffect(() => {
    if (!autoMode || autoPos || !data || firingRef.current) return
    if (
      data.state !== 'breakout_up' &&
      data.state !== 'breakdown' &&
      data.state !== 'support_bounce'
    )
      return
    // Faithful model only: breakouts and bounces take the plan leg
    // (breakdown alone goes PE). Inside-zone momentum lives in the separate
    // Momentum Scalping section — this one respects his no-trade zone.
    const side: 'CE' | 'PE' = data.state === 'breakdown' ? 'PE' : 'CE'
    if ((data.regime === 'bearish' && side === 'CE') || (data.regime === 'bullish' && side === 'PE'))
      return
    if (maxEntries > 0 && entriesLog.count >= maxEntries) {
      setOrderError(`${label} auto blocked: day cap ${entriesLog.count}/${maxEntries} reached`)
      return
    }
    if (autoBook && autoBook.qty !== 0) return // already holding — no double-up
    const nowMin = istMinutes()
    if (nowMin < ENTRY_OPEN_MIN || nowMin >= ENTRY_CLOSE_MIN) return
    if (Date.now() - lastAutoExitTs < COOLDOWN_MIN * 60000) return
    const sym = side === 'CE' ? data.atm_ce_symbol : data.atm_pe_symbol
    if (!sym) return
    if (peerHoldsSymbol(sym, `${storage}.autoPos`)) return // a peer loop holds this leg
    const lot = data.atm_lotsize && data.atm_lotsize > 0 ? data.atm_lotsize : 30
    const qty = lots * lot
    const entryLtp = tickOf(sym, 'NFO')
    if (!entryLtp) {
      setOrderError(`${label} auto blocked: no live price for the ATM leg yet`)
      return
    }
    const entrySpot = spotLtp ?? data.spot ?? 0
    if (!entrySpot) {
      setOrderError(`${label} auto blocked: no spot reference for the target level`)
      return
    }
    const target = nextLevel(data.ladder ?? [], entrySpot, side)
    firingRef.current = true
    setBusy(true)
    lowIvApi
      .placeOrder({ symbol: sym, exchange: 'NFO', action: 'BUY', product: 'MIS', quantity: qty, ltp: entryLtp, strategy })
      .then((res) => {
        if (res.status === 'success') {
          setAutoPos({
            symbol: sym,
            exchange: 'NFO',
            product: 'MIS',
            qty,
            entry: entryLtp,
            entryTs: Date.now(),
            entrySpot,
            side,
            targetSpot: target,
            entryState: data.state ?? '',
            estimated: true,
          })
          recordEntry(sym)
          setOrderError(null)
          showToast.success(`${label} auto BUY ${sym} x${qty} (fill confirming…)`)
          refreshBook(sym)
        } else {
          setOrderError(`${label} auto rejected: ${res.message ?? 'unknown reason'}`)
        }
      })
      .catch((e) => {
        setOrderError(`${label} auto failed: ${e instanceof Error ? e.message : 'unknown error'}`)
      })
      .finally(() => {
        firingRef.current = false
        setBusy(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.state, autoMode, autoPos, data, lastAutoExitTs, entriesLog.count])

  // Auto exits: structural target (next OI level), structural SL (range
  // reclaim on 15m state), plus our safety rails (premium stop, max hold).
  useEffect(() => {
    if (!autoPos || closingRef.current) return
    if (autoPos.estimated) return
    const ltp = tickOf(autoPos.symbol, autoPos.exchange) ?? autoBook?.ltp ?? undefined
    const spot = spotLtp
    if (!ltp) return
    const pnl = ((ltp - autoPos.entry) / autoPos.entry) * 100
    const heldMin = (Date.now() - autoPos.entryTs) / 60000
    let reason: string | null = null
    if (
      autoPos.targetSpot &&
      spot != null &&
      (autoPos.side === 'CE' ? spot >= autoPos.targetSpot : spot <= autoPos.targetSpot)
    ) {
      reason = `target ${autoPos.targetSpot}`
    } else if (pnl <= -premStopPct) {
      reason = `premium stop -${premStopPct}% (safety rail)`
    } else if (heldMin >= maxHoldMin) {
      reason = `max-hold ${maxHoldMin}m (safety rail)`
    } else if (data?.state) {
      const st = data.state
      // His stop is structural: a breakout that falls back into the range
      // (or beyond) has failed; bounce longs die on breakdowns.
      const reclaimed =
        autoPos.side === 'CE'
          ? st === 'inside_zone' || st === 'breakdown' || st === 'below_support'
          : st === 'inside_zone' || st === 'breakout_up' || st === 'above_resistance'
      if (reclaimed) reason = `range reclaimed (${st})`
    }
    if (!reason) return
    if (autoBook && autoBook.qty < 0) {
      setOrderError(`${label} exit blocked: book shows a short leg — close it manually`)
      return
    }
    if (autoBook && autoBook.qty === 0) return // reconcile clears tracking
    const exitQty =
      autoBook && autoBook.qty > 0 ? Math.min(autoPos.qty, autoBook.qty) : autoPos.qty
    if (exitQty <= 0) return
    if (Date.now() - lastCloseAttemptRef.current < 5000) return
    lastCloseAttemptRef.current = Date.now()
    closingRef.current = true
    setBusy(true)
    lowIvApi
      .closeLeg({
        symbol: autoPos.symbol,
        exchange: autoPos.exchange,
        action: 'SELL', // page-auto only ever holds longs
        product: autoPos.product,
        quantity: exitQty,
        strategy,
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
  }, [ticks, autoPos, data?.state])

  const state = data?.state
  const blocked = data?.regime_blocked && data?.side
  const autoLtp = autoPos ? (tickOf(autoPos.symbol, autoPos.exchange) ?? autoBook?.ltp) : undefined
  const autoPnl = autoPos && autoLtp ? ((autoLtp - autoPos.entry) / autoPos.entry) * 100 : null
  const orderHint = hintFor(orderError)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{data?.mode === 'analyze' ? 'Sandbox' : 'Live'}</Badge>
        {data?.regime && (
          <Badge variant={data.regime === 'neutral' ? 'secondary' : 'default'}>
            PCR {data.pcr ?? '—'} · {data.regime}
          </Badge>
        )}
        {data?.support != null && <Badge variant="outline">S {data.support}</Badge>}
        {data?.resistance != null && <Badge variant="outline">R {data.resistance}</Badge>}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Manual</span>
          <Switch checked={autoMode} onCheckedChange={setAutoMode} />
          <span className="text-xs text-muted-foreground">Auto</span>
        </div>
      </div>

      <p className="text-sm text-muted-foreground">
        {state ? (STATE_TEXT[state] ?? state) : 'Loading OI map…'}
        {data?.side && ` — plan leg: ATM ${data.side}`}
      </p>
      {blocked && (
        <p className="text-xs text-amber-600">
          Regime filter blocks this side under {data?.regime} PCR — shown, not placed, by design.
        </p>
      )}

      {autoMode ? (
        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">
            Auto ON {busy ? '(working…)' : '(watching 15m state)'}.
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
                  {' '}· tgt {autoPos.targetSpot ?? '—'} / reclaim SL / -{premStopPct}% / {maxHoldMin}m
                </span>
              </span>
            ) : (
              <span className="text-muted-foreground"> Flat — waiting for breakout / breakdown / bounce.</span>
            )}
            <span className="text-muted-foreground"> · entries today {entriesLog.count}/{maxEntries}</span>
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
            <label>
              Max entries<Input type="number" min={1} max={10} value={maxEntries} onChange={(e) => setMaxEntries(Math.max(1, Math.min(10, Number(e.target.value) || 3)))} />
            </label>
            <label>
              Max hold (m)<Input type="number" min={5} max={120} value={maxHoldMin} onChange={(e) => setMaxHoldMin(Math.max(5, Math.min(120, Number(e.target.value) || 30)))} />
            </label>
            <label>
              Prem stop %<Input type="number" min={5} max={50} value={premStopPct} onChange={(e) => setPremStopPct(Math.max(5, Math.min(50, Number(e.target.value) || 15)))} />
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
            <span className="text-muted-foreground">
              lots{data?.atm_lotsize ? ` × ${data.atm_lotsize}` : ''}
            </span>
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
        Levels refresh every 30s from the live chain; 15m state uses closed bars only.
        {data?.expiry && ` Expiry ${data.expiry}.`} Target = next OI level; SL = range reclaim.
      </p>
    </div>
  )
}
