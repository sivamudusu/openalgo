import type { PrimitiveRenderContext } from 'openalgo-charts'
import { describe, expect, it } from 'vitest'
import type { VisualBracketView } from './visualBracket'
import { VisualBracketOverlay } from './visualBracketOverlay'

function fakeContext() {
  const calls: Array<[string, ...unknown[]]> = []
  const ctx = new Proxy(
    {},
    {
      get(_target, prop) {
        if (prop === 'measureText') return () => ({ width: 40 })
        return (...args: unknown[]) => {
          calls.push([String(prop), ...args])
        }
      },
      set() {
        return true
      },
    }
  ) as unknown as CanvasRenderingContext2D
  return { ctx, calls }
}

const state = {
  status: 'active',
  entryReference: 100,
  targetPrice: 110,
  stopPrice: 90,
  riskReward: 1,
} as VisualBracketView

const rc = {
  dpr: 2,
  plotWidth: 800,
  plotHeight: 400,
  priceScale: { priceToY: (price: number) => 300 - price },
  theme: { axisText: '#fff', background: '#000', crosshair: '#fff' },
  dragId: null,
  hoverId: null,
} as unknown as PrimitiveRenderContext

describe('VisualBracketOverlay', () => {
  it('draws in CSS pixels on a high-DPI canvas', () => {
    const overlay = new VisualBracketOverlay(state)
    const { ctx, calls } = fakeContext()

    overlay.draw(ctx, rc)

    expect(calls).toContainEqual(['scale', 2, 2])
    expect(calls).toContainEqual(['rect', 0, 0, 800, 400])
  })
})
