"""Sahasra (YouTuber) OI model — monitoring + signal backend for /trading.

The level map comes from :mod:`services.level_map_service`, which is the one
resolver the Sahasra panel and the chart overlay both read, so the two cannot
disagree about where the walls are.

It answers, for each side of the money:

- The **headline wall**: the largest open interest on that side, at or below
  the underlying for support and above it for resistance. The panel used to
  rank candidate strikes by proximity to the money first, so a small bump
  near the money beat a large wall further away, and the two surfaces
  disagreed about the same chain.
- The **nearest wall** and the **next wall** on that side, which is where a
  target goes.
- The **magnet**: the strike whose weaker side carries the most interest,
  where price balances rather than travelling.
- The **no-trade zone** between the two headline walls.

PCR regime (<0.7 bearish, 0.7-1.0 neutral, >1.0 bullish) is read from the
live chain (what the model reads on air), cached 60s per underlying. The same
rules serve BANKNIFTY and NIFTY; the caller selects one with ?underlying=.
Manual trading reuses POST /lowiv/api/order (NFO MARKET).
"""

import time
from datetime import datetime, timedelta

import pytz
from flask import Blueprint, jsonify, request, session

from database.auth_db import get_api_key_for_tradingview
from database.sahasra_signal_db import (
    close_signal as close_sahasra_signal,
)
from database.sahasra_signal_db import (
    record_signal as record_sahasra_signal,
)
from database.sahasra_signal_db import (
    signal_evidence as sahasra_evidence,
)
from database.settings_db import get_analyze_mode
from services.expiry_service import get_expiry_dates
from services.history_service import get_history
from services.level_map_service import build_level_map
from services.option_chain_service import get_option_chain
from services.signal_confirmation import evaluate_confirmation
from utils.logging import get_logger
from utils.session import check_session_validity
from utils.thread_safe_cache import LockedTTLCache

logger = get_logger(__name__)

sahasra_bp = Blueprint("sahasra_bp", __name__, url_prefix="/")

UNDERLYINGS = ("BANKNIFTY", "NIFTY")
DEFAULT_UNDERLYING = "BANKNIFTY"
INDEX_EXCHANGE = "NSE_INDEX"
FO_EXCHANGE = "NFO"
IST = pytz.timezone("Asia/Kolkata")

PCR_BEARISH_MAX = 0.7
PCR_BULLISH_MIN = 1.0
LEVELS_TTL_SECONDS = 60

# One cache slot per underlying: the two chains refresh independently, and a
# single slot would let a NIFTY request evict the BANKNIFTY levels (or worse,
# serve NIFTY levels to the BANKNIFTY panel) every time the panels alternate.
# LockedTTLCache, because the blueprint runs under a thread pool and a plain
# dict mutated from two requests is a race.
_levels_cache = LockedTTLCache(maxsize=8, ttl=LEVELS_TTL_SECONDS)


def _resolve_underlying(value: str | None) -> str | None:
    """Allowlisted underlying from the query string, or None when unsupported."""
    underlying = (value or DEFAULT_UNDERLYING).strip().upper()
    return underlying if underlying in UNDERLYINGS else None


def _get_api_key():
    username = session.get("user")
    if not username:
        return None
    return get_api_key_for_tradingview(username)


class LevelsUnavailable(Exception):
    """The level map could not be built."""


