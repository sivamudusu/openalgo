"""Canonical option-chain level map.

Pure: no database, no broker, no clock, no logging. Every input arrives as an
argument and every decision leaves as a return value, so the map can be
exercised without a running platform and gives the same answer to every
caller.

One resolver serves every surface that draws support and resistance: the
Sahasra panel in the trading sidebar and the option-chain overlay on the chart.
They used to disagree, because each computed its own levels from the same
chain under a different rule. The panel ranked candidate strikes by proximity
to the at-the-money strike before it ranked them by open interest, so a large
wall lost to a small bump near the money. On NIFTY, whose strikes are spaced
50 points apart, the bump was not even a neighbour of the wall, so it passed
the local-maximum test as well. The two surfaces now read one map and cannot
drift apart.

The rules:

- The **headline support** is the largest put open interest at or below the
  underlying. The **headline resistance** is the largest call open interest
  above it. That is the rule the overlay has always drawn.
- A wall is a strike where one side dominates: put open interest at or above
  call open interest below the underlying, and the reverse above it. A strike
  where the two are close is a magnet rather than a wall.
- The **nearest wall** on a side is the first one the price meets on its way
  in, which is not always the headline one: a wall holding half the side's
  largest open interest is worth meeting before the largest wall further out.
- The **next wall** beyond the headline is a target, not a level to trade
  against.
- The **magnet** is the strike whose weaker side carries the most open
  interest, because that is where neither side can push price through
  alone. It is the balance point, and it is where price tends to chop.
- The no-trade zone is the open interval between the two headline walls.
"""

from typing import Any

#: A wall must hold at least this share of its side's largest open interest
#: to count as a level in its own right.
WALL_QUALIFY_FRACTION = 0.5

#: Put-to-call open-interest ratio thresholds. These match the Sahasra panel's
#: existing rule, so the canonical map does not change which trades the panel
#: blocks.
PCR_BEARISH_MAX = 0.7
PCR_BULLISH_MIN = 1.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def normalise_rows(chain: Any) -> list[dict[str, Any]]:
    """Rows sorted by strike, carrying the fields the map reads.

    The chain is accepted flat: one row per strike with ``ce_oi`` and
    ``pe_oi``. Callers holding the option-chain response adapt it first.
    """
    rows: list[dict[str, Any]] = []
    for row in chain or []:
        strike = _num(row.get("strike"))
        if strike <= 0:
            continue
        rows.append(
            {
                "strike": strike,
                "ce_oi": max(0, _int(row.get("ce_oi"))),
                "pe_oi": max(0, _int(row.get("pe_oi"))),
                "ce_volume": max(0, _int(row.get("ce_volume"))),
                "pe_volume": max(0, _int(row.get("pe_volume"))),
                "ce_ltp": _num(row.get("ce_ltp")),
                "pe_ltp": _num(row.get("pe_ltp")),
                "ce_symbol": row.get("ce_symbol"),
                "pe_symbol": row.get("pe_symbol"),
                "lotsize": _int(row.get("lotsize")) or None,
            }
        )
    rows.sort(key=lambda row: row["strike"])
    return rows


def compute_pcr(rows: Any) -> float | None:
    """Window put-to-call open-interest ratio, or None without call interest."""
    total_put = 0
    total_call = 0
    for row in rows or []:
        total_put += max(0, _int(row.get("pe_oi")))
        total_call += max(0, _int(row.get("ce_oi")))
    if total_call <= 0:
        return None
    return total_put / total_call


def classify_regime(
    pcr: float | None,
    bearish_max: float = PCR_BEARISH_MAX,
    bullish_min: float = PCR_BULLISH_MIN,
) -> str:
    """Regime read from the put-to-call ratio."""
    if pcr is None:
        return "unknown"
    if pcr < bearish_max:
        return "bearish"
    if pcr > bullish_min:
        return "bullish"
    return "neutral"


def _dominates(row: dict[str, Any], side: str) -> bool:
    """True when the strike's own open interest is dominated by that side."""
    call_oi = _int(row.get("ce_oi"))
    put_oi = _int(row.get("pe_oi"))
    if side == "support":
        return put_oi > 0 and put_oi >= call_oi
    return call_oi > 0 and call_oi >= put_oi


def _balance(row: dict[str, Any]) -> float:
    """How far the strike is from having equal interest on both sides."""
    call_oi = max(0, _int(row.get("ce_oi")))
    put_oi = max(0, _int(row.get("pe_oi")))

    combined = call_oi + put_oi
    if combined <= 0:
        return 1.0
    return abs(call_oi - put_oi) / combined


def _nearest(rows: list[dict[str, Any]], spot: float) -> dict[str, Any] | None:
    """The row whose strike sits closest to the underlying."""
    if not rows:
        return None
    return min(rows, key=lambda row: abs(row["strike"] - spot))


def _empty_map(
    spot: float,
    underlying: str | None,
    expiry: str | None,
    warning: str,
) -> dict[str, Any]:
    return {
        "underlying": underlying,
        "expiry": expiry,
        "spot": spot or None,
        "atm_strike": None,
        "support": None,
        "resistance": None,
        "nearest_support": None,
        "nearest_resistance": None,
        "next_support": None,
        "next_resistance": None,
        "magnet": None,
        "magnet_balance": None,
        "no_trade_zone": None,
        "pcr": None,
        "regime": "unknown",
        "ladder": [],
        "warning": warning,
    }


