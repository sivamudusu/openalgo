"""What the market can do today, and how a move would behave.

Pure: no database, no broker, no clock, no logging. Every input arrives as an
argument and every decision leaves as a return value, so the read can be
exercised without a running platform.

Three things come out of one option chain:

- **The expected move.** The at-the-money implied volatility and the time
  left give a standard-deviation sized range for the session. A wall outside
  that range is not a level to wait for; it is a level the market has no
  intention of reaching today.

- **The dealer gamma position.** The net gamma exposure of the open
  interest, and the strike where it changes sign. Above that strike the
  dealers hedge by selling into a rally, which damps a move and makes a wall
  hold. Below it they hedge by buying a rally, which amplifies a move and
  makes a wall give way. That is the read on whether a level in front of
  price will hold or break.

- **The implied volatility.** What a long premium position is paying for
  the right to be right, and how much of that is at risk of being taken back
  if the move does not happen.

The sign convention for gamma is stated rather than assumed: a call's open
interest counts positive and a put's negative, which is the reading in which
the dealers are the other side of the customer.
"""

import math
from typing import Any

#: Calendar days per year, the annualisation factor the expected-move
#: convention uses.
DAYS_PER_YEAR = 365.0

#: The intraday horizon for the expected move: one calendar day.
INTRADAY_T_YEARS = 1.0 / DAYS_PER_YEAR

#: A level is out of reach for the session when it sits beyond this many
#: standard deviations of the expected move.
REACH_SIGMAS = 1.0


def years_to_expiry(expiry_ts: Any, now_ts: Any) -> float:
    """Time left to expiry in years, never negative."""
    try:
        remaining = float(expiry_ts) - float(now_ts)
    except (TypeError, ValueError):
        return 0.0
    if remaining <= 0:
        return 0.0
    return remaining / (DAYS_PER_YEAR * 24 * 60 * 60)


def expected_move(spot: float, atm_iv: float | None, t_years: float) -> dict[str, float] | None:
    """The standard-deviation sized ranges around the underlying.

    Args:
        spot: The underlying price.
        atm_iv: At-the-money implied volatility, as a decimal.
        t_years: Time to expiry in years. The intraday band uses one calendar
            day, capped by the real time left.

    Returns:
        The intraday and expiry bands, or None without an implied volatility.
    """
    if not spot or spot <= 0 or not atm_iv or atm_iv <= 0:
        return None

    intraday_years = min(t_years, INTRADAY_T_YEARS) if t_years > 0 else INTRADAY_T_YEARS
    expiry_years = max(t_years, 0.0)

    def band(years: float) -> dict[str, float]:
        sigma = spot * atm_iv * math.sqrt(years) if years > 0 else 0.0
        return {
            "sigma_move": round(sigma, 2),
            "one_sigma_low": round(spot - sigma, 2),
            "one_sigma_high": round(spot + sigma, 2),
            "two_sigma_low": round(spot - 2 * sigma, 2),
            "two_sigma_high": round(spot + 2 * sigma, 2),
        }

    return {
        "atm_iv": round(atm_iv * 100, 2),
        "intraday": band(intraday_years),
        "to_expiry": band(expiry_years),
    }


def net_gex_by_strike(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Net gamma exposure per strike, lowest strike first.

    Each row carries the legs' open interest, gamma and lot size. A call's
    exposure counts positive and a put's negative.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        strike = row.get("strike")
        if strike is None:
            continue
        lotsize = row.get("lotsize") or 1
        ce_gex = (row.get("ce_gamma") or 0) * (row.get("ce_oi") or 0) * lotsize
        pe_gex = (row.get("pe_gamma") or 0) * (row.get("pe_oi") or 0) * lotsize
        out.append(
            {
                "strike": float(strike),
                "ce_gex": round(ce_gex, 2),
                "pe_gex": round(pe_gex, 2),
                "net_gex": round(ce_gex - pe_gex, 2),
            }
        )
    out.sort(key=lambda item: item["strike"])
    return out


def gamma_flip(gex_rows: list[dict[str, Any]]) -> tuple[float | None, str]:
    """The strike where the cumulative net gamma exposure changes sign.

    Walking the ladder from the lowest strike, the running total of net gamma
    exposure is the dealers' position at that price. Above the strike where it
    crosses zero the dealers are net long gamma, which damps a move: they sell
    into a rally and buy a dip, so a wall holds and a breakout tends to fail.
    Below it they are net short gamma, which amplifies a move, so a wall gives
    way and a breakout tends to run.

    Returns:
        The strike where the running total crosses zero, and the regime above
        it: ``"dampening"`` or ``"amplifying"``. The strike is None when the
        total never crosses zero inside the window.
    """
    running = 0.0
    previous_sign = 0
    for row in gex_rows:
        running += row.get("net_gex", 0) or 0
        sign = (running > 0) - (running < 0)
        if sign == 0:
            # A running total of exactly zero carries no sign yet, so the
            # strike after it decides which way the exposure runs.
            continue
        if previous_sign != 0 and sign != previous_sign:
            return row["strike"], "dampening" if sign > 0 else "amplifying"
        previous_sign = sign
    return None, "dampening" if running >= 0 else "amplifying"


def within_reach(level: float | None, spot: float, band: dict[str, float] | None) -> bool | None:
    """Whether a level sits inside the expected move.

    Args:
        level: The level to measure, or None when there is no level.
        spot: The underlying price.
        band: One band from :func:`expected_move`, the intraday or the
            to-expiry entry.

    Returns:
        True when the level is inside the band, False when it is beyond it,
        and None when there is nothing to measure against.
    """
    if level is None or not band:
        return None
    return band["one_sigma_low"] <= level <= band["one_sigma_high"]


def build_market_context(
    spot: float,
    rows: list[dict[str, Any]],
    *,
    atm_iv: float | None = None,
    t_years: float = 0.0,
) -> dict[str, Any]:
    """Assemble the read on what the market can do and how a move behaves.

    Args:
        spot: The underlying price.
        rows: One row per strike, carrying ``strike``, ``ce_oi``, ``pe_oi``,
            ``ce_gamma``, ``pe_gamma`` and ``lotsize``.
        atm_iv: At-the-money implied volatility, as a decimal.
        t_years: Time to expiry in years.

    Returns:
        The expected move, the dealer gamma position and the flip strike.
    """
    bands = expected_move(spot, atm_iv, t_years)
    gex_rows = net_gex_by_strike(rows)
    flip_strike, regime = gamma_flip(gex_rows)
    total_net_gex = round(sum(row["net_gex"] for row in gex_rows), 2)

    return {
        "expected_move": bands,
        "atm_iv": bands["atm_iv"] if bands else None,
        "gamma_flip_strike": flip_strike,
        "dealer_regime": regime,
        "total_net_gex": total_net_gex,
        "gex_by_strike": gex_rows,
    }
