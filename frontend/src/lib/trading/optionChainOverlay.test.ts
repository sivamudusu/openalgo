import type { PrimitiveRenderContext } from 'openalgo-charts'
import { describe, expect, it, vi } from 'vitest'
import type { OptionChainResponse, OptionData, OptionStrike } from '@/types/option-chain'
import {
  DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS,
  formatOverlayStrike,
  maxOiStrike,
  OptionChainOverlay,
  overlayBarWidth,
  overlayDataFromChain,
  overlaySignature,
  strikeStep,
  visibleOverlayLevels,
} from './optionChainOverlay'

const leg = (symbol: string, oi: number, ltp = 100): OptionData => ({
  symbol,
  label: symbol,
  ltp,
  bid: 99,
  ask: 101,
  bid_qty: 10,
  ask_qty: 10,
  open: 100,
  high: 102,
  low: 98,
  prev_close: 100,
  volume: oi * 2,
  oi,
  lotsize: 50,
  tick_size: 0.05,
})

const row = (strike: number, ceOi: number, peOi: number): OptionStrike => ({
  strike,
  ce: leg(`CE${strike}`, ceOi, 120),
  pe: leg(`PE${strike}`, peOi, 80),
})

const chain = (): OptionChainResponse => ({
  status: 'success',
  underlying: 'NIFTY',
  underlying_symbol: 'NIFTY',
  underlying_exchange: 'NSE_INDEX',
  underlying_ltp: 22_750,
  underlying_prev_close: 22_700,
  expiry_date: '30OCT26',
  atm_strike: 22_750,
  chain: [row(22_700, 1000, 5000), row(22_750, 3000, 2000), row(22_800, 6000, 1000)],
})

const theme = {
  background: '#131722',
  grid: '#1f2937',
  axisText: '#9ca3af',
  axisLine: '#374151',
  paneSeparator: '#1f2937',
  crosshair: '#9ca3af',
  upColor: '#10b981',
  downColor: '#f43f5e',
  wickUpColor: '#10b981',
  wickDownColor: '#f43f5e',
  lineColor: '#10b981',
  areaTopColor: '#10b981',
  areaBottomColor: '#10b981',
  baselineTopLine: '#10b981',
  baselineTopFill: '#10b981',
  baselineBottomLine: '#f43f5e',
  baselineBottomFill: '#f43f5e',
  lastPriceUp: '#10b981',
  lastPriceDown: '#f43f5e',
  lastPriceText: '#ffffff',
  buy: '#10b981',
  sell: '#f43f5e',
  profit: '#10b981',
  loss: '#f43f5e',
}

function canvas(dpr = 1) {
  const fills: { x: number; y: number; width: number; height: number; color: string }[] = []
  const strokes: { color: string; points: number[][] }[] = []
  const textCalls: { text: string; x: number; y: number }[] = []
  const stack: [number, number][] = []
  let scaleX = 1
  let scaleY = 1
  let points: number[][] = []
  const ctx = {
    fillStyle: '',
    strokeStyle: '',
    globalAlpha: 1,
    lineWidth: 1,
    font: '',
    textAlign: 'left',
    textBaseline: 'alphabetic',
    save: () => stack.push([scaleX, scaleY]),
    restore: () => {
      const saved = stack.pop()
      if (saved) {
        scaleX = saved[0]
        scaleY = saved[1]
      }
    },
    scale: (x: number, y: number) => {
      scaleX *= x
      scaleY *= y
    },
    setLineDash: vi.fn(),
    beginPath: () => {
      points = []
    },
    moveTo: (x: number, y: number) => points.push([x * scaleX, y * scaleY]),
    lineTo: (x: number, y: number) => points.push([x * scaleX, y * scaleY]),
    stroke: () => strokes.push({ color: String(ctx.strokeStyle), points: [...points] }),
    fillRect: (x: number, y: number, width: number, height: number) =>
      fills.push({
        x: x * scaleX,
        y: y * scaleY,
        width: width * scaleX,
        height: height * scaleY,
        color: String(ctx.fillStyle),
      }),
    fillText: (text: string, x: number, y: number) =>
      textCalls.push({ text, x: x * scaleX, y: y * scaleY }),
    measureText: (text: string) => ({ width: text.length * 5 }),
  }
  const rc = {
    dpr,
    plotWidth: 300,
    plotHeight: 200,
    priceAxisWidth: 50,
    priceScale: { priceToY: (price: number) => 150 - (price - 22_700) * 0.5 },
    theme,
  } as unknown as PrimitiveRenderContext
  return {
    ctx: ctx as unknown as CanvasRenderingContext2D,
    rc,
    fills,
    strokes,
    textCalls,
    dpr,
  }
}

