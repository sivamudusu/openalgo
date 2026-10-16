"""Low-IV BANKNIFTY blueprint — manual + automated trading page backend.

Mirrors blueprints/scalping.py patterns:
- session auth via check_session_validity + api key from session
- structure-only strikes (WS streams live prices) with ?quotes=true opt-in
- MARKET orders via services.place_order_service with prefetched LTP quote
- @track_latency on order paths so /latency covers the new endpoints
"""

import re
from datetime import datetime, timedelta

import pytz
from flask import Blueprint, jsonify, request, session

from database.auth_db import get_api_key_for_tradingview
from database.low_iv_db import get_settings as get_stored_settings
from database.low_iv_db import get_today_series as get_stored_iv_series
from database.low_iv_db import record_iv as store_iv_sample
from database.low_iv_db import upsert_settings as save_settings
from database.settings_db import get_analyze_mode
from services.expiry_service import get_expiry_dates
from services.history_service import get_history
from services.low_iv_service import (
    LOW_IV_DEFAULTS,
    MIN_IV_SAMPLES,
    SETTINGS_SCHEMA,
    combined_signal,
    effective_settings,
    extract_atm_iv,
    iv_percentile,
    normalize_expiry,
    validate_settings,
)
from services.option_chain_service import get_option_chain
from utils.latency_monitor import track_latency
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

low_iv_bp = Blueprint("low_iv_bp", __name__, url_prefix="/")

LOW_IV_STRATEGY = LOW_IV_DEFAULTS["strategy_tag"]
# Sidebar strategies share this order path; the tag attributes trades for
# per-strategy P&L. Allowlist only — free text would fragment attribution.
VALID_STRATEGIES = {"LowIV", "Sahasra", "Momentum"}
UNDERLYING = LOW_IV_DEFAULTS["underlying"]
INDEX_EXCHANGE = LOW_IV_DEFAULTS["index_exchange"]
FO_EXCHANGE = LOW_IV_DEFAULTS["fo_exchange"]

IST = pytz.timezone("Asia/Kolkata")
IST_OFFSET_SECONDS = 19800
CHART_INTERVAL_TRADING_DAYS = {"1m": 1, "5m": 3, "15m": 9}
MAX_LOTS = 20
MAX_ORDER_QUANTITY = 100_000
VALID_ACTIONS = {"BUY", "SELL"}
VALID_PRODUCTS = {"MIS", "NRML"}


def _get_api_key():
    username = session.get("user")
    if not username:
        return None
    return get_api_key_for_tradingview(username)


def _resolve_session_auth():
    username = session.get("user")
    if not username:
        return None, None, None, {"status": "error", "message": "Not authenticated"}, 401
    api_key = get_api_key_for_tradingview(username)
    if not api_key:
        return (
            None,
            None,
            None,
            {"status": "error", "message": "API key required. Generate one at /apikey"},
            401,
        )
    return None, None, api_key, None, None


def _validate_quantity(symbol: str, exchange: str, quantity: int) -> str | None:
    from database.symbol import SymToken
    from database.symbol import db_session as symbol_session

    rec = (
        symbol_session.query(SymToken)
        .filter(SymToken.symbol == symbol, SymToken.exchange == exchange)
        .first()
    )
    if not rec or not rec.lotsize or rec.lotsize <= 0:
        return f"Unknown symbol or lot size unavailable: {symbol}"
    lotsize = rec.lotsize
    if quantity % lotsize != 0:
        return f"quantity must be a whole number of lots (lot size {lotsize})"
    if quantity > MAX_LOTS * lotsize:
        return f"quantity exceeds the {MAX_LOTS}-lot cap"
    try:
        from database.qty_freeze_db import get_freeze_qty_for_option

        freeze = get_freeze_qty_for_option(symbol, exchange)
        if freeze and freeze > 0 and quantity > freeze:
            return f"quantity exceeds the exchange freeze limit ({freeze})"
    except Exception as e:
        logger.warning(f"LowIV freeze-qty lookup failed for {symbol}: {e}")
    return None


