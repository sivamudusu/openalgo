import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { lowIvApi, type LowIVSettings } from '@/api/low-iv'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useMarketData } from '@/hooks/useMarketData'
import { useOrderEventRefresh } from '@/hooks/useOrderEventRefresh'
import { showToast } from '@/utils/toast'
import { peerHoldsSymbol } from './strategyPeers'

const STRIKE_COUNT = 7
const AUTO_POS_KEY = 'lowiv.autoPos'
const AUTO_EXIT_KEY = 'lowiv.lastAutoExitTs'

interface LadderRow {
  strike: number
  ceSymbol: string | null
  ceLabel: string
  peSymbol: string | null
  peLabel: string
}

interface AutoPosition {
  symbol: string
  exchange: string
  product: string
  qty: number
  entry: number
  entryTs: number
  targetPct: number
  stopPct: number
  maxHoldMin: number
  /** True while entry is a WS-tick estimate awaiting the real fill price. */
  estimated: boolean
}

interface EntriesLog {
  date: string
  count: number
  symbols: string[]
}

const ENTRIES_KEY = 'lowiv.entries'

function todayStr(): string {
  return new Date().toLocaleDateString('en-CA')
}

function loadEntries(): EntriesLog {
  try {
    const raw = localStorage.getItem(ENTRIES_KEY)
    const parsed = raw ? (JSON.parse(raw) as EntriesLog) : null
    if (parsed && parsed.date === todayStr() && typeof parsed.count === 'number')
      return { date: parsed.date, count: parsed.count, symbols: parsed.symbols ?? [] }
  } catch {
    /* storage unavailable */
  }
  return { date: todayStr(), count: 0, symbols: [] }
}

function loadAutoPos(): AutoPosition | null {
  try {
    const raw = localStorage.getItem(AUTO_POS_KEY)
    return raw ? (JSON.parse(raw) as AutoPosition) : null
  } catch {
    return null
  }
}

function loadLastAutoExit(): number {
  try {
    return Number(localStorage.getItem(AUTO_EXIT_KEY) || 0)
  } catch {
    return 0
  }
}

function istHour(): number {
  return Number(
    new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Kolkata', hour: 'numeric', hour12: false }).format(
      new Date()
    )
  )
}

/** Map a raw order error to the action that fixes it. Shared with SahasraSection. */
export function hintFor(msg: string | null): string | null {
  if (!msg) return null
  const m = msg.toLowerCase()
  if (m.includes('invalid openalgo apikey') || m.includes('not authenticated') || m.includes('api key'))
    return 'Log into your broker first (Broker menu, then complete broker login). Sandbox mode still needs a broker session for login and live prices.'
  if (m.includes('unknown symbol') || m.includes('lot size unavailable') || m.includes('symbol info'))
    return 'Master contract is missing this symbol. Open Master Contract and download NFO contracts, then Refresh chain.'
  if (m.includes('freeze') || m.includes('whole number of lots'))
    return 'Quantity violates lot/freeze rules — reduce lots.'
  if (m.includes('pending') || m.includes('action center') || m.includes('semi_auto'))
    return 'Order went to Action Center for approval (semi-auto mode). Approve it there.'
  if (m.includes('fund') || m.includes('margin') || m.includes('insufficient'))
    return 'Insufficient sandbox funds — check Sandbox funds page.'
  if (m.includes('below floor'))
    return 'Premium is below your floor — the leg is too cheap/illiquid. Pick a closer-to-ATM strike or lower the floor in settings.'
  if (m.includes('spread') && m.includes('exceeds'))
    return 'Bid-ask spread is too wide — illiquid leg. Try the ATM strike or widen max spread in settings.'
  if (m.includes('quote') || m.includes('ltp') || m.includes('price'))
    return 'No live price for this contract — wait for the WebSocket feed to connect (check /websocket/test) or pick the ATM contract.'
  if (m.includes('day cap'))
    return 'Daily auto-entry cap reached. Raise it in settings or wait for tomorrow.'
  if (m.includes('profit lock'))
    return 'Profit lock engaged for today. No new auto entries until tomorrow.'
  return null
}

/**
 * Low-IV BANKNIFTY strategy section for the /trading sidebar signals panel.
 *
 * Same engine as the former standalone page (signal evaluation, page-auto
 * loop with book reconciliation, manual trading, settings) minus the chart —
 * the terminal already charts. Tightly packed: this lives in a 260-520px rail.
 */
