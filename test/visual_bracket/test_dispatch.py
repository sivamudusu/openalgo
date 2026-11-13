"""Unit tests for visual bracket dispatch chunking and the resolver."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.visual_bracket import dispatch, resolver  # noqa: E402


class TestExitChunks:
    def test_quantity_must_be_whole_lots(self):
        assert dispatch.exit_chunks(100, 65, 1800) == []

    def test_a_small_position_is_one_chunk(self):
        assert dispatch.exit_chunks(130, 65, 1800) == [130]

    def test_a_large_position_is_split_at_the_freeze_limit(self):
        assert dispatch.exit_chunks(3510, 65, 1800) == [1755, 1755]

    def test_the_remainder_is_kept_whole_lot(self):
        assert dispatch.exit_chunks(1950, 65, 1800) == [1755, 195]

    def test_unknown_freeze_does_not_split(self):
        assert dispatch.exit_chunks(3900, 65, 0) == [3900]

    def test_zero_quantity_is_empty(self):
        assert dispatch.exit_chunks(0, 65, 1800) == []


class TestResolverHelpers:
    def test_expiry_candidates_cover_iso_and_symbol_shapes(self):
        candidates = resolver._expiry_candidates("2026-12-24")
        assert "24DEC26" in candidates
        assert "24DEC2026" in candidates

    def test_expiry_candidates_cover_shorthand_year(self):
        candidates = resolver._expiry_candidates("24-DEC-26")
        assert "24DEC26" in candidates
        assert "24DEC2026" in candidates

    def test_unknown_strike_selection_is_refused(self):
        with pytest.raises(resolver.VisualBracketError):
            resolver._normalise_selection("DEEP_OTM")

    def test_selection_normalises_separators(self):
        assert resolver._normalise_selection("itm_1") == "ITM1"


def _resolved_payload(**overrides):
    payload = {
        "underlying": "NIFTY",
        "index_exchange": "NSE_INDEX",
        "action": "BUY",
        "option_type": "CE",
        "click_price": 24512.0,
        "entry_reference": 24512.0,
        "target_price": 24560.0,
        "stop_price": 24480.0,
        "strike_selection": "ATM",
        "expiry": "24DEC26",
        "lots": 1,
        "product": "MIS",
        "client_token": "token-1",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def resolved(monkeypatch):
    monkeypatch.setattr(resolver, "_index_tick_size", lambda *_: 0.05)
    monkeypatch.setattr(
        resolver,
        "get_available_strikes",
        lambda *_: [24400.0, 24450.0, 24500.0, 24550.0, 24600.0],
    )
    monkeypatch.setattr(
        resolver,
        "find_option_in_database",
        lambda symbol, exchange: {
            "symbol": symbol,
            "exchange": exchange,
            "lotsize": 65,
            "tick_size": 0.05,
        },
    )
    monkeypatch.setattr("database.qty_freeze_db.get_freeze_qty_for_option", lambda *_: 1800)
    return resolver


def test_resolve_contract_picks_the_atm_strike_from_the_click(resolved):
    result = resolved.resolve_contract(_resolved_payload(), "key")

    assert result["contract_symbol"] == "NIFTY24DEC2624500CE"
    assert result["strike"] == 24500.0
    assert result["quantity"] == 65
    assert result["lots"] == 1
    assert result["bias"] == "bullish"
    assert result["risk_reward"] == pytest.approx(48.0 / 32.0)


def test_resolve_contract_accepts_a_custom_listed_strike(resolved):
    result = resolved.resolve_contract(
        _resolved_payload(strike_selection="CUSTOM", strike=24550.0), "key"
    )
    assert result["strike"] == 24550.0


def test_resolve_contract_refuses_an_unlisted_custom_strike(resolved):
    with pytest.raises(resolver.VisualBracketError):
        resolved.resolve_contract(
            _resolved_payload(strike_selection="CUSTOM", strike=24999.0), "key"
        )


def test_resolve_contract_refuses_levels_on_the_wrong_side(resolved):
    with pytest.raises(resolver.VisualBracketError):
        resolved.resolve_contract(
            _resolved_payload(target_price=24500.0, stop_price=24480.0), "key"
        )


def test_resolve_contract_refuses_a_fractional_lot(resolved):
    with pytest.raises(resolver.VisualBracketError):
        resolved.resolve_contract(_resolved_payload(lots=None, quantity=100), "key")


def test_resolve_contract_refuses_an_unsupported_underlying(resolved):
    with pytest.raises(resolver.VisualBracketError):
        resolved.resolve_contract(_resolved_payload(underlying="RELIANCE"), "key")


def test_resolve_contract_bearish_sell_ce(resolved):
    result = resolved.resolve_contract(
        _resolved_payload(
            action="SELL",
            option_type="CE",
            target_price=24400.0,
            stop_price=24560.0,
        ),
        "key",
    )
    assert result["bias"] == "bearish"
    assert result["action"] == "SELL"


def test_reducing_exit_tags_chunks_with_the_bracket_strategy(monkeypatch):
    sent = []

    def fake_place_exit(order_data, api_key, mode):
        sent.append(order_data)
        return True, {"orderid": "X1"}, 200

    monkeypatch.setattr(dispatch, "place_exit", fake_place_exit)

    ok, _response, _code, order_ids = dispatch.reducing_exit(
        "NIFTY24DEC24500CE",
        "NFO",
        "MIS",
        "SELL",
        65,
        "key",
        "live",
        lot_size=65,
        freeze_qty=1800,
        strategy="Manual",
    )

    assert ok is True
    assert order_ids == ["X1"]
    assert sent[0]["strategy"] == "Manual"


def test_reducing_exit_defaults_to_the_visual_strategy(monkeypatch):
    sent = []
    monkeypatch.setattr(
        dispatch,
        "place_exit",
        lambda order_data, api_key, mode: sent.append(order_data) or (True, {"orderid": "X1"}, 200),
    )

    dispatch.reducing_exit(
        "NIFTY24DEC24500CE",
        "NFO",
        "MIS",
        "SELL",
        65,
        "key",
        "live",
        lot_size=65,
    )

    assert sent[0]["strategy"] == "VisualBracket"