def _num(value) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _check_liquidity(
    symbol: str,
    exchange: str,
    api_key: str,
    client_ltp: float | None,
    client_bid: float | None,
    client_ask: float | None,
    cfg: dict,
) -> tuple[dict | None, str | None]:
    """Enforce premium floor + max spread on the order path.

    Prefers a fresh server-side quote; falls back to the client's chain
    snapshot values when the feed has no depth for this leg. Returns
    (quote_used, error). quote_used carries the bid/ask/ltp the check ran on.
    """
    bid, ask, ltp = client_bid, client_ask, client_ltp
    try:
        from services.quotes_service import get_quotes

        ok, resp, _ = get_quotes(symbol=symbol, exchange=exchange, api_key=api_key)
        if ok and isinstance(resp, dict):
            data = resp.get("data") or {}
            if isinstance(data, dict):
                q_bid = _num(data.get("bid", data.get("best_bid", data.get("bidprice"))))
                q_ask = _num(data.get("ask", data.get("best_ask", data.get("askprice"))))
                q_ltp = _num(data.get("ltp", data.get("last_price", data.get("lastprice"))))
                # A nestedquote payload (some brokers) nests under the symbol.
                nested = data.get(symbol)
                if q_ltp is None and isinstance(nested, dict):
                    q_bid = q_bid or _num(nested.get("bid"))
                    q_ask = q_ask or _num(nested.get("ask"))
                    q_ltp = _num(nested.get("ltp"))
                bid = q_bid or bid
                ask = q_ask or ask
                ltp = q_ltp or ltp
    except Exception as e:
        logger.debug(f"LowIV server quote failed for {symbol}, using client values: {e}")

    ref = ask or ltp
    if ref is None:
        return None, "No live price for this contract — feed may be down, try Refresh chain"
    floor = float(cfg.get("premium_floor", 50))
    if floor > 0 and ref < floor:
        return None, (
            f"Premium {ref:.1f} below floor {floor:g} — leg too cheap/illiquid, order blocked"
        )
    max_spread = float(cfg.get("max_spread_points", 10))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        spread = ask - bid
        if spread > max_spread:
            return None, (
                f"Spread {spread:.1f} exceeds max {max_spread:g} "
                f"(bid {bid:.1f}/ask {ask:.1f}) — illiquid leg, order blocked"
            )
    return {"bid": bid, "ask": ask, "ltp": ltp}, None


@low_iv_bp.route("/lowiv/api/config", methods=["GET"])
@check_session_validity
def config():
    """Return Low-IV defaults + current sandbox/live mode for the page header."""
    return jsonify(
        {
            "status": "success",
            "data": {**LOW_IV_DEFAULTS, "mode": "analyze" if get_analyze_mode() else "live"},
        }
    )


@low_iv_bp.route("/lowiv/api/settings", methods=["GET"])
@check_session_validity
def get_settings():
    """Return effective settings (stored row merged over defaults)."""
    return jsonify({"status": "success", "data": effective_settings(get_stored_settings())})


@low_iv_bp.route("/lowiv/api/settings", methods=["POST"])
@check_session_validity
def update_settings():
    """Validate + persist tunable exits/gates/BB params. Unknown keys ignored."""
    data = request.get_json(silent=True) or {}
    clean, errors = validate_settings(data)
    if errors:
        return jsonify({"status": "error", "message": "Invalid settings", "errors": errors}), 400
    if not clean:
        return jsonify({"status": "error", "message": "No tunable settings provided"}), 400
    try:
        updated = save_settings(clean)
    except Exception:
        return jsonify({"status": "error", "message": "Failed to save settings"}), 500
    return jsonify({"status": "success", "data": effective_settings(updated)})


@low_iv_bp.route("/lowiv/api/settings/reset", methods=["POST"])
@check_session_validity
def reset_settings():
    """Restore defaults by overwriting the stored row with LOW_IV_DEFAULTS."""
    defaults = {k: LOW_IV_DEFAULTS[k] for k in SETTINGS_SCHEMA if k in LOW_IV_DEFAULTS}
    try:
        save_settings(defaults)
    except Exception:
        return jsonify({"status": "error", "message": "Failed to reset settings"}), 500
    return jsonify({"status": "success", "data": effective_settings(get_stored_settings())})


