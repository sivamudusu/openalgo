import type { IPrimitive, PrimitiveHost, PrimitiveRenderContext, ZOrder } from 'openalgo-charts'
import {
  formatOptionChainMetric,
  OPTION_CHAIN_METRICS,
  type OptionChainMetric,
  optionChainMetricValue,
} from '@/lib/optionChainMetrics'
import type { OptionChainResponse } from '@/types/option-chain'

/** One strike row, reduced to what the overlay draws. */
export interface OptionChainOverlayLevel {
  strike: number
  /** The selected metric for the call and put. Negative Greeks keep their sign. */
  ceValue: number
  peValue: number
  /** Open interest is always carried: the support and resistance lines use it. */
  ceOi: number
  peOi: number
}

export interface OptionChainOverlayData {
  underlying: string
  expiry: string
  spot: number
  atmStrike: number
  pcr: number
  levels: OptionChainOverlayLevel[]
  maxCeStrike: number | null
  maxPeStrike: number | null
}

export interface OptionChainOverlayOptions {
  metric: OptionChainMetric
  showValues: boolean
  showLevels: boolean
}

export const DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS: OptionChainOverlayOptions = {
  metric: 'oi',
  showValues: false,
  showLevels: true,
}

const CE_COLOR = '#10b981'
const PE_COLOR = '#f43f5e'
const FONT = '10px ui-sans-serif, system-ui, -apple-system, sans-serif'
const RIGHT_PAD = 6
const MIN_BAR_WIDTH = 60
const MAX_BAR_WIDTH = 200

/** The chart's chain as the primitive reads it. */
export function overlayDataFromChain(
  chain: OptionChainResponse,
  metric: OptionChainMetric
): OptionChainOverlayData {
  const levels = chain.chain.map((row) => ({
    strike: row.strike,
    ceValue: optionChainMetricValue(row.ce, metric) ?? 0,
    peValue: optionChainMetricValue(row.pe, metric) ?? 0,
    ceOi: row.ce?.oi ?? 0,
    peOi: row.pe?.oi ?? 0,
  }))
  const ceTotal = levels.reduce((sum, level) => sum + level.ceOi, 0)
  const peTotal = levels.reduce((sum, level) => sum + level.peOi, 0)
  return {
    underlying: chain.underlying,
    expiry: chain.expiry_date,
    spot: chain.underlying_ltp,
    atmStrike: chain.atm_strike,
    pcr: ceTotal > 0 ? peTotal / ceTotal : 0,
    levels,
    maxCeStrike: maxOiStrike(levels, 'ce'),
    maxPeStrike: maxOiStrike(levels, 'pe'),
  }
}

/** The strike carrying the most call or put open interest, or null when none has any. */
export function maxOiStrike(
  levels: readonly OptionChainOverlayLevel[],
  side: 'ce' | 'pe'
): number | null {
  let best: number | null = null
  let bestValue = 0
  for (const level of levels) {
    const value = side === 'ce' ? level.ceOi : level.peOi
    if (value > bestValue) {
      best = level.strike
      bestValue = value
    }
  }
  return best
}

/** The spacing the chain was built on; the minimum gap between its strikes. */
export function strikeStep(levels: readonly OptionChainOverlayLevel[]): number {
  const strikes = [...new Set(levels.map((level) => level.strike))].sort((a, b) => a - b)
  let step = 0
  for (let i = 1; i < strikes.length; i += 1) {
    const gap = strikes[i] - strikes[i - 1]
    if (gap > 0 && (step === 0 || gap < step)) step = gap
  }
  return step
}

/** Bars scale against the widest value on screen, and never invert a negative Greek. */
export function overlayBarWidth(value: number, maxValue: number, maxWidth: number): number {
  if (!(maxValue > 0) || !Number.isFinite(value)) return 0
  return Math.min(maxWidth, (Math.abs(value) / maxValue) * maxWidth)
}

/** The levels whose strike is inside the plot, with a little room for a bar at the edge. */
export function visibleOverlayLevels(
  levels: readonly OptionChainOverlayLevel[],
  priceToY: (price: number) => number,
  plotHeight: number,
  margin = 12
): OptionChainOverlayLevel[] {
  return levels.filter((level) => {
    const y = priceToY(level.strike)
    return Number.isFinite(y) && y >= -margin && y <= plotHeight + margin
  })
}

