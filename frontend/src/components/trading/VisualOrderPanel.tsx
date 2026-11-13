import { useEffect, useMemo, useRef, useState } from 'react'
import { scalpingApi } from '@/api/scalping'
import { type VisualBracketApiRow, visualBracketApi } from '@/api/visualBracket'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useOptionChainLive } from '@/hooks/useOptionChainLive'
import {
  biasFor,
  defaultLevels,
  estimateCurrencyPnl,
  nearestStrike,
  riskReward,
  statusLabel,
  strikeForSelection,
  type VisualBracketAction,
  type VisualBracketOptionType,
  type VisualBracketStrikeSelection,
  type VisualBracketView,
} from '@/lib/trading/visualBracket'
import { cn } from '@/lib/utils'

const STRIKE_OPTIONS: Array<{ value: VisualBracketStrikeSelection; label: string }> = [
  { value: 'ATM', label: 'ATM' },
  { value: 'ITM1', label: 'ITM 1' },
  { value: 'ITM2', label: 'ITM 2' },
  { value: 'OTM1', label: 'OTM 1' },
  { value: 'OTM2', label: 'OTM 2' },
  { value: 'CUSTOM', label: 'Custom' },
]

interface Props {
  apiKey: string
  underlying: string
  underlyingExchange: string
  foExchange: string
  clickPrice: number
  anchor: { x: number; y: number }
  staged: VisualBracketView | null
  onStage: (state: VisualBracketView | null) => void
  onClose: () => void
  onPlaced: (row: VisualBracketApiRow) => void
  onToast: (message: string, kind: 'ok' | 'err' | 'info') => void
}

function money(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `₹${Math.round(value).toLocaleString('en-IN')}`
}

