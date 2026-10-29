import { afterEach, describe, expect, it } from 'vitest'
import { peerHoldsSymbol } from './strategyPeers'

afterEach(() => localStorage.clear())

function hold(key: string, symbol: string) {
  localStorage.setItem(key, JSON.stringify({ symbol }))
}

describe('peerHoldsSymbol', () => {
  it('sees a NIFTY loop holding the same leg as a BANKNIFTY loop', () => {
    hold('sahasra.nifty.autoPos', 'NIFTY28OCT2625000CE')

    expect(peerHoldsSymbol('NIFTY28OCT2625000CE', 'momentum.autoPos')).toBe(true)
  })

  it('sees a NIFTY momentum loop from a NIFTY Sahasra loop', () => {
    hold('momentum.nifty.autoPos', 'NIFTY28OCT2625000PE')

    expect(peerHoldsSymbol('NIFTY28OCT2625000PE', 'sahasra.nifty.autoPos')).toBe(true)
  })

  it('ignores the asking loop own position', () => {
    hold('momentum.nifty.autoPos', 'NIFTY28OCT2625000CE')

    expect(peerHoldsSymbol('NIFTY28OCT2625000CE', 'momentum.nifty.autoPos')).toBe(false)
  })

  it('does not collide two different contracts', () => {
    hold('sahasra.nifty.autoPos', 'NIFTY28OCT2625000CE')

    expect(peerHoldsSymbol('NIFTY28OCT2626000CE', 'momentum.autoPos')).toBe(false)
  })

  it('treats an unreadable entry as no position', () => {
    localStorage.setItem('sahasra.autoPos', '{not json')

    expect(peerHoldsSymbol('BANKNIFTY28OCT2655000CE', 'momentum.autoPos')).toBe(false)
  })
})