def build_level_map(
    spot: Any,
    chain: Any,
    *,
    underlying: str | None = None,
    expiry: str | None = None,
    atm_strike: Any = None,
) -> dict[str, Any]:
    """Build the level map for one underlying from one option chain.

    Args:
        spot: Underlying price.
        chain: One row per strike, flat, with at least ``strike``, ``ce_oi``
            and ``pe_oi``.
        underlying: Underlying name, carried through for display.
        expiry: Expiry label, carried through for display.
        atm_strike: At-the-money strike when the caller already knows it.
            Otherwise the strike nearest the spot is used.

    Returns:
        A dict carrying the headline support and resistance, the nearest and
        next walls on each side, the magnet, the no-trade zone, the
        put-to-call ratio and the regime, plus the ladder it was read from.
    """
    spot = _num(spot)
    rows = normalise_rows(chain)
    if spot <= 0 or not rows:
        return _empty_map(spot, underlying, expiry, "No option-chain strikes were available.")

    atm_row = _nearest(rows, spot)
    resolved_atm = _num(atm_strike) or (atm_row["strike"] if atm_row else None)

    support_pool = [row for row in rows if row["strike"] <= spot and _dominates(row, "support")]
    resistance_pool = [
        row for row in rows if row["strike"] > spot and _dominates(row, "resistance")
    ]

    support_row = (
        max(
            support_pool,
            key=lambda row: (row["pe_oi"], -abs(row["strike"] - spot)),
        )
        if support_pool
        else None
    )
    resistance_row = (
        max(
            resistance_pool,
            key=lambda row: (row["ce_oi"], -abs(row["strike"] - spot)),
        )
        if resistance_pool
        else None
    )

    support_threshold = WALL_QUALIFY_FRACTION * (support_row["pe_oi"] if support_row else 0)
    resistance_threshold = WALL_QUALIFY_FRACTION * (
        resistance_row["ce_oi"] if resistance_row else 0
    )

    def nearest_qualifying(
        pool: list[dict[str, Any]],
        side: str,
        threshold: float,
    ) -> dict[str, Any] | None:
        candidates = [row for row in pool if _side_oi(row, side) >= threshold]
        if not candidates:
            return None
        return min(candidates, key=lambda row: abs(row["strike"] - spot))

    def next_qualifying(
        pool: list[dict[str, Any]],
        side: str,
        threshold: float,
        beyond: float | None,
        direction: str,
    ) -> dict[str, Any] | None:
        if beyond is None:
            return None
        candidates = [
            row
            for row in pool
            if row["strike"] != beyond
            and _side_oi(row, side) >= threshold
            and (row["strike"] < beyond if direction == "below" else row["strike"] > beyond)
        ]
        if not candidates:
            return None
        if direction == "below":
            return max(candidates, key=lambda row: row["strike"])
        return min(candidates, key=lambda row: row["strike"])

    nearest_support = nearest_qualifying(support_pool, "support", support_threshold)
    nearest_resistance = nearest_qualifying(resistance_pool, "resistance", resistance_threshold)
    next_support = next_qualifying(
        support_pool,
        "support",
        support_threshold,
        support_row["strike"] if support_row else None,
        "below",
    )
    next_resistance = next_qualifying(
        resistance_pool,
        "resistance",
        resistance_threshold,
        resistance_row["strike"] if resistance_row else None,
        "above",
    )

    magnet_row, magnet_balance = _magnet(rows)

    pcr = compute_pcr(rows)
    warning = None
    if len(rows) < 20:
        warning = "The chain is narrow; a wall may sit outside the window."
    if support_row is None or resistance_row is None:
        warning = "One side of the chain carries no open interest."

    ladder = [
        {
            "strike": row["strike"],
            "call_oi": row["ce_oi"],
            "put_oi": row["pe_oi"],
            "ce_symbol": row["ce_symbol"],
            "pe_symbol": row["pe_symbol"],
            "lotsize": row["lotsize"],
        }
        for row in rows
    ]

    return {
        "underlying": underlying,
        "expiry": expiry,
        "spot": spot,
        "atm_strike": resolved_atm,
        "support": support_row["strike"] if support_row else None,
        "resistance": resistance_row["strike"] if resistance_row else None,
        "nearest_support": nearest_support["strike"] if nearest_support else None,
        "nearest_resistance": nearest_resistance["strike"] if nearest_resistance else None,
        "next_support": next_support["strike"] if next_support else None,
        "next_resistance": next_resistance["strike"] if next_resistance else None,
        "magnet": magnet_row["strike"] if magnet_row else None,
        "magnet_balance": round(magnet_balance, 4) if magnet_row else None,
        "no_trade_zone": (
            {
                "low": support_row["strike"],
                "high": resistance_row["strike"],
            }
            if support_row and resistance_row and support_row["strike"] < resistance_row["strike"]
            else None
        ),
        "pcr": round(pcr, 3) if pcr is not None else None,
        "regime": classify_regime(pcr),
        "ladder": ladder,
        "warning": warning,
    }


def _side_oi(row: dict[str, Any], side: str) -> int:
    return _int(row.get("pe_oi")) if side == "support" else _int(row.get("ce_oi"))


def _magnet(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    """The strike where the smaller of the two sides carries the most interest.

    A magnet is where both sides of the chain are loaded, so neither can move
    price without meeting the other. That is the strike whose *weaker* side
    holds the most open interest: whichever side is smaller is what caps how
    far price can travel through it.

    Taking the weaker side rather than the difference is deliberate. A strike
    deep in the wings can hold equal but negligible interest on both sides and
    be the most balanced strike in the window without being anywhere price
    would go. Scoring by the weaker side's size discards it.
    """
    if not rows:
        return None, 0.0
    best = max(rows, key=lambda row: min(row["ce_oi"], row["pe_oi"]))
    weaker = min(best["ce_oi"], best["pe_oi"])
    if weaker <= 0:
        return None, 0.0
    balance = _balance(best)
    return best, balance
