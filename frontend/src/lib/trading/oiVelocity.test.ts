import { describe, expect, it } from 'vitest'
import type { OptionChainResponse, OptionData } from '@/types/option-chain'
import {
  breakWatchForLevel,
  computeOiVelocity,
  formatSignedCompact,
  formatSignedDecimal,
  nearestOiPeak,
  type OiSnapshot,
  oiSnapshotFromChain,
  strikeStepFromLevels,
  trimOiSnapshots,
} from './oiVelocity'

const leg = (symbol: string, oi: number): OptionData => ({
  symbol,
  label: symbol,
  ltp: 100,
  bid: 99,
  ask: 101,
  bid_qty: 10,
  ask_qty: 10,
  open: 100,
  high: 102,
  low: 98,
  prev_close: 100,
  volume: 100,
  oi,
  lotsize: 50,
  tick_size: 0.05,
})

function chain(spot: number, rows: [number, number, number][]): OptionChainResponse {
  return {
    status: 'success',
    underlying: 'NIFTY',
    underlying_symbol: 'NIFTY',
    underlying_exchange: 'NSE_INDEX',
    underlying_ltp: spot,
    underlying_prev_close: spot,
    expiry_date: '30OCT26',
    atm_strike: 100,
    chain: rows.map(([strike, ceOi, peOi]) => ({
      strike,
      ce: leg(`CE${strike}`, ceOi),
      pe: leg(`PE${strike}`, peOi),
    })),
  }
}

const snapshot = (timestamp: number, spot: number, rows: [number, number, number][]): OiSnapshot =>
  oiSnapshotFromChain(chain(spot, rows), timestamp)

describe('oiSnapshotFromChain', () => {
  it('keeps sorted OI levels and the chain PCR', () => {
    const result = oiSnapshotFromChain(
      chain(100, [
        [110, 5000, 1000],
        [90, 1000, 2000],
        [100, 3000, 500],
      ]),
      1_000
    )

    expect(result.timestamp).toBe(1_000)
    expect(result.spot).toBe(100)
    expect(result.pcr).toBeCloseTo(3500 / 9000)
    expect(result.levels).toEqual([
      { strike: 90, ceOi: 1000, peOi: 2000 },
      { strike: 100, ceOi: 3000, peOi: 500 },
      { strike: 110, ceOi: 5000, peOi: 1000 },
    ])
  })

  it('treats missing legs and non-positive OI as zero', () => {
    const response = chain(100, [[100, -5, 0]])
    response.chain[0].pe = null
    const result = oiSnapshotFromChain(response, 1)
    expect(result.levels).toEqual([{ strike: 100, ceOi: 0, peOi: 0 }])
    expect(result.pcr).toBe(0)
  })
})

describe('trimOiSnapshots', () => {
  it('drops samples outside the window and caps the buffer', () => {
    const samples = Array.from({ length: 40 }, (_, index) =>
      snapshot(index * 30_000, 100, [[100, index, index]])
    )
    const trimmed = trimOiSnapshots(samples, 39 * 30_000)
    expect(trimmed).toHaveLength(31)
    expect(trimmed[0].timestamp).toBe(9 * 30_000)
    expect(trimmed[trimmed.length - 1].timestamp).toBe(39 * 30_000)
  })
})

describe('strikeStepFromLevels', () => {
  it('uses the smallest positive gap', () => {
    expect(
      strikeStepFromLevels([
        { strike: 100, ceOi: 0, peOi: 0 },
        { strike: 105, ceOi: 0, peOi: 0 },
        { strike: 115, ceOi: 0, peOi: 0 },
      ])
    ).toBe(5)
    expect(strikeStepFromLevels([])).toBe(0)
  })
})

describe('nearestOiPeak', () => {
  const levels = [
    { strike: 100, ceOi: 10, peOi: 10 },
    { strike: 105, ceOi: 50, peOi: 60 },
    { strike: 110, ceOi: 20, peOi: 20 },
    { strike: 115, ceOi: 80, peOi: 30 },
    { strike: 120, ceOi: 30, peOi: 80 },
  ]

  it('picks the nearest local maximum on each side of spot', () => {
    expect(nearestOiPeak(levels, 'ce', 107)).toBe(115)
    expect(nearestOiPeak(levels, 'pe', 107)).toBe(105)
  })

  it('falls back to the side maximum and returns null without a side', () => {
    expect(nearestOiPeak(levels, 'ce', 130)).toBeNull()
    expect(nearestOiPeak(levels, 'pe', 90)).toBeNull()
    expect(
      nearestOiPeak(
        [
          { strike: 100, ceOi: 10, peOi: 10 },
          { strike: 110, ceOi: 20, peOi: 20 },
        ],
        'ce',
        100
      )
    ).toBe(110)
  })
})

