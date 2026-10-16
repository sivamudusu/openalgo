#!/usr/bin/env python
"""Low-IV BANKNIFTY engine — port of agentic_trading_v2 LowIVLongStrategy.

Runs inside OpenAlgo's /python host (same host, localhost REST). Uses the
OpenAlgo REST API (no direct broker calls) so sandbox/live routing, RMS and
audit stay intact. Latency win vs the external bot: co-located HTTP + one
chain fetch per 30s, spot/quote ticks otherwise.

Entry = Low-IV AND Bollinger %B confirmation:
- Long ATM CE when IV percentile < iv_percentile_max, debounced N ticks,
  cooldown between entries, no entries at/after max_entry_hour.
- Bollinger %B(20,2): %B in the bottom zone (<= bb_bottom_pctb, 0=oversold)
  and rising toward 0.5.
Exits are scoped per position: target +X%, stop -Y%, max hold Zm.

All tunables live on the /lowiv page (server settings) — the engine pulls
them via POST /api/v1/lowiv/settings with its OPENALGO_API_KEY at boot and
refreshes every 5 minutes. Top-of-file FALLBACK_* apply only when the server
is unreachable.
"""

import os
import time
from collections import deque
from datetime import datetime, timedelta

import pytz

try:
    import requests
except ImportError:  # pragma: no cover - openalgo SDK already requires requests
    requests = None

from openalgo import api

api_key = os.getenv("OPENALGO_API_KEY")
ws_url = os.getenv("WEBSOCKET_URL", "ws://127.0.0.1:8765")
exchange = os.getenv("OPENALGO_STRATEGY_EXCHANGE", os.getenv("EXCHANGE", "NFO"))

if not api_key:
    print("Error: OPENALGO_API_KEY environment variable not set")
    raise SystemExit(1)


def _resolve_host():
    """Pick a reachable OpenAlgo API host.

    The /python host injects HOST_SERVER from the server env, which on some
    installs points at the frontend dev server (e.g. :5173) instead of the
    Flask API (:5000). Probe candidates and use the first that answers the
    health endpoint; the engine runs on the same host, so loopback is right.
    """
    candidates = []
    env_host = (os.getenv("HOST_SERVER") or "").strip().rstrip("/")
    if env_host:
        candidates.append(env_host)
    flask_port = os.getenv("FLASK_PORT", "5000").strip() or "5000"
    candidates.append(f"http://127.0.0.1:{flask_port}")
    candidates.append("http://127.0.0.1:5000")
    seen = set()
    ordered = [h for h in candidates if not (h in seen or seen.add(h))]
    if requests is None:
        print(f"No requests lib; using {ordered[0]} blind")
        return ordered[0]
    for h in ordered:
        try:
            r = requests.get(f"{h}/auth/check-setup", timeout=3)
            if r.status_code < 500:
                if h != env_host:
                    print(f"HOST_SERVER {env_host} unreachable, using {h}")
                return h
        except Exception:
            continue
    print(f"No candidate API host answered {ordered}; using {ordered[0]}")
    return ordered[0]


host = _resolve_host()

STRATEGY = "LowIV"
UNDERLYING = "BANKNIFTY"
INDEX_EXCHANGE = "NSE_INDEX"
PRODUCT = "MIS"

# Fallbacks when the server settings endpoint is unreachable.
FALLBACK = {
    "iv_percentile_max": 25,
    "debounce_ticks": 3,
    "reentry_cooldown_min": 60,
    "max_entry_hour": 14,
    "exit_target_pct": 4,
    "exit_stop_pct": 12,
    "exit_max_hold_min": 15,
    "max_entries_per_day": 0,
    "profit_lock_enabled": True,
    "profit_lock_threshold": 4000,
    "bb_enabled": True,
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_bottom_pctb": 0.20,
    "bb_touch_tol_pct": 0.15,
}