def _to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _compute_levels(api_key, underlying):
    ok, resp, _ = get_expiry_dates(
        symbol=underlying, exchange=FO_EXCHANGE, instrumenttype="options", api_key=api_key
    )
    if not ok:
        return None, (resp.get("message") if isinstance(resp, dict) else "expiry failed")
    dates = resp.get("data") or []
    if not dates:
        return None, "No expiries available"
    expiry = str(dates[0]).replace("-", "").replace(" ", "").upper()
    ok, chain_resp, _ = get_option_chain(
        underlying=underlying,
        exchange=FO_EXCHANGE,
        expiry_date=expiry,
        strike_count=15,
        api_key=api_key,
        with_quotes=True,
        with_greeks=True,
    )
    if not ok or not isinstance(chain_resp, dict):
        msg = chain_resp.get("message") if isinstance(chain_resp, dict) else "chain failed"
        return None, msg
    chain = chain_resp.get("chain") or []
    atm = chain_resp.get("atm_strike")
    if not chain or atm is None:
        return None, "Empty chain"
    market_context = chain_resp.get("market_context") or {}
    rows = []
    atm_ce_symbol, atm_pe_symbol, atm_lotsize = None, None, None
    for row in chain:
        try:
            strike = float(row.get("strike"))
        except (TypeError, ValueError):
            continue
        ce, pe = row.get("ce") or {}, row.get("pe") or {}
        rows.append(
            {
                "strike": strike,
                "ce_oi": _to_int(ce.get("oi")),
                "pe_oi": _to_int(pe.get("oi")),
                "ce_symbol": ce.get("symbol"),
                "pe_symbol": pe.get("symbol"),
                "lotsize": _to_int(ce.get("lotsize")) or _to_int(pe.get("lotsize")) or None,
            }
        )
        if ce.get("label") == "ATM":
            atm_ce_symbol = ce.get("symbol")
            atm_pe_symbol = pe.get("symbol")
            atm_lotsize = _to_int(ce.get("lotsize")) or _to_int(pe.get("lotsize")) or None

    spot = chain_resp.get("underlying_ltp")
    # The chain service already computed the canonical map from this chain, so
    # the panel and the chart overlay read the same numbers. The local rebuild
    # is the fallback for a chain that arrives without one.
    level_map = chain_resp.get("level_map") or build_level_map(
        spot,
        rows,
        underlying=underlying,
        expiry=expiry,
        atm_strike=atm,
    )
    if level_map["support"] is None and level_map["resistance"] is None:
        return None, "The chain carried no open interest to read a level from."

    return (
        {
            "underlying": underlying,
            "expiry": expiry,
            "atm_strike": atm,
            "spot": spot,
            "support": level_map["support"],
            "resistance": level_map["resistance"],
            "nearest_support": level_map["nearest_support"],
            "nearest_resistance": level_map["nearest_resistance"],
            "next_support": level_map["next_support"],
            "next_resistance": level_map["next_resistance"],
            "magnet": level_map["magnet"],
            "magnet_balance": level_map["magnet_balance"],
            "no_trade_zone": level_map["no_trade_zone"],
            "pcr": level_map["pcr"],
            "regime": level_map["regime"],
            "atm_ce_symbol": atm_ce_symbol,
            "atm_pe_symbol": atm_pe_symbol,
            "atm_lotsize": atm_lotsize,
            "ladder": [
                {"strike": row["strike"], "call_oi": row["call_oi"], "put_oi": row["put_oi"]}
                for row in level_map["ladder"]
            ],
            "warning": level_map["warning"],
            "market_context": market_context,
            "mode": "analyze" if get_analyze_mode() else "live",
        }
    ), None


def _get_levels(api_key, underlying):
    """Return the cached level map for an underlying, computing it on a miss.

    The cache is keyed by underlying, so the two panels never read each
    other's levels. A load that overlapped an invalidation is still returned to
    its caller; the cache simply does not store it.
    """

    def load():
        data, err = _compute_levels(api_key, underlying)
        if data is None:
            raise LevelsUnavailable(err or "Levels unavailable")
        return data

    try:
        return _levels_cache.get_or_load(underlying, load), None
    except LevelsUnavailable as exc:
        return None, str(exc)


@sahasra_bp.route("/sahasra/api/levels", methods=["GET"])
@check_session_validity
def levels():
    """OI support/resistance + PCR regime from the live chain (60s cache per underlying)."""
    underlying = _resolve_underlying(request.args.get("underlying"))
    if underlying is None:
        return jsonify({"status": "error", "message": "Unsupported underlying"}), 400
    api_key = _get_api_key()
    if not api_key:
        return jsonify({"status": "error", "message": "API key not configured"}), 401
    data, err = _get_levels(api_key, underlying)
    if data is None:
        return jsonify({"status": "error", "message": err or "Levels unavailable"}), 502
    return jsonify({"status": "success", "data": data})


@sahasra_bp.route("/sahasra/api/signal-log", methods=["POST"])
@check_session_validity
def record_signal():
    """Record a signal the model produced, with the map that produced it.

    Best effort by design: a signal that cannot be written must not stop the
    trade, so the response says whether it was kept without failing the call.
    """
    underlying = _resolve_underlying((request.get_json(silent=True) or {}).get("underlying"))
    if underlying is None:
        return jsonify({"status": "error", "message": "Unsupported underlying"}), 400
    payload = request.get_json(silent=True) or {}
    state = str(payload.get("state") or "")
    side = str(payload.get("side") or "")
    if not state or side not in ("CE", "PE"):
        return jsonify({"status": "error", "message": "The state and side are required"}), 400

    signal_id = record_sahasra_signal(
        underlying,
        state,
        side,
        confirmed=bool(payload.get("confirmed")),
        level_map=payload.get("level_map"),
        market_context=payload.get("market_context"),
        entry_spot=payload.get("entry_spot"),
        entry_premium=payload.get("entry_premium"),
        quantity=payload.get("quantity"),
    )
    return jsonify({"status": "success", "data": {"id": signal_id, "kept": signal_id is not None}})


