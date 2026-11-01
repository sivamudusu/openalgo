"""Persistence contract for the Sahasra exact snapshot and context tables."""

from datetime import UTC, date, datetime

from database import sahasra_exact_db as db


def _header(spot=54925.7, underlying="BANKNIFTY", trade_date=date(2099, 1, 1), source="eod"):
    return {
        "underlying": underlying,
        "trade_date": trade_date,
        "expiry": "30OCT26",
        "spot": spot,
        "atm_strike": 54900.0,
        "pcr": 0.973,
        "total_call_oi": 1000,
        "total_put_oi": 973,
        "coverage": "window_50",
        "strikes_captured": 2,
        "prev_day_high": 55250.0,
        "prev_day_low": 54100.0,
        "prev_day_close": 54300.0,
        "levels_json": "{}",
        "source": source,
        "status": "complete",
        "captured_at": datetime(2099, 1, 1, 10, 15, tzinfo=UTC).replace(tzinfo=None),
    }


def _chain(put_oi=781440):
    return [
        {
            "strike": 54000.0,
            "ce_oi": 326500,
            "pe_oi": put_oi,
            "ce_symbol": "BANKNIFTY30OCT2654000CE",
            "pe_symbol": "BANKNIFTY30OCT2654000PE",
            "lotsize": 30,
        },
        {
            "strike": 55000.0,
            "ce_oi": 784100,
            "pe_oi": 212310,
            "ce_symbol": "BANKNIFTY30OCT2655000CE",
            "pe_symbol": "BANKNIFTY30OCT2655000PE",
            "lotsize": 30,
        },
    ]


def test_init_is_idempotent():
    db.init_db()
    db.init_db()


def test_snapshot_write_is_idempotent():
    db.init_db()
    first_id = db.save_snapshot(_header(), _chain())
    second_id = db.save_snapshot(_header(spot=54950.0), _chain(put_oi=800000))

    assert first_id == second_id
    saved = db.get_snapshot("BANKNIFTY", date(2099, 1, 1), "30OCT26")
    assert saved["spot"] == 54950.0
    assert saved["strikes_captured"] == 2
    assert len(saved["chain"]) == 2
    assert saved["chain"][0]["pe_oi"] == 800000


def test_a_manual_snapshot_is_not_treated_as_eod():
    db.init_db()
    trade_date = date(2099, 2, 1)
    db.save_snapshot(_header(underlying="NIFTY", trade_date=trade_date, source="manual"), _chain())

    assert db.has_complete_snapshot("NIFTY", trade_date) is False
    assert db.get_latest_snapshot_before("NIFTY", date(2099, 2, 2), "30OCT26") is None


def test_latest_snapshot_is_strictly_before_the_session():
    db.init_db()
    db.save_snapshot(_header(), _chain())

    assert db.get_latest_snapshot_before("BANKNIFTY", date(2099, 1, 2), "30OCT26") is not None
    assert db.get_latest_snapshot_before("BANKNIFTY", date(2099, 1, 1), "30OCT26") is None


def test_context_round_trip():
    db.init_db()
    db.save_context(
        date(2099, 1, 1),
        {"auto": [{"key": "US30", "status": "available"}], "manual": {"fed": "hawkish"}},
        "bearish",
        "high",
        "Yields up",
    )

    saved = db.get_context(date(2099, 1, 1))

    assert saved["final_bias"] == "bearish"
    assert saved["confidence"] == "high"
    assert saved["cues"]["manual"]["fed"] == "hawkish"
    assert saved["notes"] == "Yields up"


def test_a_manual_snapshot_is_not_used_as_eod():
    db.init_db()
    header = _header()
    header["underlying"] = "NIFTY"
    header["trade_date"] = date(2099, 2, 1)
    header["source"] = "manual"
    db.save_snapshot(header, _chain())

    assert db.has_complete_snapshot("NIFTY", date(2099, 2, 1)) is False
    assert db.get_latest_snapshot_before("NIFTY", date(2099, 2, 2), "30OCT26") is None

    header["source"] = "eod"
    db.save_snapshot(header, _chain())

    assert db.has_complete_snapshot("NIFTY", date(2099, 2, 1)) is True
    assert db.get_latest_snapshot_before("NIFTY", date(2099, 2, 2), "30OCT26") is not None