IV_HISTORY_LEN = 30
POLL_SECONDS = 5
CHAIN_SECONDS = 30
SETTINGS_REFRESH_SECONDS = 300
BB_HISTORY_SECONDS = 60
MIN_IV_SAMPLES = 15  # full-day window needs depth before it can fire

IST = pytz.timezone("Asia/Kolkata")

client = api(api_key=api_key, host=host, ws_url=ws_url)
iv_history: deque = deque(maxlen=IV_HISTORY_LEN)
below_count = 0
last_fire_ts = None
fires_today = 0
fire_date = ""
open_trade = None  # dict(symbol, qty, entry, entry_ts)
traded_today: set = set()  # symbols entered today (for profit-lock accounting)
profit_locked_day = ""
SETTINGS = dict(FALLBACK)
settings_ts = 0


def today_key():
    return datetime.now(IST).strftime("%Y-%m-%d")


def today_realized():
    """Today's realized Rs over symbols entered today (profit-lock input)."""
    if not traded_today:
        return 0.0
    try:
        book = client.positionbook()
        rows = (book.get("data") if isinstance(book, dict) else None) or []
        total = 0.0
        for row in rows:
            if not isinstance(row, dict):
                continue
            if (
                row.get("symbol") in traded_today
                and (row.get("exchange") or "").upper() == exchange
            ):
                try:
                    total += float(row.get("today_realized_pnl") or 0)
                except (TypeError, ValueError):
                    continue
        return total
    except Exception as e:
        print(f"Realized-P&L fetch failed: {e}")
        return 0.0


def profit_locked():
    """True once today's realized Low-IV profit hits the threshold."""
    global profit_locked_day
    if not SETTINGS.get("profit_lock_enabled", True):
        return False
    threshold = float(SETTINGS.get("profit_lock_threshold", 4000))
    if threshold <= 0:
        return False
    realized = today_realized()
    if realized >= threshold:
        if profit_locked_day != today_key():
            profit_locked_day = today_key()
            print(
                f"Profit lock: realized Rs {realized:.0f} >= {threshold:g} — no new entries today"
            )
        return True
    return False


def refresh_settings(force=False):
    """Pull tunables from the server so page + engine share one config."""
    global SETTINGS, settings_ts
    if not force and time.time() - settings_ts < SETTINGS_REFRESH_SECONDS:
        return
    try:
        if requests is None:
            return
        r = requests.post(f"{host}/api/v1/lowiv/settings", json={"apikey": api_key}, timeout=10)
        data = r.json().get("data") if r.ok else None
        if isinstance(data, dict):
            for key in FALLBACK:
                if data.get(key) is not None:
                    SETTINGS[key] = data[key]
            settings_ts = time.time()
            print(f"Low-IV settings synced: {SETTINGS}")
    except Exception as e:
        print(f"Low-IV settings fetch failed, using fallbacks: {e}")


def iv_percentile(cur, hist):
    vals = [float(v) for v in hist if v is not None]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return 50.0
    return max(0.0, min(100.0, (float(cur) - lo) / (hi - lo) * 100.0))


def record_server_iv(value, quote=None, legs=None):
    """Store one IV sample + contract snapshot + ±3 ladder (best-effort)."""
    try:
        if requests is None or not (value and value > 0):
            return
        body = {"apikey": api_key, "iv": float(value)}
        if isinstance(quote, dict):
            for key in ("strike", "bid", "ask"):
                if quote.get(key) is not None:
                    body[key] = quote[key]
        if legs:
            body["legs"] = legs[:20]
        requests.post(f"{host}/api/v1/lowiv/iv", json=body, timeout=10)
    except Exception as e:
        print(f"IV record failed: {e}")


