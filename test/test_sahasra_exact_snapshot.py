"""Manual capture becomes an EOD snapshot only after the close."""

from datetime import datetime

from services import sahasra_exact_snapshot_service as snapshot
from services.sahasra_exact_snapshot_service import IST


def test_manual_capture_before_close_is_not_eod(monkeypatch):
    monkeypatch.setattr(snapshot, "ist_now", lambda: datetime(2026, 10, 23, 15, 44, tzinfo=IST))
    monkeypatch.setattr(snapshot, "_is_trading_day", lambda trade_date: True)

    assert snapshot.manual_capture_source() == "manual"


def test_manual_capture_after_close_on_a_trading_day_is_eod(monkeypatch):
    monkeypatch.setattr(snapshot, "ist_now", lambda: datetime(2026, 10, 23, 15, 45, tzinfo=IST))
    monkeypatch.setattr(snapshot, "_is_trading_day", lambda trade_date: True)

    assert snapshot.manual_capture_source() == "eod"


def test_manual_capture_on_a_closed_day_is_not_eod(monkeypatch):
    monkeypatch.setattr(snapshot, "ist_now", lambda: datetime(2026, 10, 25, 16, 0, tzinfo=IST))
    monkeypatch.setattr(snapshot, "_is_trading_day", lambda trade_date: False)

    assert snapshot.manual_capture_source() == "manual"


def test_daily_capture_uses_the_supplied_source(monkeypatch):
    from datetime import date

    calls = []

    monkeypatch.setattr(snapshot, "_is_trading_day", lambda trade_date: True)
    monkeypatch.setattr(
        snapshot.exact_db, "has_complete_snapshot", lambda underlying, trade_date: False
    )
    monkeypatch.setattr(snapshot, "_expiries", lambda api_key, underlying: ["30OCT26"])

    def fake_capture(underlying, expiry, trade_date=None, api_key=None, source=None):
        calls.append((underlying, expiry, source))
        return {"status": "success", "underlying": underlying, "expiry": expiry, "source": source}

    monkeypatch.setattr(snapshot, "capture_snapshot", fake_capture)

    result = snapshot.capture_daily_snapshots(
        trade_date=date(2026, 10, 23), api_key="test-key", source="manual"
    )

    assert result["status"] == "success"
    assert calls == [
        ("BANKNIFTY", "30OCT26", "manual"),
        ("NIFTY", "30OCT26", "manual"),
    ]
