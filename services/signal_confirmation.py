"""Confirmation for a closed fifteen-minute candle.

Pure: no database, no broker, no clock, no logging. The caller passes the
closed candle and the candles before it; the engine decides whether the
candle confirms the setup, and says why.

The candle's own geometry carries the confirmation, not an oscillator. A
rejection is an upper wick that took back the high; a breakout is a body that
closed where it broke. An oscillator says where price sits inside a band, which
is a statement about the past, and it says the same thing on a strong
continuation as on a fading one.

Every rule here reads a closed candle. The forming candle is discarded by the
caller before it gets here, so nothing is ever judged on a number that can
still change.
"""

from typing import Any

#: A directional candle needs a body of at least this share of its range.
BODY_RATIO = 0.6

#: A bounce needs a body of at least this share of its range.
BOUNCE_BODY_RATIO = 0.5

#: A rejection needs an upper wick of at least this share of its range.
WICK_RATIO = 0.5

#: A directional candle closes in the top or bottom third of its range.
CLOSE_POSITION = 0.34

#: Volume must be at least this multiple of the twenty-bar average.
VOLUME_MULTIPLE = 1.2

#: How many bars the volume average is taken over.
VOLUME_LOOKBACK = 20


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def body_ratio(bar: dict[str, Any]) -> float:
    """The body's share of the candle's range."""
    span = _num(bar.get("h")) - _num(bar.get("l"))
    if span <= 0:
        return 0.0
    return abs(_num(bar.get("c")) - _num(bar.get("o"))) / span


def close_position(bar: dict[str, Any]) -> float:
    """Where the close sits in the candle's range, zero at the low."""
    span = _num(bar.get("h")) - _num(bar.get("l"))
    if span <= 0:
        return 0.5
    return (_num(bar.get("c")) - _num(bar.get("l"))) / span


def wick_ratios(bar: dict[str, Any]) -> tuple[float, float]:
    """The upper and lower wicks as a share of the candle's range."""
    span = _num(bar.get("h")) - _num(bar.get("l"))
    if span <= 0:
        return 0.0, 0.0
    upper = max(0.0, _num(bar.get("h")) - max(_num(bar.get("o")), _num(bar.get("c"))))
    lower = max(0.0, min(_num(bar.get("o")), _num(bar.get("c"))) - _num(bar.get("l")))
    return upper / span, lower / span


def evaluate_confirmation(
    state: str,
    side: str | None,
    bar: dict[str, Any],
    previous_bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Whether the closed candle confirms the setup, and why.

    Args:
        state: The setup the state machine read.
        side: The leg the setup points at, ``"CE"`` or ``"PE"``.
        bar: The closed candle.
        previous_bars: The candles before it, oldest first.

    Returns:
        The verdict, the rule that decided it, and the measurements behind it.
    """
    span = _num(bar.get("h")) - _num(bar.get("l"))
    body = body_ratio(bar)
    position = close_position(bar)
    upper_wick, lower_wick = wick_ratios(bar)

    if state == "rejection":
        candle_ok = span > 0 and upper_wick >= WICK_RATIO
        candle_text = "The upper wick took back half the candle's range."
    elif state == "support_bounce":
        candle_ok = body >= BOUNCE_BODY_RATIO and _num(bar.get("c")) > _num(bar.get("o"))
        candle_text = "The candle closed above its open with a solid body."
    elif side == "CE":
        candle_ok = body >= BODY_RATIO and position >= 1 - CLOSE_POSITION
        candle_text = "The candle closed in the top third with a body of at least 60%."
    elif side == "PE":
        candle_ok = body >= BODY_RATIO and position <= CLOSE_POSITION
        candle_text = "The candle closed in the bottom third with a body of at least 60%."
    else:
        candle_ok = False
        candle_text = "There is no directional candle to confirm."

    volumes = [
        _num(item.get("v"))
        for item in (previous_bars or [])[-VOLUME_LOOKBACK:]
        if _num(item.get("v")) > 0
    ]
    bar_volume = _num(bar.get("v"))
    if bar_volume > 0 and volumes:
        average = sum(volumes) / len(volumes)
        volume_ok = average > 0 and bar_volume >= average * VOLUME_MULTIPLE
        volume_text = (
            f"Volume is {bar_volume:,.0f} against a {average:,.0f} average."
            if volume_ok
            else f"Volume is below {VOLUME_MULTIPLE:.1f} times the {VOLUME_LOOKBACK}-bar average."
        )
    else:
        volume_ok = None
        volume_text = "Volume unavailable; the candle confirmation still applies."

    return {
        "candle_ok": bool(candle_ok),
        "candle_text": candle_text,
        "body_ratio": round(body, 3),
        "close_position": round(position, 3),
        "upper_wick_ratio": round(upper_wick, 3),
        "lower_wick_ratio": round(lower_wick, 3),
        "volume_ok": volume_ok,
        "volume_text": volume_text,
        "confirmed": bool(candle_ok and volume_ok is not False),
    }