describe('computeOiVelocity', () => {
  const first = snapshot(0, 100, [
    [95, 100, 200],
    [100, 300, 100],
    [105, 500, 50],
  ])
  const second = snapshot(30_000, 101, [
    [95, 120, 260],
    [100, 280, 160],
    [105, 560, 40],
  ])
  const third = snapshot(60_000, 102, [
    [95, 140, 320],
    [100, 260, 220],
    [105, 620, 30],
  ])

  it('derives per-strike change, velocity, pressure and next levels', () => {
    const result = computeOiVelocity([first, second, third], {
      windowMinutes: 1,
      pollIntervalMs: 30_000,
    })

    expect(result).not.toBeNull()
    expect(result?.samples).toBe(3)
    expect(result?.windowReady).toBe(true)
    expect(result?.netDelta).toBe(100)
    expect(result?.pressure).toBeCloseTo(100 / 1250)
    expect(result?.pcrChange).toBeCloseTo(570 / 1020 - 350 / 900)
    expect(result?.levels.find((level) => level.strike === 100)).toMatchObject({
      ceDelta: -40,
      peDelta: 120,
      netDelta: 160,
      netVelocity: 160,
    })
    expect(result?.nextResistance?.strike).toBe(105)
    expect(result?.nextSupport?.strike).toBe(95)
    expect(result?.nextResistance?.score).toBeGreaterThanOrEqual(0)
    expect(result?.nextResistance?.score).toBeLessThanOrEqual(100)
  })

  it('stays in warm-up until the 15-minute window has enough samples', () => {
    const result = computeOiVelocity([first, second, third])
    expect(result?.windowReady).toBe(false)
    expect(result?.samples).toBe(3)
    expect(result?.expectedSamples).toBe(30)
    expect(result?.nextResistance?.label).toBe('warming')
  })

  it('returns a warm-up shell for a single snapshot and null for none', () => {
    expect(computeOiVelocity([first])?.levels).toEqual([])
    expect(computeOiVelocity([])).toBeNull()
  })
})

describe('breakWatchForLevel', () => {
  it('raises the score when pressure and level erosion point the same way', () => {
    const resistance = breakWatchForLevel({
      level: 105,
      side: 'resistance',
      spot: 104,
      step: 5,
      levelChange: -100,
      levelBase: 1000,
      pressure: 0.5,
      pcrChange: 0.1,
      spotChange: 2,
      windowReady: true,
      samples: 30,
    })
    expect(resistance.score).toBeGreaterThanOrEqual(70)
    expect(resistance.label).toBe('elevated')

    const support = breakWatchForLevel({
      level: 95,
      side: 'support',
      spot: 96,
      step: 5,
      levelChange: -100,
      levelBase: 1000,
      pressure: -0.5,
      pcrChange: -0.1,
      spotChange: -2,
      windowReady: true,
      samples: 30,
    })
    expect(support.score).toBeGreaterThanOrEqual(70)
    expect(support.label).toBe('elevated')
  })

  it('uses price direction as evidence for the break', () => {
    const withTrend = breakWatchForLevel({
      level: 105,
      side: 'resistance',
      spot: 104,
      step: 5,
      levelChange: 0,
      levelBase: 1000,
      pressure: 0,
      pcrChange: 0,
      spotChange: 2,
      windowReady: true,
      samples: 30,
    })
    const againstTrend = breakWatchForLevel({
      level: 105,
      side: 'resistance',
      spot: 104,
      step: 5,
      levelChange: 0,
      levelBase: 1000,
      pressure: 0,
      pcrChange: 0,
      spotChange: -2,
      windowReady: true,
      samples: 30,
    })
    expect(withTrend.score).toBeGreaterThan(againstTrend.score)
  })

  it('reports warming until the window is ready', () => {
    const watch = breakWatchForLevel({
      level: 105,
      side: 'resistance',
      spot: 104,
      step: 5,
      levelChange: 0,
      levelBase: 1000,
      pressure: 0,
      pcrChange: 0,
      windowReady: false,
      samples: 3,
    })
    expect(watch.label).toBe('warming')
  })
})

describe('format helpers', () => {
  it('formats signed compact and decimal values', () => {
    expect(formatSignedCompact(150_000)).toBe('+1.50L')
    expect(formatSignedCompact(-1_500)).toBe('-1.5K')
    expect(formatSignedCompact(0)).toBe('0')
    expect(formatSignedDecimal(0.123)).toBe('+0.12')
    expect(formatSignedDecimal(-0.1)).toBe('-0.10')
    expect(formatSignedDecimal(0)).toBe('0.00')
  })
})
