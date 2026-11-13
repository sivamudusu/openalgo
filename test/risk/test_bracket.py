"""Tests for the pure visual-bracket rules in services/risk/bracket.py."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.risk.bracket import (  # noqa: E402
    Bias,
    bias_for,
    bias_side,
    clamp_level,
    risk_reward,
    stop_from_offset,
    target_from_offset,
    validate_levels,
)
from services.risk.models import Side  # noqa: E402


class TestBias:
    @pytest.mark.parametrize(
        ("action", "option_type", "expected"),
        [
            ("BUY", "CE", Bias.BULLISH),
            ("BUY", "PE", Bias.BEARISH),
            ("SELL", "PE", Bias.BULLISH),
            ("SELL", "CE", Bias.BEARISH),
            ("buy", " ce ", Bias.BULLISH),
        ],
    )
    def test_bias_for_known_combinations(self, action, option_type, expected):
        assert bias_for(action, option_type) is expected

    @pytest.mark.parametrize(
        ("action", "option_type"),
        [(None, "CE"), ("BUY", None), ("HOLD", "CE"), ("BUY", "XX")],
    )
    def test_bias_for_unknown_combinations_is_none(self, action, option_type):
        assert bias_for(action, option_type) is None

    def test_bias_side_maps_to_risk_core_side(self):
        assert bias_side(Bias.BULLISH) is Side.BUY
        assert bias_side(Bias.BEARISH) is Side.SELL


class TestValidateLevels:
    def test_bullish_levels_are_valid(self):
        assert validate_levels(Bias.BULLISH, 100.0, 120.0, 90.0, tick_size=0.05) == ()

    def test_bearish_levels_are_valid(self):
        assert validate_levels(Bias.BEARISH, 100.0, 80.0, 110.0, tick_size=0.05) == ()

    def test_bullish_target_below_entry_is_refused(self):
        problems = validate_levels(Bias.BULLISH, 100.0, 95.0, 90.0)
        assert any("above entry" in p for p in problems)

    def test_bullish_stop_above_entry_is_refused(self):
        problems = validate_levels(Bias.BULLISH, 100.0, 120.0, 105.0)
        assert any("below entry" in p for p in problems)

    def test_bearish_target_above_entry_is_refused(self):
        problems = validate_levels(Bias.BEARISH, 100.0, 105.0, 110.0)
        assert any("below entry" in p for p in problems)

    def test_bearish_stop_below_entry_is_refused(self):
        problems = validate_levels(Bias.BEARISH, 100.0, 80.0, 95.0)
        assert any("above entry" in p for p in problems)

    def test_missing_values_are_reported(self):
        problems = validate_levels(Bias.BULLISH, None, None, None)
        assert len(problems) == 3

    def test_zero_is_not_a_price(self):
        problems = validate_levels(Bias.BULLISH, 0, 120.0, 90.0)
        assert any("Entry price" in p for p in problems)

    def test_target_and_stop_must_differ(self):
        problems = validate_levels(Bias.BULLISH, 100.0, 120.0, 120.0)
        assert any("same price" in p for p in problems)


class TestClampLevel:
    def test_bullish_target_cannot_cross_entry(self):
        assert clamp_level(
            Bias.BULLISH, 100.0, 95.0, moving="target", tick_size=0.05
        ) == pytest.approx(100.05)

    def test_bullish_stop_cannot_cross_entry(self):
        assert clamp_level(
            Bias.BULLISH, 100.0, 105.0, moving="stop", tick_size=0.05
        ) == pytest.approx(99.95)

    def test_bearish_target_cannot_cross_entry(self):
        assert clamp_level(
            Bias.BEARISH, 100.0, 105.0, moving="target", tick_size=0.05
        ) == pytest.approx(99.95)

    def test_bearish_stop_cannot_cross_entry(self):
        assert clamp_level(
            Bias.BEARISH, 100.0, 95.0, moving="stop", tick_size=0.05
        ) == pytest.approx(100.05)

    def test_legal_values_pass_through_and_snap(self):
        assert clamp_level(
            Bias.BULLISH, 100.0, 120.03, moving="target", tick_size=0.05
        ) == pytest.approx(120.05)

    def test_unknown_moving_raises(self):
        with pytest.raises(ValueError):
            clamp_level(Bias.BULLISH, 100.0, 120.0, moving="entry")


class TestRiskReward:
    def test_one_to_two(self):
        assert risk_reward(100.0, 120.0, 90.0) == pytest.approx(2.0)

    def test_bearish_ratio(self):
        assert risk_reward(100.0, 80.0, 110.0) == pytest.approx(2.0)

    def test_invalid_levels_return_zero(self):
        assert risk_reward(None, 120.0, 90.0) == 0.0
        assert risk_reward(100.0, 100.0, 100.0) == 0.0


class TestOffsets:
    def test_bullish_offsets(self):
        assert target_from_offset(Bias.BULLISH, 100.0, 20.0) == pytest.approx(120.0)
        assert stop_from_offset(Bias.BULLISH, 100.0, 10.0) == pytest.approx(90.0)

    def test_bearish_offsets(self):
        assert target_from_offset(Bias.BEARISH, 100.0, 20.0) == pytest.approx(80.0)
        assert stop_from_offset(Bias.BEARISH, 100.0, 10.0) == pytest.approx(110.0)

    def test_invalid_offsets_return_none(self):
        assert target_from_offset(Bias.BULLISH, None, 20.0) is None
        assert stop_from_offset(Bias.BULLISH, 5.0, 10.0) is None
