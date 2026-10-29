import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { type OptionChainOverlayState, useOptionChainOverlay } from '@/hooks/useOptionChainOverlay'
import { OPTION_CHAIN_METRICS, type OptionChainMetric } from '@/lib/optionChainMetrics'
import {
  OptionChainOverlay,
  type OptionChainOverlayOptions,
} from '@/lib/trading/optionChainOverlay'
import type { ChartInstrument } from '@/lib/trading/optionChainUnderlying'

const STORAGE_PREFIX = 'openalgo_option_chain_overlay:'

export interface OptionChainOverlaySettings {
  metric: OptionChainMetric
  strikeCount: number
  showValues: boolean
  showLevels: boolean
  /** The expiry the trader chose, or null for the nearest. */
  expiry: string | null
}

const DEFAULT_SETTINGS: OptionChainOverlaySettings = {
  metric: 'oi',
  strikeCount: 10,
  showValues: false,
  showLevels: true,
  expiry: null,
}

interface StoredOverlay {
  active: boolean
  settings: OptionChainOverlaySettings
}

/** The part of the terminal this needs; structural so the provider stays light. */
export interface OptionChainOverlayHost {
  setOptionChainOverlay(overlay: OptionChainOverlay | null): void
}

function isMetric(value: unknown): value is OptionChainMetric {
  return typeof value === 'string' && OPTION_CHAIN_METRICS.some((metric) => metric.id === value)
}

function readStored(paneId: string): StoredOverlay {
  const fallback: StoredOverlay = { active: false, settings: DEFAULT_SETTINGS }
  if (typeof window === 'undefined') return fallback
  try {
    const raw = window.localStorage.getItem(`${STORAGE_PREFIX}${paneId}`)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as {
      active?: unknown
      settings?: Partial<OptionChainOverlaySettings>
    }
    const settings = parsed.settings ?? {}
    const strikeCount =
      settings.strikeCount === 10 || settings.strikeCount === 15 || settings.strikeCount === 20
        ? settings.strikeCount
        : DEFAULT_SETTINGS.strikeCount
    return {
      active: parsed.active === true,
      settings: {
        metric: isMetric(settings.metric) ? settings.metric : DEFAULT_SETTINGS.metric,
        strikeCount,
        showValues: settings.showValues === true,
        showLevels: settings.showLevels !== false,
        expiry:
          typeof settings.expiry === 'string' && settings.expiry.length > 0
            ? settings.expiry
            : null,
      },
    }
  } catch {
    return fallback
  }
}

function writeStored(paneId: string, value: StoredOverlay): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(`${STORAGE_PREFIX}${paneId}`, JSON.stringify(value))
  } catch {
    /* Storage can be full or blocked; the overlay still works for this session. */
  }
}

export interface OptionChainOverlayContextValue {
  /** The chart has an option chain this platform can serve. */
  available: boolean
  active: boolean
  settings: OptionChainOverlaySettings
  state: OptionChainOverlayState
  setActive: (active: boolean) => void
  setSettings: (patch: Partial<OptionChainOverlaySettings>) => void
}

const OptionChainOverlayContext = createContext<OptionChainOverlayContextValue | null>(null)

/**
 * Owns one pane's option-chain overlay above the toolbar, so it survives the
 * toolbar being unmounted when another pane is focused.
 *
 * The chart's toolbar is portalled into a shared row and returns null for every
 * inactive pane. A primitive created there would be destroyed the moment the
 * trader clicked another chart, which is exactly when the overlay must keep
 * drawing. The provider wraps the pane instead and the toolbar control only
 * consumes its context.
 */
export function OptionChainOverlayProvider({
  paneId,
  apiKey,
  instrument,
  terminal,
  children,
}: {
  paneId: string
  apiKey: string
  instrument: ChartInstrument | null
  terminal: OptionChainOverlayHost | null
  children: ReactNode
}) {
  const [stored, setStored] = useState<StoredOverlay>(() => readStored(paneId))
  useEffect(() => {
    writeStored(paneId, stored)
  }, [paneId, stored])

  const update = useCallback(
    (patch: Partial<OptionChainOverlaySettings> & { active?: boolean }) => {
      setStored((prev) => {
        const { active, ...settingsPatch } = patch
        return {
          active: active ?? prev.active,
          settings: { ...prev.settings, ...settingsPatch },
        }
      })
    },
    []
  )
  const setActive = useCallback((next: boolean) => update({ active: next }), [update])
  const setSettings = useCallback(
    (patch: Partial<OptionChainOverlaySettings>) => update(patch),
    [update]
  )
  const handleExpiry = useCallback((expiry: string) => update({ expiry }), [update])

  const state = useOptionChainOverlay({
    apiKey,
    active: stored.active,
    instrument,
    metric: stored.settings.metric,
    strikeCount: stored.settings.strikeCount,
    preferredExpiry: stored.settings.expiry,
    onExpiryChange: handleExpiry,
  })

  const overlayRef = useRef<OptionChainOverlay | null>(null)
  if (overlayRef.current === null) overlayRef.current = new OptionChainOverlay()
  const overlay = overlayRef.current

  const options = useMemo<OptionChainOverlayOptions>(
    () => ({
      metric: stored.settings.metric,
      showValues: stored.settings.showValues,
      showLevels: stored.settings.showLevels,
    }),
    [stored.settings.metric, stored.settings.showValues, stored.settings.showLevels]
  )

  useEffect(() => {
    overlay.setData(state.data, options)
  }, [overlay, state.data, options])

  useEffect(() => {
    if (!terminal || typeof terminal.setOptionChainOverlay !== 'function') return
    terminal.setOptionChainOverlay(stored.active ? overlay : null)
    return () => {
      terminal.setOptionChainOverlay(null)
    }
  }, [terminal, stored.active, overlay])

  const value = useMemo<OptionChainOverlayContextValue>(
    () => ({
      available: Boolean(instrument) && state.status !== 'unsupported',
      active: stored.active,
      settings: stored.settings,
      state,
      setActive,
      setSettings,
    }),
    [instrument, state, stored.active, stored.settings, setActive, setSettings]
  )

  return (
    <OptionChainOverlayContext.Provider value={value}>
      {children}
    </OptionChainOverlayContext.Provider>
  )
}

export function useOptionChainOverlayContext(): OptionChainOverlayContextValue {
  const value = useContext(OptionChainOverlayContext)
  if (!value) {
    throw new Error('OptionChainOverlayControl must be rendered inside OptionChainOverlayProvider')
  }
  return value
}
