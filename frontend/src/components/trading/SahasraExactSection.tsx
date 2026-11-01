/**
 * Sahasra exact: the assisted, manual panel.
 *
 * The level map comes from the previous session's end-of-day snapshot. The
 * signal waits for a closed 15-minute candle. Nothing here enters or exits by
 * itself; every order is a trader click through the shared NFO order path.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { type LowIVStrategyTag, lowIvApi } from '@/api/low-iv'
import {
  type SahasraExactBias,
  type SahasraExactConfidence,
  type SahasraExactLevel,
  type SahasraExactManualContext,
  type SahasraExactSignalState,
  type SahasraExactUnderlying,
  sahasraExactApi,
} from '@/api/sahasra-exact'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useMarketData } from '@/hooks/useMarketData'
import { showToast } from '@/utils/toast'
import { peerHoldsSymbol } from './strategyPeers'

const POS_KEYS = {
  BANKNIFTY: 'sahasra.exact.banknifty.pos',
  NIFTY: 'sahasra.exact.nifty.pos',
} as const

type PosKey = (typeof POS_KEYS)[keyof typeof POS_KEYS]

interface ExactPos {
  symbol: string
  exchange: string
  product: string
  qty: number
  entry: number
  ts: number
  side: 'CE' | 'PE'
  underlying: SahasraExactUnderlying
}

const STRATEGY: Record<SahasraExactUnderlying, LowIVStrategyTag> = {
  BANKNIFTY: 'SahasraExact',
  NIFTY: 'SahasraExactNifty',
}

const EMPTY_MANUAL: SahasraExactManualContext = {
  fed: '',
  inflation: '',
  geopolitics: '',
  india_yield: '',
}

const STATE_TEXT: Record<SahasraExactSignalState, { title: string; hint: string }> = {
  breakout: {
    title: 'Closed above resistance',
    hint: 'The model looks for a confirmed close above the wall, then ATM CE.',
  },
  breakdown: {
    title: 'Closed below support',
    hint: 'The model looks for a confirmed close below support, then ATM PE.',
  },
  rejection: {
    title: 'Rejected at resistance',
    hint: 'The wall held and the candle closed back below it. The model looks for ATM PE.',
  },
  bounce: {
    title: 'Held support',
    hint: 'Support was tagged and reclaimed. The model looks for ATM CE.',
  },
  inside_zone: {
    title: 'Inside the no-trade zone',
    hint: 'Wait for a closed 15-minute bar outside primary support and resistance.',
  },
  above_resistance: {
    title: 'Holding above resistance',
    hint: 'The breakout already happened. No fresh entry without a new close through a level.',
  },
  below_support: {
    title: 'Holding below support',
    hint: 'The breakdown already happened. No fresh entry without a new close through a level.',
  },
  no_levels: {
    title: 'Levels are incomplete',
    hint: 'Primary support or resistance is missing. Do not guess a level.',
  },
  warming_up: {
    title: 'Waiting for two closed 15-minute candles',
    hint: 'The forming candle is ignored, exactly as the model reads the chart.',
  },
}

function todayIst(): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
}

function loadPos(key: PosKey): ExactPos | null {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as ExactPos) : null
  } catch {
    return null
  }
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

function formatLakh(value: number | null | undefined): string {
  if (!value) return '0.00L'
  return `${(value / 100_000).toFixed(2)}L`
}

function formatChange(value: number | null | undefined): string {
  if (value === null || value === undefined) return ''
  const sign = value > 0 ? '+' : ''
  return `${sign}${formatLakh(value)}`
}

function alignmentText(bias: SahasraExactBias, side: 'CE' | 'PE' | null): string {
  if (bias === 'unset') return 'No bias recorded'
  if (bias === 'mixed') return 'Mixed context'
  if (!side) return 'No side to compare'
  if (bias === 'bullish' && side === 'CE') return 'Context aligned with CE'
  if (bias === 'bearish' && side === 'PE') return 'Context aligned with PE'
  return 'Context against the signal'
}

function LevelRow({
  item,
  side,
  change,
}: {
  item: SahasraExactLevel
  side: 'CE' | 'PE'
  change?: { ce: number; pe: number }
}) {
  const tagText = item.tags
    .filter((tag) => tag !== 'call_peak' && tag !== 'put_peak')
    .map((tag) =>
      tag === 'call_wall'
        ? 'call OI'
        : tag === 'put_wall'
          ? 'put OI'
          : tag === 'round_major'
            ? 'major round'
            : tag === 'round_minor'
              ? 'round'
              : tag
    )
    .join(' · ')
  return (
    <div className="flex items-center justify-between gap-2 border-b border-border/50 py-1 last:border-0">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-xs font-semibold">{formatNumber(item.strike)}</span>
          <span className="truncate text-[10px] text-muted-foreground">
            {item.label.replace('_', ' ')}
          </span>
        </div>
        <div className="truncate text-[10px] text-muted-foreground">{tagText || 'candidate'}</div>
      </div>
      <div className="shrink-0 text-right">
        <div className="font-mono text-[11px]">{formatLakh(item.oi)}</div>
        <div className="text-[10px] text-muted-foreground">
          {change === undefined ? 'OI chg —' : formatChange(change[side === 'CE' ? 'ce' : 'pe'])}
          {item.rejections > 0 ? ` · ${item.rejections} rej` : ''}
        </div>
      </div>
    </div>
  )
}

export function SahasraExactSection() {
  const [underlying, setUnderlying] = useState<SahasraExactUnderlying>('BANKNIFTY')
  const [lots, setLots] = useState(1)
  const [product, setProduct] = useState<'MIS' | 'NRML'>('MIS')
  const [busy, setBusy] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [pos, setPos] = useState<ExactPos | null>(() => loadPos(POS_KEYS.BANKNIFTY))
  const [manual, setManual] = useState<SahasraExactManualContext>(EMPTY_MANUAL)
  const [finalBias, setFinalBias] = useState<SahasraExactBias>('unset')
  const [confidence, setConfidence] = useState<SahasraExactConfidence>('low')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const loadedContext = useRef<string>('')
  const queryClient = useQueryClient()
  const today = todayIst()
  const posKey = POS_KEYS[underlying]

  const levelsQ = useQuery({
    queryKey: ['sahasra-exact-levels', underlying],
    queryFn: () => sahasraExactApi.getLevels(underlying),
    refetchInterval: 30_000,
    retry: 1,
  })
  const signalQ = useQuery({
    queryKey: ['sahasra-exact-signal', underlying],
    queryFn: () => sahasraExactApi.getSignal(underlying),
    refetchInterval: 30_000,
    retry: 1,
  })
  const contextQ = useQuery({
    queryKey: ['sahasra-exact-context', today],
    queryFn: () => sahasraExactApi.getContext(today),
    refetchInterval: 300_000,
    retry: 1,
  })
  const statusQ = useQuery({
    queryKey: ['sahasra-exact-status'],
    queryFn: () => sahasraExactApi.getSnapshotStatus(),
    refetchInterval: 120_000,
    retry: 1,
  })

  const levels = levelsQ.data?.data ?? signalQ.data?.data ?? null
  const signal = signalQ.data?.data ?? null
  const context = contextQ.data?.data ?? null

  useEffect(() => {
    if (!context) return
    if (loadedContext.current === context.trade_date) return
    loadedContext.current = context.trade_date
    setManual(context.manual ?? EMPTY_MANUAL)
    setFinalBias(context.final_bias)
    setConfidence(context.confidence)
    setNotes(context.notes)
  }, [context])

  useEffect(() => {
    setPos(loadPos(posKey))
  }, [posKey])

  useEffect(() => {
    try {
      if (pos) localStorage.setItem(posKey, JSON.stringify(pos))
      else localStorage.removeItem(posKey)
    } catch {
      /* storage unavailable */
    }
  }, [pos, posKey])

  const wsSymbols = useMemo(
    () => [
      { symbol: underlying, exchange: 'NSE_INDEX' },
      ...(levels?.atm_ce_symbol ? [{ symbol: levels.atm_ce_symbol, exchange: 'NFO' }] : []),
      ...(levels?.atm_pe_symbol ? [{ symbol: levels.atm_pe_symbol, exchange: 'NFO' }] : []),
    ],
    [underlying, levels?.atm_ce_symbol, levels?.atm_pe_symbol]
  )
  const { data: ticks } = useMarketData({ symbols: wsSymbols, mode: 'LTP' })
  const tickOf = (symbol: string | null, exchange: string) =>
    symbol ? ticks.get(`${exchange}:${symbol}`)?.data?.ltp : undefined
  const spot = tickOf(underlying, 'NSE_INDEX') ?? levels?.spot ?? null
  const ceLtp = tickOf(levels?.atm_ce_symbol ?? null, 'NFO')
  const peLtp = tickOf(levels?.atm_pe_symbol ?? null, 'NFO')

  const switchUnderlying = (next: SahasraExactUnderlying) => {
    setUnderlying(next)
    setPos(loadPos(POS_KEYS[next]))
  }

  const saveContext = async () => {
    setSaving(true)
    try {
      const response = await sahasraExactApi.saveContext({
        trade_date: today,
        manual,
        final_bias: finalBias,
        confidence,
        notes,
      })
      if (response.status === 'success') {
        queryClient.setQueryData(['sahasra-exact-context', today], response)
        showToast.success('Pre-open context saved')
      } else {
        showToast.error(response.message ?? 'Context was not saved')
      }
    } catch {
      showToast.error('The pre-open context was not saved. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const capture = async () => {
    setCapturing(true)
    try {
      const response = await sahasraExactApi.captureSnapshot(underlying)
      if (response.status === 'success' || response.status === 'skipped') {
        showToast.success(
          response.status === 'skipped'
            ? 'A snapshot already exists for today'
            : response.source === 'manual'
              ? 'Manual snapshot saved. It is not used as EOD until after 15:45.'
              : 'Snapshot captured'
        )
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['sahasra-exact-levels'] }),
          queryClient.invalidateQueries({ queryKey: ['sahasra-exact-signal'] }),
          queryClient.invalidateQueries({ queryKey: ['sahasra-exact-status'] }),
        ])
      } else {
        showToast.error(response.message ?? 'Snapshot capture failed')
      }
    } catch {
      showToast.error('The snapshot was not captured. Try again.')
    } finally {
      setCapturing(false)
    }
  }

  const buy = async (side: 'CE' | 'PE') => {
    if (!signal) {
      showToast.error('The signal is not ready yet.')
      return
    }
    if (!signal.entry_window_open) {
      showToast.error('The entry window is closed. No new entry after 14:30 IST.')
      return
    }
    if (pos) {
      showToast.error('Exit the current position before opening another.')
      return
    }
    const symbol = side === 'CE' ? levels?.atm_ce_symbol : levels?.atm_pe_symbol
    const ltp = side === 'CE' ? ceLtp : peLtp
    if (!symbol || !levels) {
      showToast.error('The ATM contract is not resolved yet')
      return
    }
    if (peerHoldsSymbol(symbol, posKey)) {
      showToast.error('Another panel already holds this contract')
      return
    }
    const lot = Number(levels.atm_lotsize ?? 0)
    if (!lot || lot <= 0) {
      showToast.error('The contract lot size is unavailable')
      return
    }
    setBusy(true)
    try {
      const response = await lowIvApi.placeOrder({
        symbol,
        exchange: 'NFO',
        action: 'BUY',
        product,
        quantity: lots * lot,
        ltp,
        strategy: STRATEGY[underlying],
      })
      if (response.status === 'success') {
        setPos({
          symbol,
          exchange: 'NFO',
          product,
          qty: lots * lot,
          entry: ltp ?? 0,
          ts: Date.now(),
          side,
          underlying,
        })
        showToast.success(`BUY ${symbol} x${lots * lot} placed`)
      } else {
        showToast.error(response.message ?? 'Order rejected')
      }
    } catch {
      showToast.error('The order was not placed. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const exit = async () => {
    if (!pos) return
    setBusy(true)
    try {
      const response = await lowIvApi.closeLeg({
        symbol: pos.symbol,
        exchange: pos.exchange,
        action: 'SELL',
        product: pos.product,
        quantity: pos.qty,
        strategy: STRATEGY[pos.underlying],
      })
      if (response.status === 'success') {
        setPos(null)
        showToast.success(`SELL ${pos.symbol} x${pos.qty} placed`)
      } else {
        showToast.error(response.message ?? 'Exit rejected')
      }
    } catch {
      showToast.error('The exit order was not placed. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const state = signal?.state ?? 'warming_up'
  const st = STATE_TEXT[state]
  const resistanceRows = [...(levels?.resistances ?? [])].reverse()
  const supportRows = [...(levels?.supports ?? [])].reverse()
  const canBuy = Boolean(
    signal?.entry_window_open &&
      signal.side &&
      signal.confirmed &&
      signal.trade?.tradeable &&
      !pos &&
      !busy
  )
  const sourceText = !levels
    ? '—'
    : levels.source === 'eod_snapshot'
      ? `EOD ${levels.snapshot_date ?? ''}`.trim()
      : 'live provisional'
  const recent = statusQ.data?.data?.snapshots?.slice(0, 4) ?? []

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">Sahasra exact</CardTitle>
          <div className="flex items-center gap-1">
            <Badge variant="outline">{levels?.mode ?? '—'}</Badge>
            <Badge variant={levels?.source === 'eod_snapshot' ? 'secondary' : 'destructive'}>
              {sourceText}
            </Badge>
          </div>
        </div>
        <div className="flex items-center justify-between gap-2">
          <div className="flex gap-1">
            {(['BANKNIFTY', 'NIFTY'] as const).map((item) => (
              <Button
                key={item}
                size="sm"
                variant={underlying === item ? 'default' : 'outline'}
                onClick={() => switchUnderlying(item)}
              >
                {item}
              </Button>
            ))}
          </div>
          <div className="text-[10px] text-muted-foreground">
            {levels?.expiry ? `Expiry ${levels.expiry}` : 'Expiry —'} · ATM{' '}
            {formatNumber(levels?.atm_strike)}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {levels?.warning && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
            {levels.warning}
          </div>
        )}
        {levels?.levels_warning && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
            {levels.levels_warning}
          </div>
        )}

        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded-md border p-2">
            <div className="text-[10px] text-muted-foreground">Spot</div>
            <div className="font-mono text-sm font-semibold">{formatNumber(spot)}</div>
          </div>
          <div className="rounded-md border p-2">
            <div className="text-[10px] text-muted-foreground">PCR</div>
            <div className="font-mono text-sm font-semibold">{levels?.pcr?.toFixed(3) ?? '—'}</div>
            <div className="text-[10px] text-muted-foreground">{levels?.regime ?? 'unknown'}</div>
          </div>
          <div className="rounded-md border p-2">
            <div className="text-[10px] text-muted-foreground">Coverage</div>
            <div className="text-xs font-semibold">{levels?.coverage ?? '—'}</div>
            <div className="text-[10px] text-muted-foreground">
              {levels?.snapshot_age_days !== null && levels?.snapshot_age_days !== undefined
                ? `${levels.snapshot_age_days}d old`
                : 'no snapshot'}
            </div>
          </div>
        </div>

        <div className="rounded-md border p-2">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold">Pre-open context</span>
            <Badge variant="outline">{alignmentText(finalBias, signal?.side ?? null)}</Badge>
          </div>
          <div className="grid grid-cols-2 gap-1">
            {(context?.auto_cues ?? []).map((cue) => (
              <div key={cue.key} className="flex items-center justify-between gap-1 text-[10px]">
                <span className="truncate text-muted-foreground">{cue.label}</span>
                <span className="font-mono">
                  {cue.status === 'available' ? formatNumber(cue.value) : 'unavailable'}
                  {cue.status === 'available' && cue.change_pct !== null
                    ? ` (${cue.change_pct > 0 ? '+' : ''}${cue.change_pct}%)`
                    : ''}
                </span>
              </div>
            ))}
            {!context?.auto_cues?.length && (
              <div className="col-span-2 text-[10px] text-muted-foreground">
                Global quotes are unavailable. Use the manual fields.
              </div>
            )}
          </div>
          <div className="mt-2 grid grid-cols-3 gap-1">
            <select
              aria-label="Fed tone"
              className="h-8 rounded-md border bg-background px-1 text-[10px]"
              value={manual.fed}
              onChange={(event) =>
                setManual({
                  ...manual,
                  fed: event.target.value as SahasraExactManualContext['fed'],
                })
              }
            >
              <option value="">Fed tone</option>
              <option value="dovish">Dovish</option>
              <option value="neutral">Neutral</option>
              <option value="hawkish">Hawkish</option>
            </select>
            <select
              aria-label="Inflation"
              className="h-8 rounded-md border bg-background px-1 text-[10px]"
              value={manual.inflation}
              onChange={(event) =>
                setManual({
                  ...manual,
                  inflation: event.target.value as SahasraExactManualContext['inflation'],
                })
              }
            >
              <option value="">Inflation</option>
              <option value="cool">Cool</option>
              <option value="in_line">In line</option>
              <option value="hot">Hot</option>
            </select>
            <select
              aria-label="Geopolitics"
              className="h-8 rounded-md border bg-background px-1 text-[10px]"
              value={manual.geopolitics}
              onChange={(event) =>
                setManual({
                  ...manual,
                  geopolitics: event.target.value as SahasraExactManualContext['geopolitics'],
                })
              }
            >
              <option value="">Geopolitics</option>
              <option value="risk_on">Risk on</option>
              <option value="neutral">Neutral</option>
              <option value="risk_off">Risk off</option>
            </select>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-1">
            <Input
              className="h-8 text-[10px]"
              placeholder="India 10Y yield"
              value={manual.india_yield}
              onChange={(event) => setManual({ ...manual, india_yield: event.target.value })}
            />
            <select
              aria-label="Final bias"
              className="h-8 rounded-md border bg-background px-1 text-[10px]"
              value={finalBias}
              onChange={(event) => setFinalBias(event.target.value as SahasraExactBias)}
            >
              <option value="unset">Bias unset</option>
              <option value="bullish">Bullish</option>
              <option value="bearish">Bearish</option>
              <option value="mixed">Mixed</option>
            </select>
          </div>
          <div className="mt-1 flex gap-1">
            <Input
              className="h-8 text-[10px]"
              placeholder="Context note"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
            <select
              aria-label="Context confidence"
              className="h-8 rounded-md border bg-background px-1 text-[10px]"
              value={confidence}
              onChange={(event) => setConfidence(event.target.value as SahasraExactConfidence)}
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
            <Button size="sm" variant="outline" onClick={saveContext} disabled={saving}>
              Save
            </Button>
          </div>
        </div>

        <div className="rounded-md border p-2">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-semibold">Level map</span>
            <span className="text-[10px] text-muted-foreground">
              {levels?.source === 'eod_snapshot' ? 'previous close' : 'live chain'}
            </span>
          </div>
          {resistanceRows.map((item) => (
            <LevelRow
              key={`r-${item.strike}`}
              item={item}
              side="CE"
              change={levels?.oi_change?.[String(item.strike)]}
            />
          ))}
          <div className="my-1 border-y border-dashed py-1 text-center font-mono text-xs font-bold">
            {formatNumber(spot)} · spot
          </div>
          {supportRows.map((item) => (
            <LevelRow
              key={`s-${item.strike}`}
              item={item}
              side="PE"
              change={levels?.oi_change?.[String(item.strike)]}
            />
          ))}
          <div className="mt-2 text-[10px] text-muted-foreground">
            {levels?.no_trade_zone
              ? `No trade between ${formatNumber(levels.no_trade_zone.low)} and ${formatNumber(
                  levels.no_trade_zone.high
                )}.`
              : 'A two-sided no-trade zone is not available.'}
          </div>
        </div>

        <div className="rounded-md border p-2">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-xs font-semibold">{st.title}</div>
              <div className="text-[10px] text-muted-foreground">{signal?.message ?? st.hint}</div>
            </div>
            <Badge variant={signal?.confirmed ? 'default' : 'outline'}>
              {signal?.confirmed ? 'confirmed' : 'waiting'}
            </Badge>
          </div>
          {signalQ.isError && (
            <div className="mt-2 rounded border border-amber-500/40 bg-amber-500/10 p-2 text-[10px]">
              The broker's 15-minute history is unavailable right now. Levels are still shown.
            </div>
          )}
          {signal?.confirmation && (
            <div className="mt-2 space-y-0.5 text-[10px] text-muted-foreground">
              <div>{signal.confirmation.candle_text}</div>
              <div>{signal.confirmation.volume_text}</div>
            </div>
          )}
          {signal?.trade && (
            <div className="mt-2 grid grid-cols-3 gap-1 text-[10px]">
              <div className="rounded border p-1">
                Stop
                <div className="font-mono">{formatNumber(signal.trade.stop_level)}</div>
              </div>
              <div className="rounded border p-1">
                Target 1<div className="font-mono">{formatNumber(signal.trade.target_1)}</div>
              </div>
              <div className="rounded border p-1">
                Target 2<div className="font-mono">{formatNumber(signal.trade.target_2)}</div>
              </div>
            </div>
          )}
          {signal?.trade?.reason && (
            <div className="mt-1 text-[10px] text-amber-600">{signal.trade.reason}</div>
          )}
          <div className="mt-2 flex items-center gap-1">
            <Input
              className="h-8 w-16 text-xs"
              type="number"
              min={1}
              value={lots}
              onChange={(event) => setLots(Math.max(1, Number(event.target.value) || 1))}
            />
            <select
              aria-label="Product"
              className="h-8 rounded-md border bg-background px-1 text-[10px]"
              value={product}
              onChange={(event) => setProduct(event.target.value as 'MIS' | 'NRML')}
            >
              <option value="MIS">MIS</option>
              <option value="NRML">NRML</option>
            </select>
            <Button size="sm" onClick={() => buy('CE')} disabled={!canBuy || signal?.side !== 'CE'}>
              BUY ATM CE
            </Button>
            <Button size="sm" onClick={() => buy('PE')} disabled={!canBuy || signal?.side !== 'PE'}>
              BUY ATM PE
            </Button>
          </div>
          <div className="mt-1 text-[10px] text-muted-foreground">
            {signal?.entry_window_open ? 'Entry window is open.' : 'Entry window is closed.'}{' '}
            {signal?.flat_reminder ? 'Flat-by-15:20 reminder is active.' : ''}
          </div>
        </div>

        {pos && (
          <div className="rounded-md border border-primary/30 p-2">
            <div className="flex items-center justify-between gap-2">
              <div>
                <div className="text-xs font-semibold">Manual position</div>
                <div className="font-mono text-[10px]">
                  {pos.symbol} · {pos.qty} · {pos.product}
                </div>
              </div>
              <Button size="sm" variant="destructive" onClick={exit} disabled={busy}>
                Exit
              </Button>
            </div>
          </div>
        )}

        <div className="rounded-md border p-2">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="text-xs font-semibold">Snapshot</div>
              <div className="text-[10px] text-muted-foreground">
                {statusQ.data?.data?.scheduler.next_run ?? '15:45 IST on trading days'}
              </div>
            </div>
            <Button size="sm" variant="outline" onClick={capture} disabled={capturing}>
              {capturing ? 'Capturing…' : 'Capture now'}
            </Button>
          </div>
          <div className="mt-1 space-y-0.5">
            {recent.map((row) => (
              <div key={row.id} className="flex justify-between text-[10px] text-muted-foreground">
                <span>
                  {row.trade_date} · {row.underlying} · {row.expiry}
                </span>
                <span>
                  {row.status} · {row.source} · PCR {row.pcr?.toFixed(3) ?? '—'}
                </span>
              </div>
            ))}
            {!recent.length && (
              <div className="text-[10px] text-muted-foreground">No snapshots yet.</div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