/** A strike as a trader reads it, independent of the chart's rebase mode. */
export function formatOverlayStrike(strike: number): string {
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(strike)
}

/** A cheap identity for the frame: no repaint when nothing visible changed. */
export function overlaySignature(
  data: OptionChainOverlayData | null,
  options: OptionChainOverlayOptions
): string {
  if (!data) return ''
  const levels = data.levels
    .map((level) => `${level.strike}:${level.ceValue}:${level.peValue}:${level.ceOi}:${level.peOi}`)
    .join(',')
  return [
    data.underlying,
    data.expiry,
    data.atmStrike,
    data.pcr,
    data.maxCeStrike,
    data.maxPeStrike,
    options.metric,
    options.showValues,
    options.showLevels,
    levels,
  ].join('|')
}

function metricLabel(metric: OptionChainMetric): string {
  return OPTION_CHAIN_METRICS.find((entry) => entry.id === metric)?.label ?? 'OI'
}

/**
 * Sideways option-chain bars at the strikes, anchored to the right edge of the
 * price pane. At each strike the call bar sits above the put bar and both grow
 * left from the price axis, so the ladder reads as a profile beside the prices
 * without a second scale.
 *
 * The support and resistance lines are always open interest, whatever metric
 * the bars show: a high put OI is the strike the market is defending, and that
 * does not stop being true because the trader switched the bars to delta.
 */
export class OptionChainOverlay implements IPrimitive {
  private host: PrimitiveHost | null = null
  private data: OptionChainOverlayData | null = null
  private options: OptionChainOverlayOptions = DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS
  private signature = ''

  attached(host: PrimitiveHost): void {
    this.host = host
  }

  detached(): void {
    this.host = null
  }

  zOrder(): ZOrder {
    return 'normal'
  }

  autoscaleInfo(): null {
    return null
  }

  setData(data: OptionChainOverlayData | null, options: OptionChainOverlayOptions): void {
    const next = overlaySignature(data, options)
    this.data = data
    this.options = options
    if (next === this.signature) return
    this.signature = next
    this.host?.requestUpdate()
  }

