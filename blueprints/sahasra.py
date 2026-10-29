"""Sahasra (YouTuber) OI model — monitoring + signal backend for /trading.

Encodes the distilled rules (R1/R2, citations in agentic_trading_v2
output/claims): support = nearest put-OI peak at/below ATM, resistance =
nearest call-OI peak at/above ATM, PCR regime (<0.7 bearish, 0.7-1.0
neutral, >1.0 bullish). Levels come from the LIVE chain (what he reads on
air), cached 60s per underlying. The same rules serve BANKNIFTY and NIFTY;
the caller selects one with ?underlying=. Manual trading reuses
POST /lowiv/api/order (NFO MARKET).
"""

import time
from datetime import datetime, timedelta

import pytz
from flask import Blueprint, jsonify, request, session

from database.auth_db import get_api_key_for_tradingview
from database.settings_db import get_analyze_mode
from services.expiry_service import get_expiry_dates
from services.history_service import get_history
from services.option_chain_service import get_option_chain
from utils.logging import get_logger
from utils.session import check_session_validity

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
_levels_cache: dict[str, dict] = {}


def _resolve_underlying(value: str | None) -> str | None:
    """Allowlisted underlying from the query string, or None when unsupported."""
    underlying = (value or DEFAULT_UNDERLYING).strip().upper()
    return underlying if underlying in UNDERLYINGS else None


def _get_api_key():
    username = session.get("user")
    if not username:
        return None
    return get_api_key_for_tradingview(username)


def _nearest_peak(strikes, oi_by_strike, atm, side):
    """Nearest local-maximum OI strike at/below (support) or at/above ATM."""
    ordered = sorted(strikes)
    best, best_oi = None, -1
    for i, s in enumerate(ordered):
        if side == "below" and s > atm:
            continue
        if side == "above" and s < atm:
            continue
        oi = oi_by_strike.get(s, 0)
        lo = oi_by_strike.get(ordered[i - 1]) if i > 0 else None
        hi = oi_by_strike.get(ordered[i + 1]) if i < len(ordered) - 1 else None
        if lo is not None and oi <= lo:
            continue
        if hi is not None and oi <= hi:
            continue
        dist = abs(s - atm)
        if best is None or dist < abs(best - atm) or (dist == abs(best - atm) and oi > best_oi):
            best, best_oi = s, oi
    if best is None:  # fallback: side max
        pool = [s for s in ordered if (s <= atm if side == "below" else s >= atm)]
        if pool:
            best = max(pool, key=lambda s: oi_by_strike.get(s, 0))
    return best


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
        with_greeks=False,
    )
    if not ok or not isinstance(chain_resp, dict):
        msg = chain_resp.get("message") if isinstance(chain_resp, dict) else "chain failed"
        return None, msg
    chain = chain_resp.get("chain") or []
    atm = chain_resp.get("atm_strike")
    if not chain or atm is None:
        return None, "Empty chain"
    puts, calls, strikes = {}, {}, []
    atm_ce_symbol, atm_pe_symbol, atm_lotsize = None, None, None
    for row in chain:
        try:
            s = int(float(row.get("strike")))
        except (TypeError, ValueError):
            continue
        strikes.append(s)
        ce, pe = row.get("ce") or {}, row.get("pe") or {}
        try:
            puts[s] = int(float(pe.get("oi") or 0))
        except (TypeError, ValueError):
            puts[s] = 0
        try:
            calls[s] = int(float(ce.get("oi") or 0))
        except (TypeError, ValueError):
            calls[s] = 0
        if ce.get("label") == "ATM":
            atm_ce_symbol = ce.get("symbol")
            atm_pe_symbol = pe.get("symbol")
            try:
                atm_lotsize = int(ce.get("lotsize") or pe.get("lotsize") or 0) or None
            except (TypeError, ValueError):
                atm_lotsize = None
    support = _nearest_peak(strikes, puts, atm, "below")
    resistance = _nearest_peak(strikes, calls, atm, "above")
    total_put = sum(puts.values())
    total_call = sum(calls.values())
    ladder = [
        {"strike": s, "call_oi": calls.get(s, 0), "put_oi": puts.get(s, 0)} for s in sorted(strikes)
    ]
    pcr = (total_put / total_call) if total_call > 0 else None
    if pcr is not None and pcr < PCR_BEARISH_MAX:
        regime = "bearish"
    elif pcr is not None and pcr > PCR_BULLISH_MIN:
        regime = "bullish"
    else:
        regime = "neutral"
    return (
        {
            "underlying": underlying,
            "expiry": expiry,
            "atm_strike": atm,
            "spot": chain_resp.get("underlying_ltp"),
            "support": support,
            "resistance": resistance,
            "pcr": round(pcr, 3) if pcr is not None else None,
            "regime": regime,
            "atm_ce_symbol": atm_ce_symbol,
            "atm_pe_symbol": atm_pe_symbol,
            "atm_lotsize": atm_lotsize,
            "ladder": ladder,
            "mode": "analyze" if get_analyze_mode() else "live",
        }
    ), None


def _get_levels(api_key, underlying):
    now = time.time()
    cached = _levels_cache.get(underlying) or {}
    if cached.get("data") is not None and now - cached.get("ts", 0.0) < LEVELS_TTL_SECONDS:
        return cached["data"], None
    data, err = _compute_levels(api_key, underlying)
    if data is not None:
        _levels_cache[underlying] = {"data": data, "ts": now}
    return data, err


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
    low, c, pc, o = bar["low"], bar["c"], prev["c"], bar["o"]
    if pc <= resistance < c:
        state, side = "breakout_up", "CE"
    elif pc >= support > c:
        state, side = "breakdown", "PE"
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
                "bar": {**bar, "prev_close": pc},
            },
        }
    )
