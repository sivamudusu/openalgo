# Visual Chart Orders (Draggable Bracket Orders)

Status: implemented in the working tree.
Scope: index options on `/trading`.

## What it does

A trader clicks an index chart, picks Buy CE, Buy PE, Sell CE or Sell PE, and
gets a draggable box with entry, target and stop. Placing it sends a market
option order and starts a server-side monitor. When the underlying index
crosses the target or stop, the option position is squared off at market.

The target and stop are **not** resting broker orders. They are engine-owned
levels. This is what makes the feature work with MIS and on every broker; the
platform's native GTT/OCO is limited to a few brokers and rejects MIS.

## Why a standalone engine

The strategy module already owns multi-leg, scheduled runs. A visual order is an
immediate single-leg trade whose trigger is an index level, not the option's own
premium. `services/visual_bracket/` reuses the parts that matter without taking
on the strategy module's lifecycle:

- `services/risk/position.py` for target/stop evaluation;
- `services/risk/bracket.py` for the pure bias and level rules;
- `services/strategy_module/tick_feed.py` for WebSocket and REST-fallback prices;
- `services/order...` / `sandbox_service` for broker and sandbox dispatch;
- `utils.event_bus` for order updates.

## Components

| Layer | File | Responsibility |
|---|---|---|
| Pure rules | `services/risk/bracket.py` | Bias from action/type, level validation, drag clamping, R:R |
| Persistence | `database/visual_bracket_db.py` | `vb_bracket`, `vb_bracket_event`, atomic exit claim |
| Resolver | `services/visual_bracket/resolver.py` | Index -> F&O exchange, expiry, strike, contract, lot/tick/freeze |
| Dispatch | `services/visual_bracket/dispatch.py` | Mode-fixed place/modify/cancel, freeze-safe reducing exit |
| Engine | `services/visual_bracket/engine.py` | State machine, tick evaluation, recovery, reconciliation |
| HTTP | `blueprints/visual_bracket.py` | Session-auth place/modify/cancel/exit/list |
| Overlay | `frontend/src/lib/trading/visualBracketOverlay.ts` | Canvas primitive: zones, lines, handles, hit testing |
| Controller | `frontend/src/lib/trading/visualBracketController.ts` | Chart click and drag; reattach after rebuild |
| Panel | `frontend/src/components/trading/VisualOrderPanel.tsx` | Option/expiry/strike/lots selector and confirmation |
| State hook | `frontend/src/hooks/useVisualBrackets.ts` | List restore and `bracket_update` socket frames |

## State machine

`staged -> placing -> working -> active -> exiting -> closed`
with `cancelled` for an unfilled entry and `error` for a state that needs a
person.

## Invariants

1. **One exit.** `claim_exit` is a single SQL UPDATE that checks `status` and
   `exit_kind IS NULL` and writes the marker and `exiting` together.
2. **A refused exit releases the claim.** The order stays active and retries; it
   is never reported closed while the broker still holds it.
3. **Mode is fixed at creation.** Live exits call `place_order_with_auth` with
   `force_live=True`; sandbox exits call the sandbox service directly.
4. **Recovery reconciles first.** A restart checks the broker's order and
   position books before it rearms or retries an exit.
5. **No crossing levels.** Both the browser and the server clamp a dragged
   target/stop to the legal side of entry.
6. **Freeze-safe exits.** Whole-lot chunks below the exchange freeze limit;
   each retry re-reads the net position rather than resending the original
   quantity.

## Deliberately not in this version

- Delta/gamma-estimated option-premium levels (Mode B).
- Native GTT/OCO legs.
- Non-index underlyings.
- Dragging the entry reference after the market entry is placed. The entry is
  fixed at the click price; target and stop remain draggable.

## Tests

- `test/risk/test_bracket.py` - pure rules.
- `test/visual_bracket/test_engine.py` - target/stop direction, fill activation,
  rejected entry, refused exit, reconcile behaviour.
- `test/visual_bracket/test_db_claim.py` - the atomic claim against real SQLite.
- `test/visual_bracket/test_dispatch.py` - freeze chunking and resolver.
- `test/test_strategy_module_tick_feed.py` - the shared feed's extra hooks and
  owner subscriptions.
- `frontend/src/lib/trading/visualBracket.test.ts` - browser-side level rules
  and strike selection.
