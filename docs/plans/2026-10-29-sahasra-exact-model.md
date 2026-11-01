# Sahasra exact model: assisted panel

Status: proposed. This document specifies a new, separate panel. It does not
change the existing Sahasra OI panel or its order path.

## Why this exists

The existing Sahasra panel is a simplified mechanical interpretation of the
model described in the Sahasra A Trading Community videos. It is useful as a
control, but it is not an exact implementation:

| Area | Existing panel | Model described in the videos |
|---|---|---|
| Level source | Live option chain, 15 strikes each side | Previous session end-of-day option chain, wide enough to include the major walls |
| PCR | `< 0.7` bearish, `> 1.0` bullish | `< 1.0` bearish, `> 1.0` bullish |
| Primary resistance | Largest call OI | Nearest significant call wall that has been tested and rejected |
| Major resistance | Same as primary | Largest call OI overhead |
| Confirmation | Price crossing a level | Closed 15-minute candle, body close, volume, and a change in OI |
| Short-side trade | Buys ATM CE above resistance | Rejection at resistance buys ATM PE; breakdown below support buys ATM PE |
| Targets | Largest OI wall | Next level; structural stop |
| Holding period | 30 minutes, 15% premium stop, MIS | Intraday or weekday carry; no fixed premium stop |

The 23 October 2026 BANKNIFTY session is the acceptance fixture. The model
marked 55,000 as resistance, 54,500 as temporary support, 54,000 as major
support, 56,000 as the largest call wall, and a no-trade zone of
54,000-55,000. It also read PCR 0.973 as bearish. The existing panel showed
support 54,800, resistance 55,000, neutral PCR, and would have bought CE above
55,000. That is the wrong side of the level.

This panel is manual and assisted. It never places an order by itself. It also
does not write options.

## Scope

Included:

- BANKNIFTY and NIFTY.
- A daily end-of-day snapshot stored after the NFO/BFO close.
- A level map built from that snapshot.
- A live 15-minute signal card that waits for a closed candle.
- A manual order ticket with ATM CE or ATM PE.
- A pre-open context card, with broker quotes where available and manual
  entries where they are not.
- A separate strategy tag for orders placed from this panel.

Not included:

- Automatic entries, automatic exits, or an auto square-off.
- Option selling, spreads, or any multi-leg construction.
- A news feed, headline scraping, an LLM summary, or a paid data provider.
- Changes to the current Sahasra panel.

## Evidence and confidence

The model was reconstructed from 110 transcripts in
`agentrade.youtuber_videos`, channel `sahasra-atradingcommunity824`, covering
7 June 2026 to 28 October 2026. Most are Telugu auto-captions, so the wording is
sometimes approximate.

| Rule | Evidence | Confidence |
|---|---|---|
| Option-chain OI defines support and resistance | Repeated explanations and live examples | High |
| PCR below 1 is bearish, above 1 is bullish | Repeated explanation; one episode gives the 0.7 caveat | High |
| No trade inside support-resistance | Repeated | High |
| Failed breakout and failed breakdown are tradeable | Repeated, including the 23 October session | High |
| Rejection at resistance buys PE | 23 October and other sessions | High |
| Structural stop, next-level target | Repeated examples | Medium-high |
| 15-minute closed candle is the trigger | Demonstrations use the 15-minute chart | High |
| Volume and OI change confirm | Mentioned, but not quantified | Medium |
| Fed, inflation, and geopolitics set the morning bias | Pre-open commentary | Medium-high |
| Exact wall labels | Inferred from examples, not a published formula | Medium |
| Carry overnight | A weekday example exists, but it is not a standing rule | Low-medium |

The backtest in `agentic_trading_v2/scripts/backtest_youtuber_model.py` is not
evidence for this panel. Its R1 docstring says it uses maximum put and call OI,
but `oi_levels()` uses the nearest local OI peak within 1,000 points. It covers
about 22 September 2026 BANKNIFTY sessions only.

## User-facing vocabulary

Use **Sahasra exact** for this panel. Use **Sandbox** and **Live** for the
destination. Do not use "paper" or "virtual" for the destination, and do not use
"arm" or "armed" anywhere a trader reads.

## Model rules

### 1. Snapshot

- Capture the nearest and next expiry for each underlying.
- Capture at 15:45 IST, after the 15:40 NFO/BFO close.
- Skip weekends and exchange holidays.
- Capture 50 strikes on each side of the at-the-money strike, for 101 strikes.
- Store the spot, ATM strike, total call OI, total put OI, PCR, prior-day high,
  low and close, coverage label, capture time, and source.
- The snapshot is idempotent. Re-running it replaces the rows for the same
  underlying, trade date and expiry.
