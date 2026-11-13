"""The exit claim is the OCO guard, so it is tested against a real SQLite table."""

from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import NullPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database import visual_bracket_db as store  # noqa: E402


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'visual_bracket.db'}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    store.Base.metadata.create_all(bind=engine)
    session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
    monkeypatch.setattr(store, "engine", engine)
    monkeypatch.setattr(store, "db_session", session)
    yield store
    session.remove()
    engine.dispose()


def _row_data():
    return {
        "client_token": "token-1",
        "mode": "live",
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
        "execution_mode": "SYNTHETIC_UNDERLYING_TRIGGER",
        "bias": "bullish",
        "click_price": 24500.0,
        "entry_reference": 24500.0,
        "target_price": 24620.0,
        "stop_price": 24440.0,
        "target_offset": 120.0,
        "stop_offset": 60.0,
        "risk_reward": 2.0,
    }


def test_strategy_defaults_to_visual_bracket(temp_store):
    created = temp_store.create_bracket(_row_data())

    assert created["strategy"] == "VisualBracket"


def test_strategy_is_persisted(temp_store):
    created = temp_store.create_bracket({**_row_data(), "strategy": "Manual"})

    assert created["strategy"] == "Manual"


def test_only_one_exit_can_be_claimed(temp_store):
    created = temp_store.create_bracket(_row_data())
    bracket_id = created["id"]

    assert temp_store.claim_exit(bracket_id, "target") is True
    assert temp_store.claim_exit(bracket_id, "sl") is False

    row = temp_store.get_bracket(bracket_id)
    assert row["status"] == "exiting"
    assert row["exit_kind"] == "target"


def test_a_released_claim_can_be_retried(temp_store):
    created = temp_store.create_bracket(_row_data())
    bracket_id = created["id"]

    assert temp_store.claim_exit(bracket_id, "target") is True
    assert temp_store.release_exit(bracket_id, "target") is True
    assert temp_store.get_bracket(bracket_id)["status"] == "active"
    assert temp_store.claim_exit(bracket_id, "sl") is True


def test_releasing_a_different_kind_is_refused(temp_store):
    created = temp_store.create_bracket(_row_data())
    bracket_id = created["id"]

    assert temp_store.claim_exit(bracket_id, "target") is True
    assert temp_store.release_exit(bracket_id, "sl") is False
    assert temp_store.get_bracket(bracket_id)["exit_kind"] == "target"


def test_a_repeated_client_token_returns_the_same_row(temp_store):
    first = temp_store.create_bracket(_row_data())
    second = temp_store.create_bracket(_row_data())

    assert first["id"] == second["id"]
    assert len(temp_store.list_brackets()) == 1


def test_find_by_order_id_matches_entry_and_exit(temp_store):
    created = temp_store.create_bracket(_row_data())
    bracket_id = created["id"]
    temp_store.update_bracket(bracket_id, entry_order_id="E1", exit_order_id="X1")

    assert temp_store.find_by_order_id("E1")["id"] == bracket_id
    assert temp_store.find_by_order_id("X1")["id"] == bracket_id
    assert temp_store.find_by_order_id("nope") is None
