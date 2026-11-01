"""Closed-bar signal rules for the Sahasra exact panel."""

from datetime import UTC, datetime

from services.sahasra_exact_signal_service import evaluate_signal

NOW = int(datetime(2026, 10, 23, 10, 20, tzinfo=UTC).timestamp())


def _levels():
    return {
        "primary_support": 54000.0,
        "primary_resistance": 55000.0,
        "major_support": 54000.0,
        "major_resistance": 56000.0,
        "temporary_support": 54500.0,
        "supports": [{"strike": 54000.0}, {"strike": 54500.0}],
        "resistances": [{"strike": 55000.0}, {"strike": 56000.0}],
        "atm_ce_symbol": "BANKNIFTY30OCT2655000CE",
        "atm_pe_symbol": "BANKNIFTY30OCT2655000PE",
        "atm_lotsize": 30,
    }


def _bars(current, previous_close=54980.0):
    return [
        {
            "ts": NOW - 30 * 60,
            "o": previous_close - 10,
            "h": previous_close + 10,
            "low": previous_close - 20,
            "c": previous_close,
            "v": 1000,
        },
        {"ts": NOW - 15 * 60, **current, "v": current.get("v", 1200)},
    ]


def test_forming_candle_is_ignored():
    bars = _bars({"o": 54990, "h": 55060, "low": 54980, "c": 55050})

    result = evaluate_signal(_levels(), bars, now_ts=NOW - 15 * 60)

    assert result["state"] == "warming_up"


def test_breakout_above_resistance_is_a_ce_trade():
    bars = _bars({"o": 54990, "h": 55060, "low": 54980, "c": 55050})

    result = evaluate_signal(_levels(), bars, now_ts=NOW)

    assert result["state"] == "breakout"
    assert result["side"] == "CE"
    assert result["confirmed"] is True
    assert result["trade"]["symbol"] == "BANKNIFTY30OCT2655000CE"
    assert result["trade"]["stop_level"] == 55000.0
    assert result["trade"]["target_1"] == 56000.0


def test_rejection_at_resistance_is_a_pe_trade():
    bars = _bars({"o": 54980, "h": 55100, "low": 54900, "c": 54940})

    result = evaluate_signal(_levels(), bars, now_ts=NOW)

    assert result["state"] == "rejection"
    assert result["side"] == "PE"
    assert result["confirmed"] is True
    assert result["trade"]["symbol"] == "BANKNIFTY30OCT2655000PE"
    assert result["trade"]["target_1"] == 54500.0


def test_bounce_at_support_is_a_ce_trade():
    bars = _bars({"o": 54020, "h": 54250, "low": 54000, "c": 54200}, previous_close=54050.0)

    result = evaluate_signal(_levels(), bars, now_ts=NOW)

    assert result["state"] == "bounce"
    assert result["side"] == "CE"
    assert result["trade"]["stop_level"] == 54000.0


def test_inside_zone_is_not_a_trade():
    bars = _bars({"o": 54500, "h": 54600, "low": 54450, "c": 54550}, previous_close=54510.0)

    result = evaluate_signal(_levels(), bars, now_ts=NOW)

    assert result["state"] == "inside_zone"
    assert result["side"] is None
    assert result["trade"] is None


def test_target_closer_than_stop_is_refused():
    levels = _levels()
    levels["resistances"] = [{"strike": 55000.0}, {"strike": 55100.0}]
    bars = _bars({"o": 55010, "h": 55090, "low": 55000, "c": 55080}, previous_close=54990.0)

    result = evaluate_signal(levels, bars, now_ts=NOW)

    assert result["state"] == "breakout"
    assert result["trade"]["tradeable"] is False
    assert "closer than the stop" in result["trade"]["reason"]


def test_volume_missing_does_not_hide_a_candle_confirmation():
    bars = _bars({"o": 54990, "h": 55060, "low": 54980, "c": 55050, "v": 0})

    result = evaluate_signal(_levels(), bars, now_ts=NOW)

    assert result["confirmation"]["volume_ok"] is None
    assert result["confirmed"] is True


def test_entry_window_closes_at_1430_and_flat_reminder_starts_at_1520():
    three_pm = int(datetime(2026, 10, 23, 15, 0, tzinfo=UTC).timestamp())
    bars = _bars({"o": 54990, "h": 55060, "low": 54980, "c": 55050})

    result = evaluate_signal(_levels(), bars, now_ts=three_pm)

    assert result["entry_window_open"] is False
    assert result["flat_reminder"] is True
