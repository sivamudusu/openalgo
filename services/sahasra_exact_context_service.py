"""Pre-open context for the Sahasra exact panel.

The automatic half uses the broker's existing GLOBAL_INDEX quote path. The
manual half is the trader's own reading of Fed tone, inflation, geopolitics and
the India 10-year yield. Context never changes levels and never blocks an order.
"""

from datetime import datetime

from database import sahasra_exact_db as context_db
from services.quotes_service import get_multiquotes, get_quotes
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

logger = get_logger(__name__)

AUTO_SYMBOLS = (
    ("US30", "US 30", "index"),
    ("US500", "US 500", "index"),
    ("US100", "US 100", "index"),
    ("US10YRYIELD", "US 10Y yield", "yield"),
    ("GIFTNIFTY", "GIFT Nifty", "index"),
    ("BRENTOIL", "Brent crude", "commodity"),
    ("WTIOIL", "WTI crude", "commodity"),
)

BIAS_VALUES = {"bullish", "bearish", "mixed", "unset"}
CONFIDENCE_VALUES = {"low", "medium", "high"}
MANUAL_VALUES = {
    "fed": {"", "dovish", "neutral", "hawkish"},
    "inflation": {"", "cool", "in_line", "hot"},
    "geopolitics": {"", "risk_on", "neutral", "risk_off"},
}

_auto_cache = LockedTTLCache(maxsize=4, ttl=300)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_manual():
    return {
        "fed": "",
        "inflation": "",
        "geopolitics": "",
        "india_yield": "",
    }


def _quote_result(item, data, error=None):
    symbol, label, kind = item
    data = data or {}
    ltp = _num(data.get("ltp"))
    prev_close = _num(data.get("prev_close"))
    change_pct = None
    if ltp is not None and prev_close and prev_close > 0:
        change_pct = round((ltp - prev_close) / prev_close * 100, 2)
    return {
        "key": symbol,
        "label": label,
        "kind": kind,
        "symbol": symbol,
        "exchange": "GLOBAL_INDEX",
        "value": ltp,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "status": "available" if ltp is not None and not error else "unavailable",
        "source": "broker",
        "error": error,
    }


def fetch_auto_cues(api_key):
    """Fetch the broker-exposed global cues. Never raises."""
    cached = _auto_cache.get("global")
    if cached is not None:
        return cached
    if not api_key:
        result = [
            {**_quote_result(item, None), "status": "unavailable", "error": "No API key"}
            for item in AUTO_SYMBOLS
        ]
        return result

    requested = [{"symbol": symbol, "exchange": "GLOBAL_INDEX"} for symbol, _, _ in AUTO_SYMBOLS]
    results = []
    try:
        success, response, _ = get_multiquotes(symbols=requested, api_key=api_key)
        by_symbol = {}
        if success and isinstance(response, dict):
            for row in response.get("results") or []:
                if row.get("symbol"):
                    by_symbol[row["symbol"]] = row
        for item in AUTO_SYMBOLS:
            row = by_symbol.get(item[0]) or {}
            results.append(_quote_result(item, row.get("data"), row.get("error")))
    except Exception:
        logger.exception("Sahasra exact context: multiquote failed, trying single quotes")
        results = []

    if not any(item["status"] == "available" for item in results):
        results = []
        for item in AUTO_SYMBOLS:
            try:
                ok, quote, _ = get_quotes(symbol=item[0], exchange="GLOBAL_INDEX", api_key=api_key)
                results.append(
                    _quote_result(
                        item,
                        (quote or {}).get("data") if ok else None,
                        None if ok else (quote or {}).get("message"),
                    )
                )
            except Exception as exc:
                logger.exception("Sahasra exact context: quote failed for %s", item[0])
                results.append(_quote_result(item, None, str(exc)))

    _auto_cache["global"] = results
    return results


def _normalise_manual(value):
    source = value if isinstance(value, dict) else {}
    manual = _empty_manual()
    for key, allowed in MANUAL_VALUES.items():
        candidate = str(source.get(key) or "").strip().lower()
        manual[key] = candidate if candidate in allowed else ""
    india_yield = source.get("india_yield")
    if india_yield not in (None, ""):
        try:
            manual["india_yield"] = str(float(india_yield))
        except (TypeError, ValueError):
            manual["india_yield"] = ""
    return manual


def _normalise_bias(value):
    bias = str(value or "unset").strip().lower()
    return bias if bias in BIAS_VALUES else "unset"


def _normalise_confidence(value):
    confidence = str(value or "low").strip().lower()
    return confidence if confidence in CONFIDENCE_VALUES else "low"


def default_context(trade_date):
    return {
        "trade_date": trade_date.isoformat(),
        "auto_cues": [],
        "manual": _empty_manual(),
        "final_bias": "unset",
        "confidence": "low",
        "notes": "",
        "captured_at": None,
        "updated_at": None,
        "saved": False,
    }


def get_context_view(trade_date, api_key):
    """Return fresh automatic cues merged with the stored manual reading."""
    try:
        stored = context_db.get_context(trade_date)
    except Exception:
        logger.exception("Sahasra exact context: could not read saved context")
        stored = None
    view = default_context(trade_date)
    cues = (stored or {}).get("cues") or {}
    if stored:
        view.update(
            {
                "manual": _normalise_manual(cues.get("manual")),
                "final_bias": _normalise_bias(stored.get("final_bias")),
                "confidence": _normalise_confidence(stored.get("confidence")),
                "notes": stored.get("notes") or "",
                "captured_at": stored.get("captured_at"),
                "updated_at": stored.get("updated_at"),
                "saved": True,
            }
        )
    view["auto_cues"] = fetch_auto_cues(api_key)
    return view


def save_context_view(trade_date, api_key, payload):
    """Validate and persist one session's context, including the quote snapshot."""
    source = payload if isinstance(payload, dict) else {}
    manual = _normalise_manual(source.get("manual"))
    final_bias = _normalise_bias(source.get("final_bias"))
    confidence = _normalise_confidence(source.get("confidence"))
    notes = str(source.get("notes") or "").strip()[:1000]
    automatic = fetch_auto_cues(api_key)
    context_db.save_context(
        trade_date,
        {"auto": automatic, "manual": manual},
        final_bias,
        confidence,
        notes,
    )
    return get_context_view(trade_date, api_key)


def alignment_for(signal_side, final_bias):
    if final_bias == "unset":
        return "unset"
    if final_bias == "mixed":
        return "mixed"
    if signal_side == "CE":
        return "aligned" if final_bias == "bullish" else "against"
    if signal_side == "PE":
        return "aligned" if final_bias == "bearish" else "against"
    return "unset"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")