def server_iv_series():
    """Today's persistent IV series, or [] when unreachable."""
    try:
        if requests is None:
            return []
        r = requests.post(f"{host}/api/v1/lowiv/iv_series", json={"apikey": api_key}, timeout=10)
        data = r.json().get("data") if r.ok else None
        ivs = (data or {}).get("ivs") or []
        return [float(x) for x in ivs if x and float(x) > 0]
    except Exception as e:
        print(f"IV series fetch failed: {e}")
        return []


def bollinger_gate(closes):
    """True when %B (0=lower/OS, 0.5=basis, 1=upper/OB) is in the bottom zone
    and rising toward 0.5 — the same oscillator broker charts show."""
    if not SETTINGS.get("bb_enabled", True):
        return True, "Bollinger gate disabled"
    period = int(SETTINGS.get("bb_period", 20))
    std = float(SETTINGS.get("bb_std", 2.0))
    bottom = float(SETTINGS.get("bb_bottom_pctb", 0.20))
    vals = []
    for c in closes or []:
        try:
            vals.append(float(c))
        except (TypeError, ValueError):
            continue
    if len(vals) < period + 1:
        return False, f"Need {period + 1} closes for Bollinger %B({period},{std})"
    bands = []
    for window in (vals[-(period + 1) : -1], vals[-period:]):
        basis = sum(window) / period
        sd = (sum((x - basis) ** 2 for x in window) / period) ** 0.5
        upper, lower = basis + std * sd, basis - std * sd
        denom = upper - lower
        bands.append((window[-1] - lower) / denom if denom > 0 else 0.5)
    prev_pct_b, pct_b = bands
    at_bottom = pct_b <= bottom
    rising = vals[-1] > vals[-2] and pct_b > prev_pct_b
    if not at_bottom:
        return False, f"%B {pct_b:.2f} above bottom zone {bottom:.2f} (0=OS, 0.5=mid, 1=OB)"
    if not rising:
        return False, f"%B {pct_b:.2f} in bottom zone but not yet rising"
    return True, f"%B {pct_b:.2f} bottomed and rising toward 0.5"


def atm_iv_from_chain(chain_resp):
    chain = (
        (chain_resp or {}).get("data", {}).get("chain") if isinstance(chain_resp, dict) else None
    )
    if isinstance(chain_resp, dict) and "chain" in chain_resp:
        chain = chain_resp["chain"]
    if not chain:
        return None, None, None, {}, []
    atm_idx = next(
        (i for i, row in enumerate(chain) if (row.get("ce") or {}).get("label") == "ATM"),
        None,
    )
    legs = []
    if atm_idx is not None:
        for row in chain[max(0, atm_idx - 3) : atm_idx + 4]:
            try:
                strike = int(row.get("strike"))
            except (TypeError, ValueError):
                continue
            for side in ("ce", "pe"):
                leg = row.get(side) or {}
                try:
                    leg_iv = float(leg.get("implied_volatility"))
                except (TypeError, ValueError):
                    continue
                if not leg_iv or leg_iv <= 0 or leg_iv > 200:
                    continue
                legs.append(
                    {
                        "strike": strike,
                        "option_type": side.upper(),
                        "iv": leg_iv,
                        "bid": leg.get("bid"),
                        "ask": leg.get("ask"),
                    }
                )
    for row in chain:
        ce, pe = row.get("ce", {}), row.get("pe", {})
        if ce.get("label") == "ATM":
            ivs = [x for x in (ce.get("implied_volatility"), pe.get("implied_volatility")) if x]
            atm = row.get("strike")
            sym = ce.get("symbol")
            ltp = ce.get("ltp") or 0
            iv = sum(ivs) / len(ivs) if ivs else None
            quote = {"strike": atm, "bid": ce.get("bid"), "ask": ce.get("ask")}
            return iv, atm, (sym, ltp), quote, legs
    return None, None, None, {}, legs


