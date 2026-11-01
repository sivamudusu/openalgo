"""End-of-day snapshot capture and scheduler for the Sahasra exact panel.

One job at 15:45 IST, after the 15:40 NFO/BFO close, captures the nearest and
next expiry for BANKNIFTY and NIFTY. The job is idempotent, so a restart after
15:45 can catch up without creating a second copy.
"""

import json
import threading
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from database import sahasra_exact_db as exact_db
from database.auth_db import get_first_available_api_key
from database.market_calendar_db import get_effective_session_window
from services.expiry_service import get_expiry_dates
from services.history_service import get_history
from services.option_chain_service import get_option_chain
from services.sahasra_exact_levels import (
    build_level_map,
    compute_pcr,
    count_rejections,
    swing_levels_from_bars,
)
from utils.db_sessions import releases_scoped_sessions
from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
UNDERLYINGS = ("BANKNIFTY", "NIFTY")
FO_EXCHANGE = "NFO"
INDEX_EXCHANGE = "NSE_INDEX"
STRIKES_EACH_SIDE = 50
SNAPSHOT_SOURCE = "eod"
SCHEDULER_ID = "sahasra_exact_eod_snapshot"
CATCHUP_ID = "sahasra_exact_eod_catchup"

_scheduler = None
_scheduler_lock = threading.Lock()


class SnapshotError(Exception):
    """A capture could not be completed."""


def ist_now():
    return datetime.now(IST)


def ist_today():
    return ist_now().date()


def manual_capture_source(trade_date=None):
    """A manual capture is EOD only after the close on a trading day."""
    trade_date = trade_date or ist_today()
    if ist_now().time() >= clock_time(15, 45) and _is_trading_day(trade_date):
        return SNAPSHOT_SOURCE
    return "manual"


def normalise_expiry(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw[:10]).strftime("%d%b%y").upper()
    except ValueError:
        pass
    text = raw.upper().replace("-", "").replace(" ", "")
    if len(text) == 8 and text.isdigit():
        return text
    return text


def _rows_from_response(response):
    if not isinstance(response, dict):
        return []
    data = response.get("data")
    return data if isinstance(data, list) else []


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _timestamp_date(value):
    if value is None:
        return None
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, IST).date()
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _daily_bars(response):
    bars = []
    for row in _rows_from_response(response):
        day = _timestamp_date(row.get("timestamp"))
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        if day and high and low and close:
            bars.append({"date": day, "h": high, "low": low, "c": close})
    bars.sort(key=lambda item: item["date"])
    return bars


