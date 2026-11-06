import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor } from '@/test/test-utils'
import { ManualSection } from './ManualSection'

const mocks = vi.hoisted(() => ({
  signal: vi.fn(),
  placeOrder: vi.fn(),
  closeLeg: vi.fn(),
  toast: vi.fn(),
}))

vi.mock('@/api/sahasra', () => ({
  sahasraApi: { getSignal: mocks.signal },
}))
vi.mock('@/api/low-iv', () => ({
  lowIvApi: { placeOrder: mocks.placeOrder, closeLeg: mocks.closeLeg },
}))
vi.mock('@/hooks/useMarketData', () => ({
  useMarketData: () => ({
    data: new Map([
      ['NSE_INDEX:BANKNIFTY', { data: { ltp: 54_000 } }],
      ['NFO:BANKNIFTY30OCT2654000CE', { data: { ltp: 120.5 } }],
      ['NFO:BANKNIFTY30OCT2654000PE', { data: { ltp: 90.25 } }],
    ]),
  }),
}))
vi.mock('@/utils/toast', () => ({
  showToast: { success: mocks.toast, error: mocks.toast, info: mocks.toast },
}))
vi.mock('./LowIvSection', () => ({ hintFor: () => null }))

const signal = {
  status: 'success',
  data: {
    underlying: 'BANKNIFTY',
    expiry: '30OCT26',
    atm_strike: 54_000,
    spot: 54_000,
    support: null,
    resistance: null,
    pcr: null,
    regime: 'neutral',
    atm_ce_symbol: 'BANKNIFTY30OCT2654000CE',
    atm_pe_symbol: 'BANKNIFTY30OCT2654000PE',
    atm_lotsize: 15,
    ladder: [],
    mode: 'analyze',
  },
}

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ManualSection />
    </QueryClientProvider>
  )
}

describe('Manual ATM section', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    mocks.signal.mockResolvedValue(signal)
    mocks.placeOrder.mockResolvedValue({ status: 'success' })
    mocks.closeLeg.mockResolvedValue({ status: 'success' })
  })

  it('buys the selected underlying ATM call as a Manual order', async () => {
    const user = userEvent.setup()
    renderSection()
    await screen.findByText('BANKNIFTY30OCT2654000CE')

    await user.click(screen.getByRole('button', { name: 'BUY ATM CE' }))

    expect(mocks.placeOrder).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: 'BANKNIFTY30OCT2654000CE',
        exchange: 'NFO',
        action: 'BUY',
        product: 'MIS',
        quantity: 15,
        ltp: 120.5,
        strategy: 'Manual',
      })
    )
    expect(await screen.findByText('Manual positions')).toBeInTheDocument()
  })

  it('switches the ATM strike when NIFTY is selected', async () => {
    const user = userEvent.setup()
    renderSection()
    await screen.findByText('BANKNIFTY30OCT2654000CE')

    await user.click(screen.getByRole('button', { name: 'NIFTY' }))

    await waitFor(() => expect(mocks.signal).toHaveBeenLastCalledWith('NIFTY'))
  })

  it('sells the tracked leg through the manual exit path', async () => {
    const user = userEvent.setup()
    renderSection()
    await screen.findByText('BANKNIFTY30OCT2654000CE')
    await user.click(screen.getByRole('button', { name: 'BUY ATM CE' }))
    await screen.findByText('Manual positions')

    await user.click(screen.getByRole('button', { name: 'Sell' }))

    expect(mocks.closeLeg).toHaveBeenCalledWith(
      expect.objectContaining({
        symbol: 'BANKNIFTY30OCT2654000CE',
        exchange: 'NFO',
        action: 'SELL',
        product: 'MIS',
        quantity: 15,
        strategy: 'Manual',
      })
    )
    await waitFor(() => expect(screen.queryByText('Manual positions')).not.toBeInTheDocument())
  })

})
