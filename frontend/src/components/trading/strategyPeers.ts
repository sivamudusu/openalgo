/**
 * Cross-strategy collision guard for the sidebar auto loops.
 *
 * Low-IV, Sahasra and Momentum each track their own position, but all three
 * can favor the same ATM contract. Before auto-entering a symbol, a loop
 * asks whether a *peer* loop already holds it — two loops buying the same
 * leg doubles size, and one loop exiting while another enters the same leg
 * nets to confusion in the book. Reads are localStorage-only (no requests).
 */

const PEER_KEYS = ['lowiv.autoPos', 'sahasra.autoPos', 'momentum.autoPos'] as const

export function peerHoldsSymbol(symbol: string, selfKey: (typeof PEER_KEYS)[number]): boolean {
  for (const key of PEER_KEYS) {
    if (key === selfKey) continue
    try {
      const raw = localStorage.getItem(key)
      if (raw && (JSON.parse(raw) as { symbol?: unknown }).symbol === symbol) return true
    } catch {
      /* unreadable entry counts as no position */
    }
  }
  return false
}