const bars = (paint: ReturnType<typeof canvas>) =>
  paint.fills.filter(
    (fill) =>
      fill.height === 6 * paint.dpr &&
      fill.width > 6 * paint.dpr &&
      (fill.color === '#10b981' || fill.color === '#f43f5e')
  )

describe('overlayDataFromChain', () => {
  it('carries the selected metric and the open interest the levels need', () => {
    const data = overlayDataFromChain(chain(), 'oi')
    expect(data.underlying).toBe('NIFTY')
    expect(data.expiry).toBe('30OCT26')
    expect(data.spot).toBe(22_750)
    expect(data.atmStrike).toBe(22_750)
    expect(data.pcr).toBeCloseTo(0.8)
    expect(data.maxCeStrike).toBe(22_800)
    expect(data.maxPeStrike).toBe(22_700)
    expect(data.levels[0]).toEqual({
      strike: 22_700,
      ceValue: 1000,
      peValue: 5000,
      ceOi: 1000,
      peOi: 5000,
    })
  })

  it('reads the metric for the bars but keeps OI for support and resistance', () => {
    const data = overlayDataFromChain(chain(), 'ltp')
    expect(data.levels[0].ceValue).toBe(120)
    expect(data.levels[0].peValue).toBe(80)
    expect(data.maxCeStrike).toBe(22_800)
    expect(data.maxPeStrike).toBe(22_700)
  })

  it('treats a missing leg as zero and a chain with no OI as no levels', () => {
    const empty = chain()
    empty.chain = [row(22_700, 0, 0)]
    const data = overlayDataFromChain(empty, 'oi')
    expect(data.pcr).toBe(0)
    expect(data.maxCeStrike).toBeNull()
    expect(data.maxPeStrike).toBeNull()
  })
})

describe('maxOiStrike', () => {
  it('picks the highest open interest and the first strike on a tie', () => {
    const levels = [
      { strike: 100, ceValue: 0, peValue: 0, ceOi: 10, peOi: 30 },
      { strike: 110, ceValue: 0, peValue: 0, ceOi: 30, peOi: 10 },
      { strike: 120, ceValue: 0, peValue: 0, ceOi: 30, peOi: 0 },
    ]
    expect(maxOiStrike(levels, 'ce')).toBe(110)
    expect(maxOiStrike(levels, 'pe')).toBe(100)
    expect(maxOiStrike([], 'ce')).toBeNull()
  })
})

describe('strikeStep', () => {
  it('uses the smallest gap between strikes', () => {
    expect(
      strikeStep([
        { strike: 100, ceValue: 0, peValue: 0, ceOi: 0, peOi: 0 },
        { strike: 105, ceValue: 0, peValue: 0, ceOi: 0, peOi: 0 },
        { strike: 115, ceValue: 0, peValue: 0, ceOi: 0, peOi: 0 },
      ])
    ).toBe(5)
    expect(strikeStep([])).toBe(0)
  })
})

describe('overlayBarWidth', () => {
  it('scales against the widest value, clamps, and keeps a negative Greek visible', () => {
    expect(overlayBarWidth(50, 100, 200)).toBe(100)
    expect(overlayBarWidth(500, 100, 200)).toBe(200)
    expect(overlayBarWidth(-50, 100, 200)).toBe(100)
    expect(overlayBarWidth(10, 0, 200)).toBe(0)
    expect(overlayBarWidth(Number.NaN, 100, 200)).toBe(0)
  })
})

describe('visibleOverlayLevels', () => {
  it('keeps strikes inside the plot and drops the rest', () => {
    const levels = [100, 200, 300, 400].map((strike) => ({
      strike,
      ceValue: 0,
      peValue: 0,
      ceOi: 0,
      peOi: 0,
    }))
    expect(
      visibleOverlayLevels(levels, (price) => price, 300).map((level) => level.strike)
    ).toEqual([100, 200, 300])
  })
})

describe('formatOverlayStrike', () => {
  it('reads as Indian grouped thousands', () => {
    expect(formatOverlayStrike(22_800)).toBe('22,800')
    expect(formatOverlayStrike(29_250.5)).toBe('29,250.5')
  })
})

