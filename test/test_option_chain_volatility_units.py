"""The chain's volatility is a percentage, and the expected move needs a decimal.

An index that moves thirty thousand points in a day is not a market, so a
hundred-fold error in the expected move is not a number anybody would believe.
It is also wrong in the direction that hides the answer: a band so wide that
every level sits inside it makes the reach test always pass.
"""

from services import option_chain_service
from services.market_context import expected_move


def _chain(atm_iv_percent: float, atm_strike: float = 25150.0) -> list[dict]:
    return [
        {
            "strike": atm_strike,
            "ce": {"implied_volatility": atm_iv_percent},
            "pe": {"implied_volatility": atm_iv_percent},
        }
    ]


def test_the_atm_volatility_is_scaled_to_a_decimal():
    """The chain carries a percentage, so it comes back as a decimal."""
    iv = option_chain_service._atm_implied_volatility(_chain(22.6), 25150.0)

    assert iv == 0.226


def test_the_two_atm_legs_are_averaged():
    chain = [
        {
            "strike": 25150.0,
            "ce": {"implied_volatility": 22.0},
            "pe": {"implied_volatility": 24.0},
        }
    ]

    assert option_chain_service._atm_implied_volatility(chain, 25150.0) == 0.23


def test_a_chain_without_volatility_has_none():
    chain = [{"strike": 25150.0, "ce": {}, "pe": {}}]

    assert option_chain_service._atm_implied_volatility(chain, 25150.0) is None


def test_the_expected_move_is_a_plausible_number_of_points():
    """Twenty-three percent implied volatility on a 25,000 index."""
    iv = option_chain_service._atm_implied_volatility(_chain(22.6), 25150.0)
    band = expected_move(25144.3, iv, 1.3311 / 365.0)

    assert band is not None
    sigma = band["intraday"]["sigma_move"]
    assert 100 < sigma < 1000, f"a one-day move of {sigma} points is not plausible"
    assert band["atm_iv"] == 22.6


def test_the_band_is_centred_on_the_underlying():
    iv = option_chain_service._atm_implied_volatility(_chain(22.6), 25150.0)
    band = expected_move(25144.3, iv, 1.3311 / 365.0)

    intraday = band["intraday"]
    assert intraday["one_sigma_high"] > 25144.3 > intraday["one_sigma_low"]
    assert intraday["two_sigma_high"] > intraday["one_sigma_high"]