export function LowIvSection() {
  const [expiry, setExpiry] = useState('')
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [selectedSide, setSelectedSide] = useState<'CE' | 'PE'>('CE')
  const [lots, setLots] = useState(1)
  const [product, setProduct] = useState('MIS')
  const [autoMode, setAutoMode] = useState(() => {
    try {
      return localStorage.getItem('lowiv.autoMode') === '1'
    } catch {
      return false
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem('lowiv.autoMode', autoMode ? '1' : '0')
    } catch {
      /* storage unavailable */
    }
  }, [autoMode])
  const belowCountRef = useRef(0)
  const [signalText, setSignalText] = useState('Waiting for chain data')
  const [signalFire, setSignalFire] = useState(false)
  const [bbText, setBbText] = useState('Bollinger: waiting for candles')
  const [pctB, setPctB] = useState<number | null>(null)
  const [orderError, setOrderError] = useState<string | null>(null)
  const [autoPos, setAutoPos] = useState<AutoPosition | null>(loadAutoPos)
  const [lastAutoExitTs, setLastAutoExitTs] = useState<number>(loadLastAutoExit)
  const [autoBusy, setAutoBusy] = useState(false)
  const [entriesLog, setEntriesLog] = useState<EntriesLog>(loadEntries)
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
      if (autoPos) localStorage.setItem(AUTO_POS_KEY, JSON.stringify(autoPos))
      else localStorage.removeItem(AUTO_POS_KEY)
    } catch {
      /* storage unavailable */
    }
  }, [autoPos])
  useEffect(() => {
    try {
      localStorage.setItem(AUTO_EXIT_KEY, String(lastAutoExitTs))
    } catch {
      /* storage unavailable */
    }
  }, [lastAutoExitTs])
  useEffect(() => {
    try {
      localStorage.setItem(ENTRIES_KEY, JSON.stringify(entriesLog))
    } catch {
      /* storage unavailable */
    }
  }, [entriesLog])
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
  const configQ = useQuery({ queryKey: ['lowiv-config'], queryFn: lowIvApi.getConfig })
  const cfg = configQ.data?.data
  const settingsQ = useQuery({ queryKey: ['lowiv-settings'], queryFn: lowIvApi.getSettings })
  const settings = settingsQ.data?.data
  const [draft, setDraft] = useState<Partial<LowIVSettings>>({})
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (settingsQ.data?.data) setDraft(settingsQ.data.data)
  }, [settingsQ.data])
  const foExchange = cfg?.fo_exchange ?? 'NFO'
  const indexExchange = cfg?.index_exchange ?? 'NSE_INDEX'
  const underlying = cfg?.underlying ?? 'BANKNIFTY'

  const expiryQ = useQuery({
    queryKey: ['lowiv-expiry'],
    queryFn: lowIvApi.getExpiry,
    enabled: !!cfg,
  })
  useEffect(() => {
    const list = expiryQ.data?.data ?? []
    if (list.length > 0 && !expiry) setExpiry(list[0])
  }, [expiryQ.data, expiry])

  const strikesQ = useQuery({
    queryKey: ['lowiv-strikes', expiry],
    queryFn: () => lowIvApi.getStrikes(expiry, STRIKE_COUNT, true),
    enabled: !!expiry,
    refetchInterval: 30000,
  })

  // %B timeframe: match this to your broker chart's interval when comparing
  // values — %B(20) on 1m vs 5m are completely different readings.
  const [bbTf, setBbTf] = useState(() => {
    try {
      const v = localStorage.getItem('lowiv.bbTf')
      return v === '5m' || v === '15m' ? v : '1m'
    } catch {
      return '1m'
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem('lowiv.bbTf', bbTf)
    } catch {
      /* storage unavailable */
    }
  }, [bbTf])
  const bbHistoryQ = useQuery({
    queryKey: ['lowiv-bb-history', underlying, indexExchange, bbTf],
    queryFn: () => lowIvApi.getHistory(underlying, indexExchange, bbTf),
    enabled: !!cfg,
    refetchInterval: 60000,
  })

  const ladder: LadderRow[] = useMemo(() => {
    const chain = strikesQ.data?.chain ?? []
    return chain.map((r) => ({
      strike: r.strike,
      ceSymbol: r.ce.symbol,
      ceLabel: r.ce.label,
      peSymbol: r.pe.symbol,
      peLabel: r.pe.label,
    }))
  }, [strikesQ.data])

  // Live WS ticks: spot + every ladder leg (structure from REST, prices live).
  const wsSymbols = useMemo(() => {
    const syms = [{ symbol: underlying, exchange: indexExchange }]
    for (const row of ladder) {
      if (row.ceSymbol) syms.push({ symbol: row.ceSymbol, exchange: foExchange })
      if (row.peSymbol) syms.push({ symbol: row.peSymbol, exchange: foExchange })
    }
    return syms
  }, [ladder, underlying, indexExchange, foExchange])
  const atmRow = useMemo(() => {
    const chain = strikesQ.data?.chain ?? []
    return chain.find((r) => r.ce.label === 'ATM') ?? chain[Math.floor(chain.length / 2)]
  }, [strikesQ.data])
  const atmCeSymbol = atmRow?.ce.symbol ?? null

  // Real book state for reconciliation: open qty + average FILL price + today's
  // realized P&L. Polled fast while a fill is confirming, slower otherwise.
  const refreshBook = useCallback(
    async (sym: string) => {
      try {
        const res = await lowIvApi.getAutoState(sym, foExchange, product, entriesLog.symbols)
        if (res.status === 'success') setAutoBook(res.data)
      } catch {
        /* keep last book; page keeps local tracking as fallback */
      }
    },
    [foExchange, product, entriesLog.symbols]
  )
  useEffect(() => {
    const sym = autoPos?.symbol ?? atmCeSymbol
    if (!sym || (!autoMode && !autoPos)) return
    refreshBook(sym)
    const interval = autoPos?.estimated ? 5000 : 15000
    const t = setInterval(() => refreshBook(sym), interval)
    return () => clearInterval(t)
  }, [autoPos?.symbol, autoPos?.estimated, atmCeSymbol, autoMode, autoPos, refreshBook])

  // Reconcile page tracking against the real book on every refresh.
  useEffect(() => {
    if (!autoPos || !autoBook) return
    if (autoBook.qty === 0) {
      // Fill still pending right after entry — give the engine room to fill.
      if (autoPos.estimated && Date.now() - autoPos.entryTs < 60000) return
      // Closed outside this page (manual SELL, expiry, another session):
      // clear WITHOUT sending another SELL (that would open a naked short).
      setAutoPos(null)
      setLastAutoExitTs(Date.now())
      showToast.info('Auto position is flat in the book — tracking cleared, no exit order sent')
      return
    }
    const updates: Partial<AutoPosition> = {}
    if (autoBook.qty !== autoPos.qty) updates.qty = autoBook.qty
    if (autoPos.estimated && autoBook.avg_price > 0) {
      updates.entry = autoBook.avg_price
      updates.estimated = false
    }
    if (Object.keys(updates).length > 0) setAutoPos({ ...autoPos, ...updates })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoBook])
  const { data: ticks } = useMarketData({ symbols: wsSymbols, mode: 'LTP' })
  const tickOf = (symbol: string, exchange: string) =>
    ticks.get(`${exchange}:${symbol}`)?.data?.ltp

  const spotLtp = tickOf(underlying, indexExchange) ?? strikesQ.data?.underlying_ltp
  const atmStrike = strikesQ.data?.atm_strike
  const atmIv = strikesQ.data?.atm_iv

  // Full-day IV window: every fresh chain reading is recorded server-side
  // (garbage prints like IV 0 are rejected there, never stored). Evaluation
  // ticks every 5s against the persistent 09:15→now series — no in-memory
  // deque, so reloads never reset the window.
  const latestIvRef = useRef<number | null>(null)
  const [ivSamples, setIvSamples] = useState<number | null>(null)
  const [ivPct, setIvPct] = useState<number | null>(null)
  const [evalTick, setEvalTick] = useState(0)
  useEffect(() => {
    if (atmIv == null || Number.isNaN(atmIv)) return
    latestIvRef.current = atmIv
    // ±3 replay ladder around ATM (7 strikes × CE/PE) rides along with the
    // ATM-mean sample; the live signal window ignores leg rows server-side.
    const chain = strikesQ.data?.chain ?? []
    const atmIdx = chain.findIndex((r) => r.ce.label === 'ATM')
    const legs =
      atmIdx >= 0
        ? chain.slice(Math.max(0, atmIdx - 3), atmIdx + 4).flatMap((r) =>
            (['CE', 'PE'] as const).map((side) => ({
              strike: r.strike,
              option_type: side,
              iv: (side === 'CE' ? r.ce : r.pe).implied_volatility ?? 0,
              bid: (side === 'CE' ? r.ce : r.pe).bid,
              ask: (side === 'CE' ? r.ce : r.pe).ask,
            }))
          )
        : []
    lowIvApi
      .recordIv(atmIv, { strike: atmStrike ?? null, bid: atmRow?.ce.bid, ask: atmRow?.ce.ask, legs })
      .then((res) => {
        if (res.status === 'success' && res.data) setIvSamples(res.data.samples_today)
      })
      .catch(() => {
        /* recording is best-effort; evaluation still works on stored series */
      })
  }, [atmIv])
  useEffect(() => {
    const t = setInterval(() => setEvalTick((n) => n + 1), 5000)
    return () => clearInterval(t)
  }, [])

  // %B closes: history candles at the selected timeframe, fused with the live
  // WS spot as the forming close — exactly what a live chart plots. History
  // bar times carry the +5h30m chart offset, so the current-bar comparison
  // uses the same shift.
  const BB_TF_SEC: Record<string, number> = { '1m': 60, '5m': 300, '15m': 900 }
  const bbCloses = useMemo(() => {
    const candles = bbHistoryQ.data?.candles ?? []
    const closes = candles.map((c) => c.close)
    const lastBarTime = candles.length > 0 ? candles[candles.length - 1].time : null
    const spot = tickOf(underlying, indexExchange)
    let live = false
    if (spot != null && closes.length > 0 && lastBarTime != null) {
      const tf = BB_TF_SEC[bbTf] ?? 60
      const currentBar = Math.floor((Date.now() / 1000 + 19800) / tf) * tf
      if (lastBarTime < currentBar) closes.push(spot)
      else closes[closes.length - 1] = spot
      live = true
    }
    return { closes, lastBarTime, live }
  }, [bbHistoryQ.data, bbTf, ticks, underlying, indexExchange])

  useEffect(() => {
    const threshold = settings?.iv_percentile_max ?? cfg?.iv_percentile_max ?? 25
    const cur = latestIvRef.current
    if (cur == null) {
      setSignalText('Waiting for chain data')
      setSignalFire(false)
      return
    }
    // Server ranks cur over today's persistent series (pure math, no broker call).
    const closes = bbCloses.closes
    const below = belowCountRef.current
    lowIvApi
      .evaluate(cur, [], below, closes)
      .then((res) => {
        if (res.status !== 'success') return
        const d = res.data
        if (d.iv_percentile == null) {
          belowCountRef.current = 0
          setIvPct(null)
          setSignalFire(false)
          setSignalText(d.reason)
          if (typeof d.samples_today === 'number') setIvSamples(d.samples_today)
          return
        }
        setIvPct(d.iv_percentile)
        if (typeof d.samples_today === 'number') setIvSamples(d.samples_today)
        if (d.iv_percentile < threshold) belowCountRef.current += 1
        else belowCountRef.current = 0
        const confirmed = belowCountRef.current
        setSignalFire(d.fire)
        setSignalText(d.fire ? `${d.reason} (${confirmed} consecutive)` : d.reason)
        const bb = d.bb
        setBbText(
          bb
            ? `Bollinger %B: ${bb.reason} (bottom=${String(bb.at_bottom)} rising=${String(bb.rising)})`
            : 'Bollinger %B: n/a'
        )
        setPctB(typeof bb?.pct_b === 'number' ? bb.pct_b : null)
      })
      .catch(() => {
        setSignalFire(false)
        setSignalText('Signal evaluation unavailable — retrying')
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evalTick])

  // Page-auto entry: fires a real BUY on the ATM CE when the combined signal
  // fires. Guarded by day-cap, profit-lock, cooldown, entry window, and the
  // real book (no doubling up on an already-open leg).
  useEffect(() => {
    if (!autoMode || !signalFire || autoPos || !settings || firingRef.current) return
    const sym = atmRow?.ce.symbol
    if (!atmRow || !sym) return
    if (peerHoldsSymbol(sym, 'lowiv.autoPos')) return // a peer loop holds this leg
    const dayCap = settings.max_entries_per_day ?? 0
    if (dayCap > 0 && entriesLog.count >= dayCap) {
      setOrderError(`Auto blocked: day cap ${entriesLog.count}/${dayCap} reached`)
      return
    }
    if (
      settings.profit_lock_enabled &&
      autoBook?.realized_supported &&
      autoBook.realized_today >= (settings.profit_lock_threshold ?? 4000)
    ) {
      setOrderError(
        `Auto blocked: profit lock — realized Rs ${autoBook.realized_today.toFixed(0)} >= ${settings.profit_lock_threshold}`
      )
      return
    }
    if (autoBook && autoBook.qty !== 0) return // already holding this leg — no double-up
    const cooldownMs = (settings.reentry_cooldown_min ?? 60) * 60000
    if (Date.now() - lastAutoExitTs < cooldownMs) return
    if (settings.max_entry_hour && istHour() >= settings.max_entry_hour) return
    const lot = atmRow.ce.lotsize ?? 30
    const qty = lots * lot
    const entryLtp = tickOf(sym, foExchange) ?? atmRow.ce.ltp ?? 0
    if (!entryLtp) {
      setOrderError('Auto blocked: no live price for the ATM CE yet — wait for the feed.')
      return
    }
    firingRef.current = true
    setAutoBusy(true)
    lowIvApi
      .placeOrder({
        symbol: sym,
        exchange: foExchange,
        action: 'BUY',
        product,
        quantity: qty,
        ltp: entryLtp,
        bid: atmRow.ce.bid,
        ask: atmRow.ce.ask,
        strategy: 'LowIV',
      })
      .then((res) => {
        if (res.status === 'success') {
          // Entry is a WS-tick ESTIMATE until the book confirms the fill price.
          setAutoPos({
            symbol: sym,
            exchange: foExchange,
            product,
            qty,
            entry: entryLtp,
            entryTs: Date.now(),
            targetPct: settings.exit_target_pct,
            stopPct: settings.exit_stop_pct,
            maxHoldMin: settings.exit_max_hold_min,
            estimated: true,
          })
          recordEntry(sym)
          setOrderError(null)
          showToast.success(`Auto BUY ${sym} x${qty} (fill confirming…)`)
          refreshBook(sym)
        } else {
          setOrderError(`Auto order rejected: ${res.message ?? 'unknown reason'}`)
        }
      })
      .catch((e) => {
        setOrderError(`Auto order failed: ${e instanceof Error ? e.message : 'unknown error'}`)
      })
      .finally(() => {
        firingRef.current = false
        setAutoBusy(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signalFire, autoMode, settings, strikesQ.data, lastAutoExitTs, autoPos])

  // Page-auto exit monitor: watches the open auto position on every tick and
  // flattens it at target / stop / max-hold. Exit quantity always comes from
  // the real book — never from page state — so a leg closed elsewhere can
  // never turn into a second SELL (naked short).
  useEffect(() => {
    if (!autoPos || closingRef.current) return
    if (autoPos.estimated) return // no verified fill yet — reconcile first
    const ltp = tickOf(autoPos.symbol, autoPos.exchange) ?? autoBook?.ltp ?? undefined
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
      setOrderError('Auto exit blocked: book shows a short leg — close it manually')
      return
    }
    if (autoBook && autoBook.qty === 0) return // reconcile effect clears tracking
    const exitQty = autoBook && autoBook.qty > 0 ? Math.min(autoPos.qty, autoBook.qty) : autoPos.qty
    if (exitQty <= 0) return
    // Throttle close attempts so a persistently failing exit retries every
    // few seconds instead of on every tick.
    if (Date.now() - lastCloseAttemptRef.current < 5000) return
    lastCloseAttemptRef.current = Date.now()
    closingRef.current = true
    setAutoBusy(true)
    lowIvApi
      .closeLeg({
        symbol: autoPos.symbol,
        exchange: autoPos.exchange,
        action: 'SELL',
        product: autoPos.product,
        quantity: exitQty,
        strategy: 'LowIV',
      })
      .then((res) => {
        if (res.status === 'success') {
          showToast.success(`Auto exit (${reason}) ${autoPos.symbol} @ ${ltp.toFixed(2)}`)
          setAutoPos(null)
          setLastAutoExitTs(Date.now())
          setOrderError(null)
        } else {
          setOrderError(`Auto exit rejected (${reason}): ${res.message ?? 'unknown reason'}`)
        }
      })
      .catch((e) => {
        setOrderError(`Auto exit failed (${reason}): ${e instanceof Error ? e.message : 'unknown error'}`)
      })
      .finally(() => {
        closingRef.current = false
        setAutoBusy(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticks, autoPos])

  useOrderEventRefresh(
    () => {
      strikesQ.refetch()
    },
    { events: ['order_event', 'analyzer_update', 'close_position_event'] }
  )

  const selectedLotsize = useMemo(() => {
    const chain = strikesQ.data?.chain ?? []
    for (const r of chain) {
      const leg = selectedSide === 'CE' ? r.ce : r.pe
      if (leg.symbol === selectedSymbol) return leg.lotsize ?? 30
    }
    return 30
  }, [strikesQ.data, selectedSymbol, selectedSide])

  const setDraftField = (k: keyof LowIVSettings, v: number | boolean) =>
    setDraft((d) => ({ ...d, [k]: v }))

  const saveSettings = async () => {
    setSaving(true)
    try {
      const res = await lowIvApi.saveSettings(draft)
      if (res.status === 'success' && res.data) {
        setDraft(res.data)
        settingsQ.refetch()
        showToast.success('Low-IV settings saved — exits, gates and engine follow them')
      } else {
        const errs = res.errors
          ? Object.entries(res.errors)
              .map(([k, v]) => `${k}: ${v}`)
              .join('; ')
          : (res.message ?? 'Save failed')
        showToast.error(errs)
      }
    } catch (e) {
      showToast.error(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const resetSettings = async () => {
    setSaving(true)
    try {
      const res = await lowIvApi.resetSettings()
      if (res.status === 'success') {
        setDraft(res.data)
        settingsQ.refetch()
        showToast.success('Low-IV settings reset to defaults (T4/S12/H15)')
      } else showToast.error('Reset failed')
    } catch (e) {
      showToast.error(e instanceof Error ? e.message : 'Reset failed')
    } finally {
      setSaving(false)
    }
  }

  const num = (v: number | undefined, fallback: number) => v ?? fallback
  const bool = (v: boolean | undefined, fallback: boolean) => v ?? fallback

  const placeManual = async (action: 'BUY' | 'SELL') => {
    if (!selectedSymbol) {
      setOrderError('Select an option contract first')
      showToast.error('Select an option contract first')
      return
    }
    try {
      const chain = strikesQ.data?.chain ?? []
      const selRow = chain.find((r) => r.ce.symbol === selectedSymbol || r.pe.symbol === selectedSymbol)
      const selLeg = selRow
        ? selRow.ce.symbol === selectedSymbol
          ? selRow.ce
          : selRow.pe
        : null
      const res = await lowIvApi.placeOrder({
        symbol: selectedSymbol,
        exchange: foExchange,
        action,
        product,
        quantity: lots * selectedLotsize,
        ltp: tickOf(selectedSymbol, foExchange),
        bid: selLeg?.bid,
        ask: selLeg?.ask,
        strategy: 'LowIV',
      })
      if (res.status === 'success') {
        setOrderError(null)
        showToast.success(`${action} ${selectedSymbol} placed`)
      } else {
        setOrderError(`Order rejected: ${res.message ?? 'unknown reason'}`)
        showToast.error(res.message ?? 'Order failed')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Order failed'
      setOrderError(`Order failed: ${msg}`)
      showToast.error(msg)
    }
  }

  const autoLtp = autoPos ? tickOf(autoPos.symbol, autoPos.exchange) : undefined
  const autoPnl =
    autoPos && autoLtp ? ((autoLtp - autoPos.entry) / autoPos.entry) * 100 : null
  const autoHeldMin = autoPos ? (Date.now() - autoPos.entryTs) / 60000 : null
  const orderHint = hintFor(orderError)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>{cfg?.mode === 'analyze' ? 'Sandbox' : 'Live'}</Badge>
        {ivPct != null && (
          <Badge variant={signalFire ? 'default' : 'secondary'}>
            IV %ile {ivPct.toFixed(1)} / {settings?.iv_percentile_max ?? cfg?.iv_percentile_max ?? 25}
            {ivSamples != null && ` · ${ivSamples} today`}
          </Badge>
        )}
        {atmStrike != null && <Badge variant="outline">ATM {atmStrike}</Badge>}
        {spotLtp != null && <Badge variant="outline">{Number(spotLtp).toFixed(1)}</Badge>}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Manual</span>
          <Switch checked={autoMode} onCheckedChange={setAutoMode} />
          <span className="text-xs text-muted-foreground">Auto</span>
        </div>
      </div>

      <p className="text-sm text-muted-foreground">{signalText}</p>
      <p className="text-sm text-muted-foreground">{bbText}</p>
      {pctB != null && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono">%B {pctB.toFixed(2)}</span>
          <div className="relative h-1.5 w-48 rounded bg-muted">
            <div className="absolute top-0 h-full w-px bg-muted-foreground" style={{ left: '50%' }} />
            <div
              className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary"
              style={{ left: `${Math.max(0, Math.min(1, pctB)) * 100}%` }}
            />
          </div>
          <span>0 oversold · 0.5 middle · 1.0 overbought</span>
          <Select value={bbTf} onValueChange={setBbTf}>
            <SelectTrigger className="h-7 w-20">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="1m">1m</SelectItem>
              <SelectItem value="5m">5m</SelectItem>
              <SelectItem value="15m">15m</SelectItem>
            </SelectContent>
          </Select>
          <span>
            {bbCloses.closes.length} {bbTf} closes{bbCloses.live ? ' + live' : ''}
            {bbCloses.lastBarTime != null &&
              ` · last bar ${new Date(bbCloses.lastBarTime * 1000).toISOString().slice(5, 16).replace('T', ' ')} IST`}
          </span>
        </div>
      )}
      {autoMode ? (
        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">
            Auto trading ON — page-auto {autoBusy ? '(working…)' : '(watching)'}.
            {autoPos ? (
              <span>
                {' '}Holding <span className="font-mono">{autoPos.symbol}</span> x{autoPos.qty} @{' '}
                {autoPos.estimated ? 'fill confirming…' : autoPos.entry.toFixed(2)}
                {autoPnl != null && !autoPos.estimated && (
                  <span className={autoPnl >= 0 ? 'text-green-600' : 'text-red-600'}>
                    {' '}({autoPnl >= 0 ? '+' : ''}{autoPnl.toFixed(2)}%)
                  </span>
                )}
                {autoHeldMin != null && <span> · {autoHeldMin.toFixed(1)}m held</span>}
                <span className="text-muted-foreground">
                  {' '}· exits T+{autoPos.targetPct}% / S-{autoPos.stopPct}% / {autoPos.maxHoldMin}m
                </span>
              </span>
            ) : (
              <span className="text-muted-foreground"> Flat — waiting for Low-IV + Bollinger signal.</span>
            )}
            <span className="text-muted-foreground">
              {' '}· entries today {entriesLog.count}
              {settings?.max_entries_per_day ? `/${settings.max_entries_per_day}` : ''}
              {autoBook && (
                <>
                  {' '}· realized Rs {autoBook.realized_today.toFixed(0)}
                  {!autoBook.realized_supported && ' (book only)'}
                </>
              )}
            </span>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            Trades from this open tab using the settings below (sandbox or live follows the global
            platform mode). For 24/7 automation use the /python engine instead — it reads the same
            settings from the server.
          </div>
        </div>
      ) : (
        autoPos && (
          <div className="rounded-md border p-3 text-sm">
            Auto trading is OFF but a page-auto position is still open:{' '}
            <span className="font-mono">{autoPos.symbol}</span> — turn Auto back on to resume exit
            monitoring, or close it with SELL above.
          </div>
        )
      )}

      <details className="rounded-md border">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium">
          Strategy settings — exits, gates, Bollinger
        </summary>
        <div className="space-y-4 px-3 pb-3">
          <div>
            <div className="mb-2 text-sm font-medium">Scoped exits (per-position)</div>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs">
                Target %<Input type="number" step="0.5" value={num(draft.exit_target_pct, 4)} onChange={(e) => setDraftField('exit_target_pct', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Stop %<Input type="number" step="0.5" value={num(draft.exit_stop_pct, 12)} onChange={(e) => setDraftField('exit_stop_pct', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Max hold (min)<Input type="number" value={num(draft.exit_max_hold_min, 15)} onChange={(e) => setDraftField('exit_max_hold_min', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Premium floor<Input type="number" value={num(draft.premium_floor, 50)} onChange={(e) => setDraftField('premium_floor', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Max spread (pts)<Input type="number" step="0.5" value={num(draft.max_spread_points, 10)} onChange={(e) => setDraftField('max_spread_points', Number(e.target.value))} />
              </label>
            </div>
          </div>
          <div>
            <div className="mb-2 text-sm font-medium">Entry gates</div>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs">
                IV %ile max<Input type="number" value={num(draft.iv_percentile_max, 25)} onChange={(e) => setDraftField('iv_percentile_max', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Debounce ticks<Input type="number" value={num(draft.debounce_ticks, 3)} onChange={(e) => setDraftField('debounce_ticks', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Cooldown (min)<Input type="number" value={num(draft.reentry_cooldown_min, 60)} onChange={(e) => setDraftField('reentry_cooldown_min', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                No entries at/after hour<Input type="number" value={num(draft.max_entry_hour, 14)} onChange={(e) => setDraftField('max_entry_hour', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Profit lock Rs<Input type="number" step="500" value={num(draft.profit_lock_threshold, 4000)} onChange={(e) => setDraftField('profit_lock_threshold', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Max entries/day (0=unlimited)<Input type="number" value={num(draft.max_entries_per_day, 0)} onChange={(e) => setDraftField('max_entries_per_day', Number(e.target.value))} />
              </label>
              <label className="flex items-end gap-2 text-xs">
                <Switch checked={bool(draft.profit_lock_enabled, true)} onCheckedChange={(v) => setDraftField('profit_lock_enabled', v)} /> Profit lock
              </label>
            </div>
          </div>
          <div>
            <div className="mb-2 text-sm font-medium">Bollinger confirmation (bottom + rising)</div>
            <div className="grid grid-cols-2 gap-3">
              <label className="flex items-end gap-2 text-xs">
                <Switch checked={bool(draft.bb_enabled, true)} onCheckedChange={(v) => setDraftField('bb_enabled', v)} /> Enabled
              </label>
              <label className="text-xs">
                Period<Input type="number" value={num(draft.bb_period, 20)} onChange={(e) => setDraftField('bb_period', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Std dev<Input type="number" step="0.1" value={num(draft.bb_std, 2)} onChange={(e) => setDraftField('bb_std', Number(e.target.value))} />
              </label>
              <label className="text-xs">
                Bottom %B (0=OS)<Input type="number" step="0.05" value={num(draft.bb_bottom_pctb, 0.2)} onChange={(e) => setDraftField('bb_bottom_pctb', Number(e.target.value))} />
              </label>
            </div>
          </div>
          <div className="flex gap-2">
            <Button onClick={saveSettings} disabled={saving}>Save settings</Button>
            <Button variant="outline" onClick={resetSettings} disabled={saving}>Reset to defaults</Button>
          </div>
        </div>
      </details>

      <Card>
        <CardHeader>
          <CardTitle>Manual trade</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
            <div className="text-sm">
              Selected: <span className="font-mono">{selectedSymbol ?? 'none'}</span> ({selectedSide})
            </div>
            <div className="flex gap-2">
              <Input
                type="number"
                min={1}
                max={20}
                value={lots}
                onChange={(e) => setLots(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
              />
              <Select value={product} onValueChange={setProduct}>
                <SelectTrigger className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="MIS">MIS</SelectItem>
                  <SelectItem value="NRML">NRML</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="text-xs text-muted-foreground">
              Qty {lots * selectedLotsize} (lot {selectedLotsize}) — same lots used for auto entries
            </div>
            <div className="flex gap-2">
              <Button className="flex-1" onClick={() => placeManual('BUY')} disabled={!selectedSymbol}>
                BUY
              </Button>
              <Button
                className="flex-1"
                variant="destructive"
                onClick={() => placeManual('SELL')}
                disabled={!selectedSymbol}
              >
                SELL
              </Button>
            </div>
            <Button
              variant="outline"
              className="w-full"
              onClick={async () => {
                const res = await lowIvApi.cancelAll()
                if (res.status === 'success') {
                  setOrderError(null)
                  showToast.success(res.message ?? 'Cancel-all sent')
                } else {
                  setOrderError(`Cancel-all rejected: ${res.message ?? 'unknown reason'}`)
                  showToast.error(res.message ?? 'Cancel-all failed')
                }
              }}
            >
              Cancel all orders
            </Button>
            {orderError && (
              <div className="rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-800">
                <div className="font-medium">{orderError}</div>
                {orderHint && <div className="mt-1">Fix: {orderHint}</div>}
              </div>
            )}
          </CardContent>
        </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-2">
          <CardTitle>Option contracts (ATM ±{STRIKE_COUNT})</CardTitle>
          <div className="flex gap-2">
            <Select value={expiry} onValueChange={setExpiry}>
              <SelectTrigger className="h-8 w-32">
                <SelectValue placeholder="Expiry" />
              </SelectTrigger>
              <SelectContent>
                {(expiryQ.data?.data ?? []).map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button size="sm" variant="outline" onClick={() => strikesQ.refetch()}>
              Refresh
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>CE LTP</TableHead>
                <TableHead>CE contract</TableHead>
                <TableHead>Strike</TableHead>
                <TableHead>PE contract</TableHead>
                <TableHead>PE LTP</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ladder.map((row) => (
                <TableRow key={row.strike}>
                  <TableCell>{row.ceSymbol ? (tickOf(row.ceSymbol, foExchange)?.toFixed(2) ?? '—') : '—'}</TableCell>
                  <TableCell>
                    {row.ceSymbol && (
                      <Button
                        size="sm"
                        variant={selectedSymbol === row.ceSymbol ? 'default' : 'outline'}
                        onClick={() => {
                          setSelectedSymbol(row.ceSymbol)
                          setSelectedSide('CE')
                        }}
                      >
                        {row.ceLabel}
                      </Button>
                    )}
                  </TableCell>
                  <TableCell className="font-semibold">{row.strike}</TableCell>
                  <TableCell>
                    {row.peSymbol && (
                      <Button
                        size="sm"
                        variant={selectedSymbol === row.peSymbol ? 'default' : 'outline'}
                        onClick={() => {
                          setSelectedSymbol(row.peSymbol)
                          setSelectedSide('PE')
                        }}
                      >
                        {row.peLabel}
                      </Button>
                    )}
                  </TableCell>
                  <TableCell>{row.peSymbol ? (tickOf(row.peSymbol, foExchange)?.toFixed(2) ?? '—') : '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
