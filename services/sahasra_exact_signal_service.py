"""Closed-bar signal engine for the Sahasra exact model.

The engine never reads a clock, a database or a broker. The caller passes the
level map and the broker's 15-minute bars; the engine drops the forming candle,
evaluates the last two closed candles and returns a state, a confirmation and a
suggested long-premium trade.
"""

from datetime import UTC, datetime, timedelta

BAR_SECONDS = 15 * 60
IST_OFFSET = timedelta(hours=5, minutes=30)
ENTRY_OPEN_MINUTE = 9 * 60 + 15
ENTRY_CLOSE_MINUTE = 14 * 60 + 30
FLAT_REMINDER_MINUTE = 15 * 60 + 20
VOLUME_MULTIPLE = 1.2
VOLUME_LOOKBACK = 20


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_history_rows(rows):
    """Normalize broker history rows into sorted bar dicts."""
    bars = []
    for row in rows or []:
        try:
            timestamp = int(float(row.get("timestamp")))
            if timestamp > 10_000_000_000:
                timestamp //= 1000
            bars.append(
                {
                    "ts": timestamp,
                    "o": float(row.get("open")),
                    "h": float(row.get("high")),
                    "low": float(row.get("low")),
                    "c": float(row.get("close")),
                    "v": float(row.get("volume") or 0),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    bars.sort(key=lambda bar: bar["ts"])
    return bars


def closed_bars(bars, now_ts=None):
    """Drop any bar whose 15-minute interval has not finished yet."""
    now_ts = int(now_ts if now_ts is not None else datetime.now(UTC).timestamp())
    return [bar for bar in bars or [] if bar["ts"] + BAR_SECONDS <= now_ts]


def _ist_minute(ts):
    moment = datetime.fromtimestamp(ts, UTC) + IST_OFFSET
    return moment.hour * 60 + moment.minute


def _body_ratio(bar):
    span = bar["h"] - bar["low"]
    if span <= 0:
        return 0.0
    return abs(bar["c"] - bar["o"]) / span


def _close_position(bar):
    span = bar["h"] - bar["low"]
    if span <= 0:
        return 0.5
    return (bar["c"] - bar["low"]) / span


def _confirm(side, state, bar, previous_bars):
    span = bar["h"] - bar["low"]
    body_ratio = _body_ratio(bar)
    close_position = _close_position(bar)
    upper_wick = max(0.0, bar["h"] - max(bar["o"], bar["c"]))
    lower_wick = max(0.0, min(bar["o"], bar["c"]) - bar["low"])
    if state == "rejection":
        candle_ok = span > 0 and upper_wick / span >= 0.5
        candle_text = "Upper wick is at least half the candle range."
    elif state == "bounce":
        candle_ok = body_ratio >= 0.5 and bar["c"] > bar["o"]
        candle_text = "Bounce candle closes above its open with a solid body."
    elif side == "CE":
        candle_ok = body_ratio >= 0.6 and close_position >= 0.66
        candle_text = "CE candle closes in the top third with a body of at least 60%."
    elif side == "PE":
        candle_ok = body_ratio >= 0.6 and close_position <= 0.34
        candle_text = "PE candle closes in the bottom third with a body of at least 60%."
    else:
        candle_ok = False
        candle_text = "No directional candle to confirm."

    volumes = [item["v"] for item in previous_bars[-VOLUME_LOOKBACK:] if item["v"] > 0]
    if bar["v"] > 0 and volumes:
        average = sum(volumes) / len(volumes)
        volume_ok = average > 0 and bar["v"] >= average * VOLUME_MULTIPLE
        volume_text = (
            f"Volume is {bar['v']:,.0f} against a {average:,.0f} average."
            if volume_ok
            else f"Volume is below {VOLUME_MULTIPLE:.1f}x the 20-bar average."
        )
    else:
        volume_ok = None
        volume_text = "Volume unavailable; the candle and level confirmations still apply."

    return {
        "candle_ok": bool(candle_ok),
        "candle_text": candle_text,
        "body_ratio": round(body_ratio, 3),
        "close_position": round(close_position, 3),
        "upper_wick_ratio": round(upper_wick / span, 3) if span > 0 else 0.0,
        "lower_wick_ratio": round(lower_wick / span, 3) if span > 0 else 0.0,
        "volume_ok": volume_ok,
        "volume_text": volume_text,
        "confirmed": bool(candle_ok and volume_ok is not False),
    }


def _levels_above(levels, level):
    return sorted(
        item["strike"] for item in (levels or {}).get("resistances", []) if item["strike"] > level
    )


def _levels_below(levels, level):
    return sorted(
        (item["strike"] for item in (levels or {}).get("supports", []) if item["strike"] < level),
        reverse=True,
    )


def _trade(levels, state, side, bar):
    support = levels.get("primary_support")
    resistance = levels.get("primary_resistance")
    if side not in ("CE", "PE") or support is None or resistance is None:
        return None

    entry_proxy = bar["c"]
    target_1 = None
    target_2 = levels.get("major_resistance" if side == "CE" else "major_support")

    if state == "breakout":
        stop = resistance
        above = _levels_above(levels, resistance)
        target_1 = above[0] if above else target_2
    elif state == "bounce":
        stop = support
        target_1 = resistance
    elif state == "breakdown":
        stop = support
        below = _levels_below(levels, support)
        target_1 = below[0] if below else target_2
    elif state == "rejection":
        stop = resistance
        below = _levels_below(levels, resistance)
        target_1 = below[0] if below else support
        target_2 = levels.get("major_support")
    else:
        return None

    if target_1 is None:
        span = abs(resistance - support)
        target_1 = entry_proxy + span if side == "CE" else entry_proxy - span
    if side == "CE" and target_1 <= entry_proxy:
        return {
            "tradeable": False,
            "reason": "The first target is not above the current price.",
            "side": side,
            "stop_level": stop,
            "target_1": target_1,
            "target_2": target_2,
            "rr": None,
            "symbol": levels.get("atm_ce_symbol"),
            "lotsize": levels.get("atm_lotsize"),
        }
    if side == "PE" and target_1 >= entry_proxy:
        return {
            "tradeable": False,
            "reason": "The first target is not below the current price.",
            "side": side,
            "stop_level": stop,
            "target_1": target_1,
            "target_2": target_2,
            "rr": None,
            "symbol": levels.get("atm_pe_symbol"),
            "lotsize": levels.get("atm_lotsize"),
        }

    risk = abs(entry_proxy - stop)
    reward = abs(target_1 - entry_proxy)
    rr = (reward / risk) if risk > 0 else None
    tradeable = rr is not None and rr >= 1.0
    reason = None
    if rr is None:
        reason = "The stop is at the current price, so the risk cannot be measured."
    elif rr < 1.0:
        reason = "The first target is closer than the stop; the model requires at least 1:1."

    return {
        "tradeable": tradeable,
        "reason": reason,
        "side": side,
        "symbol": levels.get("atm_ce_symbol" if side == "CE" else "atm_pe_symbol"),
        "lotsize": levels.get("atm_lotsize"),
        "stop_level": stop,
        "target_1": target_1,
        "target_2": target_2,
        "rr": round(rr, 2) if rr is not None else None,
    }


def evaluate_signal(levels, bars, now_ts=None):
    """Evaluate the last two closed bars against the level map."""
    now_ts = int(now_ts if now_ts is not None else datetime.now(UTC).timestamp())
    closed = closed_bars(bars, now_ts)
    support = levels.get("primary_support")
    resistance = levels.get("primary_resistance")
    base = {
        "state": "warming_up",
        "side": None,
        "confirmed": False,
        "confirmation": None,
        "trade": None,
        "bar": closed[-1] if closed else None,
        "entry_window_open": ENTRY_OPEN_MINUTE <= _ist_minute(now_ts) <= ENTRY_CLOSE_MINUTE,
        "flat_reminder": _ist_minute(now_ts) >= FLAT_REMINDER_MINUTE,
    }
    if support is None or resistance is None:
        return {**base, "state": "no_levels", "message": "Support or resistance is missing."}
    if len(closed) < 2:
        return {
            **base,
            "message": "Waiting for two closed 15-minute candles.",
        }

    bar = closed[-1]
    previous = closed[-2]
    close = bar["c"]
    previous_close = previous["c"]

    if previous_close <= resistance < close:
        state, side = "breakout", "CE"
    elif previous_close >= support > close:
        state, side = "breakdown", "PE"
    elif bar["h"] >= resistance and close < resistance:
        state, side = "rejection", "PE"
    elif bar["low"] <= support and close > support and close > bar["o"]:
        state, side = "bounce", "CE"
    elif support < close < resistance:
        state, side = "inside_zone", None
    elif close >= resistance:
        state, side = "above_resistance", None
    else:
        state, side = "below_support", None

    confirmation = _confirm(side, state, bar, closed[:-1])
    trade = _trade(levels, state, side, bar)
    return {
        **base,
        "state": state,
        "side": side,
        "confirmed": confirmation["confirmed"] if side else False,
        "confirmation": confirmation,
        "trade": trade,
        "bar": {**bar, "prev_close": previous_close},
        "message": None,
    }
