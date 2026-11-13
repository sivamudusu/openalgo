import { describe, expect, it } from 'vitest'
import {
  biasFor,
  clampVisualLevel,
  defaultLevels,
  estimateCurrencyPnl,
  isVisualUnderlying,
  manualBracketLevels,
  nearestStrike,
  riskReward,
  roundToTick,
  strikeForSelection,
  strikeStepFromLadder,
  type VisualBracketView,
  validateVisualLevels,
  withVisualLevels,
} from './visualBracket'

describe('visual bracket helpers', () => {
  it('maps option actions to an index bias', () => {
    expect(biasFor('BUY', 'CE')).toBe('bullish')
    expect(biasFor('SELL', 'PE')).toBe('bullish')
    expect(biasFor('BUY', 'PE')).toBe('bearish')
    expect(biasFor('SELL', 'CE')).toBe('bearish')
  })

  it('recognises only supported index underlyings', () => {
    expect(isVisualUnderlying('NIFTY', 'NSE_INDEX')).toBe(true)
    expect(isVisualUnderlying('SENSEX', 'BSE_INDEX')).toBe(true)
    expect(isVisualUnderlying('NIFTY', 'NFO')).toBe(false)
    expect(isVisualUnderlying('RELIANCE', 'NSE')).toBe(false)
  })

  it('rounds to the instrument tick', () => {
    expect(roundToTick(24500.03, 0.05)).toBe(24500.05)
    expect(roundToTick(24500.03, 0)).toBe(24500.03)
  })

  it('clamps a bullish target above entry and a stop below it', () => {
    expect(clampVisualLevel('bullish', 24500, 24400, 'target', 0.05)).toBe(24500.05)
    expect(clampVisualLevel('bullish', 24500, 24600, 'stop', 0.05)).toBe(24499.95)
  })

  it('clamps a bearish target below entry and a stop above it', () => {
    expect(clampVisualLevel('bearish', 24500, 24600, 'target', 0.05)).toBe(24499.95)
    expect(clampVisualLevel('bearish', 24500, 24400, 'stop', 0.05)).toBe(24500.05)
  })

  it('accepts legal levels and rejects crossed ones', () => {
    expect(
      validateVisualLevels(
        'bullish',
        { entryReference: 24500, targetPrice: 24600, stopPrice: 24400 },
        0.05
      )
    ).toBeNull()
    expect(
      validateVisualLevels(
        'bullish',
        { entryReference: 24500, targetPrice: 24400, stopPrice: 24450 },
        0.05
      )
    ).toContain('above entry')
  })

  it('computes risk reward from the absolute distances', () => {
    expect(riskReward(24500, 24620, 24440)).toBeCloseTo(2)
    expect(riskReward(24500, 24380, 24560)).toBeCloseTo(2)
    expect(riskReward(24500, 24500, 24500)).toBe(0)
  })

  it('finds the nearest listed strike to the click', () => {
    expect(nearestStrike(24512, [24400, 24450, 24500, 24550])).toBe(24500)
    expect(nearestStrike(24512, [])).toBeNull()
  })

  it('walks the strike ladder for CE and PE offsets', () => {
    const strikes = [24400, 24450, 24500, 24550, 24600]
    expect(strikeForSelection(strikes, 24500, 'ATM', 'CE')).toBe(24500)
    expect(strikeForSelection(strikes, 24500, 'ITM1', 'CE')).toBe(24450)
    expect(strikeForSelection(strikes, 24500, 'OTM1', 'CE')).toBe(24550)
    expect(strikeForSelection(strikes, 24500, 'ITM1', 'PE')).toBe(24550)
    expect(strikeForSelection(strikes, 24500, 'OTM1', 'PE')).toBe(24450)
  })

  it('builds a two-to-one default bracket around entry', () => {
    expect(defaultLevels('bullish', 24500, 50)).toEqual({
      entryReference: 24500,
      targetPrice: 24600,
      stopPrice: 24450,
    })
    expect(defaultLevels('bearish', 24500, 50)).toEqual({
      entryReference: 24500,
      targetPrice: 24400,
      stopPrice: 24550,
    })
  })

  it('uses the signal levels when they sit on the right side of entry', () => {
    const ladder = [{ strike: 53900 }, { strike: 54000 }, { strike: 54100 }]
    expect(manualBracketLevels('bullish', 54000, 53800, 54300, ladder)).toEqual({
      entryReference: 54000,
      targetPrice: 54300,
      stopPrice: 53800,
    })
    expect(manualBracketLevels('bearish', 54000, 53800, 54300, ladder)).toEqual({
      entryReference: 54000,
      targetPrice: 53800,
      stopPrice: 54300,
    })
  })

  it('falls back to strike steps when the signal levels are missing', () => {
    const ladder = [{ strike: 53900 }, { strike: 54000 }, { strike: 54100 }]
    expect(manualBracketLevels('bullish', 54000, null, null, ladder)).toEqual({
      entryReference: 54000,
      targetPrice: 54200,
      stopPrice: 53900,
    })
    expect(manualBracketLevels('bearish', 54000, null, null, ladder)).toEqual({
      entryReference: 54000,
      targetPrice: 53800,
      stopPrice: 54100,
    })
  })

  it('falls back when the signal levels are crossed', () => {
    const ladder = [{ strike: 53900 }, { strike: 54000 }, { strike: 54100 }]
    expect(manualBracketLevels('bullish', 54000, 54300, 53800, ladder)).toEqual({
      entryReference: 54000,
      targetPrice: 54200,
      stopPrice: 53900,
    })
  })

  it('reads the strike step from the signal ladder', () => {
    expect(strikeStepFromLadder([{ strike: 53900 }, { strike: 54000 }, { strike: 54100 }])).toBe(
      100
    )
    expect(strikeStepFromLadder([])).toBe(50)
    expect(strikeStepFromLadder([{ strike: 54000 }])).toBe(50)
  })

  it('rounds the manual levels to the index tick', () => {
    expect(manualBracketLevels('bullish', 54000.03, 53800.02, 54300.04, [], 0.05)).toEqual({
      entryReference: 54000.05,
      targetPrice: 54300.05,
      stopPrice: 53800,
    })
  })

  it('applies dragged levels and recomputes derived fields', () => {
    const view: VisualBracketView = {
      status: 'staged',
      underlying: 'NIFTY',
      underlyingExchange: 'NSE_INDEX',
      foExchange: 'NFO',
      action: 'BUY',
      optionType: 'CE',
      bias: 'bullish',
      entryReference: 24500,
      targetPrice: 24600,
      stopPrice: 24400,
      clickPrice: 24500,
      tickSize: 0.05,
      lotSize: 65,
      lots: 1,
      quantity: 65,
      targetOffset: 100,
      stopOffset: 100,
      riskReward: 1,
    }
    const next = withVisualLevels(view, {
      entryReference: 24500,
      targetPrice: 24650,
      stopPrice: 24425,
    })
    expect(next.targetOffset).toBe(150)
    expect(next.stopOffset).toBe(75)
    expect(next.riskReward).toBeCloseTo(2)
  })

  it('estimates currency P&L from delta only when delta is present', () => {
    expect(estimateCurrencyPnl(0.5, 100, 65)).toBe(3250)
    expect(estimateCurrencyPnl(0.5, -100, 65)).toBe(-3250)
    expect(estimateCurrencyPnl(0.5, -100, 65, 'SELL')).toBe(3250)
    expect(estimateCurrencyPnl(null, 100, 65)).toBeNull()
  })
})