- A startup catch-up runs when the application starts after 15:45 on a trading
  day and a complete snapshot is missing.
- A manual capture before 15:45 is stored as `source=manual` and is never read
  as the end-of-day map. A manual capture after 15:45 on a trading day is stored
  as `source=eod`, so it can stand in when the scheduled capture failed.

The 50-strike window is a deliberate compromise. The model reads a wide chain;
a live full chain is too large to poll from the panel every 30 seconds. The
snapshot is labelled `window_50`. Its PCR is a window PCR, not a full-chain PCR.
The difference must be visible to the trader and measured against the 22 October
reference before the PCR rule is treated as calibrated.

### 2. PCR

- `PCR = total put OI / total call OI`, using the captured strikes.
- `PCR < 1.0` is bearish.
- `PCR > 1.0` is bullish.
- `PCR = 1.0` is neutral.
- The displayed value is rounded to three decimal places.
- PCR never overrides a price trigger. It is context, not an entry by itself.

### 3. Level map

The level map is built from the previous session's snapshot, not the live chain.

Support candidates are strikes at or below spot. Resistance candidates are
strikes above spot. A candidate can come from:

- A call wall: call OI at that strike.
- A put wall: put OI at that strike.
- A local OI peak.
- A round number: multiples of 100 for NIFTY and 500 for BANKNIFTY.
- Prior-day high, low, or close.
- A recent daily swing high or low.

Candidates within 0.05% of each other are merged, keeping the higher OI and
adding the tags.

Each candidate receives a transparent score in the range 0 to 1:

```
score = 0.40 * normalised_oi
      + 0.25 * normalised_rejection_count
      + 0.20 * confluence
      + 0.15 * proximity
```

- `normalised_oi` is the candidate's OI divided by the largest OI on that side.
- `normalised_rejection_count` is the number of 15-minute touches that failed at
  that strike, divided by the largest count in the candidate set.
- `confluence` is 1.0 for a major round number, 0.5 for a minor round number,
  and receives 0.5 for a prior-day or swing level at the same strike, capped at
  1.0.
- `proximity` is `max(0, 1 - distance_to_spot / (0.03 * spot))`.

Labels:

- **Major support**: the largest put-OI strike below spot. This is the primary
  support by definition.
- **Temporary support**: the highest-scoring support other than major support.
- **Primary resistance**: the highest-scoring call wall above spot that has at
  least 50% of the largest call OI and either has a rejection count above zero
  or has no rejection data. This is the nearest meaningful, tested wall, not
  necessarily the largest wall.
- **Major resistance**: the largest call OI strike above spot.
- If primary and major resistance are the same strike, show one label.
- If there is no support below spot, say so. Do not manufacture a support above
  spot. If there is no resistance above spot, say so. Do not force
  `support < resistance`.

The no-trade zone is the open interval between primary support and primary
resistance. A price inside it is not a trade.

### 4. Signal

Only closed 15-minute candles are evaluated. The current forming candle is
discarded.

States:

- `warming_up`: fewer than two closed candles.
- `no_levels`: support or resistance is missing.
- `breakout`: previous close at or below resistance, current close above it.
- `breakdown`: previous close at or above support, current close below it.
- `rejection`: high reaches resistance, close falls back below it, and the upper
  wick is at least 50% of the candle range.
- `bounce`: low reaches support, close recovers above it, and the candle closes
  above its open.
- `inside_zone`: close is strictly inside the no-trade zone.
- `above_resistance`: close is above resistance without a fresh cross.
- `below_support`: close is below support without a fresh cross.

A price trigger is necessary but not sufficient. Confirmation:

- For CE: body at least 60% of the candle range and close in the top third.
- For PE: body at least 60% of the candle range and close in the bottom third.
- For rejection: upper wick at least 50% of the range.
- For bounce: body at least 50% of the range and close above the open.
- Volume: at least 1.2 times the 20-bar average, when volume is present. If
  volume is unavailable, report `volume unavailable` and do not fail the other
  confirmations.
- OI change: displayed as operator context. The option-chain endpoint does not
  provide a signed time series, so OI change is not used as an automatic veto.

Suggested trade:

- Side: CE for breakout and bounce; PE for breakdown and rejection.
- Instrument: current ATM CE or PE for the selected expiry.
- Stop: the broken level for a breakout or breakdown; the support for a bounce;
  the resistance for a rejection.
- First target: the next level in the direction of the trade.
- Second target: the major level in that direction.
- Reject the suggestion when the first-target distance is less than the
  stop distance, or when the target would be at or behind entry.

Operational rails, not spoken rules:

