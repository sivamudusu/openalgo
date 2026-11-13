/**
 * Strategy signals for the charting terminal: manual trading first, then the
 * hosted models side by side, each monitoring and each able to trade from here.
 *
 * - **Manual** buys the at-the-money call or put on BANKNIFTY or NIFTY. The
 *   order carries an index target and stop watched by the server.
 * - **Sahasra** reads positioning: OI support/resistance, PCR regime, and the
 *   15m bar's state against the levels.
 * - **Momentum** is the inside-range experiment run on the same levels.
 *
 * Sahasra and Momentum each have a BANKNIFTY and a NIFTY section with its own
 * Auto toggle, so both underlyings can run at once without sharing a position.
 * Everything trades through the platform's own order path, so all of it
 * follows the global Sandbox/Live mode shown on the badges.
 */

import { PANEL_HEADER, PanelShell } from './panelShell'
import { ManualSection } from './ManualSection'
import { MomentumSection } from './MomentumSection'
import { SahasraSection } from './SahasraSection'

export function SignalsPanel({
  onShowChart,
}: {
  onShowChart?: (symbol: string, exchange: string) => void
}) {
  return (
    <PanelShell id="oa-panel-signals" label="Strategy signals" storageKey="trading.panel.signals">
      <div className={PANEL_HEADER}>
        <span className="text-sm font-medium">Signals</span>
      </div>
      <div className="no-scrollbar flex-1 space-y-5 overflow-y-auto p-2">
        <section aria-label="Manual ATM trading">
          <h3 className="mb-2 text-sm font-semibold">Manual · ATM</h3>
          <ManualSection onShowChart={onShowChart} />
        </section>
        <section aria-label="Sahasra OI model BANKNIFTY">
          <h3 className="mb-2 text-sm font-semibold">Sahasra · BANKNIFTY</h3>
          <SahasraSection variant="BANKNIFTY" />
        </section>
        <section aria-label="Sahasra OI model NIFTY">
          <h3 className="mb-2 text-sm font-semibold">Sahasra · NIFTY</h3>
          <SahasraSection variant="NIFTY" />
        </section>
        <section aria-label="Momentum scalping experiment BANKNIFTY">
          <h3 className="mb-2 text-sm font-semibold">Momentum · BANKNIFTY</h3>
          <MomentumSection variant="BANKNIFTY" />
        </section>
        <section aria-label="Momentum scalping experiment NIFTY">
          <h3 className="mb-2 text-sm font-semibold">Momentum · NIFTY</h3>
          <MomentumSection variant="NIFTY" />
        </section>
      </div>
    </PanelShell>
  )
}