@low_iv_bp.route("/lowiv/api/iv", methods=["POST"])
@check_session_validity
def record_iv_sample():
    """Store one ATM-IV sample + contract snapshot into today's series.

    Called on each fresh chain refresh (30s), NOT on every evaluation tick.
    Non-positive prints (failed greeks inversion) are rejected, never stored.
    """
    data = request.get_json(silent=True) or {}
    try:
        iv = float(data.get("iv"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "iv is required"}), 400
    try:
        count = store_iv_sample(
            iv, data.get("strike"), data.get("bid"), data.get("ask"), data.get("legs")
        )
    except Exception:
        return jsonify({"status": "error", "message": "Failed to store IV sample"}), 500
    if count is None:
        return jsonify({"status": "error", "message": "IV print rejected (non-positive)"}), 400
    return jsonify({"status": "success", "data": {"samples_today": count}})


@low_iv_bp.route("/lowiv/api/iv/series", methods=["GET"])
@check_session_validity
def iv_series():
    """Today's IV series (values only, ascending)."""
    series = get_stored_iv_series()
    return jsonify(
        {"status": "success", "data": {"ivs": [iv for _, iv in series], "count": len(series)}}
    )


@low_iv_bp.route("/lowiv/api/iv/series/full", methods=["GET"])
@check_session_validity
def iv_series_full():
    """Today's full samples (ts/iv/strike/bid/ask) — the backtest feed."""
    from database.low_iv_db import get_today_series_full as get_full

    return jsonify({"status": "success", "data": get_full()})


@low_iv_bp.route("/lowiv/api/expiry", methods=["GET"])
@check_session_validity
def expiry():
    api_key = _get_api_key()
    if not api_key:
        return jsonify({"status": "error", "message": "API key not configured"}), 401
    success, response, status_code = get_expiry_dates(
        symbol=UNDERLYING, exchange=FO_EXCHANGE, instrumenttype="options", api_key=api_key
    )
    if not success:
        return jsonify(response), status_code
    raw_dates = response.get("data", []) or []
    return jsonify({"status": "success", "data": [normalize_expiry(d) for d in raw_dates]})


@low_iv_bp.route("/lowiv/api/strikes", methods=["GET"])
@check_session_validity
def strikes():
    """ATM +-strike ladder for BANKNIFTY. Structure-only by default (WS for live LTP)."""
    expiry_date = normalize_expiry((request.args.get("expiry") or "").strip().upper())
    if not expiry_date:
        return jsonify({"status": "error", "message": "expiry parameter is required"}), 400
    try:
        strike_count = int(request.args.get("strike_count", LOW_IV_DEFAULTS["strike_count"]))
    except (TypeError, ValueError):
        strike_count = LOW_IV_DEFAULTS["strike_count"]
    strike_count = max(1, min(strike_count, 25))
    with_quotes = request.args.get("quotes", "false").strip().lower() == "true"
    with_greeks = (
        request.args.get("greeks", "true" if with_quotes else "false").strip().lower() == "true"
    )

    api_key = _get_api_key()
    if not api_key:
        return jsonify({"status": "error", "message": "API key not configured"}), 401

    success, response, status_code = get_option_chain(
        underlying=UNDERLYING,
        exchange=FO_EXCHANGE,
        expiry_date=expiry_date,
        strike_count=strike_count,
        api_key=api_key,
        with_quotes=with_quotes,
        with_greeks=(with_greeks and with_quotes),
    )
    if not success:
        return jsonify(response), status_code
    if isinstance(response, dict):
        response["fo_exchange"] = FO_EXCHANGE
        response["underlying_symbol"] = UNDERLYING
        response["underlying_exchange"] = INDEX_EXCHANGE
        if with_quotes:
            response["atm_iv"] = extract_atm_iv(response.get("chain", []))
    return jsonify(response), status_code