- Show entries between 09:15 and 14:30 IST.
- Show a flat-by-15:20 reminder.
- Allow NRML only when the trader chooses it. The default is MIS.

### 5. Pre-open context

The context card sits above the level map. It is context, not a filter. It never
changes the levels, the PCR, or the no-trade zone. It never blocks an order.

Automatic, when the broker exposes `GLOBAL_INDEX`:

- US30
- US500
- US100
- US10YRYIELD
- GIFTNIFTY
- BRENTOIL and WTIOIL where the broker provides them

If a symbol is unavailable, show `unavailable`. Do not turn a failed quote into
a neutral reading. Crude can fall back to MCX crude only when the broker
resolves a front-month contract; otherwise it stays manual.

Manual:

- Fed tone: dovish, neutral, hawkish.
- Inflation: cool, in line, hot.
- Geopolitics: risk-on, neutral, risk-off.
- India 10-year yield, when the broker does not provide it.
- A short note.

The trader selects the final bias: bullish, bearish, mixed, or unset, plus a
confidence of low, medium, or high. The panel shows an alignment badge:

- Aligned: bias matches the signal side.
- Against: bias is the opposite of the signal side.
- Mixed: bias is mixed.
- Unset: no bias recorded.

Alignment is advisory. The order button remains available.

## Data model

All three tables live in `openalgo.db` and are created by
`database/sahasra_exact_db.py`.

### `sahasra_exact_snapshots`

| Column | Type | Notes |
|---|---|---|
| id | integer | primary key |
| underlying | string | BANKNIFTY or NIFTY |
| trade_date | date | session date |
| expiry | string | normalized expiry |
| spot | float | spot at capture |
| atm_strike | float | nearest strike |
| pcr | float | window PCR |
| total_call_oi | bigint | sum over captured strikes |
| total_put_oi | bigint | sum over captured strikes |
| coverage | string | `window_50` |
| strikes_captured | integer | count |
| prev_day_high | float | previous session high |
| prev_day_low | float | previous session low |
| prev_day_close | float | previous session close |
| levels_json | text | serialized level map |
| captured_at | datetime | UTC, naive |
| source | string | `eod` or `manual` |
| status | string | `complete` or `partial` |
| warning | text | coverage or data warning |

Unique key: `(underlying, trade_date, expiry)`.

### `sahasra_exact_chain`

| Column | Type | Notes |
|---|---|---|
| id | integer | primary key |
| snapshot_id | integer | parent |
| strike | float | strike |
| ce_oi | bigint | call OI |
| pe_oi | bigint | put OI |
| ce_volume | bigint | call volume |
| pe_volume | bigint | put volume |
| ce_ltp | float | call last price |
| pe_ltp | float | put last price |
| ce_symbol | string | OpenAlgo symbol |
| pe_symbol | string | OpenAlgo symbol |
| lotsize | integer | contract lot size |

Unique key: `(snapshot_id, strike)`.

### `sahasra_exact_context`

| Column | Type | Notes |
|---|---|---|
| id | integer | primary key |
| trade_date | date | unique |
| cues_json | text | automatic quotes and manual selections |
| final_bias | string | bullish, bearish, mixed, unset |
| confidence | string | low, medium, high |
| notes | text | trader note |
| captured_at | datetime | first save |
| updated_at | datetime | last save |

Unique key: `trade_date`.

## API

All routes require a valid session and return trader-facing messages.

| Method | Path | Purpose |
|---|---|---|
| GET | `/sahasra-exact/api/levels?underlying=BANKNIFTY` | Level map, snapshot metadata, live spot and ATM symbols |
| GET | `/sahasra-exact/api/signal?underlying=BANKNIFTY` | Closed-bar signal and suggested trade |
| GET | `/sahasra-exact/api/snapshot/status` | Last captures and next run |
| POST | `/sahasra-exact/api/snapshot` | Manual capture; optional `underlying` |
| GET | `/sahasra-exact/api/context?date=YYYY-MM-DD` | Auto cues, manual context, final bias |
| POST | `/sahasra-exact/api/context` | Save manual context and final bias |

The levels and signal routes use the previous completed snapshot for the current
expiry. If no snapshot exists, they build a live provisional level map from a
15-strike chain and mark it `live_provisional`. The panel must show that label
and a warning. A missing snapshot is never presented as an end-of-day map.

The signal route reads 15-minute history from the broker. If the broker returns
no usable bars, it returns `warming_up` with an explicit message.

## Frontend

Add one section to `frontend/src/components/trading/SignalsPanel.tsx`. Do not
add a page, route, or navigation entry.

The section contains:

