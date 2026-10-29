/**
 * The video arm: the levels the YouTuber read out on 22 Oct for the 23 Oct
 * session, executed as-is.
 *
 * This is not a model tier. It exists to test his spoken levels against the
 * Sahasra panel's computed ones. His BankNifty plan is short-only: BUY PE on
 * a 15m rejection at 55,000, or on a 15m close below 54,000. Inside the
 * 54,000-55,000 range he has no trade, and above 55,000 he has no long.
 *
 * Nothing here triggers or exits automatically. The buttons place and close
 * a manual ATM leg tagged strategy=Video, and the state shown is advice, not
 * an order.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { lowIvApi } from '@/api/low-iv'
import { type SahasraLevels, sahasraApi } from '@/api/sahasra'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useMarketData } from '@/hooks/useMarketData'
import { showToast } from '@/utils/toast'

const VIDEO_ID = '6dDQJICrb0Y'
const PLAN_DATE = '2026-10-23'
const RESISTANCE = 55_000
const SUPPORT_1 = 54_500
const SUPPORT_2 = 54_000
const ENTRY_OPEN_MIN = 9 * 60 + 15
const ENTRY_CLOSE_MIN = 14 * 60 + 30

type PlanState = 'breakdown' | 'rejection' | 'above' | 'inside' | 'waiting'

const STATE_TEXT: Record<PlanState, { title: string; hint: string; trade: boolean }> = {
  breakdown: {
    title: 'Below 54,000',
    hint: 'Last closed 15m bar closed under the major support. His rule: BUY PE.',
    trade: true,
  },
  rejection: {
    title: 'Rejected 55,000',
    hint: 'Last closed 15m bar traded at 55,000 and closed back below. His rule: BUY PE.',
    trade: true,
  },
  above: {
    title: 'Above 55,000',
    hint: 'His BankNifty plan has no long here. No trade.',
    trade: false,
  },
  inside: {
    title: 'Inside 54,000–55,000',
    hint: 'His no-trade zone. Wait for a 15m close outside it.',
    trade: false,
  },
  waiting: {
    title: 'Waiting for a closed 15m bar',
    hint: 'The plan uses closed bars only; no signal from a forming bar or yesterday.',
    trade: false,
  },
}

interface VideoPos {
  symbol: string
  qty: number
  ts: number
}

function loadPos(): VideoPos | null {
  try {
    const raw = localStorage.getItem('video.pos')
    return raw ? (JSON.parse(raw) as VideoPos) : null
  } catch {
    return null
  }
}

function istDate(d = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(d)
}

function barDate(ts: number): string {
  const ms = ts > 1e12 ? ts : ts * 1000
  return istDate(new Date(ms))
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

function barTime(ts: number): string {
  const ms = ts > 1e12 ? ts : ts * 1000
  return new Date(ms).toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function planState(bar: SahasraLevels['bar'], today: string): PlanState {
  if (today < PLAN_DATE) return 'waiting'
  if (!bar || barDate(bar.ts) !== today) return 'waiting'
  if (bar.c < SUPPORT_2) return 'breakdown'
  if (bar.h >= RESISTANCE && bar.c < RESISTANCE) return 'rejection'
  if (bar.c >= RESISTANCE) return 'above'
  return 'inside'
}

const inr = (n: number) => n.toLocaleString('en-IN')

export function VideoSection() {
  const [lots, setLots] = useState(1)
  const [busy, setBusy] = useState(false)
  const [orderError, setOrderError] = useState<string | null>(null)
  const [pos, setPos] = useState<VideoPos | null>(loadPos)

  const signalQ = useQuery({
    queryKey: ['sahasra-signal', 'BANKNIFTY'],
    queryFn: () => sahasraApi.getSignal('BANKNIFTY'),
    refetchInterval: 30000,
  })
  const data = signalQ.data?.data ?? null

  const wsSymbols = [
    { symbol: 'BANKNIFTY', exchange: 'NSE_INDEX' },
    ...(data?.atm_pe_symbol ? [{ symbol: data.atm_pe_symbol, exchange: 'NFO' }] : []),
  ]
  const { data: ticks } = useMarketData({ symbols: wsSymbols, mode: 'LTP' })
  const tickOf = (symbol: string | null, exchange: string) =>
    symbol ? ticks.get(`${exchange}:${symbol}`)?.data?.ltp : undefined
  const spot = tickOf('BANKNIFTY', 'NSE_INDEX') ?? data?.spot ?? undefined
  const peLtp = tickOf(data?.atm_pe_symbol ?? null, 'NFO')

  useEffect(() => {
    if (pos) localStorage.setItem('video.pos', JSON.stringify(pos))
    else localStorage.removeItem('video.pos')
  }, [pos])

  const today = istDate()
  const stale = today > PLAN_DATE
  const planPending = today < PLAN_DATE
  const state = planState(data?.bar, today)
  const st = STATE_TEXT[state]
  const nowMin = istMinutes()
  const sessionOpen = today === PLAN_DATE && nowMin >= ENTRY_OPEN_MIN && nowMin <= ENTRY_CLOSE_MIN
  const shownTitle = planPending
    ? 'Plan starts 23 Oct'
    : today === PLAN_DATE && nowMin < ENTRY_OPEN_MIN
      ? 'Waiting for the 09:15 open'
      : st.title
  const shownHint = planPending
    ? 'Waiting for the first closed 15m bar of the 23 Oct session.'
    : today === PLAN_DATE && nowMin < ENTRY_OPEN_MIN
      ? 'Entries start at 09:15 IST.'
      : st.hint
  const lot = data?.atm_lotsize && data.atm_lotsize > 0 ? data.atm_lotsize : 30
  const qty = lots * lot

  const buyPe = async () => {
    const symbol = data?.atm_pe_symbol
    if (!symbol) {
      setOrderError('No ATM PE resolved yet — wait for the chain to load')
      return
    }
    setBusy(true)
    try {
      const res = await lowIvApi.placeOrder({
        symbol,
        exchange: 'NFO',
        action: 'BUY',
        product: 'MIS',
        quantity: qty,
        ltp: peLtp,
        strategy: 'Video',
      })
      if (res.status === 'success') {
        setOrderError(null)
        setPos({ symbol, qty, ts: Date.now() })
        showToast.success(`BUY ${symbol} x${qty} placed (Video)`)
      } else {
        setOrderError(`Order rejected: ${res.message ?? 'unknown reason'}`)
        showToast.error(res.message ?? 'Order failed')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Order failed'
      setOrderError(`Order failed: ${msg}`)
      showToast.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const sellPe = async () => {
    if (!pos) return
    setBusy(true)
    try {
      const res = await lowIvApi.placeOrder({
        symbol: pos.symbol,
        exchange: 'NFO',
        action: 'SELL',
        product: 'MIS',
        quantity: pos.qty,
        strategy: 'Video',
      })
      if (res.status === 'success') {
        setOrderError(null)
        setPos(null)
        showToast.success(`SELL ${pos.symbol} x${pos.qty} placed (Video)`)
      } else {
        setOrderError(`Exit rejected: ${res.message ?? 'unknown reason'}`)
        showToast.error(res.message ?? 'Exit failed')
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Exit failed'
      setOrderError(`Exit failed: ${msg}`)
      showToast.error(msg)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">Video plan · 23 Oct</CardTitle>
          <div className="flex items-center gap-1">
            {stale && <Badge variant="outline">stale</Badge>}
            <Badge variant="outline">{data?.mode ?? '—'}</Badge>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          <a
            className="underline underline-offset-2"
            href={`https://youtu.be/${VIDEO_ID}`}
            target="_blank"
            rel="noreferrer"
          >
            Sahasra video
          </a>{' '}
          levels, executed as-is. No auto-trigger, no auto-exit.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded-md border p-2">
            <div className="text-[11px] text-muted-foreground">Resistance</div>
            <div className="font-mono text-sm">{inr(RESISTANCE)}</div>
          </div>
          <div className="rounded-md border p-2">
            <div className="text-[11px] text-muted-foreground">Support 1</div>
            <div className="font-mono text-sm">{inr(SUPPORT_1)}</div>
          </div>
          <div className="rounded-md border p-2">
            <div className="text-[11px] text-muted-foreground">Support 2</div>
            <div className="font-mono text-sm">{inr(SUPPORT_2)}</div>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs">
          <span>
            Spot <span className="font-mono">{spot != null ? inr(Math.round(spot)) : '—'}</span>
          </span>
          <span>
            15m close{' '}
            <span className="font-mono">{data?.bar ? inr(Math.round(data.bar.c)) : '—'}</span>
            {data?.bar && <span className="text-muted-foreground"> · {barTime(data.bar.ts)}</span>}
          </span>
        </div>

        <div
          className={
            st.trade
              ? 'rounded-md border border-emerald-300 bg-emerald-50 p-2 text-xs text-emerald-900'
              : 'rounded-md border p-2 text-xs'
          }
        >
          <div className="font-medium">{shownTitle}</div>
          <div className="mt-0.5 opacity-80">{shownHint}</div>
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
            lots × {lot}
            {data?.atm_pe_symbol ? ` · ${data.atm_pe_symbol}` : ''}
            {peLtp != null ? ` @ ${peLtp.toFixed(2)}` : ''}
          </span>
        </div>

        <div className="flex gap-2">
          <Button
            className="flex-1"
            variant="destructive"
            disabled={busy || stale || planPending || !sessionOpen || !data?.atm_pe_symbol}
            onClick={buyPe}
          >
            BUY ATM PE
          </Button>
          <Button className="flex-1" variant="outline" disabled={busy || !pos} onClick={sellPe}>
            SELL / exit
          </Button>
        </div>

        {pos && (
          <div className="flex items-center justify-between rounded-md border p-2 text-xs">
            <span>
              Video open: <span className="font-mono">{pos.symbol}</span> × {pos.qty}
            </span>
            <button
              type="button"
              className="text-muted-foreground underline underline-offset-2"
              onClick={() => setPos(null)}
            >
              clear
            </button>
          </div>
        )}

        {orderError && (
          <div className="rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-800">
            {orderError}
          </div>
        )}

        {stale && (
          <div className="text-xs text-muted-foreground">
            This card is pinned to the 23 Oct plan. Update the levels before trading another
            session.
          </div>
        )}

        <p className="text-[11px] text-muted-foreground">
          Short-only: reject 55,000 or close below 54,000. Inside the range and above 55,000: no
          trade. Entries 09:15–14:30 IST; one position; flat by 15:20; 60m max hold (ours). Fill in{' '}
          {data?.mode ?? '—'} mode.
        </p>
      </CardContent>
    </Card>
  )
}