@low_iv_bp.route("/lowiv/api/history", methods=["GET"])
@check_session_validity
def chart_history():
    """Candles for BANKNIFTY spot (or a selected option leg) for lightweight-charts."""
    api_key = _get_api_key()
    if not api_key:
        return jsonify({"status": "error", "message": "API key not configured"}), 401

    symbol = (request.args.get("symbol", UNDERLYING) or UNDERLYING).strip().upper()[:50]
    exchange = (request.args.get("exchange", INDEX_EXCHANGE) or INDEX_EXCHANGE).strip().upper()[:20]
    interval = (request.args.get("interval", "1m") or "1m").strip()
    if interval not in CHART_INTERVAL_TRADING_DAYS:
        interval = "1m"

    date_param = (request.args.get("date", "") or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_param):
        start_date = end_date = date_param
        keep_days = 1
    else:
        keep_days = CHART_INTERVAL_TRADING_DAYS[interval]
        today = datetime.now(IST).date()
        start_date = (today - timedelta(days=keep_days * 2 + 5)).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")

    try:
        success, response, status_code = get_history(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            api_key=api_key,
        )
    except Exception as e:
        logger.exception(f"lowiv chart history error for {symbol}.{exchange}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    if not success:
        message = response.get("message") if isinstance(response, dict) else str(response)
        return jsonify(
            {"status": "error", "message": message or "History fetch failed"}
        ), status_code

    rows = response.get("data", []) if isinstance(response, dict) else []
    by_date: dict[str, list] = {}
    for r in rows:
        ts = r.get("timestamp")
        if ts is None:
            continue
        try:
            ts = int(float(ts))
        except (TypeError, ValueError):
            continue
        ist_dt = datetime.fromtimestamp(ts, tz=pytz.utc).astimezone(IST)
        by_date.setdefault(ist_dt.strftime("%Y-%m-%d"), []).append((ts, r))
    if not by_date:
        return jsonify(
            {
                "status": "success",
                "symbol": symbol,
                "exchange": exchange,
                "interval": interval,
                "date": None,
                "candles": [],
            }
        ), 200

    selected_dates = sorted(by_date.keys())[-keep_days:]
    day_rows: list = []
    for dkey in selected_dates:
        day_rows.extend(by_date[dkey])
    day_rows.sort(key=lambda x: x[0])

    candles = []
    for ts, r in day_rows:
        bar_time = ((ts + IST_OFFSET_SECONDS) // 60) * 60
        try:
            candles.append(
                {
                    "time": bar_time,
                    "open": float(r.get("open", 0)),
                    "high": float(r.get("high", 0)),
                    "low": float(r.get("low", 0)),
                    "close": float(r.get("close", 0)),
                    "volume": float(r.get("volume", 0) or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    return jsonify(
        {
            "status": "success",
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "date": selected_dates[-1],
            "candles": candles,
        }
    ), 200


@low_iv_bp.route("/lowiv/api/signal", methods=["POST"])
@check_session_validity
def signal():
    """Combined Low-IV + Bollinger evaluator (pure math, no broker calls).

    The IV percentile is ranked over today's persistent series (09:15→now),
    shared by page and engine — not a 15-minute in-memory deque. The client
    posts the current IV + recent closes + its consecutive-below count; the
    server applies stored settings. Below MIN_IV_SAMPLES the verdict is
    always "collecting" (warm-up, never a fire).
    """
    data = request.get_json(silent=True) or {}
    try:
        current_iv = float(data.get("current_iv"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "current_iv is required"}), 400
    if not (current_iv > 0):
        return jsonify({"status": "error", "message": "current_iv must be positive"}), 400
    try:
        below_count = int(data.get("below_count", 0))
    except (TypeError, ValueError):
        below_count = 0
    closes = data.get("closes") or []
    cfg = effective_settings(get_stored_settings())
    series = [iv for _, iv in get_stored_iv_series()]
    if len(series) < MIN_IV_SAMPLES:
        return jsonify(
            {
                "status": "success",
                "data": {
                    "current_iv": current_iv,
                    "iv_percentile": None,
                    "threshold": cfg["iv_percentile_max"],
                    "samples_today": len(series),
                    "min_samples": MIN_IV_SAMPLES,
                    "fire": False,
                    "reason": f"Collecting IV history ({len(series)}/{MIN_IV_SAMPLES} samples)",
                    "settings": {k: cfg[k] for k in SETTINGS_SCHEMA},
                },
            }
        )
    pct = iv_percentile(current_iv, series + [current_iv])
    result = combined_signal(pct, below_count, closes, cfg)
    return jsonify(
        {
            "status": "success",
            "data": {
                "current_iv": current_iv,
                "iv_percentile": pct,
                "threshold": cfg["iv_percentile_max"],
                "samples_today": len(series),
                "min_samples": MIN_IV_SAMPLES,
                "settings": {k: cfg[k] for k in SETTINGS_SCHEMA},
                **result,
            },
        }
    )


@low_iv_bp.route("/lowiv/api/order", methods=["POST"])
@check_session_validity
@track_latency("PLACE")
def order():
    """Manual MARKET order for a Low-IV leg (BUY/SELL CE). Tagged strategy=LowIV."""
    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip()
    exchange = (data.get("exchange") or FO_EXCHANGE).strip().upper()
    action = (data.get("action") or "").strip().upper()
    product = (data.get("product") or "MIS").strip().upper()
    try:
        quantity = int(data.get("quantity", 0))
    except (TypeError, ValueError):
        quantity = 0

    if not symbol:
        return jsonify({"status": "error", "message": "symbol is required"}), 400
    if exchange != FO_EXCHANGE:
        return jsonify({"status": "error", "message": f"LowIV page trades {FO_EXCHANGE} only"}), 400
    if action not in VALID_ACTIONS:
        return jsonify({"status": "error", "message": f"Invalid action: {action}"}), 400
    if product not in VALID_PRODUCTS:
        return jsonify({"status": "error", "message": f"Invalid product: {product}"}), 400
    if quantity <= 0:
        return jsonify({"status": "error", "message": "quantity must be positive"}), 400
    if quantity > MAX_ORDER_QUANTITY:
        return jsonify({"status": "error", "message": "quantity exceeds the safety limit"}), 400
    qty_err = _validate_quantity(symbol, exchange, quantity)
    if qty_err:
        return jsonify({"status": "error", "message": qty_err}), 400
    strategy = (data.get("strategy") or LOW_IV_STRATEGY).strip()
    if strategy not in VALID_STRATEGIES:
        return jsonify({"status": "error", "message": f"Invalid strategy: {strategy}"}), 400

    _, _, api_key, err, code = _resolve_session_auth()
    if err:
        return jsonify(err), code

    cfg = effective_settings(get_stored_settings())
    quote_used, liq_err = _check_liquidity(
        symbol,
        exchange,
        api_key,
        _num(data.get("ltp")),
        _num(data.get("bid")),
        _num(data.get("ask")),
        cfg,
    )
    if liq_err:
        return jsonify({"status": "error", "message": liq_err}), 400

    from services.place_order_service import place_order

    order_data = {
        "strategy": strategy,
        "symbol": symbol,
        "exchange": exchange,
        "action": action,
        "pricetype": "MARKET",
        "product": product,
        "quantity": quantity,
    }
    prefetched_quote = None
    try:
        ltp_val = (quote_used or {}).get("ltp") or 0
        if ltp_val > 0:
            prefetched_quote = {"ltp": ltp_val}
    except (TypeError, ValueError):
        prefetched_quote = None

    success, response, status_code = place_order(
        order_data=order_data,
        api_key=api_key,
        auth_token=None,
        broker=None,
        prefetched_quote=prefetched_quote,
    )
    if isinstance(response, dict):
        response["fill_reference_ltp"] = (quote_used or {}).get("ltp")
    return jsonify(response), status_code


@low_iv_bp.route("/lowiv/api/auto_state", methods=["GET"])
@check_session_validity
def auto_state():
    """Reconciliation source for page-auto: real book state for its legs.

    Query params: symbol, exchange, product for the tracked leg (repeat
    `symbols=` for extra legs whose realized P&L counts toward profit-lock).
    Returns open qty + average fill price (never the page's estimate) and
    today's realized P&L summed over the given symbols. In live mode the
    broker book carries no strategy tag or per-symbol realized field, so
    realized_today is sandbox-only and flagged accordingly.
    """
    _, _, api_key, err, code = _resolve_session_auth()
    if err:
        return jsonify(err), code

    symbol = (request.args.get("symbol") or "").strip()
    exchange = (request.args.get("exchange") or FO_EXCHANGE).strip().upper()
    product = (request.args.get("product") or "").strip().upper()
    extra = [s.strip() for s in (request.args.get("symbols") or "").split(",") if s.strip()]
    wanted = [symbol, *extra] if symbol else extra

    from services.positionbook_service import get_positionbook

    ok, resp, _ = get_positionbook(api_key=api_key, auth_token=None, broker=None)
    if not ok or not isinstance(resp, dict):
        msg = resp.get("message") if isinstance(resp, dict) else "positionbook unavailable"
        return jsonify({"status": "error", "message": f"Position book unavailable: {msg}"}), 502

    mode = "analyze" if get_analyze_mode() else "live"
    book = resp.get("data") or []
    position = {"qty": 0, "avg_price": 0.0, "ltp": 0.0}
    realized_today = 0.0
    realized_supported = mode == "analyze"
    for row in book:
        if not isinstance(row, dict):
            continue
        if (row.get("symbol") or "") != symbol:
            continue
        if (row.get("exchange") or "").upper() != exchange:
            continue
        if product and (row.get("product") or "").upper() != product:
            continue
        try:
            qty = int(float(row.get("quantity") or 0))
        except (TypeError, ValueError):
            qty = 0
        if qty != 0:
            try:
                avg = float(row.get("average_price") or 0)
            except (TypeError, ValueError):
                avg = 0.0
            try:
                ltp = float(row.get("ltp") or 0)
            except (TypeError, ValueError):
                ltp = 0.0
            position = {"qty": qty, "avg_price": avg, "ltp": ltp}
    if realized_supported:
        for row in book:
            if not isinstance(row, dict):
                continue
            if (row.get("symbol") or "") not in wanted:
                continue
            if (row.get("exchange") or "").upper() != exchange:
                continue
            try:
                realized_today += float(row.get("today_realized_pnl") or 0)
            except (TypeError, ValueError):
                continue
    return jsonify(
        {
            "status": "success",
            "data": {
                "mode": mode,
                "position": position,
                "realized_today": round(realized_today, 2),
                "realized_supported": realized_supported,
            },
        }
    )


@low_iv_bp.route("/lowiv/api/close_leg", methods=["POST"])
@check_session_validity
@track_latency("CLOSE")
def close_leg():
    """Risk-reducing exit for one Low-IV leg (whole-lot, bypasses entry lot cap)."""
    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip()
    exchange = (data.get("exchange") or FO_EXCHANGE).strip().upper()
    action = (data.get("action") or "").strip().upper()
    product = (data.get("product") or "MIS").strip().upper()
    try:
        quantity = int(data.get("quantity", 0))
    except (TypeError, ValueError):
        quantity = 0
    if not symbol or exchange != FO_EXCHANGE or action not in VALID_ACTIONS:
        return jsonify({"status": "error", "message": "Invalid symbol/exchange/action"}), 400
    if product not in VALID_PRODUCTS or quantity <= 0:
        return jsonify({"status": "error", "message": "Invalid product/quantity"}), 400
    strategy = (data.get("strategy") or LOW_IV_STRATEGY).strip()
    if strategy not in VALID_STRATEGIES:
        return jsonify({"status": "error", "message": f"Invalid strategy: {strategy}"}), 400

    _, _, api_key, err, code = _resolve_session_auth()
    if err:
        return jsonify(err), code
    from services.place_order_service import place_order

    success, response, status_code = place_order(
        order_data={
            "strategy": strategy,
            "symbol": symbol,
            "exchange": exchange,
            "action": action,
            "pricetype": "MARKET",
            "product": product,
            "quantity": quantity,
        },
        api_key=api_key,
        auth_token=None,
        broker=None,
    )
    return jsonify(response), status_code


@low_iv_bp.route("/lowiv/api/cancel_all", methods=["POST"])
@check_session_validity
@track_latency("CANCEL_ALL")
def cancel_all():
    _, _, api_key, err, code = _resolve_session_auth()
    if err:
        return jsonify(err), code
    from services.cancel_all_order_service import cancel_all_orders

    success, response, status_code = cancel_all_orders(
        order_data={}, api_key=api_key, auth_token=None, broker=None
    )
    return jsonify(response), status_code
