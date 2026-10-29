import { beforeEach, describe, expect, it, vi } from 'vitest'
import { sahasraApi } from './sahasra'

const get = vi.fn()
vi.mock('./client', () => ({
  webClient: { get: (...args: unknown[]) => get(...args) },
}))

beforeEach(() => {
  get.mockReset()
  get.mockResolvedValue({ data: { status: 'success', data: {} } })
})

describe('sahasraApi underlying selection', () => {
  it('asks for BANKNIFTY when the caller names no underlying', async () => {
    await sahasraApi.getLevels()
    await sahasraApi.getSignal()

    expect(get).toHaveBeenNthCalledWith(1, '/sahasra/api/levels', {
      params: { underlying: 'BANKNIFTY' },
    })
    expect(get).toHaveBeenNthCalledWith(2, '/sahasra/api/signal', {
      params: { underlying: 'BANKNIFTY' },
    })
  })

  it('passes NIFTY to both routes', async () => {
    await sahasraApi.getLevels('NIFTY')
    await sahasraApi.getSignal('NIFTY')

    expect(get).toHaveBeenNthCalledWith(1, '/sahasra/api/levels', {
      params: { underlying: 'NIFTY' },
    })
    expect(get).toHaveBeenNthCalledWith(2, '/sahasra/api/signal', {
      params: { underlying: 'NIFTY' },
    })
  })
})