def spot_closes():
    """Recent 1m BANKNIFTY closes for the Bollinger gate."""
    try:
        end = datetime.now(IST).strftime("%Y-%m-%d")
        start = (datetime.now(IST) - timedelta(days=3)).strftime("%Y-%m-%d")
        df = client.history(
            symbol=UNDERLYING,
            exchange=INDEX_EXCHANGE,
            interval="1m",
            start_date=start,
            end_date=end,
        )
        if df is None or getattr(df, "empty", True):
            return []
        return [float(x) for x in df["close"].tolist()]
    except Exception as e:
        print(f"History fetch failed: {e}")
        return []


def in_entry_window():
    max_hour = int(SETTINGS.get("max_entry_hour", 14))
    if not max_hour:
        return True
    return datetime.now(IST).hour < max_hour


def check_exit(ltp):
    if not open_trade:
        return None
    entry = open_trade["entry"]
    held_min = (datetime.now(IST) - open_trade["entry_ts"]).total_seconds() / 60
    pnl_pct = (ltp - entry) / entry * 100 if entry else 0
    if pnl_pct >= float(SETTINGS.get("exit_target_pct", 4)):
        return "TARGET_HIT"
    if pnl_pct <= -float(SETTINGS.get("exit_stop_pct", 12)):
        return "STOPLOSS_HIT"
    if held_min >= int(SETTINGS.get("exit_max_hold_min", 15)):
        return "MAX_HOLD_REACHED"
    return None