@sahasra_bp.route("/sahasra/api/signal-log/close", methods=["POST"])
@check_session_validity
def close_signal():
    """Record what a signal turned into."""
    payload = request.get_json(silent=True) or {}
    signal_id = payload.get("id")
    outcome = str(payload.get("outcome") or "")
    if not signal_id or not outcome:
        return jsonify(
            {"status": "error", "message": "The signal id and outcome are required"}
        ), 400
    kept = close_sahasra_signal(signal_id, outcome, pnl_pct=payload.get("pnl_pct"))
    return jsonify({"status": "success", "data": {"kept": kept}})


@sahasra_bp.route("/sahasra/api/evidence", methods=["GET"])
@check_session_validity
def evidence():
    """The live record: how many signals, and how many reached their target."""
    underlying = _resolve_underlying(request.args.get("underlying"))
    if underlying is None:
        return jsonify({"status": "error", "message": "Unsupported underlying"}), 400
    return jsonify({"status": "success", "data": sahasra_evidence(underlying)})


@sahasra_bp.route("/sahasra/api/signal", methods=["GET"])
@check_session_validity
def signal():
    """15m-bar state vs S/R: breakout / breakdown / bounce setup / inside zone.

    Uses the last two CLOSED 15m bars of the requested underlying (no
    look-ahead). Regime gating is reported, not enforced — the panel shows
    longs blocked under bearish PCR rather than hiding them.
    """
    underlying = _resolve_underlying(request.args.get("underlying"))
    if underlying is None:
        return jsonify({"status": "error", "message": "Unsupported underlying"}), 400
    api_key = _get_api_key()
    if not api_key:
        return jsonify({"status": "error", "message": "API key not configured"}), 401
    data, err = _get_levels(api_key, underlying)
    if data is None:
        return jsonify({"status": "error", "message": err or "Levels unavailable"}), 502
    support, resistance = data["support"], data["resistance"]
    if support is None or resistance is None or not (support < resistance):
        return jsonify({"status": "success", "data": {**data, "state": "no_levels"}})

    today = datetime.now(IST).date()
    start = (today - timedelta(days=4)).strftime("%Y-%m-%d")
    try:
        ok, resp, _ = get_history(
            symbol=underlying,
            exchange=INDEX_EXCHANGE,
            interval="15m",
            start_date=start,
            end_date=today.strftime("%Y-%m-%d"),
            api_key=api_key,
        )
    except Exception as e:
        logger.exception(f"sahasra signal history error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    if not ok:
        msg = resp.get("message") if isinstance(resp, dict) else "History fetch failed"
        return jsonify({"status": "error", "message": msg}), 502
    rows = (resp.get("data") or []) if isinstance(resp, dict) else []
    bars = []
    for r in rows:
        try:
            bars.append(
                {
                    "ts": int(float(r["timestamp"])),
                    "o": float(r["open"]),
                    "h": float(r["high"]),
                    "low": float(r["low"]),
                    "c": float(r["close"]),
                    "v": float(r.get("volume") or 0),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    bars.sort(key=lambda b: b["ts"])
    now_ts = datetime.now(pytz.utc).timestamp()
    closed = [b for b in bars if b["ts"] + 15 * 60 <= now_ts]
    if len(closed) < 2:
        return jsonify({"status": "success", "data": {**data, "state": "warming_up"}})
    bar, prev = closed[-1], closed[-2]
    low, high, c, pc, o = bar["low"], bar["h"], bar["c"], prev["c"], bar["o"]
    if pc <= resistance < c:
        state, side = "breakout_up", "CE"
    elif pc >= support > c:
        state, side = "breakdown", "PE"
    elif high >= resistance and c < resistance:
        # The level was reached and handed back, so the wall held. This is the
        # setup that buys a put at resistance.
        state, side = "rejection", "PE"
    elif low <= support < c:
        state, side = "support_bounce", "CE"
    elif support < c < resistance:
        # Mid-range momentum (backtested as MIDRANGE): a closed 15m bar
        # entirely inside the zone takes the side of its own direction.
        # Smallest sample of the model (n=19, one regime) — the day cap,
        # cooldown, premium stop and max-hold rails carry the risk here.
        state, side = "inside_zone", ("CE" if c > o else ("PE" if c < o else None))
    elif c >= resistance:
        state, side = "above_resistance", None
    else:
        state, side = "below_support", None

    confirmation = evaluate_confirmation(state, side, bar, closed[:-1])

    regime = data["regime"]
    blocked = (regime == "bearish" and side == "CE") or (regime == "bullish" and side == "PE")
    return jsonify(
        {
            "status": "success",
            "data": {
                **data,
                "state": state,
                "side": side,
                "regime_blocked": blocked,
                "confirmation": confirmation,
                "bar": {**bar, "prev_close": pc},
            },
        }
    )
