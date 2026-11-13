"""HTTP surface for visual chart orders (draggable bracket orders).

Session-auth and CSRF, matching the scalping terminal's UI endpoints. The chart
already carries the user's session; the broker credentials stay on the server.

The endpoint names are the trader's words: place, modify, cancel, exit. The
engine owns the state machine and every safety rule behind them.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from database.auth_db import get_api_key_for_tradingview
from database.settings_db import get_analyze_mode
from services.visual_bracket.engine import get_visual_bracket_engine
from services.visual_bracket.resolver import SUPPORTED_UNDERLYINGS, VisualBracketError
from utils.latency_monitor import track_latency
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

visual_bracket_bp = Blueprint("visual_bracket_bp", __name__, url_prefix="/")


def _current_mode() -> str:
    return "sandbox" if get_analyze_mode() else "live"


def _resolve_api_key():
    username = session.get("user")
    if not username:
        return None, {"status": "error", "message": "Not authenticated"}, 401
    api_key = get_api_key_for_tradingview(username)
    if not api_key:
        return (
            None,
            {
                "status": "error",
                "message": "API key required. Generate one at /apikey",
            },
            401,
        )
    return api_key, None, None


def _bracket_id(data: dict) -> int | None:
    try:
        value = int(data.get("id"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@visual_bracket_bp.route("/bracket/api/underlyings", methods=["GET"])
@check_session_validity
def underlyings():
    data = [
        {
            "underlying": name,
            "index_exchange": cfg["index_exchange"],
            "fo_exchange": cfg["fo_exchange"],
        }
        for name, cfg in SUPPORTED_UNDERLYINGS.items()
    ]
    return jsonify({"status": "success", "data": data})


@visual_bracket_bp.route("/bracket/api/list", methods=["GET"])
@check_session_validity
def list_brackets():
    engine = get_visual_bracket_engine()
    rows = engine.list_brackets(mode=_current_mode())
    return jsonify({"status": "success", "data": rows})


@visual_bracket_bp.route("/bracket/api/place", methods=["POST"])
@check_session_validity
@track_latency("PLACE")
def place():
    data = request.get_json(silent=True) or {}
    api_key, error, code = _resolve_api_key()
    if error:
        return jsonify(error), code

    try:
        success, response, status_code = get_visual_bracket_engine().place(
            data, api_key, _current_mode()
        )
        return jsonify(response), status_code
    except VisualBracketError as e:
        return jsonify({"status": "error", "message": e.message}), e.status_code
    except Exception as e:
        logger.exception("Visual bracket placement failed: %s", e)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "The visual order could not be placed. Try again.",
                }
            ),
            500,
        )


@visual_bracket_bp.route("/bracket/api/modify", methods=["POST"])
@check_session_validity
def modify():
    data = request.get_json(silent=True) or {}
    bracket_id = _bracket_id(data)
    if bracket_id is None:
        return jsonify({"status": "error", "message": "A visual order id is required."}), 400

    api_key, error, code = _resolve_api_key()
    if error:
        return jsonify(error), code

    try:
        success, response, status_code = get_visual_bracket_engine().modify(
            bracket_id, data, api_key
        )
        return jsonify(response), status_code
    except VisualBracketError as e:
        return jsonify({"status": "error", "message": e.message}), e.status_code
    except Exception as e:
        logger.exception("Visual bracket modify failed: %s", e)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "The visual order could not be changed. Try again.",
                }
            ),
            500,
        )


@visual_bracket_bp.route("/bracket/api/cancel", methods=["POST"])
@check_session_validity
def cancel():
    data = request.get_json(silent=True) or {}
    bracket_id = _bracket_id(data)
    if bracket_id is None:
        return jsonify({"status": "error", "message": "A visual order id is required."}), 400

    api_key, error, code = _resolve_api_key()
    if error:
        return jsonify(error), code

    try:
        success, response, status_code = get_visual_bracket_engine().cancel(bracket_id, api_key)
        return jsonify(response), status_code
    except Exception as e:
        logger.exception("Visual bracket cancel failed: %s", e)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "The visual order could not be cancelled. Try again.",
                }
            ),
            500,
        )


@visual_bracket_bp.route("/bracket/api/exit", methods=["POST"])
@check_session_validity
@track_latency("CLOSE")
def exit_position():
    data = request.get_json(silent=True) or {}
    bracket_id = _bracket_id(data)
    if bracket_id is None:
        return jsonify({"status": "error", "message": "A visual order id is required."}), 400

    api_key, error, code = _resolve_api_key()
    if error:
        return jsonify(error), code

    try:
        success, response, status_code = get_visual_bracket_engine().exit(bracket_id, api_key)
        return jsonify(response), status_code
    except Exception as e:
        logger.exception("Visual bracket exit failed: %s", e)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "The position could not be exited. Try again.",
                }
            ),
            500,
        )
