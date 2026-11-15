"""Confirmation comes from the candle's own geometry, not from an oscillator.

A rejection is an upper wick that took back the high. A breakout is a body
that closed where it broke. An oscillator says where price sits inside a
band, which is a statement about the past, and it reads the same on a strong
continuation as on a fading one.
"""

from services.signal_confirmation import (
    body_ratio,
    close_position,
    evaluate_confirmation,
    wick_ratios,
)


def _bar(open_, high, low, close, v=0):
    return {"o": open_, "h": high, "l": low, "c": close, "v": v}


def test_a_rejection_needs_an_upper_wick_of_half_the_range():
    bar = _bar(100, 110, 99, 101)  # range 11, upper wick 9

    result = evaluate_confirmation("rejection", "PE", bar)

    assert result["candle_ok"] is True
    assert result["upper_wick_ratio"] > 0.5


def test_a_rejection_without_the_wick_does_not_confirm():
    bar = _bar(100, 110, 99, 109)  # closed near the high

    result = evaluate_confirmation("rejection", "PE", bar)

    assert result["candle_ok"] is False


def test_a_call_needs_a_body_and_a_close_in_the_top_third():
    strong = _bar(100, 105, 99, 104.5)
    weak = _bar(100, 105, 99, 101)

    assert evaluate_confirmation("breakout_up", "CE", strong)["candle_ok"] is True
    assert evaluate_confirmation("breakout_up", "CE", weak)["candle_ok"] is False


def test_a_put_needs_a_body_and_a_close_in_the_bottom_third():
    strong = _bar(100, 101, 95, 95.5)
    weak = _bar(100, 104, 95, 103)

    assert evaluate_confirmation("breakdown", "PE", strong)["candle_ok"] is True
    assert evaluate_confirmation("breakdown", "PE", weak)["candle_ok"] is False


def test_a_bounce_needs_a_body_and_a_close_above_the_open():
    strong = _bar(99, 102, 98, 101)
    weak = _bar(101, 102, 98, 99)

    assert evaluate_confirmation("support_bounce", "CE", strong)["candle_ok"] is True
    assert evaluate_confirmation("support_bounce", "CE", weak)["candle_ok"] is False


def test_volume_must_reach_one_and_a_quarter_of_the_average():
    previous = [_bar(0, 1, 0, 0, v=100) for _ in range(20)]
    strong = evaluate_confirmation("breakout_up", "CE", _bar(100, 105, 99, 104.5, v=150), previous)
    weak = evaluate_confirmation("breakout_up", "CE", _bar(100, 105, 99, 104.5, v=110), previous)

    assert strong["volume_ok"] is True
    assert weak["volume_ok"] is False


def test_volume_that_is_unavailable_does_not_fail_the_candle():
    result = evaluate_confirmation("breakout_up", "CE", _bar(100, 105, 99, 104.5))

    assert result["volume_ok"] is None
    assert result["confirmed"] is True


def test_a_setup_with_no_direction_is_not_confirmed():
    result = evaluate_confirmation("inside_zone", None, _bar(100, 105, 99, 104.5))

    assert result["candle_ok"] is False
    assert result["confirmed"] is False


def test_the_measurements_are_reported():
    result = evaluate_confirmation("breakout_up", "CE", _bar(100, 110, 100, 109))

    assert result["body_ratio"] == 0.9
    assert result["close_position"] == 0.9
    assert 0 < result["upper_wick_ratio"] < 1


def test_a_doji_has_no_body():
    assert body_ratio(_bar(100, 100, 100, 100)) == 0.0
    assert close_position(_bar(100, 100, 100, 100)) == 0.5
    assert wick_ratios(_bar(100, 100, 100, 100)) == (0.0, 0.0)
