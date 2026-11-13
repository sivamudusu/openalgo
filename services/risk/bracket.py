"""Pure rules for a visual bracket's target and stop levels.

A visual order is placed on an index chart, but the position is an option
contract. The index level is the trigger; the option is what is actually bought
or sold. These helpers answer the three questions that must be answered the
same way by the browser and the server:

* which way is the trade biased,
* are the target and stop on the legal side of entry, and
* what is the risk-to-reward ratio.

No I/O. No clock. No database. The browser clamps a drag with the same rules the
server enforces on the way in, and ``test/risk/vectors.json`` pins the shared
cases so the two copies cannot drift.
"""

from __future__ import annotations

from enum import StrEnum

from services.risk.models import Side, as_float, as_price

MIN_TICK = 1e-4


class Bias(StrEnum):
    """The direction the index is expected to move."""

    BULLISH = "bullish"
    BEARISH = "bearish"


_BIAS_BY_ACTION: dict[tuple[str, str], Bias] = {
    ("BUY", "CE"): Bias.BULLISH,
    ("BUY", "PE"): Bias.BEARISH,
    ("SELL", "PE"): Bias.BULLISH,
    ("SELL", "CE"): Bias.BEARISH,
}


def bias_for(action: str | None, option_type: str | None) -> Bias | None:
    """Return the index bias for an option action and type, or None.

    Buying a call and selling a put are both bullish; buying a put and selling a
    call are both bearish. Unknown combinations are refused rather than guessed.
    """
    key = (str(action or "").strip().upper(), str(option_type or "").strip().upper())
    return _BIAS_BY_ACTION.get(key)


def bias_side(bias: Bias) -> Side:
    """The side a :class:`services.risk.models.PositionRisk` uses for a bias.

    The shared risk core only knows BUY and SELL. For a visual order those mean
    "bullish index" and "bearish index"; the option's own BUY/SELL is a separate
    field and is never used for the target and stop comparisons.
    """
    return Side.BUY if bias is Bias.BULLISH else Side.SELL


def _gap(tick_size: float | None) -> float:
    tick = as_float(tick_size)
    return tick if tick > 0 else MIN_TICK


def _round_to_tick(price: float, tick_size: float | None) -> float:
    tick = as_float(tick_size)
    if tick <= 0:
        return price
    return round(price / tick) * tick


def validate_levels(
    bias: Bias,
    entry: float | None,
    target: float | None,
    stop: float | None,
    tick_size: float | None = None,
) -> tuple[str, ...]:
    """Return plain-text reasons the levels are unusable, empty when fine.

    The messages are written for a trader and are safe to show directly.
    """
    problems: list[str] = []
    entry_price = as_price(entry)
    target_price = as_price(target)
    stop_price = as_price(stop)

    if entry_price is None:
        problems.append("Entry price is missing or not a positive number.")
    if target_price is None:
        problems.append("Target price is missing or not a positive number.")
    if stop_price is None:
        problems.append("Stop price is missing or not a positive number.")
    if problems:
        return tuple(problems)

    gap = _gap(tick_size)
    if abs(target_price - stop_price) < gap:
        problems.append("Target and stop cannot be the same price.")

    if bias is Bias.BULLISH:
        if target_price <= entry_price + gap:
            problems.append("Target must be above entry for a bullish trade.")
        if stop_price >= entry_price - gap:
            problems.append("Stop must be below entry for a bullish trade.")
    else:
        if target_price >= entry_price - gap:
            problems.append("Target must be below entry for a bearish trade.")
        if stop_price <= entry_price + gap:
            problems.append("Stop must be above entry for a bearish trade.")

    return tuple(problems)


def clamp_level(
    bias: Bias,
    entry: float,
    value: float,
    *,
    moving: str,
    tick_size: float | None = None,
) -> float:
    """Clamp a dragged target or stop so it cannot cross the entry.

    ``moving`` is ``"target"`` or ``"stop"``. A target dragged past entry is
    pinned one tick on the legal side; the same for a stop. The value is
    rounded to the instrument's tick after clamping.
    """
    price = as_price(value)
    if price is None:
        price = as_price(entry) or 0.0
    gap = _gap(tick_size)
    entry_price = as_price(entry) or 0.0

    if moving == "target":
        if bias is Bias.BULLISH:
            price = max(price, entry_price + gap)
        else:
            price = min(price, entry_price - gap)
    elif moving == "stop":
        if bias is Bias.BULLISH:
            price = min(price, entry_price - gap)
        else:
            price = max(price, entry_price + gap)
    else:
        raise ValueError("moving must be 'target' or 'stop'")

    price = _round_to_tick(price, tick_size)
    if price <= 0:
        price = gap
    return price


def risk_reward(entry: float | None, target: float | None, stop: float | None) -> float:
    """Risk-to-reward ratio, or zero when the levels cannot produce one."""
    entry_price = as_price(entry)
    target_price = as_price(target)
    stop_price = as_price(stop)
    if entry_price is None or target_price is None or stop_price is None:
        return 0.0
    risk = abs(entry_price - stop_price)
    reward = abs(target_price - entry_price)
    if risk <= 0:
        return 0.0
    return reward / risk


def target_from_offset(bias: Bias, entry: float | None, points: float | None) -> float | None:
    """Target price ``points`` away from entry in the bias direction."""
    entry_price = as_price(entry)
    distance = as_price(points)
    if entry_price is None or distance is None:
        return None
    price = entry_price + distance if bias is Bias.BULLISH else entry_price - distance
    return price if price > 0 else None


def stop_from_offset(bias: Bias, entry: float | None, points: float | None) -> float | None:
    """Stop price ``points`` away from entry against the bias direction."""
    entry_price = as_price(entry)
    distance = as_price(points)
    if entry_price is None or distance is None:
        return None
    price = entry_price - distance if bias is Bias.BULLISH else entry_price + distance
    return price if price > 0 else None
