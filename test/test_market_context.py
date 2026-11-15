"""What the market can do today, and how a move would behave.

The expected move answers whether a wall is worth waiting for. The dealer
gamma position answers whether it will hold.
"""

from services.market_context import (
    build_market_context,
    expected_move,
    gamma_flip,
    net_gex_by_strike,
    within_reach,
    years_to_expiry,
)


def _row(strike, ce_oi, pe_oi, ce_gamma=0.0, pe_gamma=0.0, lotsize=1):
    return {
        "strike": strike,
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "ce_gamma": ce_gamma,
        "pe_gamma": pe_gamma,
        "lotsize": lotsize,
    }


def test_years_to_expiry_never_goes_negative():
    assert years_to_expiry(1000, 2000) == 0.0
    assert years_to_expiry(None, 2000) == 0.0
    assert years_to_expiry(2000, 1000) > 0


def test_the_expected_move_is_a_band_around_the_underlying():
    band = expected_move(25100, 0.14, 0.05)

    assert band["atm_iv"] == 14.0
    assert band["intraday"]["one_sigma_low"] < 25100 < band["intraday"]["one_sigma_high"]
    # The session band is narrower than the band to expiry.
    assert band["intraday"]["sigma_move"] < band["to_expiry"]["sigma_move"]


def test_there_is_no_expected_move_without_an_implied_volatility():
    assert expected_move(25100, None, 0.05) is None
    assert expected_move(25100, 0, 0.05) is None


def test_a_level_inside_the_band_is_in_reach_and_one_outside_is_not():
    band = expected_move(25100, 0.14, 0.01)["intraday"]

    assert within_reach(25150, 25100, band) is True
    assert within_reach(27000, 25100, band) is False
    assert within_reach(None, 25100, band) is None
    assert within_reach(25150, 25100, None) is None


def test_net_gamma_exposure_counts_calls_positive_and_puts_negative():
    rows = net_gex_by_strike(
        [
            _row(25000, 100, 200, ce_gamma=0.01, pe_gamma=0.02),
        ]
    )

    assert rows[0]["ce_gex"] == 1.0
    assert rows[0]["pe_gex"] == 4.0
    assert rows[0]["net_gex"] == -3.0


def test_the_ladder_is_returned_lowest_strike_first():
    rows = net_gex_by_strike([_row(25200, 1, 1), _row(25000, 1, 1)])

    assert [row["strike"] for row in rows] == [25000, 25200]


def test_the_flip_is_where_the_running_total_changes_sign():
    # The running total is -2, then exactly zero, then +4. A total of exactly
    # zero carries no sign yet, so the strike after it decides.
    rows = net_gex_by_strike(
        [
            _row(25000, 0, 100, pe_gamma=0.02),
            _row(25100, 100, 0, ce_gamma=0.02),
            _row(25200, 200, 0, ce_gamma=0.02),
        ]
    )
    flip, regime = gamma_flip(rows)

    assert flip == 25200
    assert regime == "dampening"


def test_the_context_carries_the_move_the_gamma_and_the_reach():
    rows = [
        _row(25000, 0, 100, pe_gamma=0.02),
        _row(25100, 100, 0, ce_gamma=0.02),
        _row(25200, 200, 0, ce_gamma=0.02),
    ]
    context = build_market_context(25100, rows, atm_iv=0.14, t_years=0.01)

    assert context["atm_iv"] == 14.0
    assert context["expected_move"]["intraday"]["one_sigma_low"] < 25100
    assert context["expected_move"]["intraday"]["one_sigma_high"] > 25100
    assert context["gamma_flip_strike"] == 25200
    assert context["dealer_regime"] == "dampening"
    assert context["total_net_gex"] != 0