  draw(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    const data = this.data
    if (!data || data.levels.length === 0 || rc.plotWidth <= 0 || rc.plotHeight <= 0) return

    const sorted = [...data.levels].sort((a, b) => a.strike - b.strike)
    const step = strikeStep(sorted)
    const first = sorted[0]
    const spacing =
      step > 0
        ? Math.abs(
            rc.priceScale.priceToY(first.strike + step) - rc.priceScale.priceToY(first.strike)
          )
        : 0
    const barHeight = Math.max(2, Math.min(6, spacing > 0 ? spacing * 0.24 : 4))
    const pairGap = this.options.showValues ? 5 : 1
    const visible = visibleOverlayLevels(
      sorted,
      (price) => rc.priceScale.priceToY(price),
      rc.plotHeight,
      barHeight * 2 + pairGap
    )
    if (visible.length === 0) return

    const maxBarWidth = Math.min(Math.max(rc.plotWidth * 0.18, MIN_BAR_WIDTH), MAX_BAR_WIDTH)
    const rightEdge = rc.plotWidth - RIGHT_PAD
    const maxValue = Math.max(
      1,
      ...visible.flatMap((level) => [Math.abs(level.ceValue), Math.abs(level.peValue)])
    )
    const textColor = rc.theme.axisText
    const metric = metricLabel(this.options.metric)

    ctx.save()
    ctx.scale(rc.dpr, rc.dpr)

    if (data.atmStrike > 0) {
      const y = rc.priceScale.priceToY(data.atmStrike)
      if (Number.isFinite(y) && y >= 0 && y <= rc.plotHeight) {
        const bandHalf = barHeight + pairGap / 2
        ctx.globalAlpha = 0.06
        ctx.fillStyle = rc.theme.crosshair
        ctx.fillRect(0, y - bandHalf, rc.plotWidth, bandHalf * 2)
      }
    }

    if (this.options.showLevels) {
      this.drawLevel(ctx, rc, data.maxCeStrike, CE_COLOR, 'R')
      this.drawLevel(ctx, rc, data.maxPeStrike, PE_COLOR, 'S')
    }

    ctx.globalAlpha = 0.78
    for (const level of visible) {
      const y = rc.priceScale.priceToY(level.strike)
      const ceWidth = overlayBarWidth(level.ceValue, maxValue, maxBarWidth)
      const peWidth = overlayBarWidth(level.peValue, maxValue, maxBarWidth)
      const ceY = y - barHeight - pairGap / 2
      const peY = y + pairGap / 2
      if (ceWidth > 0) {
        ctx.fillStyle = CE_COLOR
        ctx.fillRect(rightEdge - ceWidth, ceY, ceWidth, barHeight)
      }
      if (peWidth > 0) {
        ctx.fillStyle = PE_COLOR
        ctx.fillRect(rightEdge - peWidth, peY, peWidth, barHeight)
      }
    }

    if (this.options.showValues) {
      ctx.globalAlpha = 0.82
      ctx.font = FONT
      ctx.textBaseline = 'middle'
      for (const level of visible) {
        const y = rc.priceScale.priceToY(level.strike)
        const ceWidth = overlayBarWidth(level.ceValue, maxValue, maxBarWidth)
        const peWidth = overlayBarWidth(level.peValue, maxValue, maxBarWidth)
        if (level.ceValue !== 0) {
          const label = formatMetricValue(level.ceValue, this.options.metric)
          ctx.fillStyle = CE_COLOR
          ctx.textAlign = 'right'
          ctx.fillText(label, Math.max(2, rightEdge - ceWidth - 4), y - barHeight / 2 - pairGap / 2)
        }
        if (level.peValue !== 0) {
          const label = formatMetricValue(level.peValue, this.options.metric)
          ctx.fillStyle = PE_COLOR
          ctx.textAlign = 'right'
          ctx.fillText(label, Math.max(2, rightEdge - peWidth - 4), y + barHeight / 2 + pairGap / 2)
        }
      }
    }

    ctx.globalAlpha = 0.88
    ctx.font = FONT
    ctx.textAlign = 'left'
    ctx.textBaseline = 'bottom'
    const keyY = rc.plotHeight - 18
    ctx.fillStyle = CE_COLOR
    ctx.fillRect(6, keyY, 6, 6)
    ctx.fillStyle = textColor
    ctx.fillText(`CE ${metric}`, 16, keyY + 6)
    const ceWidth = ctx.measureText(`CE ${metric}`).width
    const putX = 16 + ceWidth + 12
    ctx.fillStyle = PE_COLOR
    ctx.fillRect(putX, keyY, 6, 6)
    ctx.fillStyle = textColor
    ctx.fillText(`PE ${metric}`, putX + 10, keyY + 6)

    const pcr = data.pcr > 0 ? data.pcr.toFixed(2) : '-'
    ctx.globalAlpha = 0.7
    ctx.fillStyle = textColor
    ctx.textBaseline = 'bottom'
    ctx.fillText(`${data.underlying} · ${data.expiry} · PCR ${pcr}`, 6, rc.plotHeight - 6)

    ctx.restore()
  }

  private drawLevel(
    ctx: CanvasRenderingContext2D,
    rc: PrimitiveRenderContext,
    strike: number | null,
    color: string,
    label: string
  ): void {
    if (strike === null) return
    const y = rc.priceScale.priceToY(strike)
    if (!Number.isFinite(y) || y < -8 || y > rc.plotHeight + 8) return

    ctx.save()
    ctx.globalAlpha = 0.55
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.setLineDash([4, 4])
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(rc.plotWidth, y)
    ctx.stroke()

    ctx.setLineDash([])
    ctx.font = FONT
    ctx.textAlign = 'left'
    ctx.textBaseline = 'bottom'
    const text = `${label} ${formatOverlayStrike(strike)}`
    const width = ctx.measureText(text).width
    const labelY = Math.max(11, y - 3)
    ctx.globalAlpha = 0.75
    ctx.fillStyle = rc.theme.background
    ctx.fillRect(2, labelY - 11, width + 4, 12)
    ctx.globalAlpha = 1
    ctx.fillStyle = color
    ctx.fillText(text, 4, labelY)
    ctx.restore()
  }
}

function formatMetricValue(value: number, metric: OptionChainMetric): string {
  if (metric === 'oi' || metric === 'volume') {
    const formatted = formatOptionChainMetric(Math.abs(value), metric)
    return value < 0 ? `-${formatted}` : formatted
  }
  return formatOptionChainMetric(value, metric)
}