describe('overlaySignature', () => {
  it('changes only when something the frame draws changes', () => {
    const data = overlayDataFromChain(chain(), 'oi')
    const base = overlaySignature(data, DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    expect(
      overlaySignature(overlayDataFromChain(chain(), 'oi'), DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    ).toBe(base)
    expect(
      overlaySignature(data, { ...DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS, metric: 'ltp' })
    ).not.toBe(base)
    expect(
      overlaySignature(data, { ...DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS, showValues: true })
    ).not.toBe(base)
    expect(overlaySignature(null, DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)).toBe('')
  })
})

describe('OptionChainOverlay', () => {
  it('anchors both sides to the right edge and stacks calls above puts', () => {
    const paint = canvas()
    const overlay = new OptionChainOverlay()
    overlay.setData(overlayDataFromChain(chain(), 'oi'), DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    overlay.draw(paint.ctx, paint.rc)

    const drawn = bars(paint)
    expect(drawn).toHaveLength(6)
    const calls = drawn.filter((fill) => fill.color === '#10b981')
    const puts = drawn.filter((fill) => fill.color === '#f43f5e')
    expect(calls).toHaveLength(3)
    expect(puts).toHaveLength(3)
    const rightEdge = 300 - 6
    expect(calls.every((fill) => fill.x + fill.width === rightEdge)).toBe(true)
    expect(puts.every((fill) => fill.x + fill.width === rightEdge)).toBe(true)
    expect(Math.max(...calls.map((fill) => fill.width))).toBe(60)
    expect(Math.max(...puts.map((fill) => fill.width))).toBe(50)

    const callYs = calls.map((fill) => fill.y).sort((a, b) => a - b)
    const putYs = puts.map((fill) => fill.y).sort((a, b) => a - b)
    callYs.forEach((callY, index) => {
      expect(putYs[index] - (callY + 6)).toBe(1)
    })
  })

  it('anchors to the right edge on a high-density display', () => {
    const paint = canvas(2)
    const overlay = new OptionChainOverlay()
    overlay.setData(overlayDataFromChain(chain(), 'oi'), DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    overlay.draw(paint.ctx, paint.rc)

    const drawn = bars(paint)
    expect(drawn).toHaveLength(6)
    const rightEdge = (300 - 6) * 2
    expect(drawn.every((fill) => fill.x + fill.width === rightEdge)).toBe(true)
  })

  it('draws support and resistance at the heaviest open interest', () => {
    const paint = canvas()
    const overlay = new OptionChainOverlay()
    overlay.setData(overlayDataFromChain(chain(), 'oi'), DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    overlay.draw(paint.ctx, paint.rc)

    expect(paint.strokes).toHaveLength(2)
    expect(paint.strokes[0].color).toBe('#10b981')
    expect(paint.strokes[0].points[0][1]).toBe(100)
    expect(paint.strokes[1].color).toBe('#f43f5e')
    expect(paint.strokes[1].points[0][1]).toBe(150)
    expect(paint.textCalls.map((call) => call.text)).toEqual(
      expect.arrayContaining(['R 22,800', 'S 22,700', 'CE OI', 'PE OI'])
    )
  })

  it('labels the bars only when values are on', () => {
    const off = canvas()
    const overlay = new OptionChainOverlay()
    const data = overlayDataFromChain(chain(), 'oi')
    overlay.setData(data, DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    overlay.draw(off.ctx, off.rc)
    expect(off.textCalls.some((call) => call.text === '6.0K')).toBe(false)

    const on = canvas()
    overlay.setData(data, { ...DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS, showValues: true })
    overlay.draw(on.ctx, on.rc)
    expect(on.textCalls.some((call) => call.text === '6.0K')).toBe(true)
    expect(on.textCalls.some((call) => call.text === '5.0K')).toBe(true)
  })

  it('draws nothing without data or with an empty chain', () => {
    const paint = canvas()
    const overlay = new OptionChainOverlay()
    overlay.draw(paint.ctx, paint.rc)
    expect(paint.fills).toHaveLength(0)
    expect(paint.textCalls).toHaveLength(0)
  })

  it('requests a repaint only when the frame changes', () => {
    const requestUpdate = vi.fn()
    const overlay = new OptionChainOverlay()
    overlay.attached({ requestUpdate })
    const data = overlayDataFromChain(chain(), 'oi')
    overlay.setData(data, DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    expect(requestUpdate).toHaveBeenCalledOnce()

    overlay.setData(overlayDataFromChain(chain(), 'oi'), DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    expect(requestUpdate).toHaveBeenCalledOnce()

    overlay.setData(data, { ...DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS, metric: 'ltp' })
    expect(requestUpdate).toHaveBeenCalledTimes(2)

    overlay.setData(null, DEFAULT_OPTION_CHAIN_OVERLAY_OPTIONS)
    expect(requestUpdate).toHaveBeenCalledTimes(3)
  })

  it('does not take part in autoscale and paints over the series', () => {
    const overlay = new OptionChainOverlay()
    expect(overlay.zOrder()).toBe('normal')
    expect(overlay.autoscaleInfo()).toBeNull()
  })
})
