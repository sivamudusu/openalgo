import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { scalpingApi } from '@/api/scalping'
import {
  loadOptionUnderlyings,
  matchOptionUnderlying,
  optionChainCandidates,
  resetOptionUnderlyingsCache,
} from './optionChainUnderlying'

vi.mock('@/api/scalping', () => ({
  scalpingApi: { getAllUnderlyings: vi.fn() },
}))

const getAllUnderlyings = vi.mocked(scalpingApi.getAllUnderlyings)

describe('optionChainCandidates', () => {
  it('tries the index symbol before its display name', () => {
    expect(
      optionChainCandidates({ symbol: 'NIFTY', exchange: 'NSE_INDEX', name: 'NIFTY 50' })
    ).toEqual({ segment: 'NFO', candidates: ['NIFTY', 'NIFTY 50'] })
  })

  it('tries a derivative contract name before the contract symbol', () => {
    expect(
      optionChainCandidates({ symbol: 'NIFTY29SEP26FUT', exchange: 'NFO', name: 'NIFTY' })
    ).toEqual({ segment: 'NFO', candidates: ['NIFTY', 'NIFTY29SEP26FUT'] })
  })

  it('tries a cash symbol before the company name', () => {
    expect(
      optionChainCandidates({
        symbol: 'RELIANCE',
        exchange: 'NSE',
        name: 'Reliance Industries',
      })
    ).toEqual({ segment: 'NFO', candidates: ['RELIANCE', 'Reliance Industries'] })
  })

  it('maps BSE to BFO and CDS/MCX to their own segments', () => {
    expect(
      optionChainCandidates({ symbol: 'SENSEX', exchange: 'BSE_INDEX', name: 'SENSEX' })
    ).toEqual({ segment: 'BFO', candidates: ['SENSEX'] })
    expect(
      optionChainCandidates({ symbol: 'USDINR25DEC24FUT', exchange: 'CDS', name: 'USDINR' })
    ).toEqual({ segment: 'CDS', candidates: ['USDINR', 'USDINR25DEC24FUT'] })
    expect(
      optionChainCandidates({ symbol: 'CRUDEOIL25DEC24FUT', exchange: 'MCX', name: 'CRUDEOIL' })
    ).toEqual({ segment: 'MCX', candidates: ['CRUDEOIL', 'CRUDEOIL25DEC24FUT'] })
  })

  it('refuses exchanges and expressions with no chain', () => {
    expect(optionChainCandidates({ symbol: 'BTCUSD', exchange: 'CRYPTO', name: 'BTC' })).toBeNull()
    expect(
      optionChainCandidates({
        symbol: 'NIFTY/RELIANCE',
        exchange: 'NSE',
        name: 'Expression',
        synthetic: true,
      })
    ).toBeNull()
    expect(optionChainCandidates(null)).toBeNull()
  })
})

describe('matchOptionUnderlying', () => {
  it('matches the chart symbol exactly', () => {
    expect(matchOptionUnderlying(['NIFTY', 'BANKNIFTY'], ['NIFTY', 'NIFTY 50'])).toBe('NIFTY')
  })

  it('bridges index display names to the chain spelling', () => {
    expect(matchOptionUnderlying(['NIFTY', 'BANKNIFTY'], ['NIFTY 50'])).toBe('NIFTY')
    expect(matchOptionUnderlying(['BANKNIFTY'], ['NIFTY BANK'])).toBe('BANKNIFTY')
    expect(matchOptionUnderlying(['FINNIFTY'], ['NIFTY FIN SERVICE'])).toBe('FINNIFTY')
    expect(matchOptionUnderlying(['MIDCPNIFTY'], ['NIFTY MID SELECT'])).toBe('MIDCPNIFTY')
    expect(matchOptionUnderlying(['NIFTYNXT50'], ['NIFTY NEXT 50'])).toBe('NIFTYNXT50')
  })

  it('falls through to the contract name and then gives up', () => {
    expect(matchOptionUnderlying(['NIFTY'], ['NIFTY29SEP26FUT', 'NIFTY'])).toBe('NIFTY')
    expect(matchOptionUnderlying(['NIFTY'], ['SENSEX'])).toBeNull()
    expect(matchOptionUnderlying([], ['NIFTY'])).toBeNull()
  })
})

describe('loadOptionUnderlyings', () => {
  beforeEach(() => {
    resetOptionUnderlyingsCache()
    getAllUnderlyings.mockReset()
    vi.useRealTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shares one request between simultaneous panes', async () => {
    getAllUnderlyings.mockResolvedValue({ status: 'success', data: ['NIFTY', 'BANKNIFTY'] })

    const [first, second] = await Promise.all([
      loadOptionUnderlyings('NFO'),
      loadOptionUnderlyings('NFO'),
    ])

    expect(getAllUnderlyings).toHaveBeenCalledTimes(1)
    expect(first).toEqual(['NIFTY', 'BANKNIFTY'])
    expect(second).toEqual(['NIFTY', 'BANKNIFTY'])
  })

  it('drops empty names', async () => {
    getAllUnderlyings.mockResolvedValue({ status: 'success', data: ['NIFTY', '', '   '] })
    await expect(loadOptionUnderlyings('NFO')).resolves.toEqual(['NIFTY'])
  })

  it('retries after a failed request', async () => {
    getAllUnderlyings
      .mockRejectedValueOnce(new Error('unavailable'))
      .mockResolvedValueOnce({ status: 'success', data: ['NIFTY'] })

    await expect(loadOptionUnderlyings('NFO')).rejects.toThrow('unavailable')
    await expect(loadOptionUnderlyings('NFO')).resolves.toEqual(['NIFTY'])
    expect(getAllUnderlyings).toHaveBeenCalledTimes(2)
  })

  it('refetches once the five-minute cache expires', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    getAllUnderlyings.mockResolvedValue({ status: 'success', data: ['NIFTY'] })

    await loadOptionUnderlyings('NFO')
    await loadOptionUnderlyings('NFO')
    expect(getAllUnderlyings).toHaveBeenCalledTimes(1)

    vi.setSystemTime(new Date('2026-01-01T00:05:01Z'))
    await loadOptionUnderlyings('NFO')
    expect(getAllUnderlyings).toHaveBeenCalledTimes(2)
  })
})
