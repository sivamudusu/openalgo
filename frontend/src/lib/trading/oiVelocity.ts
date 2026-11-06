import { compactOptionChainNumber } from '@/lib/optionChainMetrics'
import type { OptionChainResponse } from '@/types/option-chain'

export const OI_VELOCITY_WINDOW_MINUTES = 15
export const OI_VELOCITY_POLL_MS = 30_000
export const OI_VELOCITY_MAX_GAP_MS = OI_VELOCITY_POLL_MS * 2
export const OI_VELOCITY_EXPECTED_SAMPLES = Math.ceil(
  (OI_VELOCITY_WINDOW_MINUTES * 60_000) / OI_VELOCITY_POLL_MS
)

export interface OiLevelSnapshot {
  strike: number
  ceOi: number
  peOi: number
}

export interface OiSnapshot {
  timestamp: number
  spot: number
  pcr: number
  levels: OiLevelSnapshot[]
}

export interface OiVelocityLevel {
  strike: number
  ceDelta: number
  peDelta: number
  ceVelocity: number
  peVelocity: number
  netDelta: number
  netVelocity: number
  pressure: number
}

export type OiBreakSide = 'support' | 'resistance'
export type OiBreakLabel = 'warming' | 'quiet' | 'building' | 'elevated'

export interface OiBreakWatch {
  strike: number
  side: OiBreakSide
  score: number
  label: OiBreakLabel
  pressure: number
  levelChange: number
  distance: number
}

export interface OiVelocityData {
  windowMinutes: number
  samples: number
  expectedSamples: number
  windowReady: boolean
  elapsedMs: number
  pressure: number
  netDelta: number
  netVelocity: number
  pcrChange: number
  pcrVelocity: number
  spotChange: number
  spotVelocity: number
  levels: OiVelocityLevel[]
  nextSupport: OiBreakWatch | null
  nextResistance: OiBreakWatch | null
}

export interface OiVelocityOptions {
  windowMinutes?: number
  pollIntervalMs?: number
}

