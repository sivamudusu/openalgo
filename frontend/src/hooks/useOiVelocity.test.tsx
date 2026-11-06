import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { OptionChainResponse, OptionData } from '@/types/option-chain'
import { useOiVelocity } from './useOiVelocity'

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

function chain(
  spot: number,
  rows: [number, number, number][],
  expiry = '30OCT26'
): OptionChainResponse {
  return {
    status: 'success',
    underlying: 'NIFTY',
    underlying_symbol: 'NIFTY',
    underlying_exchange: 'NSE_INDEX',
    underlying_ltp: spot,
    underlying_prev_close: spot,
    expiry_date: expiry,
    atm_strike: 100,
    chain: rows.map(([strike, ceOi, peOi]) => ({
      strike,
      ce: leg(`CE${strike}`, ceOi),
      pe: leg(`PE${strike}`, peOi),
    })),
  }
}

describe('useOiVelocity', () => {
  it('accumulates polled OI snapshots and resets when the expiry changes', async () => {
    const { result, rerender } = renderHook(
      ({ data, at }: { data: OptionChainResponse; at: Date }) =>
        useOiVelocity({
          enabled: true,
          chain: data,
          lastPollUpdate: at,
          strikeCount: 10,
          windowMinutes: 15,
          pollIntervalMs: 30_000,
        }),
      {
        initialProps: {
          data: chain(100, [[100, 100, 200]]),
          at: new Date(0),
        },
      }
    )

    await waitFor(() => expect(result.current.velocity?.samples).toBe(1))

    rerender({
      data: chain(101, [[100, 120, 260]]),
      at: new Date(30_000),
    })
    await waitFor(() => expect(result.current.velocity?.samples).toBe(2))
    expect(result.current.velocity?.windowReady).toBe(false)
    expect(result.current.velocity?.netDelta).toBe(40)

    rerender({
      data: chain(102, [[100, 140, 320]], '27NOV26'),
      at: new Date(60_000),
    })
    await waitFor(() => expect(result.current.velocity?.samples).toBe(1))
  })

  it('restarts the window after a polling gap', async () => {
    const { result, rerender } = renderHook(
      ({ data, at }: { data: OptionChainResponse; at: Date }) =>
        useOiVelocity({
          enabled: true,
          chain: data,
          lastPollUpdate: at,
          strikeCount: 10,
          windowMinutes: 15,
          pollIntervalMs: 30_000,
        }),
      {
        initialProps: {
          data: chain(100, [[100, 100, 200]]),
          at: new Date(0),
        },
      }
    )
    await waitFor(() => expect(result.current.velocity?.samples).toBe(1))

    rerender({ data: chain(101, [[100, 120, 260]]), at: new Date(30_000) })
    await waitFor(() => expect(result.current.velocity?.samples).toBe(2))

    rerender({ data: chain(102, [[100, 140, 320]]), at: new Date(120_000) })
    await waitFor(() => expect(result.current.velocity?.samples).toBe(1))
    expect(result.current.velocity?.levels).toEqual([])
  })

  it('clears history when disabled', async () => {
    const { result, rerender } = renderHook(
      ({ enabled, data, at }: { enabled: boolean; data: OptionChainResponse; at: Date }) =>
        useOiVelocity({
          enabled,
          chain: data,
          lastPollUpdate: at,
          strikeCount: 10,
          windowMinutes: 15,
          pollIntervalMs: 30_000,
        }),
      {
        initialProps: {
          enabled: true,
          data: chain(100, [[100, 100, 200]]),
          at: new Date(0),
        },
      }
    )
    await waitFor(() => expect(result.current.velocity?.samples).toBe(1))

    rerender({ enabled: false, data: chain(100, [[100, 100, 200]]), at: new Date(0) })
    await waitFor(() => expect(result.current.velocity).toBeNull())
  })
})
