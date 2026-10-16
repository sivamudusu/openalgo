import os

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource
from marshmallow import ValidationError

from database.auth_db import verify_api_key
from database.low_iv_db import get_settings as get_stored_settings
from database.low_iv_db import get_today_series as get_stored_iv_series
from database.low_iv_db import record_iv as store_iv_sample
from limiter import limiter
from services.low_iv_service import LOW_IV_DEFAULTS, effective_settings
from utils.logging import get_logger

from .account_schema import PingSchema

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")
api = Namespace("lowiv", description="Low-IV BANKNIFTY strategy settings (API-key auth)")

logger = get_logger(__name__)

apikey_schema = PingSchema()


@api.route("/settings", strict_slashes=False)
class LowIVSettings(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Return the effective Low-IV settings (stored row merged over defaults).

        Lets the /python engine run with the same exits/gates the operator
        tuned on the /lowiv page, using its injected OPENALGO_API_KEY.
        """
        try:
            data = apikey_schema.load(request.json)
            user_id = verify_api_key(data["apikey"])
            if not user_id:
                return make_response(
                    jsonify({"status": "error", "message": "Invalid openalgo apikey"}), 403
                )
            cfg = effective_settings(get_stored_settings())
            public = {k: cfg[k] for k in sorted(cfg) if k not in ("strategy_tag",)}
            public["underlying"] = LOW_IV_DEFAULTS["underlying"]
            public["index_exchange"] = LOW_IV_DEFAULTS["index_exchange"]
            public["fo_exchange"] = LOW_IV_DEFAULTS["fo_exchange"]
            public["strategy_tag"] = LOW_IV_DEFAULTS["strategy_tag"]
            return make_response(jsonify({"status": "success", "data": public}), 200)
        except ValidationError as err:
            return make_response(jsonify({"status": "error", "message": err.messages}), 400)
        except Exception:
            logger.exception("Unexpected error in lowiv settings endpoint")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )


def _require_user(payload):
    """Verify the API key in a restx body. Returns (user_id, error_response)."""
    try:
        data = apikey_schema.load(payload)
    except ValidationError as err:
        return None, (jsonify({"status": "error", "message": err.messages}), 400)
    user_id = verify_api_key(data["apikey"])
    if not user_id:
        return None, (jsonify({"status": "error", "message": "Invalid openalgo apikey"}), 403)
    return user_id, None


@api.route("/iv", strict_slashes=False)
class LowIVRecord(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Record one ATM-IV sample + contract snapshot (engine path).

        Non-positive prints are rejected, never stored.
        """
        _, err = _require_user(request.json)
        if err:
            body, code = err
            return make_response(body, code)
        body = request.json or {}
        try:
            iv = float(body.get("iv"))
        except (TypeError, ValueError):
            return make_response(jsonify({"status": "error", "message": "iv is required"}), 400)
        try:
            count = store_iv_sample(
                iv, body.get("strike"), body.get("bid"), body.get("ask"), body.get("legs")
            )
        except Exception:
            logger.exception("Unexpected error recording low-iv sample")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )
        if count is None:
            return make_response(
                jsonify({"status": "error", "message": "IV print rejected (non-positive)"}), 400
            )
        return make_response(jsonify({"status": "success", "data": {"samples_today": count}}), 200)


@api.route("/iv_series", strict_slashes=False)
class LowIVSeries(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Return today's IV series for percentile ranking (engine path)."""
        _, err = _require_user(request.json)
        if err:
            body, code = err
            return make_response(body, code)
        try:
            series = get_stored_iv_series()
            from database.low_iv_db import get_today_series_full as get_full

            return make_response(
                jsonify(
                    {
                        "status": "success",
                        "data": {
                            "ivs": [iv for _, iv in series],
                            "count": len(series),
                            "samples": get_full(),
                        },
                    }
                ),
                200,
            )
        except Exception:
            logger.exception("Unexpected error reading low-iv series")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )
