"""The canonical level map is the answer every surface gives.

The Sahasra panel and the chart overlay used to disagree: the panel ranked
candidate strikes by proximity to the at-the-money strike before it ranked them
by open interest, so a large wall lost to a small bump near the money. On
NIFTY, whose strikes are spaced 50 points apart, the bump was not even a
neighbour of the wall, so it passed the local-maximum test as well.

These tests pin the rules that fixed it. They need no broker, no database and
no running platform.
"""

import pytest

from services.level_map_service import (
    build_level_map,
    classify_regime,
    compute_pcr,
    normalise_rows,
)


def _row(strike, ce_oi, pe_oi):
    return {"strike": strike, "ce_oi": ce_oi, "pe_oi": pe_oi}


def _nifty_ladder():
    """The ladder behind the reported disagreement.

    NIFTY strikes are spaced 50 points apart. The put wall sits at 25000 with
    26.78L, the magnet at 25100 with a near-equal pair, and the call wall at
    25200 with 26.78L.
    """
    return [
        _row(24800, 8_000_000, 9_000_000),
        _row(24850, 3_000_000, 4_000_000),
        _row(24900, 6_000_000, 12_000_000),
        _row(24950, 3_000_000, 5_000_000),
        _row(25000, 5_000_000, 26_780_000),
        _row(25050, 3_000_000, 2_000_000),
        _row(25100, 16_620_000, 13_820_000),
        _row(25150, 3_000_000, 2_000_000),
        _row(25200, 26_780_000, 7_000_000),
        _row(25250, 4_000_000, 3_000_000),
        _row(25300, 9_000_000, 5_000_000),
    ]


def test_the_wall_wins_over_the_bump_near_the_money():
    """The reported case: the panel named the magnet as support."""
    level_map = build_level_map(25100, _nifty_ladder(), underlying="NIFTY")

    assert level_map["support"] == 25000
    assert level_map["resistance"] == 25200


def test_the_magnet_is_the_balanced_strike():
    level_map = build_level_map(25100, _nifty_ladder(), underlying="NIFTY")

    assert level_map["magnet"] == 25100
    assert level_map["magnet_balance"] == pytest.approx(0.092, abs=0.001)


def test_a_wing_strike_with_negligible_balanced_interest_is_not_the_magnet():
    """24800 holds 8L against 9L: balanced, but nowhere near the money."""
    level_map = build_level_map(25100, _nifty_ladder(), underlying="NIFTY")

    assert level_map["magnet"] != 24800


def test_the_no_trade_zone_spans_the_two_walls():
    level_map = build_level_map(25100, _nifty_ladder(), underlying="NIFTY")

    assert level_map["no_trade_zone"] == {"low": 25000, "high": 25200}


def test_the_magnet_sits_inside_the_no_trade_zone():
    level_map = build_level_map(25100, _nifty_ladder(), underlying="NIFTY")

    zone = level_map["no_trade_zone"]
    assert zone["low"] < level_map["magnet"] < zone["high"]


def test_a_wide_strike_spacing_gives_the_same_answer():
    """BANKNIFTY spaces strikes 100 points apart.

    On that ladder 25000 and 25100 are neighbours, so the magnet fails the
    local-maximum test and the old code happened to land on the wall. The
    answer must not depend on the spacing.
    """
    ladder = [
        _row(24800, 8_000_000, 9_000_000),
        _row(24900, 6_000_000, 12_000_000),
        _row(25000, 5_000_000, 26_780_000),
        _row(25100, 16_620_000, 13_820_000),
        _row(25200, 26_780_000, 7_000_000),
        _row(25300, 9_000_000, 5_000_000),
    ]
    level_map = build_level_map(25100, ladder, underlying="BANKNIFTY")

    assert level_map["support"] == 25000
    assert level_map["resistance"] == 25200


def test_support_and_resistance_never_collide_on_the_atm_strike():
    """A strike may not be both the support and the resistance."""
    ladder = [
        _row(25000, 5_000_000, 26_780_000),
        _row(25050, 3_000_000, 2_000_000),
        _row(25100, 26_780_000, 26_780_000),
        _row(25150, 3_000_000, 2_000_000),
        _row(25200, 26_780_000, 7_000_000),
    ]
    level_map = build_level_map(25100, ladder)

    assert level_map["support"] != level_map["resistance"]
    assert level_map["support"] <= 25100
    assert level_map["resistance"] > 25100


