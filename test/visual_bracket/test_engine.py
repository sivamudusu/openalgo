"""Unit tests for the visual bracket engine's state machine and exit guard.

The broker, the database and the tick feed are faked. The rules under test are
the ones that would otherwise need a live position to observe: one exit claim,
a refused exit leaving the bracket active, fill activation and target/stop
direction.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.visual_bracket import engine as engine_module  # noqa: E402


def _row(**overrides):
    row = {
        "id": 1,
        "client_token": "token-1",
        "mode": "live",
        "strategy": "VisualBracket",
        "status": "active",
        "underlying_symbol": "NIFTY",
        "underlying_exchange": "NSE_INDEX",
        "fo_exchange": "NFO",
        "contract_symbol": "NIFTY24DEC24500CE",
        "contract_exchange": "NFO",
        "option_type": "CE",
        "strike": 24500.0,
        "expiry": "24DEC26",
        "lot_size": 65,
        "tick_size": 0.05,
        "freeze_qty": 1800,
        "action": "BUY",
        "product": "MIS",
        "quantity": 65,
        "entry_order_type": "MARKET",
        "entry_limit_price": None,
        "entry_order_id": "E1",
        "entry_fill_price": 142.5,
        "execution_mode": "SYNTHETIC_UNDERLYING_TRIGGER",
        "bias": "bullish",
        "click_price": 24500.0,
        "entry_reference": 24500.0,
        "target_price": 24620.0,
        "stop_price": 24440.0,
        "target_offset": 120.0,
        "stop_offset": 60.0,
        "risk_reward": 2.0,
        "exit_kind": None,
        "exit_order_id": None,
        "exit_reason": None,
        "error_message": None,
        "filled_at": None,
        "closed_at": None,
        "last_reconciled_at": None,
        "created_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "updated_at": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def engine(monkeypatch):
    eng = engine_module.VisualBracketEngine()
    eng._init_once()
    eng._started = True
    eng._recovered = True
    monkeypatch.setattr(eng, "_background_api_key", lambda: "test-key")
    monkeypatch.setattr(eng, "_emit", lambda _row: None)
    monkeypatch.setattr(eng, "_subscribe", lambda _row: None)
    monkeypatch.setattr(eng, "_unsubscribe", lambda _row: None)
    yield eng
    eng._stop_event.set()


def test_entry_order_data_uses_the_bracket_strategy(engine):
    assert engine._entry_order_data(_row(strategy="Manual"))["strategy"] == "Manual"
    assert engine._entry_order_data(_row(strategy=None))["strategy"] == "VisualBracket"


def test_price_on_target_requests_a_target_exit(engine, monkeypatch):
    engine._remember(_row())
    calls = []
    monkeypatch.setattr(engine, "_request_exit", lambda bid, kind: calls.append((bid, kind)))

    engine.on_price("NIFTY", "NSE_INDEX", 24621.0)

    assert calls == [(1, "target")]


def test_price_on_stop_requests_a_stop_exit(engine, monkeypatch):
    engine._remember(_row())
    calls = []
    monkeypatch.setattr(engine, "_request_exit", lambda bid, kind: calls.append((bid, kind)))

    engine.on_price("NIFTY", "NSE_INDEX", 24439.0)

    assert calls == [(1, "sl")]


def test_price_between_levels_does_nothing(engine, monkeypatch):
    engine._remember(_row())
    calls = []
    monkeypatch.setattr(engine, "_request_exit", lambda bid, kind: calls.append((bid, kind)))

    engine.on_price("NIFTY", "NSE_INDEX", 24550.0)

    assert calls == []


def test_a_bearish_bracket_triggers_on_the_opposite_side(engine, monkeypatch):
    engine._remember(
        _row(bias="bearish", entry_reference=24500.0, target_price=24380.0, stop_price=24560.0)
    )
    calls = []
    monkeypatch.setattr(engine, "_request_exit", lambda bid, kind: calls.append((bid, kind)))

    engine.on_price("NIFTY", "NSE_INDEX", 24379.0)
    engine.on_price("NIFTY", "NSE_INDEX", 24561.0)

    assert calls == [(1, "target"), (1, "sl")]


def test_a_filled_entry_activates_and_watches(engine, monkeypatch):
    row = _row(status="working", entry_fill_price=None, filled_at=None)
    activated = []
    monkeypatch.setattr(engine_module.store, "find_by_order_id", lambda _oid: row)
    monkeypatch.setattr(
        engine_module.store,
        "update_bracket",
        lambda bid, **fields: {**row, **fields, "status": "active"},
    )
    monkeypatch.setattr(engine_module.store, "record_event", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_remember", lambda updated: activated.append(updated["id"]))

    engine.on_order_update(
        SimpleNamespace(
            orderid="E1",
            order_status="complete",
            filled_quantity=65,
            average_price=142.5,
            rejection_reason="",
        )
    )

    assert activated == [1]


def test_a_rejected_entry_cancels_and_forgets(engine, monkeypatch):
    row = _row(status="working")
    forgotten = []
    monkeypatch.setattr(engine_module.store, "find_by_order_id", lambda _oid: row)
    monkeypatch.setattr(
        engine_module.store,
        "update_bracket",
        lambda bid, **fields: {**row, **fields},
    )
    monkeypatch.setattr(engine_module.store, "record_event", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_open_position", lambda row, key: 0)
    monkeypatch.setattr(engine, "_forget", lambda bid: forgotten.append(bid))

    engine.on_order_update(
        SimpleNamespace(
            orderid="E1",
            order_status="rejected",
            filled_quantity=0,
            average_price=0.0,
            rejection_reason="RMS blocked",
        )
    )

    assert forgotten == [1]


def test_a_refused_exit_releases_the_claim_and_keeps_the_bracket_active(engine, monkeypatch):
    exiting = _row(status="exiting", exit_kind="target", exit_reason="target")
    released = []
    monkeypatch.setattr(engine_module.store, "claim_exit", lambda *a, **k: True)
    monkeypatch.setattr(engine_module.store, "get_bracket", lambda _bid: exiting)
    monkeypatch.setattr(engine_module.dispatch, "net_position", lambda *a, **k: 65)
    monkeypatch.setattr(
        engine_module.dispatch,
        "reducing_exit",
        lambda *a, **k: (
            False,
            {"status": "error", "message": "Broker refused the exit"},
            400,
            [],
        ),
    )
    monkeypatch.setattr(
        engine_module.store, "release_exit", lambda bid, kind: released.append((bid, kind))
    )
    monkeypatch.setattr(engine_module.store, "update_bracket", lambda *a, **k: exiting)
    monkeypatch.setattr(engine_module.store, "record_event", lambda *a, **k: None)

    engine._exit_worker(1, "target")

    assert released == [(1, "target")]


def test_a_flat_position_closes_without_sending_an_order(engine, monkeypatch):
    exiting = _row(status="exiting", exit_kind="target", exit_reason="target")
    closed = []
    monkeypatch.setattr(engine_module.store, "claim_exit", lambda *a, **k: True)
    monkeypatch.setattr(engine_module.store, "get_bracket", lambda _bid: exiting)
    monkeypatch.setattr(engine_module.dispatch, "net_position", lambda *a, **k: 0)
    monkeypatch.setattr(engine, "_close", lambda row, reason, detail="": closed.append(reason))

    engine._exit_worker(1, "target")

    assert closed == ["target"]


def test_an_exit_order_that_is_still_working_is_not_retried(engine, monkeypatch):
    row = _row(status="exiting", exit_kind="sl", exit_reason="sl", exit_order_id="X1")
    retried = []
    monkeypatch.setattr(engine_module.store, "list_brackets", lambda **k: [row])
    monkeypatch.setattr(engine_module.dispatch, "net_position", lambda *a, **k: 65)
    monkeypatch.setattr(
        engine_module.dispatch,
        "order_status",
        lambda *a, **k: {"order_status": "open", "filled_quantity": 0},
    )
    monkeypatch.setattr(engine, "_request_retry_claimed", lambda bid: retried.append(bid))

    engine._reconcile_once()

    assert retried == []


def test_an_exit_order_that_was_rejected_is_released(engine, monkeypatch):
    row = _row(status="exiting", exit_kind="sl", exit_reason="sl", exit_order_id="X1")
    failed = []
    monkeypatch.setattr(engine_module.store, "list_brackets", lambda **k: [row])
    monkeypatch.setattr(engine_module.dispatch, "net_position", lambda *a, **k: 65)
    monkeypatch.setattr(
        engine_module.dispatch,
        "order_status",
        lambda *a, **k: {"order_status": "rejected", "rejection_reason": "RMS"},
    )
    monkeypatch.setattr(
        engine,
        "_exit_failed",
        lambda failed_row, message: failed.append((failed_row["id"], message)),
    )

    engine._reconcile_once()

    assert failed == [(1, "RMS")]


def test_a_claimed_exit_is_retried_without_claiming_again(engine, monkeypatch):
    exiting = _row(status="exiting", exit_kind="target", exit_reason="target")
    claimed = []
    monkeypatch.setattr(engine_module.store, "get_bracket", lambda _bid: exiting)
    monkeypatch.setattr(
        engine_module.store, "claim_exit", lambda *a, **k: claimed.append(a) or False
    )
    monkeypatch.setattr(engine_module.dispatch, "net_position", lambda *a, **k: 65)
    monkeypatch.setattr(
        engine_module.dispatch,
        "reducing_exit",
        lambda *a, **k: (True, {"status": "success"}, 200, ["X1"]),
    )
    monkeypatch.setattr(engine_module.store, "update_bracket", lambda *a, **k: exiting)
    monkeypatch.setattr(engine_module.store, "record_event", lambda *a, **k: None)

    engine._exit_worker(1, "target")

    assert claimed == []
