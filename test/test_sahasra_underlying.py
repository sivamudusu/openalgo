"""The Sahasra blueprint serves BANKNIFTY and NIFTY from one rule set.

The trading sidebar runs a Sahasra panel and a Momentum panel for each index,
and every one of them calls the same two routes with ``?underlying=``. Two
things therefore have to hold without a broker in the loop:

- **The levels cache is keyed by underlying.** A single slot would serve one
  index's support and resistance to the other panel every time the two poll
  alternately, which is the kind of wrong that still looks like a working panel.
- **Only the two supported indices are accepted.** The query string reaches
  the broker services, so an unknown symbol is refused before any request
  leaves the platform, and an absent one keeps the original BANKNIFTY default
  for existing callers.

The route tests patch the two broker-facing functions (levels and history), so
they exercise the real allowlist, session check and response shape.
"""

import time
from datetime import datetime

import pytest
import pytz
from flask import Flask

import blueprints.sahasra as sahasra


def _levels(underlying: str) -> dict:
    return {
        "underlying": underlying,
        "support": 100.0,
        "resistance": 200.0,
        "pcr": 1.0,
        "regime": "neutral",
    }


@pytest.fixture(autouse=True)
def clean_levels_cache():
    sahasra._levels_cache.clear()
    yield
    sahasra._levels_cache.clear()


@pytest.fixture
def client(monkeypatch):
    """A real test client with a valid session and the broker calls stood in."""
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.register_blueprint(sahasra.sahasra_bp)

    calls: list[tuple[str, str]] = []

    def fake_levels(api_key, underlying):
        calls.append(("levels", underlying))
        return _levels(underlying), None

    def fake_history(**kwargs):
        calls.append(("history", kwargs["symbol"]))
        now = int(time.time())
        return (
            True,
            {
                "data": [
                    {"timestamp": now - 1800, "open": 200, "high": 215, "low": 195, "close": 210},
                    {"timestamp": now - 900, "open": 210, "high": 212, "low": 200, "close": 205},
                ]
            },
            200,
        )

    monkeypatch.setattr(sahasra, "_get_api_key", lambda: "test-key")
    monkeypatch.setattr(sahasra, "_get_levels", fake_levels)
    monkeypatch.setattr(sahasra, "get_history", fake_history)

    test_client = app.test_client()
    with test_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
    return test_client, calls


def test_resolve_underlying_defaults_and_allowlist():
    assert sahasra._resolve_underlying(None) == "BANKNIFTY"
    assert sahasra._resolve_underlying("") == "BANKNIFTY"
    assert sahasra._resolve_underlying(" nifty ") == "NIFTY"
    assert sahasra._resolve_underlying("banknifty") == "BANKNIFTY"
    assert sahasra._resolve_underlying("GOLD") is None


def test_levels_cache_is_keyed_by_underlying(monkeypatch):
    calls: list[str] = []

    def fake_compute(api_key, underlying):
        calls.append(underlying)
        return _levels(underlying), None

    monkeypatch.setattr(sahasra, "_compute_levels", fake_compute)

    banknifty, _ = sahasra._get_levels("key", "BANKNIFTY")
    nifty, _ = sahasra._get_levels("key", "NIFTY")

    assert banknifty["underlying"] == "BANKNIFTY"
    assert nifty["underlying"] == "NIFTY"
    assert calls == ["BANKNIFTY", "NIFTY"]

    # Inside the TTL each panel is served its own slot, not a recomputation and
    # not the other index's levels.
    assert sahasra._get_levels("key", "BANKNIFTY")[0]["underlying"] == "BANKNIFTY"
    assert sahasra._get_levels("key", "NIFTY")[0]["underlying"] == "NIFTY"
    assert calls == ["BANKNIFTY", "NIFTY"]


def test_expired_cache_recomputes_only_that_underlying(monkeypatch):
    calls: list[str] = []

    def fake_compute(api_key, underlying):
        calls.append(underlying)
        return _levels(underlying), None

    monkeypatch.setattr(sahasra, "_compute_levels", fake_compute)

    sahasra._get_levels("key", "BANKNIFTY")
    sahasra._get_levels("key", "NIFTY")
    sahasra._levels_cache["NIFTY"]["ts"] = time.time() - sahasra.LEVELS_TTL_SECONDS - 1

    assert sahasra._get_levels("key", "BANKNIFTY")[0]["underlying"] == "BANKNIFTY"
    assert sahasra._get_levels("key", "NIFTY")[0]["underlying"] == "NIFTY"
    assert calls == ["BANKNIFTY", "NIFTY", "NIFTY"]


def test_levels_route_passes_the_requested_underlying(client):
    test_client, calls = client

    response = test_client.get("/sahasra/api/levels?underlying=nifty")

    assert response.status_code == 200
    assert response.get_json()["data"]["underlying"] == "NIFTY"
    assert calls == [("levels", "NIFTY")]


def test_levels_route_defaults_to_banknifty(client):
    test_client, calls = client

    response = test_client.get("/sahasra/api/levels")

    assert response.status_code == 200
    assert response.get_json()["data"]["underlying"] == "BANKNIFTY"
    assert calls == [("levels", "BANKNIFTY")]


def test_levels_route_rejects_an_unsupported_underlying(client):
    test_client, calls = client

    response = test_client.get("/sahasra/api/levels?underlying=GOLD")

    assert response.status_code == 400
    assert response.get_json()["message"] == "Unsupported underlying"
    assert calls == []


def test_signal_route_reads_history_for_the_requested_underlying(client):
    test_client, calls = client

    response = test_client.get("/sahasra/api/signal?underlying=NIFTY")

    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["underlying"] == "NIFTY"
    assert body["data"]["state"] == "above_resistance"
    assert calls == [("levels", "NIFTY"), ("history", "NIFTY")]


def test_signal_route_rejects_an_unsupported_underlying(client):
    test_client, calls = client

    response = test_client.get("/sahasra/api/signal?underlying=SENSEX")

    assert response.status_code == 400
    assert calls == []