def test_the_next_wall_is_the_nearest_qualifying_one():
    ladder = [
        _row(24900, 6_000_000, 20_000_000),
        _row(25000, 5_000_000, 26_780_000),
        _row(25100, 16_620_000, 13_820_000),
        _row(25200, 26_780_000, 7_000_000),
        _row(25300, 24_000_000, 7_000_000),
        _row(25400, 9_000_000, 5_000_000),
    ]
    level_map = build_level_map(25100, ladder)

    assert level_map["support"] == 25000
    assert level_map["next_support"] == 24900
    assert level_map["resistance"] == 25200
    assert level_map["next_resistance"] == 25300


def test_a_wall_below_the_money_is_not_a_resistance():
    """Deep in-the-money calls carry the largest open interest.

    The old rule took the largest call open interest anywhere in the window,
    so it could name a resistance below the underlying, where nothing has to
    clear it.
    """
    ladder = [
        _row(24800, 900_000_000, 10_000_000),
        _row(25000, 270_000_000, 813_000_000),
        _row(25100, 446_000_000, 730_000_000),
        _row(25200, 84_000_000, 10_500_000),
    ]
    level_map = build_level_map(25100, ladder)

    assert level_map["resistance"] == 25200
    assert level_map["support"] == 25000


def test_a_wall_at_the_money_is_support_and_not_resistance():
    """A strike at the money cannot be both."""
    ladder = [
        _row(25000, 270_000_000, 813_000_000),
        _row(25100, 446_000_000, 730_000_000),
        _row(25200, 84_000_000, 10_500_000),
    ]
    level_map = build_level_map(25100, ladder)

    assert level_map["support"] != level_map["resistance"]
    assert level_map["support"] <= 25100
    assert level_map["resistance"] > 25100


def test_a_minor_wall_does_not_become_the_next_wall():
    """A wall holding a small share of the side's interest is not a level."""
    ladder = [
        _row(24900, 6_000_000, 2_000_000),
        _row(25000, 5_000_000, 26_780_000),
        _row(25100, 16_620_000, 13_820_000),
        _row(25200, 26_780_000, 7_000_000),
        _row(25300, 9_000_000, 5_000_000),
    ]
    level_map = build_level_map(25100, ladder)

    assert level_map["next_support"] is None


def test_a_strike_where_call_interest_dominates_is_not_support():
    """A strike is a wall only when the side in question dominates it."""
    ladder = [
        _row(25000, 30_000_000, 4_000_000),
        _row(25050, 3_000_000, 2_000_000),
        _row(25100, 26_780_000, 7_000_000),
    ]
    level_map = build_level_map(25050, ladder)

    assert level_map["support"] is None
    assert level_map["resistance"] == 25100


def test_an_empty_chain_returns_a_readable_map():
    level_map = build_level_map(25100, [], underlying="NIFTY")

    assert level_map["support"] is None
    assert level_map["resistance"] is None
    assert level_map["magnet"] is None
    assert level_map["no_trade_zone"] is None
    assert level_map["regime"] == "unknown"
    assert level_map["warning"]


def test_a_chain_with_no_price_returns_a_readable_map():
    level_map = build_level_map(0, _nifty_ladder(), underlying="NIFTY")

    assert level_map["support"] is None
    assert level_map["spot"] is None


def test_rows_are_sorted_and_the_strike_resolved():
    ladder = list(reversed(_nifty_ladder()))
    level_map = build_level_map(25100, ladder, underlying="NIFTY")

    strikes = [row["strike"] for row in level_map["ladder"]]
    assert strikes == sorted(strikes)
    assert level_map["atm_strike"] == 25100


def test_a_supplied_atm_strike_is_carried_through():
    level_map = build_level_map(25100, _nifty_ladder(), atm_strike=25050)

    assert level_map["atm_strike"] == 25050


def test_pcr_and_regime():
    ladder = [
        _row(25000, 30_000_000, 10_000_000),
        _row(25100, 26_780_000, 7_000_000),
    ]
    level_map = build_level_map(25100, ladder)

    assert level_map["pcr"] == pytest.approx(17_000_000 / 56_780_000, abs=0.001)
    assert level_map["regime"] == "bearish"


def test_compute_pcr_without_call_interest_is_none():
    assert compute_pcr([_row(25000, 0, 10_000_000)]) is None


def test_classify_regime_boundaries():
    assert classify_regime(None) == "unknown"
    assert classify_regime(0.6) == "bearish"
    assert classify_regime(0.8) == "neutral"
    assert classify_regime(1.2) == "bullish"