- BANKNIFTY and NIFTY toggle.
- Source badge: `EOD snapshot 28 Oct`, `live provisional`, or `stale`.
- Spot, expiry, ATM strike, PCR and regime.
- Pre-open context card with automatic quotes, manual selects, final bias, and a
  save button.
- Level ladder. Resistances are shown above spot from nearest to farthest;
  supports below spot from nearest to farthest. Each row shows strike, label,
  OI in lakhs, rejection count, and tags.
- No-trade zone.
- Scenario card for the next closed 15-minute candle.
- Confirmation card: body close, volume, OI change, alignment.
- Manual BUY CE and BUY PE buttons. They place only ATM options, and only after
  a trader click.
- Manual exit. No automatic exit is added.
- Snapshot status and a manual capture button.

The panel uses the shared `useMarketData` hook for live spot and the ATM
symbols. The backend remains the source of strikes and levels.

Strategy tags:

- `SahasraExact` for BANKNIFTY.
- `SahasraExactNifty` for NIFTY.

Position keys:

- `sahasra.exact.banknifty.pos`
- `sahasra.exact.nifty.pos`

Add both to the shared strategy-peer guard so two panels cannot claim the same
symbol at the same time.

## Failure handling

- No API key: show "Connect a broker to capture the end-of-day chain." Do not
  start a background retry storm.
- No chain data: keep the previous snapshot visible and mark it stale.
- Partial chain: save `status=partial`, show the missing-strike warning, and
  refuse to compute a primary resistance that depends on a missing wall.
- No global quotes: show `unavailable` and leave the manual fields usable.
- Broker history failure: show `warming_up` or `history unavailable`; never
  invent a candle.
- Expiry rollover: use the current nearest expiry. If the snapshot's expiry is
  no longer current, show `live provisional` until the next capture.
- Scheduler restart: catch up once after 15:45; do not run duplicates.

## Security and runtime constraints

- No new external service. Context quotes come from the existing broker quote
  path.
- No `asyncio`.
- Use `create_db_engine()` and `NullPool`; register the scoped session in
  `utils/db_sessions.py`.
- Use `LockedTTLCache`, never a bare `TTLCache`.
- The scheduler runs one job at 15:45 IST, Monday to Friday. It checks the
  exchange calendar inside the job. It uses a dedicated `get_first_available_api_key()`
  lookup and does not borrow a request session.
- Wrap the scheduled call with `@releases_scoped_sessions` so a broker call
  cannot hold a database session open.
- Manual capture uses the logged-in session's API key.
- Run `fd-audit` after the DB, scheduler, and history work.

## Tests

Backend:

- Level map reproduces the 23 October fixture: support 54,000, temporary support
  54,500, primary resistance 55,000, major resistance 56,000, no-trade zone
  54,000-55,000.
- PCR uses `< 1` and `> 1`, not `0.7`.
- A missing support does not become a resistance.
- Rejection at resistance produces PE; breakdown produces PE; breakout and
  bounce produce CE.
- A signal is not emitted from the forming candle.
- Target-behind-entry and target-distance-below-stop are rejected.
- Snapshot save is idempotent.
- A manual capture before the close is not used as an EOD map.
- Holiday skip and expiry rollover.
- Context partial quote failure does not become neutral.
- API requires a session, allowlists the underlying, and refuses an invalid
  context date.
- No order is placed by any GET route.

Frontend:

- Panel renders EOD and provisional states.
- Context save sends the final bias and manual fields.
- BUY buttons use the correct strategy tag and ATM symbol.
- The peer guard blocks a symbol already held by another panel.

Run:

```sh
uv run pytest test/test_sahasra_exact_levels.py test/test_sahasra_exact_signals.py \
  test/test_sahasra_exact_api.py test/test_sahasra_exact_db.py \
  test/test_sahasra_exact_context.py test/test_sahasra_exact_snapshot.py -v
cd frontend && npm run build
```

## Rollout

1. Land the spec and the database migration.
2. Land the snapshot service and capture one EOD snapshot manually.
3. Compare the level map with the 23 October plan and the current Sahasra panel.
4. Land the signal and context endpoints.
5. Land the panel behind its section; keep the current Sahasra panel unchanged.
6. Run both panels in parallel for ten trading sessions. Record disagreements,
   especially PCR, primary resistance, and rejection trades.
7. Only after that review, decide whether any automation is warranted. This
   document does not authorize automation.

## Open questions

- Whether the 50-strike window is wide enough for the next expiry. The panel
  labels it, and the calibration review answers it.
- Whether the broker's OI change is signed and reliable enough to become a
  confirmation rule. It is displayed only for now.
- Whether the model's "next level" target should skip round numbers that have no
  OI. The first implementation includes round numbers as candidates and shows
  their tags so a trader can see why a target was chosen.