function safeOi(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function oiSnapshotFromChain(chain: OptionChainResponse, timestamp: number): OiSnapshot {
  const levels = chain.chain
    .map((row) => ({
      strike: row.strike,
      ceOi: safeOi(row.ce?.oi),
      peOi: safeOi(row.pe?.oi),
    }))
    .sort((a, b) => a.strike - b.strike)
  const ceTotal = levels.reduce((sum, level) => sum + level.ceOi, 0)
  const peTotal = levels.reduce((sum, level) => sum + level.peOi, 0)
  return {
    timestamp,
    spot: Number.isFinite(chain.underlying_ltp) ? chain.underlying_ltp : 0,
    pcr: ceTotal > 0 ? peTotal / ceTotal : 0,
    levels,
  }
}

export function trimOiSnapshots(
  snapshots: readonly OiSnapshot[],
  latestTimestamp: number,
  options: OiVelocityOptions = {}
): OiSnapshot[] {
  const windowMinutes = options.windowMinutes ?? OI_VELOCITY_WINDOW_MINUTES
  const pollIntervalMs = options.pollIntervalMs ?? OI_VELOCITY_POLL_MS
  const windowMs = windowMinutes * 60_000
  const maxSamples = Math.ceil(windowMs / pollIntervalMs) + 1
  const ordered = [...snapshots]
    .filter((snapshot) => snapshot.timestamp <= latestTimestamp)
    .sort((a, b) => a.timestamp - b.timestamp)
    .filter((snapshot) => snapshot.timestamp >= latestTimestamp - windowMs)
  return ordered.length > maxSamples ? ordered.slice(-maxSamples) : ordered
}

export function strikeStepFromLevels(levels: readonly OiLevelSnapshot[]): number {
  const strikes = [...new Set(levels.map((level) => level.strike))].sort((a, b) => a - b)
  let step = 0
  for (let i = 1; i < strikes.length; i += 1) {
    const gap = strikes[i] - strikes[i - 1]
    if (gap > 0 && (step === 0 || gap < step)) step = gap
  }
  return step
}

export function nearestOiPeak(
  levels: readonly OiLevelSnapshot[],
  side: 'ce' | 'pe',
  spot: number
): number | null {
  if (!(spot > 0)) return null
  const ordered = [...new Set(levels.map((level) => level.strike))].sort((a, b) => a - b)
  const oiByStrike = new Map(
    levels.map((level) => [level.strike, level[side === 'ce' ? 'ceOi' : 'peOi']])
  )
  let best: number | null = null
  let bestOi = -1
  let bestDistance = Number.POSITIVE_INFINITY

  for (let i = 0; i < ordered.length; i += 1) {
    const strike = ordered[i]
    if (side === 'ce' && strike < spot) continue
    if (side === 'pe' && strike > spot) continue
    const oi = oiByStrike.get(strike) ?? 0
    const lower = i > 0 ? (oiByStrike.get(ordered[i - 1]) ?? 0) : null
    const higher = i < ordered.length - 1 ? (oiByStrike.get(ordered[i + 1]) ?? 0) : null
    if (lower !== null && oi <= lower) continue
    if (higher !== null && oi <= higher) continue
    const distance = Math.abs(strike - spot)
    if (
      best === null ||
      distance < bestDistance ||
      (distance === bestDistance && oi > bestOi) ||
      (distance === bestDistance && oi === bestOi && strike < best)
    ) {
      best = strike
      bestOi = oi
      bestDistance = distance
    }
  }

  if (best !== null) return best

  const pool = ordered.filter((strike) => (side === 'ce' ? strike >= spot : strike <= spot))
  if (pool.length === 0) return null
  return pool.reduce((chosen, strike) => {
    const oi = oiByStrike.get(strike) ?? 0
    const chosenOi = oiByStrike.get(chosen) ?? 0
    if (oi > chosenOi) return strike
    if (oi < chosenOi) return chosen
    return Math.abs(strike - spot) < Math.abs(chosen - spot) ? strike : chosen
  })
}

export function breakWatchForLevel({
  level,
  side,
  spot,
  step,
  levelChange,
  levelBase,
  pressure,
  pcrChange,
  spotChange,
  windowReady,
  samples,
}: {
  level: number
  side: OiBreakSide
  spot: number
  step: number
  levelChange: number
  levelBase: number
  pressure: number
  pcrChange: number
  spotChange?: number
  windowReady: boolean
  samples: number
}): OiBreakWatch {
  const distance = Math.abs(level - spot)
  const proximity = step > 0 ? clamp(1 - distance / (3 * step), 0, 1) : 0
  const levelErosion = levelBase > 0 ? clamp(-levelChange / levelBase, -1, 1) : 0
  const directionalPressure = side === 'resistance' ? pressure : -pressure
  const directionalPcr = clamp((side === 'resistance' ? pcrChange : -pcrChange) / 0.2, -1, 1)
  const spotTrend = step > 0 ? clamp((spotChange ?? 0) / (2 * step), -1, 1) : 0
  const directionalSpot = side === 'resistance' ? spotTrend : -spotTrend
  const evidence =
    0.4 * clamp(directionalPressure, -1, 1) +
    0.25 * levelErosion +
    0.15 * directionalSpot +
    0.1 * directionalPcr +
    0.1 * proximity
  const score = Math.round(100 * clamp(0.5 + evidence / 2, 0, 1))
  const label: OiBreakLabel =
    !windowReady || samples < 2
      ? 'warming'
      : score >= 70
        ? 'elevated'
        : score >= 58
          ? 'building'
          : 'quiet'
  return {
    strike: level,
    side,
    score,
    label,
    pressure: directionalPressure,
    levelChange,
    distance,
  }
}

export function computeOiVelocity(
  snapshots: readonly OiSnapshot[],
  options: OiVelocityOptions = {}
): OiVelocityData | null {
  if (snapshots.length === 0) return null
  const windowMinutes = options.windowMinutes ?? OI_VELOCITY_WINDOW_MINUTES
  const pollIntervalMs = options.pollIntervalMs ?? OI_VELOCITY_POLL_MS
  const expectedSamples = Math.ceil((windowMinutes * 60_000) / pollIntervalMs)
  const windowMs = windowMinutes * 60_000
  const ordered = [...snapshots].sort((a, b) => a.timestamp - b.timestamp)
  const latest = ordered[ordered.length - 1]
  const inWindow = ordered.filter((snapshot) => snapshot.timestamp >= latest.timestamp - windowMs)
  const oldest = inWindow[0]
  const elapsedMs = latest.timestamp - oldest.timestamp
  const windowReady = inWindow.length >= expectedSamples && elapsedMs >= windowMs - pollIntervalMs

  if (inWindow.length < 2 || elapsedMs <= 0) {
    return {
      windowMinutes,
      samples: inWindow.length,
      expectedSamples,
      windowReady: false,
      elapsedMs,
      pressure: 0,
      netDelta: 0,
      netVelocity: 0,
      pcrChange: 0,
      pcrVelocity: 0,
      spotChange: 0,
      spotVelocity: 0,
      levels: [],
      nextSupport: null,
      nextResistance: null,
    }
  }

  const minutes = elapsedMs / 60_000
  const levels: OiVelocityLevel[] = []
  let totalNetDelta = 0
  let totalBaseOi = 0

  for (const current of latest.levels) {
    const start = oldest.levels.find((level) => level.strike === current.strike)
    if (!start) continue
    const ceDelta = current.ceOi - start.ceOi
    const peDelta = current.peOi - start.peOi
    const netDelta = peDelta - ceDelta
    const baseOi = start.ceOi + start.peOi
    levels.push({
      strike: current.strike,
      ceDelta,
      peDelta,
      ceVelocity: ceDelta / minutes,
      peVelocity: peDelta / minutes,
      netDelta,
      netVelocity: netDelta / minutes,
      pressure: baseOi > 0 ? clamp(netDelta / baseOi, -1, 1) : 0,
    })
    totalNetDelta += netDelta
    totalBaseOi += baseOi
  }

  levels.sort((a, b) => a.strike - b.strike)
  const pressure = totalBaseOi > 0 ? clamp(totalNetDelta / totalBaseOi, -1, 1) : 0
  const pcrChange = latest.pcr - oldest.pcr
  const spotChange = latest.spot - oldest.spot
  const step = strikeStepFromLevels(latest.levels)
  const resistanceStrike = nearestOiPeak(latest.levels, 'ce', latest.spot)
  const supportStrike = nearestOiPeak(latest.levels, 'pe', latest.spot)
  const resistanceLevel = levels.find((level) => level.strike === resistanceStrike)
  const supportLevel = levels.find((level) => level.strike === supportStrike)
  const resistanceBase =
    resistanceStrike === null
      ? 0
      : (oldest.levels.find((level) => level.strike === resistanceStrike)?.ceOi ?? 0)
  const supportBase =
    supportStrike === null
      ? 0
      : (oldest.levels.find((level) => level.strike === supportStrike)?.peOi ?? 0)

  return {
    windowMinutes,
    samples: inWindow.length,
    expectedSamples,
    windowReady,
    elapsedMs,
    pressure,
    netDelta: totalNetDelta,
    netVelocity: minutes > 0 ? totalNetDelta / minutes : 0,
    pcrChange,
    pcrVelocity: minutes > 0 ? pcrChange / minutes : 0,
    spotChange,
    spotVelocity: minutes > 0 ? spotChange / minutes : 0,
    levels,
    nextSupport:
      supportStrike === null
        ? null
        : breakWatchForLevel({
            level: supportStrike,
            side: 'support',
            spot: latest.spot,
            step,
            levelChange: supportLevel?.peDelta ?? 0,
            levelBase: supportBase,
            pressure,
            pcrChange,
            spotChange,
            windowReady,
            samples: inWindow.length,
          }),
    nextResistance:
      resistanceStrike === null
        ? null
        : breakWatchForLevel({
            level: resistanceStrike,
            side: 'resistance',
            spot: latest.spot,
            step,
            levelChange: resistanceLevel?.ceDelta ?? 0,
            levelBase: resistanceBase,
            pressure,
            pcrChange,
            spotChange,
            windowReady,
            samples: inWindow.length,
          }),
  }
}

export function formatSignedCompact(value: number): string {
  if (!Number.isFinite(value) || value === 0) return '0'
  const compact = compactOptionChainNumber(Math.abs(value))
  if (compact === '-') return '0'
  return `${value > 0 ? '+' : '-'}${compact}`
}

export function formatSignedDecimal(value: number, dp = 2): string {
  if (!Number.isFinite(value) || value === 0) return '0.00'
  return `${value > 0 ? '+' : ''}${value.toFixed(dp)}`
}