export function VisualOrderPanel({
  apiKey,
  underlying,
  underlyingExchange,
  foExchange,
  clickPrice,
  anchor,
  staged,
  onStage,
  onClose,
  onPlaced,
  onToast,
}: Props) {
  const [expiries, setExpiries] = useState<string[]>([])
  const [expiry, setExpiry] = useState('')
  const [action, setAction] = useState<VisualBracketAction>('BUY')
  const [optionType, setOptionType] = useState<VisualBracketOptionType>('CE')
  const [selection, setSelection] = useState<VisualBracketStrikeSelection>('ATM')
  const [customStrike, setCustomStrike] = useState('')
  const [lots, setLots] = useState(1)
  const [placing, setPlacing] = useState(false)
  const stagedRef = useRef(staged)
  stagedRef.current = staged

  useEffect(() => {
    let alive = true
    setExpiry('')
    setExpiries([])
    void (async () => {
      try {
        const response = await scalpingApi.getExpiry(underlying, foExchange, 'options')
        if (!alive) return
        const dates = response.data ?? []
        setExpiries(dates)
        setExpiry(dates[0] ?? '')
      } catch {
        if (!alive) return
        setExpiries([])
      }
    })()
    return () => {
      alive = false
    }
  }, [underlying, foExchange])

  const {
    data: chain,
    isLoading,
    error: chainError,
  } = useOptionChainLive(apiKey, underlying, foExchange, foExchange, expiry, 10, {
    enabled: Boolean(expiry),
    oiRefreshInterval: 30000,
    pauseWhenHidden: true,
  })

  const strikes = useMemo(() => chain?.chain.map((row) => row.strike) ?? [], [chain])
  const atm = nearestStrike(clickPrice, strikes)
  const selectedStrike =
    selection === 'CUSTOM'
      ? Number(customStrike) || null
      : strikeForSelection(strikes, atm, selection, optionType)
  const chainRow = chain?.chain.find((row) => row.strike === selectedStrike) ?? null
  const leg = optionType === 'CE' ? chainRow?.ce : chainRow?.pe
  const bias = biasFor(action, optionType)
  const strikeStep =
    strikes.length > 1
      ? Math.abs(strikes[1] - strikes[0])
      : Math.max(1, Math.round(clickPrice / 500))
  const lotSize = leg?.lotsize ?? 1
  const quantity = Math.max(1, lots) * lotSize

  // The staged levels are owned by the pane, so a drag on the chart survives a
  // selector change. Recompute them only when the bias or the entry changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: stagedRef is read deliberately; adding `staged` would make this effect fight the drag callback that writes it
  useEffect(() => {
    if (!leg || !selectedStrike || !Number.isFinite(selectedStrike)) {
      onStage(null)
      return
    }
    const previous = stagedRef.current
    const levels =
      previous && previous.bias === bias && previous.entryReference === clickPrice
        ? {
            entryReference: previous.entryReference,
            targetPrice: previous.targetPrice,
            stopPrice: previous.stopPrice,
          }
        : defaultLevels(bias, clickPrice, strikeStep)
    onStage({
      status: 'staged',
      underlying,
      underlyingExchange,
      foExchange,
      action,
      optionType,
      bias,
      entryReference: levels.entryReference,
      targetPrice: levels.targetPrice,
      stopPrice: levels.stopPrice,
      clickPrice,
      tickSize: 0.05,
      lotSize,
      lots: Math.max(1, lots),
      quantity,
      contractSymbol: leg.symbol,
      contractExchange: foExchange,
      expiry,
      strike: selectedStrike,
      targetOffset: Math.abs(levels.targetPrice - levels.entryReference),
      stopOffset: Math.abs(levels.entryReference - levels.stopPrice),
      riskReward: riskReward(levels.entryReference, levels.targetPrice, levels.stopPrice),
    })
  }, [
    action,
    bias,
    clickPrice,
    expiry,
    foExchange,
    leg?.symbol,
    lotSize,
    lots,
    onStage,
    optionType,
    quantity,
    selectedStrike,
    strikeStep,
    underlying,
    underlyingExchange,
  ])

  const levels = staged
    ? {
        entry: staged.entryReference,
        target: staged.targetPrice,
        stop: staged.stopPrice,
        rr: staged.riskReward,
      }
    : null
  const delta = leg?.delta ?? null
  const estimatedProfit = staged
    ? estimateCurrencyPnl(
        delta,
        staged.targetPrice - staged.entryReference,
        staged.quantity,
        staged.action
      )
    : null
  const estimatedLoss = staged
    ? estimateCurrencyPnl(
        delta,
        staged.stopPrice - staged.entryReference,
        staged.quantity,
        staged.action
      )
    : null

  const strikeListed = selectedStrike != null && strikes.includes(selectedStrike)
  const unavailable =
    !expiry || isLoading || Boolean(chainError) || strikes.length === 0 || !strikeListed

  const place = async () => {
    if (!staged || !staged.contractSymbol || !staged.strike || !staged.expiry) return
    setPlacing(true)
    try {
      const row = await visualBracketApi.place({
        underlying: staged.underlying,
        index_exchange: staged.underlyingExchange,
        contract_symbol: staged.contractSymbol,
        contract_exchange: staged.contractExchange ?? foExchange,
        option_type: staged.optionType,
        action: staged.action,
        product: 'MIS',
        quantity: staged.quantity,
        lots: staged.lots,
        click_price: staged.clickPrice,
        entry_reference: staged.entryReference,
        target_price: staged.targetPrice,
        stop_price: staged.stopPrice,
        execution_mode: 'SYNTHETIC_UNDERLYING_TRIGGER',
        entry_order_type: 'MARKET',
        expiry: staged.expiry,
        strike: staged.strike,
        strike_selection: selection,
        client_token: crypto.randomUUID(),
      })
      onPlaced(row)
      onToast('Visual order placed.', 'ok')
      onClose()
    } catch (error) {
      onToast(
        error instanceof Error ? error.message : 'The visual order could not be placed.',
        'err'
      )
    } finally {
      setPlacing(false)
    }
  }

  return (
    <div
      className="absolute z-30 w-[19rem] rounded-lg border border-border bg-background/95 p-3 shadow-xl backdrop-blur"
      style={{
        left: Math.max(8, Math.min(anchor.x, 640)),
        top: Math.max(8, Math.min(anchor.y, 420)),
      }}
      role="dialog"
      aria-label="Visual order"
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Visual order
          </div>
          <div className="text-sm font-medium">
            {underlying} · {clickPrice.toFixed(2)}
          </div>
        </div>
        <Button variant="ghost" size="sm" className="h-7 px-2" onClick={onClose}>
          Cancel
        </Button>
      </div>

      <div className="mt-3 grid grid-cols-4 gap-1">
        {(
          [
            ['BUY', 'CE', 'Buy CE'],
            ['BUY', 'PE', 'Buy PE'],
            ['SELL', 'CE', 'Sell CE'],
            ['SELL', 'PE', 'Sell PE'],
          ] as const
        ).map(([nextAction, nextType, label]) => (
          <Button
            key={label}
            type="button"
            size="sm"
            variant={action === nextAction && optionType === nextType ? 'default' : 'outline'}
            className={cn(
              'h-8 px-1 text-[11px]',
              action === nextAction &&
                optionType === nextType &&
                (nextAction === 'BUY'
                  ? 'bg-emerald-600 hover:bg-emerald-600'
                  : 'bg-rose-600 hover:bg-rose-600')
            )}
            onClick={() => {
              setAction(nextAction)
              setOptionType(nextType)
            }}
          >
            {label}
          </Button>
        ))}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <label className="text-[11px] text-muted-foreground">
          Expiry
          <select
            className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-xs text-foreground"
            value={expiry}
            onChange={(event) => setExpiry(event.target.value)}
          >
            {expiries.map((date) => (
              <option key={date} value={date}>
                {date}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-muted-foreground">
          Strike
          <select
            className="mt-1 h-8 w-full rounded-md border border-border bg-background px-2 text-xs text-foreground"
            value={selection}
            onChange={(event) => setSelection(event.target.value as VisualBracketStrikeSelection)}
          >
            {STRIKE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {selection === 'CUSTOM' && (
        <label className="mt-2 block text-[11px] text-muted-foreground">
          Custom strike
          <Input
            className="mt-1 h-8"
            inputMode="decimal"
            value={customStrike}
            onChange={(event) => setCustomStrike(event.target.value)}
            placeholder="24500"
          />
        </label>
      )}

      <div className="mt-2 grid grid-cols-2 gap-2">
        <label className="text-[11px] text-muted-foreground">
          Lots
          <Input
            className="mt-1 h-8"
            type="number"
            min={1}
            max={20}
            value={lots}
            onChange={(event) =>
              setLots(Math.min(20, Math.max(1, Number(event.target.value) || 1)))
            }
          />
        </label>
        <div className="text-[11px] text-muted-foreground">
          Quantity
          <div className="mt-1 flex h-8 items-center rounded-md border border-border px-2 text-xs text-foreground">
            {quantity.toLocaleString('en-IN')}
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-md border border-border/70 bg-muted/30 p-2 text-xs">
        {unavailable ? (
          <div className="text-amber-600">
            {isLoading ? 'Loading option quotes…' : 'Option quotes unavailable.'}
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between">
              <span className="truncate font-medium">{leg?.symbol ?? '—'}</span>
              <span className="tabular-nums">{leg ? leg.ltp.toFixed(2) : '—'}</span>
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
              <div>
                <div className="text-muted-foreground">Entry</div>
                <div className="tabular-nums">{levels ? levels.entry.toFixed(2) : '—'}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Target</div>
                <div className="tabular-nums text-emerald-600">
                  {levels ? levels.target.toFixed(2) : '—'}
                </div>
              </div>
              <div>
                <div className="text-muted-foreground">Stop</div>
                <div className="tabular-nums text-rose-600">
                  {levels ? levels.stop.toFixed(2) : '—'}
                </div>
              </div>
            </div>
            <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
              <span>Drag target or stop on the chart</span>
              <span>{levels ? `R:R 1:${levels.rr.toFixed(2)}` : ''}</span>
            </div>
            <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
              <span>Estimated at target</span>
              <span className="tabular-nums text-emerald-600">{money(estimatedProfit)}</span>
            </div>
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span>Estimated at stop</span>
              <span className="tabular-nums text-rose-600">{money(estimatedLoss)}</span>
            </div>
          </>
        )}
      </div>

      <div className="mt-3 flex gap-2">
        <Button
          className="h-9 flex-1"
          disabled={unavailable || placing || !staged?.contractSymbol}
          onClick={() => void place()}
        >
          {placing ? 'Placing…' : 'Place Order'}
        </Button>
        <Button
          variant="outline"
          className="h-9"
          disabled={placing}
          onClick={() => {
            onStage(null)
            onClose()
          }}
        >
          Cancel
        </Button>
      </div>

      {staged?.status && staged.status !== 'staged' && (
        <div className="mt-2 text-[11px] text-muted-foreground">
          Status: {statusLabel(staged.status)}
        </div>
      )}
    </div>
  )
}
