import { describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent } from '@/test/test-utils'
import { OptionChainOverlayControl } from './OptionChainOverlayControl'
import type { OptionChainOverlayContextValue } from './OptionChainOverlayProvider'

const context = vi.hoisted(() => ({ current: null as OptionChainOverlayContextValue | null }))

vi.mock('./OptionChainOverlayProvider', () => ({
  useOptionChainOverlayContext: () => context.current,
}))

function contextValue(
  overrides: Partial<OptionChainOverlayContextValue> = {}
): OptionChainOverlayContextValue {
  return {
    available: true,
    active: true,
    settings: {
      metric: 'oi',
      strikeCount: 10,
      showValues: false,
      showLevels: true,
      showVelocity: true,
      expiry: null,
    },
    state: {
      status: 'ready',
      identity: { underlying: 'NIFTY', exchange: 'NSE_INDEX', expiry: '30OCT26' },
      expiry: '30OCT26',
      expiries: ['30OCT26'],
      chain: null,
      data: { spot: 24_175.65, pcr: 1.08, underlying: 'NIFTY', expiry: '30OCT26', levels: [] },
      velocity: {
        windowMinutes: 15,
        samples: 30,
        expectedSamples: 30,
        windowReady: true,
        elapsedMs: 870_000,
        pressure: 0.32,
        netDelta: 1_200,
        netVelocity: 80,
        pcrChange: 0.05,
        pcrVelocity: 0.003,
        spotChange: 42,
        spotVelocity: 2.8,
        levels: [],
        nextSupport: {
          strike: 24_000,
          side: 'support',
          score: 68,
          label: 'building',
          pressure: 0.4,
          levelChange: -200,
          distance: 175,
        },
        nextResistance: {
          strike: 24_300,
          side: 'resistance',
          score: 75,
          label: 'elevated',
          pressure: 0.4,
          levelChange: -200,
          distance: 124,
        },
      },
      isLoading: false,
      isStreaming: true,
      isPaused: false,
      error: null,
      lastUpdate: new Date(),
      retry: vi.fn(),
      setExpiry: vi.fn(),
    } as OptionChainOverlayContextValue['state'],
    setActive: vi.fn(),
    setSettings: vi.fn(),
    ...overrides,
  }
}

describe('OptionChainOverlayControl', () => {
  it('shows the velocity readout and toggles the setting', async () => {
    context.current = contextValue()
    render(<OptionChainOverlayControl />)

    await userEvent.click(screen.getByRole('button', { name: /chain/i }))

    expect(await screen.findByText('OI 15m +1.2K net · pressure +0.32')).toBeInTheDocument()
    expect(screen.getByText('Next R 24,300: 75/100 elevated')).toBeInTheDocument()
    expect(screen.getByText('Next S 24,000: 68/100 building')).toBeInTheDocument()

    await userEvent.click(
      screen.getByRole('switch', {
        name: 'Show 15-minute open interest velocity and break watch',
      })
    )
    expect(context.current?.setSettings).toHaveBeenCalledWith({ showVelocity: false })
  })
})
