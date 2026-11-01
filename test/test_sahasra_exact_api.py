"""Route contract for the Sahasra exact panel."""

from datetime import datetime

import pytest
import pytz
from flask import Flask

import blueprints.sahasra_exact as routes


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(routes.sahasra_exact_bp)

    calls = []

    def fake_levels(api_key, underlying):
        calls.append(("levels", api_key, underlying))
        return {"underlying": underlying, "source": "eod_snapshot"}

    def fake_signal(api_key, underlying):
        calls.append(("signal", api_key, underlying))
        return {"underlying": underlying, "state": "inside_zone", "side": None}

    def fake_context(trade_date, api_key):
        calls.append(("context_get", trade_date.isoformat(), api_key))
        return {"trade_date": trade_date.isoformat(), "final_bias": "unset"}

    def fake_save(trade_date, api_key, payload):
        calls.append(("context_save", trade_date.isoformat(), payload["final_bias"]))
        return {"trade_date": trade_date.isoformat(), "final_bias": payload["final_bias"]}

    def fake_capture(underlying, api_key=None):
        calls.append(("capture", underlying))
        return {"status": "success", "underlying": underlying}

    def fake_status():
        return {"snapshots": [], "scheduler": {}, "underlyings": ["BANKNIFTY", "NIFTY"]}

    monkeypatch.setattr(routes, "_api_key", lambda: "test-key")
    monkeypatch.setattr(routes, "get_levels", fake_levels)
    monkeypatch.setattr(routes, "get_signal", fake_signal)
    monkeypatch.setattr(routes, "get_context_view", fake_context)
    monkeypatch.setattr(routes, "save_context_view", fake_save)
    monkeypatch.setattr(routes, "capture_underlying_snapshots", fake_capture)
    monkeypatch.setattr(routes, "get_snapshot_status", fake_status)

    test_client = app.test_client()
    with test_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
    return test_client, calls


def test_levels_rejects_an_unknown_underlying(client):
    test_client, calls = client

    response = test_client.get("/sahasra-exact/api/levels?underlying=GOLD")

    assert response.status_code == 400
    assert calls == []


def test_levels_serves_the_requested_index(client):
    test_client, calls = client

    response = test_client.get("/sahasra-exact/api/levels?underlying=nifty")

    assert response.status_code == 200
    assert response.get_json()["data"]["underlying"] == "NIFTY"
    assert calls == [("levels", "test-key", "NIFTY")]


def test_signal_serves_the_requested_index(client):
    test_client, calls = client

    response = test_client.get("/sahasra-exact/api/signal?underlying=BANKNIFTY")

    assert response.status_code == 200
    assert response.get_json()["data"]["state"] == "inside_zone"
    assert calls == [("signal", "test-key", "BANKNIFTY")]


def test_context_get_defaults_to_today(client):
    test_client, calls = client

    response = test_client.get("/sahasra-exact/api/context")

    assert response.status_code == 200
    assert calls[0][0] == "context_get"
    assert calls[0][1] == datetime.now(pytz.timezone("Asia/Kolkata")).date().isoformat()


def test_context_post_rejects_an_invalid_date(client):
    test_client, calls = client

    response = test_client.post(
        "/sahasra-exact/api/context",
        json={"trade_date": "not-a-date", "final_bias": "bearish"},
    )

    assert response.status_code == 400
    assert calls == []


def test_context_post_saves_the_bias(client):
    test_client, calls = client

    response = test_client.post(
        "/sahasra-exact/api/context",
        json={
            "trade_date": "2026-10-23",
            "final_bias": "bearish",
            "confidence": "high",
            "manual": {"fed": "hawkish"},
            "notes": "yields up",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["final_bias"] == "bearish"
    assert calls == [("context_save", "2026-10-23", "bearish")]


def test_capture_accepts_one_index(client):
    test_client, calls = client

    response = test_client.post("/sahasra-exact/api/snapshot", json={"underlying": "NIFTY"})

    assert response.status_code == 200
    assert calls == [("capture", "NIFTY")]


def test_status_is_read_only(client):
    test_client, calls = client

    response = test_client.get("/sahasra-exact/api/snapshot/status")

    assert response.status_code == 200
    assert calls == []
