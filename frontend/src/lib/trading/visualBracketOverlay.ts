import type {
  IPrimitive,
  PrimitiveHit,
  PrimitiveHost,
  PrimitiveRenderContext,
  ZOrder,
} from 'openalgo-charts'
import type { VisualBracketView } from './visualBracket'

const TARGET = '#26a69a'
const STOP = '#ef5350'
const ENTRY = '#78909c'

const TARGET_FILL = 'rgba(38, 166, 154, 0.14)'
const STOP_FILL = 'rgba(239, 83, 80, 0.14)'

function pointsBetween(a: number, b: number): number {
  return Math.abs(a - b)
}

function priceText(price: number): string {
  return price.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}

/**
 * The shaded target/stop box with draggable lines and handles.
 *
 * It paints on the chart's own canvas rather than as DOM, so it follows pan,
 * zoom, theme changes and pane resizes without a synchronisation loop. Hit
 * testing is what makes the lines draggable; the terminal receives the drag
 * through `chart.subscribeDrag` under the ids `vb:target`, `vb:stop` and
 * `vb:entry`.
 */
export class VisualBracketOverlay implements IPrimitive {
  private _state: VisualBracketView | null = null
  private _host: PrimitiveHost | null = null

  constructor(state: VisualBracketView | null = null) {
    this._state = state
  }

  state(): VisualBracketView | null {
    return this._state
  }

  setState(state: VisualBracketView | null): void {
    this._state = state
    this._host?.requestUpdate()
  }

  attached(host: PrimitiveHost): void {
    this._host = host
  }

  detached(): void {
    this._host = null
  }

  zOrder(): ZOrder {
    return 'top'
  }

  autoscaleInfo(): null {
    return null
  }

  private lineY(price: number, rc: PrimitiveRenderContext): number | null {
    const y = rc.priceScale.priceToY(price)
    return Number.isFinite(y) ? y : null
  }

  draw(ctx: CanvasRenderingContext2D, rc: PrimitiveRenderContext): void {
    const state = this._state
    if (!state) return
    const entryY = this.lineY(state.entryReference, rc)
    const targetY = this.lineY(state.targetPrice, rc)
    const stopY = this.lineY(state.stopPrice, rc)
    if (entryY === null || targetY === null || stopY === null) return

    const width = rc.plotWidth
    const active = state.status === 'active' || state.status === 'exiting'

    ctx.save()
    ctx.beginPath()
    ctx.rect(0, 0, width, rc.plotHeight)
    ctx.clip()

    const targetTop = Math.min(entryY, targetY)
    const targetHeight = Math.max(1, Math.abs(entryY - targetY))
    const stopTop = Math.min(entryY, stopY)
    const stopHeight = Math.max(1, Math.abs(entryY - stopY))

    ctx.fillStyle = TARGET_FILL
    ctx.fillRect(0, targetTop, width, targetHeight)
    ctx.fillStyle = STOP_FILL
    ctx.fillRect(0, stopTop, width, stopHeight)

    const dashed = !active
    const drawLine = (y: number, price: number, color: string, label: string) => {
      ctx.beginPath()
      ctx.setLineDash(dashed ? [5, 4] : [])
      ctx.strokeStyle = color
      ctx.lineWidth = 1.5
      ctx.moveTo(0, y)
      ctx.lineTo(width, y)
      ctx.stroke()
      ctx.setLineDash([])

      const text = `${label}  ${priceText(price)}`
      ctx.font = '11px ui-sans-serif, system-ui, sans-serif'
      const textWidth = ctx.measureText(text).width
      const x = Math.max(4, width - textWidth - 14)
      ctx.fillStyle = 'rgba(8, 12, 18, 0.78)'
      ctx.fillRect(x - 4, y - 15, textWidth + 8, 14)
      ctx.fillStyle = color
      ctx.textBaseline = 'middle'
      ctx.textAlign = 'left'
      ctx.fillText(text, x, y - 8)
    }

    drawLine(entryY, state.entryReference, ENTRY, 'Entry')
    drawLine(
      targetY,
      state.targetPrice,
      TARGET,
      `Target +${pointsBetween(state.targetPrice, state.entryReference).toFixed(2)}`
    )
    drawLine(
      stopY,
      state.stopPrice,
      STOP,
      `Stop -${pointsBetween(state.stopPrice, state.entryReference).toFixed(2)}`
    )

    const drawHandle = (y: number, id: string, color: string) => {
      const hovered = rc.dragId === id || rc.hoverId === id
      ctx.beginPath()
      ctx.arc(width - 7, y, hovered ? 6 : 4.5, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()
      ctx.strokeStyle = 'rgba(8, 12, 18, 0.7)'
      ctx.lineWidth = 1
      ctx.stroke()
    }
    drawHandle(targetY, 'vb:target', TARGET)
    drawHandle(stopY, 'vb:stop', STOP)

    if (state.riskReward > 0) {
      const label = `R:R 1:${state.riskReward.toFixed(2)}`
      ctx.font = '11px ui-sans-serif, system-ui, sans-serif'
      const w = ctx.measureText(label).width
      ctx.fillStyle = 'rgba(8, 12, 18, 0.78)'
      ctx.fillRect(width - w - 16, Math.min(entryY, targetY, stopY) - 30, w + 8, 14)
      ctx.fillStyle = '#e2e8f0'
      ctx.textAlign = 'left'
      ctx.textBaseline = 'middle'
      ctx.fillText(label, width - w - 12, Math.min(entryY, targetY, stopY) - 23)
    }

    ctx.restore()
  }

  hitBounds(rc: PrimitiveRenderContext): {
    left: number
    top: number
    right: number
    bottom: number
  } {
    return { left: 0, top: 0, right: rc.plotWidth, bottom: rc.plotHeight }
  }

  hitTest(x: number, y: number, rc: PrimitiveRenderContext): PrimitiveHit | null {
    const state = this._state
    if (!state) return null
    const entryY = this.lineY(state.entryReference, rc)
    const targetY = this.lineY(state.targetPrice, rc)
    const stopY = this.lineY(state.stopPrice, rc)
    if (entryY === null || targetY === null || stopY === null) return null

    const candidates: Array<{ id: string; lineY: number; enabled: boolean }> = [
      { id: 'vb:target', lineY: targetY, enabled: true },
      { id: 'vb:stop', lineY: stopY, enabled: true },
    ]

    let best: PrimitiveHit | null = null
    for (const candidate of candidates) {
      if (!candidate.enabled) continue
      const distance = Math.abs(y - candidate.lineY)
      const onHandle = x >= rc.plotWidth - 22 && distance <= 12
      if (distance > 5 && !onHandle) continue
      const hit: PrimitiveHit = {
        externalId: candidate.id,
        distance: onHandle ? 0 : distance,
        zOrder: 'top',
        cursor: 'ns-resize',
        draggable: true,
        hoverKey: onHandle ? 'handle' : 'line',
      }
      if (best === null || hit.distance < best.distance) best = hit
    }
    return best
  }
}
