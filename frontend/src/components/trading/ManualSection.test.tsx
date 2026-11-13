import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VisualBracketView } from '@/lib/trading/visualBracket'
import { render, screen, userEvent, waitFor } from '@/test/test-utils'
import { ManualSection } from './ManualSection'

const mocks = vi.hoisted(() => ({
  signal: vi.fn(),
  place: vi.fn(),
  exit: vi.fn(),
  cancel: vi.fn(),
  refresh: vi.fn(),
  brackets: [] as VisualBracketView[],
  toast: vi.fn(),
}))

vi.mock('@/api/sahasra', () => ({
  sahasraApi: { getSignal: mocks.signal },
}))
vi.mock('@/api/visualBracket', () => ({
  visualBracketApi: { place: mocks.place, exit: mocks.exit, cancel: mocks.cancel },
}))
vi.mock('@/hooks/useVisualBrackets', () => ({
  useVisualBrackets: () => ({ brackets: mocks.brackets, refresh: mocks.refresh }),
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

const signal = {
  status: 'success',
  data: {
    underlying: 'BANKNIFTY',
    expiry: '30OCT26',
    atm_strike: 54_000,
    spot: 54_000,
    support: 53_800,
    resistance: 54_300,
    pcr: null,
    regime: 'neutral',
    atm_ce_symbol: 'BANKNIFTY30OCT2654000CE',
    atm_pe_symbol: 'BANKNIFTY30OCT2654000PE',
    atm_lotsize: 15,
    ladder: [
      { strike: 53_900, call_oi: 0, put_oi: 0 },
      { strike: 54_000, call_oi: 0, put_oi: 0 },
      { strike: 54_100, call_oi: 0, put_oi: 0 },
    ],
    mode: 'analyze',
  },
}

const manualBracket: VisualBracketView = {
  id: 7,
  clientToken: 'token-7',
  strategy: 'Manual',
  status: 'active',
  underlying: 'BANKNIFTY',
  underlyingExchange: 'NSE_INDEX',
  foExchange: 'NFO',
  action: 'BUY',
  optionType: 'CE',
  bias: 'bullish',
  entryReference: 54_000,
  targetPrice: 54_300,
  stopPrice: 53_800,
  clickPrice: 54_000,
  tickSize: 0.05,
  lotSize: 15,
  lots: 1,
  quantity: 15,
  contractSymbol: 'BANKNIFTY30OCT2654000CE',
  contractExchange: 'NFO',
  expiry: '30OCT26',
  strike: 54_000,
  entryFillPrice: 120.5,
  targetOffset: 300,
  stopOffset: 200,
  riskReward: 1.5,
}

function renderSection(ordersAllowed = true, onShowChart = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ManualSection ordersAllowed={ordersAllowed} onShowChart={onShowChart} />
    </QueryClientProvider>
  )
}

describe('Manual ATM section', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.brackets = []
    mocks.signal.mockResolvedValue(signal)
    mocks.place.mockResolvedValue({
      ...manualBracket,
      id: 8,
      status: 'working',
      entryFillPrice: null,
    })
    mocks.exit.mockResolvedValue(manualBracket)
    mocks.cancel.mockResolvedValue(manualBracket)
    mocks.refresh.mockResolvedValue(undefined)
  })

  it('places the ATM call as a Manual bracket with signal levels', async () => {
    const user = userEvent.setup()
    renderSection()
    await screen.findByText('BANKNIFTY30OCT2654000CE')

    await user.click(screen.getByRole('button', { name: 'BUY ATM CE' }))

    expect(mocks.place).toHaveBeenCalledWith(
      expect.objectContaining({
        underlying: 'BANKNIFTY',
        index_exchange: 'NSE_INDEX',
        contract_symbol: 'BANKNIFTY30OCT2654000CE',
        option_type: 'CE',
        action: 'BUY',
        product: 'MIS',
        quantity: 15,
        lots: 1,
        click_price: 54_000,
        entry_reference: 54_000,
        target_price: 54_300,
        stop_price: 53_800,
        expiry: '30OCT26',
        strike: 54_000,
        strike_selection: 'CUSTOM',
        strategy: 'Manual',
      })
    )
    expect(mocks.refresh).toHaveBeenCalled()
  })

  it('uses strike steps when the signal has no usable levels', async () => {
    const user = userEvent.setup()
    mocks.signal.mockResolvedValue({
      ...signal,
      data: { ...signal.data, support: null, resistance: null },
    })
    renderSection()
    await screen.findByText('BANKNIFTY30OCT2654000CE')

    await user.click(screen.getByRole('button', { name: 'BUY ATM CE' }))

    expect(mocks.place).toHaveBeenCalledWith(
      expect.objectContaining({
        target_price: 54_200,
        stop_price: 53_900,
      })
    )
  })

  it('switches the ATM strike when NIFTY is selected', async () => {
    const user = userEvent.setup()
    renderSection()
    await screen.findByText('BANKNIFTY30OCT2654000CE')

    await user.click(screen.getByRole('button', { name: 'NIFTY' }))

    await waitFor(() => expect(mocks.signal).toHaveBeenLastCalledWith('NIFTY'))
  })

  it('shows only manual brackets and exits an active one', async () => {
    const user = userEvent.setup()
    mocks.brackets = [
      manualBracket,
      {
        ...manualBracket,
        id: 9,
        strategy: 'VisualBracket',
        contractSymbol: 'BANKNIFTY30OCT2654100CE',
      },
    ]
    renderSection()
    await screen.findByText('Manual orders')

    expect(screen.queryByText('BANKNIFTY30OCT2654100CE')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Exit' }))

    expect(mocks.exit).toHaveBeenCalledWith(7)
    expect(mocks.refresh).toHaveBeenCalled()
  })

  it('cancels a working manual order', async () => {
    const user = userEvent.setup()
    mocks.brackets = [{ ...manualBracket, status: 'working', entryFillPrice: null }]
    renderSection()
    await screen.findByText('Manual orders')

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(mocks.cancel).toHaveBeenCalledWith(7)
  })

  it('shows the index chart for a manual order', async () => {
    const user = userEvent.setup()
    const onShowChart = vi.fn()
    mocks.brackets = [manualBracket]
    renderSection(true, onShowChart)
    await screen.findByText('Manual orders')

    await user.click(screen.getByRole('button', { name: 'Show chart' }))

    expect(onShowChart).toHaveBeenCalledWith('BANKNIFTY', 'NSE_INDEX')
  })

  it('keeps new orders disabled while paused but allows an exit', async () => {
    mocks.brackets = [manualBracket]
    renderSection(false)
    await screen.findByText('Manual orders')

    expect(screen.getByRole('button', { name: 'BUY ATM CE' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'BUY ATM PE' })).toBeDisabled()
    expect(screen.getByText(/New manual orders are paused/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Exit' })).toBeEnabled()
  })
})
