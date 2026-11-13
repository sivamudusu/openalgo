import type { Chart } from 'openalgo-charts'
import {
  clampVisualLevel,
  riskReward,
  type VisualBracketLevels,
  type VisualBracketView,
} from './visualBracket'
import { VisualBracketOverlay } from './visualBracketOverlay'

export interface VisualBracketClick {
  price: number
  time: number
  paneIndex: number
  point: { x: number; y: number }
  shift: boolean
}

export interface VisualBracketControllerOptions {
  chart: Chart
  onClick: (click: VisualBracketClick) => void
  onLevelChange: (levels: VisualBracketLevels) => void
  onLevelCommit: (levels: VisualBracketLevels) => void
}

/**
 * Owns one chart's visual bracket primitive and its drag gestures.
 *
 * React owns the staged state; this controller only paints it and reports what
 * the pointer did. That keeps the terminal free of the option-chain and order
 * state, and lets the pane reattach the same state after a chart rebuild.
 */
export class VisualBracketController {
  private readonly chart: Chart
  private readonly opts: VisualBracketControllerOptions
  private readonly overlay = new VisualBracketOverlay()
  private attached = false
  private enabled = false
  private state: VisualBracketView | null = null
  private readonly offClick: () => void
  private readonly offDrag: () => void

  constructor(opts: VisualBracketControllerOptions) {
    this.opts = opts
    this.chart = opts.chart

    this.offClick = this.chart.on('click', (event) => {
      if (!this.enabled && !event.modifiers.shift) return
      if (event.id !== null || event.price == null) return
      this.opts.onClick({
        price: event.price,
        time: event.time,
        paneIndex: event.paneIndex,
        point: event.point,
        shift: event.modifiers.shift,
      })
    })

    this.offDrag = this.chart.subscribeDrag(
      (id, price) => {
        if (!id.startsWith('vb:') || !this.state) return
        const moving = id.slice(3) as 'entry' | 'target' | 'stop'
        if (moving === 'entry') return
        const next = this.withLevel(moving, price)
        this.state = next
        this.overlay.setState(next)
        this.opts.onLevelChange(this.levels(next))
      },
      (id, price) => {
        if (!id.startsWith('vb:') || !this.state) return
        const moving = id.slice(3) as 'entry' | 'target' | 'stop'
        if (moving === 'entry') return
        const next = this.withLevel(moving, price)
        this.state = next
        this.overlay.setState(next)
        this.opts.onLevelCommit(this.levels(next))
      }
    )
  }

  setEnabled(on: boolean): void {
    this.enabled = on
  }

  isEnabled(): boolean {
    return this.enabled
  }

  setOverlay(state: VisualBracketView | null): void {
    this.state = state
    this.overlay.setState(state)
    if (state && !this.attached) {
      this.chart.addPrimitive(this.overlay, 0)
      this.attached = true
    } else if (!state && this.attached) {
      this.chart.removePrimitive(this.overlay)
      this.attached = false
    }
  }

  overlayState(): VisualBracketView | null {
    return this.state
  }

  destroy(): void {
    this.offClick()
    this.offDrag()
    if (this.attached) this.chart.removePrimitive(this.overlay)
    this.attached = false
    this.state = null
  }

  private levels(state: VisualBracketView): VisualBracketLevels {
    return {
      entryReference: state.entryReference,
      targetPrice: state.targetPrice,
      stopPrice: state.stopPrice,
    }
  }

  private withLevel(moving: 'target' | 'stop', price: number): VisualBracketView {
    const state = this.state as VisualBracketView
    const target =
      moving === 'target'
        ? clampVisualLevel(state.bias, state.entryReference, price, 'target', state.tickSize)
        : state.targetPrice
    const stop =
      moving === 'stop'
        ? clampVisualLevel(state.bias, state.entryReference, price, 'stop', state.tickSize)
        : state.stopPrice
    return {
      ...state,
      targetPrice: target,
      stopPrice: stop,
      targetOffset: Math.abs(target - state.entryReference),
      stopOffset: Math.abs(state.entryReference - stop),
      riskReward: riskReward(state.entryReference, target, stop),
    }
  }
}