def test_normalise_rows_drops_strikes_without_a_price():
    rows = normalise_rows([_row(0, 1, 1), _row(25000, 1, 1)])

    assert len(rows) == 1
    assert rows[0]["strike"] == 25000


def test_the_magnet_is_the_strike_whose_weaker_side_is_strongest():
    """The magnet is where neither side can push price through alone.

    At 25000 the puts carry 26.78L and the calls 5M, so the calls are the
    binding side. At 25100 the calls carry 26.78L and the puts 1M. The
    strike whose weaker side is stronger is the one price balances at.
    """
    ladder = [
        _row(25000, 5_000_000, 26_780_000),
        _row(25100, 26_780_000, 1_000_000),
    ]
    level_map = build_level_map(25050, ladder)

    assert level_map["magnet"] == 25000


def test_there_is_no_magnet_without_any_interest_on_both_sides():
    ladder = [_row(25000, 0, 26_780_000), _row(25100, 26_780_000, 0)]
    level_map = build_level_map(25050, ladder)

    assert level_map["magnet"] is None


# The chain a NIFTY trader pasted from a live session, 50-point strikes,
# forward near 25150. The put wall is at 25000 (81.3L), the biggest call wall
# at 25500 (106L), and 25100 is the magnet: both sides are loaded there, so
# price balances instead of travelling.
#
# The strike at 25150 is left out. The paste was copied from a table that was
# updating live, and that row's call and put sides came from different
# moments, so it fails the put-call parity that every other row satisfies.
# A fixture has to be data that can be trusted.
REAL_NIFTY_CHAIN = [
    {"strike": 24650, "ce_oi": 79_000, "pe_oi": 213_000_000},
    {"strike": 24700, "ce_oi": 48_500_000, "pe_oi": 621_000_000},
    {"strike": 24750, "ce_oi": 15_900_000, "pe_oi": 220_000_000},
    {"strike": 24800, "ce_oi": 68_900_000, "pe_oi": 643_000_000},
    {"strike": 24850, "ce_oi": 14_200_000, "pe_oi": 276_000_000},
    {"strike": 24900, "ce_oi": 69_200_000, "pe_oi": 450_000_000},
    {"strike": 24950, "ce_oi": 22_300_000, "pe_oi": 240_000_000},
    {"strike": 25000, "ce_oi": 270_000_000, "pe_oi": 813_000_000},
    {"strike": 25050, "ce_oi": 110_000_000, "pe_oi": 271_000_000},
    {"strike": 25100, "ce_oi": 446_000_000, "pe_oi": 730_000_000},
    {"strike": 25250, "ce_oi": 422_000_000, "pe_oi": 105_000_000},
    {"strike": 25300, "ce_oi": 779_000_000, "pe_oi": 219_000_000},
    {"strike": 25350, "ce_oi": 495_000_000, "pe_oi": 99_200_000},
    {"strike": 25400, "ce_oi": 856_000_000, "pe_oi": 129_000_000},
    {"strike": 25450, "ce_oi": 319_000_000, "pe_oi": 20_600_000},
    {"strike": 25500, "ce_oi": 1_060_000_000, "pe_oi": 151_000_000},
    {"strike": 25550, "ce_oi": 365_000_000, "pe_oi": 67_600},
    {"strike": 25600, "ce_oi": 536_000_000, "pe_oi": 19_500_000},
    {"strike": 25650, "ce_oi": 233_000_000, "pe_oi": 76_900},
]


def test_the_real_chain_names_the_put_wall_as_support():
    """The reported case: the panel named the magnet as support.

    The wall at 25000 carries 81.3L of put open interest and the strike at
    25100 carries 73L. The old rule ranked candidates by proximity to the
    money first, so the nearer and smaller 25100 won.
    """
    level_map = build_level_map(25100, REAL_NIFTY_CHAIN, underlying="NIFTY")

    assert level_map["support"] == 25000
    assert level_map["resistance"] == 25500


def test_the_real_chain_names_the_magnet_at_25100():
    """Both sides are loaded at 25100, so that is where price balances."""
    level_map = build_level_map(25100, REAL_NIFTY_CHAIN, underlying="NIFTY")

    assert level_map["magnet"] == 25100


def test_the_real_chain_keeps_the_magnet_out_of_the_no_trade_zone_edge():
    """The magnet is inside the zone, not one of its walls."""
    level_map = build_level_map(25100, REAL_NIFTY_CHAIN, underlying="NIFTY")

    zone = level_map["no_trade_zone"]
    assert zone["low"] == 25000
    assert zone["high"] == 25500
    assert zone["low"] < level_map["magnet"] < zone["high"]