def _intraday_bars(response):
    bars = []
    for row in _rows_from_response(response):
        try:
            stamp = int(float(row.get("timestamp")))
            if stamp > 10_000_000_000:
                stamp //= 1000
            bars.append(
                {
                    "ts": stamp,
                    "h": float(row.get("high")),
                    "low": float(row.get("low")),
                    "c": float(row.get("close")),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    bars.sort(key=lambda item: item["ts"])
    return bars


def _prior_day(daily_bars, trade_date):
    previous = [bar for bar in daily_bars if bar["date"] < trade_date]
    if not previous:
        return None
    row = previous[-1]
    return {"high": row["h"], "low": row["low"], "close": row["c"]}


def _expiries(api_key, underlying):
    ok, response, _ = get_expiry_dates(
        symbol=underlying,
        exchange=FO_EXCHANGE,
        instrumenttype="options",
        api_key=api_key,
    )
    if not ok or not isinstance(response, dict):
        message = response.get("message") if isinstance(response, dict) else "Expiry lookup failed"
        raise SnapshotError(str(message))
    values = response.get("data") or []
    if not isinstance(values, list) or not values:
        raise SnapshotError(f"No expiries are available for {underlying}.")
    return [normalise_expiry(value) for value in values[:2] if value]


def _fetch_chain(api_key, underlying, expiry):
    ok, response, _ = get_option_chain(
        underlying=underlying,
        exchange=FO_EXCHANGE,
        expiry_date=expiry,
        strike_count=STRIKES_EACH_SIDE,
        api_key=api_key,
        with_quotes=True,
        with_greeks=False,
    )
    if not ok or not isinstance(response, dict):
        message = response.get("message") if isinstance(response, dict) else "Chain lookup failed"
        raise SnapshotError(str(message))
    return response


def _parse_chain(response, expiry):
    spot = _to_float(response.get("underlying_ltp"))
    atm = _to_float(response.get("atm_strike"))
    rows = []
    for row in response.get("chain") or []:
        strike = _to_float(row.get("strike"))
        if strike is None or strike <= 0:
            continue
        ce = row.get("ce") or {}
        pe = row.get("pe") or {}
        lotsize = ce.get("lotsize") or pe.get("lotsize")
        rows.append(
            {
                "strike": strike,
                "ce_oi": _to_int(ce.get("oi")),
                "pe_oi": _to_int(pe.get("oi")),
                "ce_volume": _to_int(ce.get("volume")),
                "pe_volume": _to_int(pe.get("volume")),
                "ce_ltp": _to_float(ce.get("ltp")) or 0.0,
                "pe_ltp": _to_float(pe.get("ltp")) or 0.0,
                "ce_symbol": ce.get("symbol"),
                "pe_symbol": pe.get("symbol"),
                "lotsize": _to_int(lotsize) or None,
            }
        )
    rows.sort(key=lambda item: item["strike"])
    if not rows or spot is None:
        raise SnapshotError(f"No strikes were returned for {expiry}.")
    return spot, atm, rows


def _history(api_key, underlying, interval, start, end):
    try:
        ok, response, _ = get_history(
            symbol=underlying,
            exchange=INDEX_EXCHANGE,
            interval=interval,
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            api_key=api_key,
        )
        return response if ok else {}
    except Exception:
        logger.exception("Sahasra exact snapshot: history failed for %s %s", underlying, interval)
        return {}


@releases_scoped_sessions
def run_scheduled_capture():
    """Scheduler entry point. Sessions are released even when a broker fails."""
    try:
        return capture_daily_snapshots()
    except Exception:
        logger.exception("Sahasra exact snapshot: scheduled capture failed")
        return {"status": "error", "message": "Snapshot capture failed"}


def capture_snapshot(underlying, expiry, trade_date=None, api_key=None, source=SNAPSHOT_SOURCE):
    """Capture one underlying/expiry snapshot. Returns a small summary dict."""
    underlying = str(underlying or "").upper()
    if underlying not in UNDERLYINGS:
        raise SnapshotError("Unsupported underlying.")
    expiry = normalise_expiry(expiry)
    if not expiry:
        raise SnapshotError("Expiry is required.")
    trade_date = trade_date or ist_today()
    api_key = api_key or get_first_available_api_key()
    if not api_key:
        raise SnapshotError("Connect a broker before capturing the end-of-day chain.")

    response = _fetch_chain(api_key, underlying, expiry)
    spot, atm, rows = _parse_chain(response, expiry)
    daily = _daily_bars(
        _history(api_key, underlying, "D", trade_date - timedelta(days=20), trade_date)
    )
    intraday = _intraday_bars(
        _history(api_key, underlying, "15m", trade_date - timedelta(days=8), trade_date)
    )
    prior_day = _prior_day(daily, trade_date)
    swings = swing_levels_from_bars(daily)
    rejection_counts = count_rejections(
        intraday,
        [row["strike"] for row in rows],
        tolerance=max(1.0, spot * 0.0002),
    )
    levels = build_level_map(
        spot,
        rows,
        prior_day=prior_day,
        swing_levels=swings,
        rejection_counts=rejection_counts,
        expiration=expiry,
    )
    pcr = compute_pcr(rows)
    warning = levels.get("warning") or ""
    status = "complete"
    if len(rows) < 20:
        status = "partial"
        warning = (warning + " " if warning else "") + "Fewer than 20 strikes were captured."
    header = {
        "underlying": underlying,
        "trade_date": trade_date,
        "expiry": expiry,
        "spot": spot,
        "atm_strike": atm or levels.get("atm_strike"),
        "pcr": levels.get("pcr") if levels.get("pcr") is not None else pcr,
        "total_call_oi": sum(row["ce_oi"] for row in rows),
        "total_put_oi": sum(row["pe_oi"] for row in rows),
        "coverage": f"window_{STRIKES_EACH_SIDE}",
        "strikes_captured": len(rows),
        "prev_day_high": (prior_day or {}).get("high"),
        "prev_day_low": (prior_day or {}).get("low"),
        "prev_day_close": (prior_day or {}).get("close"),
        "levels_json": json.dumps(levels, separators=(",", ":"), default=str),
        "source": source,
        "status": status,
        "warning": warning.strip() or None,
    }
    snapshot_id = exact_db.save_snapshot(header, rows)
    return {
        "status": "success",
        "snapshot_id": snapshot_id,
        "underlying": underlying,
        "trade_date": trade_date.isoformat(),
        "expiry": expiry,
        "strikes": len(rows),
        "pcr": header["pcr"],
        "source": source,
        "snapshot_status": status,
        "warning": header["warning"],
    }


def _is_trading_day(trade_date):
    return get_effective_session_window(trade_date, FO_EXCHANGE) is not None


def capture_underlying_snapshots(underlying, trade_date=None, api_key=None, source=None):
    """Capture the nearest and next expiry for one underlying."""
    underlying = str(underlying or "").upper()
    if underlying not in UNDERLYINGS:
        raise SnapshotError("Choose BANKNIFTY or NIFTY.")
    trade_date = trade_date or ist_today()
    api_key = api_key or get_first_available_api_key()
    if not api_key:
        raise SnapshotError("Connect a broker before capturing the end-of-day chain.")
    source = source or manual_capture_source(trade_date)
    expiries = _expiries(api_key, underlying)
    results = []
    for expiry in expiries:
        results.append(
            capture_snapshot(
                underlying,
                expiry,
                trade_date=trade_date,
                api_key=api_key,
                source=source,
            )
        )
    return {
        "status": "success",
        "underlying": underlying,
        "source": source,
        "results": results,
    }


def capture_daily_snapshots(trade_date=None, api_key=None, source=None):
    """Capture nearest and next expiry for both underlyings, skipping closed days."""
    trade_date = trade_date or ist_today()
    source = source or SNAPSHOT_SOURCE
    if not _is_trading_day(trade_date):
        return {
            "status": "skipped",
            "reason": "The exchange is closed on this date.",
            "trade_date": trade_date.isoformat(),
            "results": [],
        }
    api_key = api_key or get_first_available_api_key()
    if not api_key:
        return {
            "status": "error",
            "message": "Connect a broker before capturing the end-of-day chain.",
            "trade_date": trade_date.isoformat(),
            "results": [],
        }
    if all(exact_db.has_complete_snapshot(underlying, trade_date) for underlying in UNDERLYINGS):
        return {
            "status": "skipped",
            "reason": "A complete snapshot already exists for this session.",
            "trade_date": trade_date.isoformat(),
            "results": [],
        }

    results = []
    for underlying in UNDERLYINGS:
        try:
            expiries = _expiries(api_key, underlying)
        except Exception as exc:
            logger.exception("Sahasra exact snapshot: expiry lookup failed for %s", underlying)
            results.append({"underlying": underlying, "status": "error", "message": str(exc)})
            continue
        for expiry in expiries:
            try:
                results.append(
                    capture_snapshot(
                        underlying,
                        expiry,
                        trade_date=trade_date,
                        api_key=api_key,
                        source=source,
                    )
                )
            except Exception as exc:
                logger.exception(
                    "Sahasra exact snapshot: capture failed for %s %s", underlying, expiry
                )
                results.append(
                    {
                        "underlying": underlying,
                        "expiry": expiry,
                        "status": "error",
                        "message": str(exc),
                    }
                )
    captured = [item for item in results if item.get("status") == "success"]
    return {
        "status": "success" if captured else "error",
        "trade_date": trade_date.isoformat(),
        "results": results,
    }


def init_scheduler():
    """Start the 15:45 IST job once, and catch up when boot happens later."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            return _scheduler
        scheduler = BackgroundScheduler(
            timezone=IST,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900},
        )
        scheduler.add_job(
            run_scheduled_capture,
            CronTrigger(
                day_of_week="mon-fri",
                hour=15,
                minute=45,
                timezone=IST,
            ),
            id=SCHEDULER_ID,
            replace_existing=True,
        )
        scheduler.start()
        now = ist_now()
        if now.time() >= clock_time(15, 45) and _is_trading_day(now.date()):
            scheduler.add_job(
                run_scheduled_capture,
                DateTrigger(run_date=now + timedelta(seconds=20), timezone=IST),
                id=CATCHUP_ID,
                replace_existing=True,
            )
        _scheduler = scheduler
        logger.debug("Sahasra exact snapshot scheduler initialized")
        return _scheduler


def scheduler_status():
    now = ist_now()
    return {
        "enabled": _scheduler is not None,
        "next_run": "15:45 IST on trading days",
        "timezone": "Asia/Kolkata",
        "now": now.isoformat(timespec="seconds"),
    }
