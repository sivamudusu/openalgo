"""The signal log is how the model's live record is read.

A rule that has never been measured is a belief. The platform's own standard
is that a model is judged on fifty or more live trades rather than on one
session's backtest, and that needs a durable record of what was signalled and
what became of it.
"""

import importlib

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh signal log on its own SQLite file, per test.

    The module is reloaded so its engine binds to this file, and the session
    is released at the end. Sharing the platform's own database would make the
    test depend on whatever else happened to run before it.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'signals.db'}")
    import database.sahasra_signal_db as module

    module = importlib.reload(module)
    module.init_db()
    yield module
    module.db_session.remove()


def test_a_signal_is_recorded_with_the_map_that_produced_it(db):
    signal_id = db.record_signal(
        "NIFTY",
        "rejection",
        "PE",
        confirmed=True,
        level_map={"support": 25000, "resistance": 25200, "magnet": 25100},
        market_context={"gamma_flip_strike": 25200, "dealer_regime": "dampening"},
        entry_spot=25100,
        entry_premium=112.8,
        quantity=75,
    )

    assert signal_id is not None
    row = db.db_session.query(db.SahasraSignal).filter(db.SahasraSignal.id == signal_id).first()
    assert row.state == "rejection"
    assert row.side == "PE"
    assert row.confirmed is True
    assert row.level_map["magnet"] == 25100
    assert row.market_context["dealer_regime"] == "dampening"
    assert row.entry_spot == 25100
    assert row.quantity == 75
    db.db_session.remove()


def test_a_signal_can_be_closed_with_its_outcome(db):
    signal_id = db.record_signal("NIFTY", "breakout_up", "CE", entry_spot=25100)
    assert signal_id is not None

    kept = db.close_signal(signal_id, db.OUTCOME_TARGET, pnl_pct=12.5)

    assert kept is True
    row = db.db_session.query(db.SahasraSignal).filter(db.SahasraSignal.id == signal_id).first()
    assert row.outcome == db.OUTCOME_TARGET
    assert row.pnl_pct == 12.5
    assert row.outcome_at is not None
    db.db_session.remove()


def test_closing_a_signal_that_is_not_there_reports_it(db):
    assert db.close_signal(999999, db.OUTCOME_STOP) is False


def test_the_evidence_counts_signals_and_outcomes(db):
    for _ in range(3):
        signal_id = db.record_signal("NIFTY", "breakout_up", "CE")
        assert signal_id is not None
    signal_id = db.record_signal("NIFTY", "breakout_up", "CE")
    db.close_signal(signal_id, db.OUTCOME_TARGET, pnl_pct=8.0)

    evidence = db.signal_evidence("NIFTY")

    assert evidence["signals"] == 4
    assert evidence["closed"] == 1
    assert evidence["targets"] == 1
    assert evidence["hit_rate"] == 1.0
    assert evidence["enough_evidence"] is False


def test_the_evidence_only_claims_a_verdict_once_there_are_enough_signals(db):
    for _ in range(db.EVIDENCE_TARGET):
        signal_id = db.record_signal("NIFTY", "breakout_up", "CE")
        db.close_signal(signal_id, db.OUTCOME_TARGET, pnl_pct=5.0)

    evidence = db.signal_evidence("NIFTY")

    assert evidence["closed"] == db.EVIDENCE_TARGET
    assert evidence["enough_evidence"] is True


def test_the_evidence_of_one_underlying_does_not_include_another(db):
    db.record_signal("BANKNIFTY", "breakout_up", "CE")
    db.record_signal("NIFTY", "breakout_up", "CE")

    assert db.signal_evidence("NIFTY")["signals"] == 1
    assert db.signal_evidence("BANKNIFTY")["signals"] == 1


def test_recent_signals_come_back_newest_first(db):
    first = db.record_signal("NIFTY", "breakout_up", "CE")
    second = db.record_signal("NIFTY", "rejection", "PE")

    rows = db.recent_signals("NIFTY")

    assert [row["id"] for row in rows][:2] == [second, first]
