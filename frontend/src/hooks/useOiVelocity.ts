import { useCallback, useEffect, useRef, useState } from 'react'
import {
  computeOiVelocity,
  OI_VELOCITY_MAX_GAP_MS,
  OI_VELOCITY_POLL_MS,
  OI_VELOCITY_WINDOW_MINUTES,
  type OiSnapshot,
  type OiVelocityData,
  type OiVelocityOptions,
  oiSnapshotFromChain,
  trimOiSnapshots,
} from '@/lib/trading/oiVelocity'
import type { OptionChainResponse } from '@/types/option-chain'

export interface UseOiVelocityOptions extends OiVelocityOptions {
  enabled: boolean
  chain: OptionChainResponse | null
  lastPollUpdate: Date | null
  strikeCount?: number
}

export interface UseOiVelocityState {
  velocity: OiVelocityData | null
  reset: () => void
}

export function useOiVelocity({
  enabled,
  chain,
  lastPollUpdate,
  strikeCount,
  windowMinutes = OI_VELOCITY_WINDOW_MINUTES,
  pollIntervalMs = OI_VELOCITY_POLL_MS,
}: UseOiVelocityOptions): UseOiVelocityState {
  const bufferRef = useRef<OiSnapshot[]>([])
  const identityRef = useRef('')
  const [velocity, setVelocity] = useState<OiVelocityData | null>(null)

  const reset = useCallback(() => {
    bufferRef.current = []
    identityRef.current = ''
    setVelocity(null)
  }, [])

  useEffect(() => {
    if (!enabled || !chain || !lastPollUpdate) {
      reset()
      return
    }

    const identity = `${chain.underlying_exchange}:${chain.underlying}:${chain.expiry_date}:${strikeCount ?? ''}`
    if (identity !== identityRef.current) {
      identityRef.current = identity
      bufferRef.current = []
    }

    const timestamp = lastPollUpdate.getTime()
    const previous = bufferRef.current[bufferRef.current.length - 1]
    if (previous && timestamp <= previous.timestamp) return
    if (previous && timestamp - previous.timestamp > OI_VELOCITY_MAX_GAP_MS) {
      bufferRef.current = []
    }

    const snapshot = oiSnapshotFromChain(chain, timestamp)
    bufferRef.current = trimOiSnapshots([...bufferRef.current, snapshot], timestamp, {
      windowMinutes,
      pollIntervalMs,
    })
    setVelocity(computeOiVelocity(bufferRef.current, { windowMinutes, pollIntervalMs }))
  }, [enabled, chain, lastPollUpdate, strikeCount, windowMinutes, pollIntervalMs, reset])

  return { velocity, reset }
}
