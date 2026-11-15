"""The gamma flip strike is the read on whether a wall holds or breaks.

Cumulative net gamma exposure is the dealers' position at a price. Above the
strike where it crosses zero they are net long gamma, which damps a move: a
wall holds and a breakout tends to fail. Below it they are net short gamma,
which amplifies a move: a wall gives way and a breakout tends to run.

The convention is stated rather than assumed. A call's open interest counts
positive and a put's negative, which is the reading in which the dealers are
the other side of the customer.
"""

from services.gex_service import gamma_flip_strike


def _row(strike, net_gex):
    return {"strike": strike, "net_gex": net_gex}


def test_the_service_delegates_to_the_shared_rule():
    """The rule lives in one place, so the service and the panel cannot drift."""
    from services.market_context import gamma_flip

    chain = [
        _row(25000, -400.0),
        _row(25100, 100.0),
        _row(25200, 800.0),
    ]
    assert gamma_flip_strike(chain) == gamma_flip(chain)


def test_the_flip_is_where_the_running_total_crosses_zero():
    # The running total is -400, -700, -600, -100 and then +700, so it crosses
    # zero at 25200 and the regime above it is the dampening one.
    chain = [
        _row(25000, -400.0),
        _row(25050, -300.0),
        _row(25100, 100.0),
        _row(25150, 500.0),
        _row(25200, 800.0),
    ]
    flip, regime = gamma_flip_strike(chain)

    assert flip == 25200
    assert regime == "dampening"


def test_a_ladder_that_never_crosses_zero_has_no_flip():
    chain = [
        _row(25000, -100.0),
        _row(25100, -50.0),
        _row(25200, -10.0),
    ]
    flip, regime = gamma_flip_strike(chain)

    assert flip is None
    assert regime == "amplifying"


def test_a_ladder_that_is_positive_throughout_has_no_flip():
    chain = [_row(25000, 100.0), _row(25100, 50.0)]
    flip, regime = gamma_flip_strike(chain)

    assert flip is None
    assert regime == "dampening"


def test_an_empty_chain_has_no_flip():
    flip, regime = gamma_flip_strike([])

    assert flip is None
    assert regime == "dampening"


def test_a_ladder_crossing_from_positive_to_negative_amplifies():
    chain = [
        _row(25000, 300.0),
        _row(25100, -400.0),
        _row(25200, -50.0),
    ]
    flip, regime = gamma_flip_strike(chain)

    assert flip == 25100
    assert regime == "amplifying"


def test_a_running_total_of_exactly_zero_is_decided_by_the_next_strike():
    chain = [
        _row(25000, -300.0),
        _row(25100, 300.0),
        _row(25200, 400.0),
    ]
    flip, regime = gamma_flip_strike(chain)

    assert flip == 25200
    assert regime == "dampening"