def run():
    global below_count, last_fire_ts, fires_today, fire_date, open_trade, traded_today
    refresh_settings(force=True)
    print(f"Starting Low-IV {UNDERLYING} engine (strategy={STRATEGY}, exchange={exchange})...")
    expiry = None
    chain_cache_ts = 0
    chain_resp = None
    bb_cache_ts = 0
    closes: list = []
    series_cache: list = []
    series_ts = 0
    last_collecting_count = -1
    no_iv_count = 0
    last_heartbeat = 0

    while True:
        try:
            refresh_settings()
            day = today_key()
            if day != fire_date:
                fire_date, fires_today, below_count = day, 0, 0
                traded_today.clear()

            if expiry is None:
                exp = client.expiry(symbol=UNDERLYING, exchange=exchange, instrumenttype="options")
                dates = (exp.get("data") if isinstance(exp, dict) else None) or []
                expiry = str(dates[0]).replace("-", "").upper() if dates else None
                if not expiry:
                    print("No expiry found, retrying...")
                    time.sleep(POLL_SECONDS)
                    continue

            now = time.time()
            if now - chain_cache_ts >= CHAIN_SECONDS:
                chain_resp = client.optionchain(
                    underlying=UNDERLYING,
                    exchange=exchange,
                    expiry_date=expiry,
                    strike_count=7,
                    with_quotes=True,
                    with_greeks=True,  # legs must carry implied_volatility for the IV gate
                )
                chain_cache_ts = now
            if now - bb_cache_ts >= BB_HISTORY_SECONDS:
                closes = spot_closes()
                bb_cache_ts = now
            if now - series_ts >= CHAIN_SECONDS:
                series_cache = server_iv_series()
                series_ts = now

            cur_iv, atm, ce_info, leg_quote, leg_ladder = atm_iv_from_chain(chain_resp)
            if cur_iv is not None and cur_iv > 0:
                iv_history.append(float(cur_iv))
                no_iv_count = 0
                if now - chain_cache_ts < POLL_SECONDS * 2:
                    record_server_iv(cur_iv, leg_quote, leg_ladder)
            elif now - chain_cache_ts < POLL_SECONDS * 2:
                # Fresh chain but no IV in the legs (greeks missing/failed).
                no_iv_count += 1
                if no_iv_count <= 3:
                    print(
                        "WARNING: chain legs carry no implied_volatility "
                        f"({no_iv_count}x) — IV gate blind until greeks arrive"
                    )
            # Heartbeat so a quiet engine is distinguishable from a dead one.
            if now - last_heartbeat >= 60:
                last_heartbeat = now
                print(
                    f"HB iv={cur_iv} series={len(series_cache)} below={below_count} "
                    f"pos={open_trade['symbol'] if open_trade else 'flat'}"
                )
            # Percentile over today's persistent series (09:15→now); fall back
            # to the local deque only when the server series is unreachable.
            if cur_iv is not None and len(series_cache) >= MIN_IV_SAMPLES:
                pct = iv_percentile(cur_iv, series_cache + [cur_iv])
            elif cur_iv is not None and series_cache:
                if len(series_cache) != last_collecting_count:
                    last_collecting_count = len(series_cache)
                    print(f"Collecting IV history ({len(series_cache)}/{MIN_IV_SAMPLES} samples)")
                pct = None
            else:
                pct = iv_percentile(cur_iv, list(iv_history)) if cur_iv is not None else None

            # Manage open position first (exits-first, like the external orchestrator).
            if open_trade:
                q = client.quotes(symbol=open_trade["symbol"], exchange=exchange)
                ltp = float(((q.get("data") or {}) if isinstance(q, dict) else {}).get("ltp") or 0)
                reason = check_exit(ltp) if ltp else None
                if reason:
                    resp = client.placesmartorder(
                        strategy=STRATEGY,
                        symbol=open_trade["symbol"],
                        action="SELL",
                        exchange=exchange,
                        price_type="MARKET",
                        product=PRODUCT,
                        quantity=open_trade["qty"],
                        position_size=0,
                    )
                    print(f"Exit {reason} {open_trade['symbol']} @ {ltp}: {resp}")
                    open_trade = None
                time.sleep(POLL_SECONDS)
                continue

            iv_max = float(SETTINGS.get("iv_percentile_max", 25))
            if pct is None or not in_entry_window():
                time.sleep(POLL_SECONDS)
                continue
            day_cap = int(SETTINGS.get("max_entries_per_day", 0))
            if day_cap > 0 and fires_today >= day_cap:
                print(f"Day cap reached ({fires_today}/{day_cap}) — no new entries")
                time.sleep(POLL_SECONDS)
                continue
            if profit_locked():
                time.sleep(POLL_SECONDS)
                continue
            if pct >= iv_max:
                below_count = 0
                time.sleep(POLL_SECONDS)
                continue
            cooldown = int(SETTINGS.get("reentry_cooldown_min", 60))
            if last_fire_ts and (datetime.now(IST) - last_fire_ts).total_seconds() < cooldown * 60:
                time.sleep(POLL_SECONDS)
                continue
            below_count += 1
            if below_count < int(SETTINGS.get("debounce_ticks", 3)):
                time.sleep(POLL_SECONDS)
                continue

            bb_ok, bb_reason = bollinger_gate(closes)
            if not bb_ok:
                print(f"IV {pct:.1f} < {iv_max} but Bollinger blocks: {bb_reason}")
                time.sleep(POLL_SECONDS)
                continue

            symbol, ltp = ce_info or (None, 0)
            if not symbol or not ltp:
                time.sleep(POLL_SECONDS)
                continue
            qty = 30  # 1 lot BANKNIFTY default; size via UI / POSITION_SIZING in live use
            resp = client.placesmartorder(
                strategy=STRATEGY,
                symbol=symbol,
                action="BUY",
                exchange=exchange,
                price_type="MARKET",
                product=PRODUCT,
                quantity=qty,
                position_size=qty,
            )
            print(f"Entry IV% {pct:.1f} + BB [{bb_reason}] BUY {symbol} @ {ltp}: {resp}")
            open_trade = {
                "symbol": symbol,
                "qty": qty,
                "entry": float(ltp),
                "entry_ts": datetime.now(IST),
            }
            last_fire_ts = datetime.now(IST)
            fires_today += 1
            traded_today.add(symbol)
            below_count = 0
        except Exception as e:
            print(f"Error in low-iv engine: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
