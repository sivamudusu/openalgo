/**
 * Strategy signals for the charting terminal: the two hosted models side by
 * side, each monitoring and each able to trade from here.
 *
 * - **Low-IV** counsels patience: an IV percentile, a Bollinger %B gauge, and
 *   a page-auto loop that holds one position with server-set exits.
 * - **Sahasra** reads positioning: OI support/resistance, PCR regime, and the
 *   15m bar's state against the levels, with quick manual ATM legs.
 *
 * Both trade through the platform's own order path, so both follow the global
 * Sandbox/Live mode shown on their badges. Neither runs from this tab except
 * Low-IV page-auto, which says so where it arms.
 */

import { PANEL_HEADER, PanelShell } from './panelShell'
import { LowIvSection } from './LowIvSection'
import { MomentumSection } from './MomentumSection'
import { SahasraSection } from './SahasraSection'

export function SignalsPanel() {
  return (
    <PanelShell id="oa-panel-signals" label="Strategy signals" storageKey="trading.panel.signals">
      <div className={PANEL_HEADER}>
        <span className="text-sm font-medium">Signals</span>
      </div>
      <div className="no-scrollbar flex-1 space-y-5 overflow-y-auto p-2">
        <section aria-label="Low-IV strategy">
          <h3 className="mb-2 text-sm font-semibold">Low-IV · BANKNIFTY</h3>
          <LowIvSection />
        </section>
        <section aria-label="Sahasra OI model">
          <h3 className="mb-2 text-sm font-semibold">Sahasra · OI model</h3>
          <SahasraSection />
        </section>
        <section aria-label="Momentum scalping experiment">
          <h3 className="mb-2 text-sm font-semibold">Momentum · experiment</h3>
          <MomentumSection />
        </section>
      </div>
    </PanelShell>
  )
}
