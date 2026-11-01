"""Sahasra exact model panel backend.

Manual only. Every GET is read-only. The two POST routes either capture a
snapshot or save the trader's pre-open context; neither places an order.
"""

from datetime import date

from flask import Blueprint, jsonify, request, session

from database.auth_db import get_api_key_for_tradingview
from database.sahasra_exact_db import as_date, ist_today
from services.sahasra_exact_context_service import get_context_view, save_context_view
from services.sahasra_exact_service import LevelsError, get_levels, get_signal, get_snapshot_status
from services.sahasra_exact_snapshot_service import (
    UNDERLYINGS,
    SnapshotError,
    capture_daily_snapshots,
    capture_underlying_snapshots,
    manual_capture_source,
)
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

sahasra_exact_bp = Blueprint("sahasra_exact_bp", __name__, url_prefix="/")


def _api_key():
    username = session.get("user")
    if not username:
        return None
    return get_api_key_for_tradingview(username)


def _underlying(value):
    underlying = str(value or "BANKNIFTY").strip().upper()
    return underlying if underlying in UNDERLYINGS else None


def _date_arg():
    raw = request.args.get("date") or request.get_json(silent=True, force=True) or {}
    if isinstance(raw, dict):
        raw = raw.get("trade_date") or raw.get("date")
    if not raw:
        return ist_today()
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


@sahasra_exact_bp.route("/sahasra-exact/api/levels", methods=["GET"])
@check_session_validity
def levels():
    underlying = _underlying(request.args.get("underlying"))
    if underlying is None:
        return jsonify({"status": "error", "message": "Choose BANKNIFTY or NIFTY."}), 400
    api_key = _api_key()
    if not api_key:
        return jsonify({"status": "error", "message": "Connect a broker to read the levels."}), 401
    try:
        data = get_levels(api_key, underlying)
    except LevelsError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502
    except Exception:
        logger.exception("Sahasra exact levels failed")
        return jsonify({"status": "error", "message": "The levels could not be read."}), 500
    return jsonify({"status": "success", "data": data})


@sahasra_exact_bp.route("/sahasra-exact/api/signal", methods=["GET"])
@check_session_validity
def signal():
    underlying = _underlying(request.args.get("underlying"))
    if underlying is None:
        return jsonify({"status": "error", "message": "Choose BANKNIFTY or NIFTY."}), 400
    api_key = _api_key()
    if not api_key:
        return jsonify({"status": "error", "message": "Connect a broker to read the signal."}), 401
    try:
        data = get_signal(api_key, underlying)
    except LevelsError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502
    except Exception:
        logger.exception("Sahasra exact signal failed")
        return jsonify({"status": "error", "message": "The signal could not be read."}), 500
    return jsonify({"status": "success", "data": data})


@sahasra_exact_bp.route("/sahasra-exact/api/snapshot/status", methods=["GET"])
@check_session_validity
def snapshot_status():
    try:
        return jsonify({"status": "success", "data": get_snapshot_status()})
    except Exception:
        logger.exception("Sahasra exact snapshot status failed")
        return jsonify({"status": "error", "message": "Snapshot status is unavailable."}), 500


@sahasra_exact_bp.route("/sahasra-exact/api/snapshot", methods=["POST"])
@check_session_validity
def capture_snapshot():
    api_key = _api_key()
    if not api_key:
        return jsonify({"status": "error", "message": "Connect a broker before capturing."}), 401
    payload = request.get_json(silent=True) or {}
    underlying = payload.get("underlying")
    try:
        if underlying:
            chosen = _underlying(underlying)
            if chosen is None:
                return jsonify({"status": "error", "message": "Choose BANKNIFTY or NIFTY."}), 400
            result = capture_underlying_snapshots(chosen, api_key=api_key)
        else:
            result = capture_daily_snapshots(api_key=api_key, source=manual_capture_source())
    except SnapshotError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502
    except Exception:
        logger.exception("Sahasra exact snapshot capture failed")
        return jsonify({"status": "error", "message": "The snapshot could not be captured."}), 500
    code = 200 if result.get("status") in {"success", "skipped"} else 502
    return jsonify(result), code


@sahasra_exact_bp.route("/sahasra-exact/api/context", methods=["GET"])
@check_session_validity
def context_get():
    trade_date = _date_arg()
    if trade_date is None:
        return jsonify({"status": "error", "message": "Use a valid date."}), 400
    api_key = _api_key()
    try:
        data = get_context_view(trade_date, api_key)
    except Exception:
        logger.exception("Sahasra exact context read failed")
        return jsonify({"status": "error", "message": "The pre-open context is unavailable."}), 500
    return jsonify({"status": "success", "data": data})


@sahasra_exact_bp.route("/sahasra-exact/api/context", methods=["POST"])
@check_session_validity
def context_post():
    payload = request.get_json(silent=True) or {}
    raw_date = payload.get("trade_date") or payload.get("date") or ist_today()
    trade_date = as_date(raw_date)
    if trade_date is None:
        return jsonify({"status": "error", "message": "Use a valid date."}), 400
    api_key = _api_key()
    try:
        data = save_context_view(trade_date, api_key, payload)
    except Exception:
        logger.exception("Sahasra exact context save failed")
        return jsonify({"status": "error", "message": "The context could not be saved."}), 500
    return jsonify({"status": "success", "data": data})
