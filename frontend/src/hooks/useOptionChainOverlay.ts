import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { scalpingApi } from '@/api/scalping'
import type { OptionChainMetric } from '@/lib/optionChainMetrics'
import { type OptionChainOverlayData, overlayDataFromChain } from '@/lib/trading/optionChainOverlay'
import {
  type ChartInstrument,
  loadOptionUnderlyings,
  matchOptionUnderlying,
  type OptionChainIdentity,
  optionChainCandidates,
} from '@/lib/trading/optionChainUnderlying'
import type { OptionChainResponse } from '@/types/option-chain'
import { useOptionChainLive } from './useOptionChainLive'

export type OptionChainOverlayStatus = 'idle' | 'loading' | 'ready' | 'unsupported' | 'unavailable'

export interface UseOptionChainOverlayOptions {
  apiKey: string
  /** The overlay is visible. Off still resolves the chart and its expiries. */
  active: boolean
  instrument: ChartInstrument | null
  metric: OptionChainMetric
  strikeCount: number
  /** The expiry last chosen for this pane, when it still exists. */
  preferredExpiry?: string | null
  onExpiryChange?: (expiry: string) => void
}

export interface OptionChainOverlayState {
  status: OptionChainOverlayStatus
  error: string | null
  identity: OptionChainIdentity | null
  expiries: string[]
  expiry: string
  setExpiry: (expiry: string) => void
  chain: OptionChainResponse | null
  data: OptionChainOverlayData | null
  isLoading: boolean
  isStreaming: boolean
  isPaused: boolean
  lastUpdate: Date | null
  retry: () => void
}

/**
 * Resolve the chart's option chain, keep it live, and reduce it to the overlay's
 * shape.
 *
 * The chart is the source of truth for what is charted, so the underlying is
 * derived from its symbol, name and exchange rather than asked for again. The
 * expiry defaults to the nearest and is only remembered per pane when the
 * trader changes it.
 *
 * The chain is off until the overlay is switched on; resolving the underlying
 * and its expiry list still runs, because the control has to name them before
 * the trader decides.
 */
export function useOptionChainOverlay({
  apiKey,
  active,
  instrument,
  metric,
  strikeCount,
  preferredExpiry,
  onExpiryChange,
}: UseOptionChainOverlayOptions): OptionChainOverlayState {
  const candidates = useMemo(() => optionChainCandidates(instrument), [instrument])
  const [identity, setIdentity] = useState<OptionChainIdentity | null>(null)
  const [resolveStatus, setResolveStatus] = useState<
    'idle' | 'loading' | 'ready' | 'unsupported' | 'unavailable'
  >('idle')
  const [resolveError, setResolveError] = useState<string | null>(null)

  const [expiries, setExpiries] = useState<string[]>([])
  const [expiry, setExpiryState] = useState('')
  const [expiryError, setExpiryError] = useState<string | null>(null)

  const preferredRef = useRef(preferredExpiry)
  preferredRef.current = preferredExpiry
  const expiryChangeRef = useRef(onExpiryChange)
  expiryChangeRef.current = onExpiryChange
  const resolveGeneration = useRef(0)
  const expiryGeneration = useRef(0)

  const resolve = useCallback(async () => {
    const generation = ++resolveGeneration.current
    if (!candidates) {
      setIdentity(null)
      setResolveStatus('unsupported')
      setResolveError(null)
      return
    }
    setResolveStatus('loading')
    setResolveError(null)
    try {
      const underlyings = await loadOptionUnderlyings(candidates.segment)
      if (generation !== resolveGeneration.current) return
      const underlying =
        matchOptionUnderlying(underlyings, candidates.candidates) ??
        candidates.candidates[0] ??
        null
      if (!underlying) {
        setIdentity(null)
        setResolveStatus('unsupported')
        return
      }
      setIdentity({ underlying, exchange: candidates.segment })
      setResolveStatus('ready')
    } catch {
      if (generation !== resolveGeneration.current) return
      setIdentity(null)
      setResolveStatus('unavailable')
      setResolveError('Could not read the option chain for this chart. Try again.')
    }
  }, [candidates])

  const loadExpiries = useCallback(async () => {
    const generation = ++expiryGeneration.current
    if (!identity) {
      setExpiries([])
      setExpiryState('')
      setExpiryError(null)
      return
    }
    setExpiryError(null)
    try {
      const response = await scalpingApi.getExpiry(
        identity.underlying,
        identity.exchange,
        'options'
      )
      if (generation !== expiryGeneration.current) return
      const list = (response.data ?? []).filter(
        (value): value is string => typeof value === 'string' && value.trim().length > 0
      )
      setExpiries(list)
      if (list.length === 0) {
        setExpiryState('')
        setExpiryError('No expiry is available for this chart.')
        return
      }
      setExpiryState((current) => {
        const preferred = preferredRef.current
        if (preferred && list.includes(preferred)) return preferred
        if (current && list.includes(current)) return current
        return list[0]
      })
    } catch {
      if (generation !== expiryGeneration.current) return
      setExpiries([])
      setExpiryState('')
      setExpiryError('Could not read the expiries for this chart. Try again.')
    }
  }, [identity])

  useEffect(() => {
    void resolve()
  }, [resolve])

  useEffect(() => {
    void loadExpiries()
  }, [loadExpiries])

  useEffect(
    () => () => {
      resolveGeneration.current += 1
      expiryGeneration.current += 1
    },
    []
  )

  const setExpiry = useCallback((next: string) => {
    setExpiryState(next)
    expiryChangeRef.current?.(next)
  }, [])

  const live = useOptionChainLive(
    apiKey,
    identity?.underlying ?? '',
    identity?.exchange ?? 'NFO',
    identity?.exchange ?? 'NFO',
    expiry,
    strikeCount,
    {
      enabled: active && Boolean(identity && expiry),
      oiRefreshInterval: 30000,
      pauseWhenHidden: true,
      mode: 'LTP',
    }
  )

  const data = useMemo(
    () => (live.data ? overlayDataFromChain(live.data, metric) : null),
    [live.data, metric]
  )

  const status: OptionChainOverlayStatus =
    resolveStatus === 'unsupported'
      ? 'unsupported'
      : resolveStatus === 'unavailable'
        ? 'unavailable'
        : resolveStatus === 'loading'
          ? 'loading'
          : !identity
            ? 'idle'
            : !expiry
              ? expiryError
                ? 'unavailable'
                : 'loading'
              : !active
                ? 'idle'
                : live.data
                  ? 'ready'
                  : live.error
                    ? 'unavailable'
                    : 'loading'

  const liveError = live.error
    ? /^(HTTP error!|Failed to fetch|NetworkError|Load failed|TypeError)/i.test(live.error)
      ? 'The option chain is not updating right now. Try again in a moment.'
      : live.error
    : null
  const error = resolveError ?? expiryError ?? liveError
  const isLoading =
    resolveStatus === 'loading' ||
    (Boolean(identity) && !expiry && !expiryError) ||
    (active && !live.data && live.isLoading)

  const retry = useCallback(() => {
    void resolve()
    void loadExpiries()
    live.refetch()
  }, [resolve, loadExpiries, live.refetch])

  return {
    status,
    error,
    identity,
    expiries,
    expiry,
    setExpiry,
    chain: live.data,
    data,
    isLoading,
    isStreaming: live.isStreaming,
    isPaused: live.isPaused,
    lastUpdate: live.lastUpdate,
    retry,
  }
}
