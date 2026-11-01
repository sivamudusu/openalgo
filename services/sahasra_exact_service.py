"""Read-side service for the Sahasra exact panel.

The panel reads its level map from the previous completed end-of-day snapshot.
A small live chain call supplies the current spot, ATM contract and OI change.
When no snapshot exists, the level map is built from that live chain and marked
``live_provisional`` so it is never mistaken for the model's EOD map.
"""

from datetime import date, datetime, timedelta

from database import sahasra_exact_db as exact_db
from database.settings_db import get_analyze_mode
from services.expiry_service import get_expiry_dates
from services.history_service import get_history
from services.option_chain_service import get_option_chain
from services.sahasra_exact_levels import build_level_map, classify_regime, compute_pcr
from services.sahasra_exact_signal_service import evaluate_signal, parse_history_rows
from services.sahasra_exact_snapshot_service import (
    FO_EXCHANGE,
    INDEX_EXCHANGE,
    UNDERLYINGS,
    normalise_expiry,
    scheduler_status,
)
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

logger = get_logger(__name__)

LIVE_TTL_SECONDS = 20
EXPIRY_TTL_SECONDS = 300
STALE_AFTER_DAYS = 4

_live_cache = LockedTTLCache(maxsize=8, ttl=LIVE_TTL_SECONDS)
_expiry_cache = LockedTTLCache(maxsize=8, ttl=EXPIRY_TTL_SECONDS)


class LevelsError(Exception):
    """The level map could not be built."""


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


def _get_expiries(api_key, underlying):
    cached = _expiry_cache.get(underlying)
    if cached:
        return cached
    ok, response, _ = get_expiry_dates(
        symbol=underlying,
        exchange=FO_EXCHANGE,
        instrumenttype="options",
        api_key=api_key,
    )
    if not ok or not isinstance(response, dict):
        message = response.get("message") if isinstance(response, dict) else "Expiry lookup failed"
        raise LevelsError(str(message))
    values = response.get("data") or []
    expiries = [normalise_expiry(value) for value in values if value]
    if not expiries:
        raise LevelsError(f"No expiries are available for {underlying}.")
    _expiry_cache[underlying] = expiries
    return expiries


def _parse_chain(response):
    spot = _to_float(response.get("underlying_ltp"))
    atm = _to_float(response.get("atm_strike"))
    rows = []
    for row in response.get("chain") or []:
        strike = _to_float(row.get("strike"))
        if strike is None or strike <= 0:
            continue
        ce = row.get("ce") or {}
        pe = row.get("pe") or {}
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
                "lotsize": _to_int(ce.get("lotsize") or pe.get("lotsize")) or None,
            }
        )
    rows.sort(key=lambda item: item["strike"])
    if not rows or spot is None:
        raise LevelsError("The broker returned no usable strikes for the current expiry.")
    return spot, atm, rows


def _live_chain(api_key, underlying):
    cached = _live_cache.get(underlying)
    if cached:
        return cached
    expiries = _get_expiries(api_key, underlying)
    expiry = expiries[0]
    ok, response, _ = get_option_chain(
        underlying=underlying,
        exchange=FO_EXCHANGE,
        expiry_date=expiry,
        strike_count=15,
        api_key=api_key,
        with_quotes=True,
        with_greeks=False,
    )
    if not ok or not isinstance(response, dict):
        message = response.get("message") if isinstance(response, dict) else "Chain lookup failed"
        raise LevelsError(str(message))
    spot, atm, rows = _parse_chain(response)
    result = {"expiry": expiry, "spot": spot, "atm": atm, "rows": rows}
    _live_cache[underlying] = result
    return result


def _snapshot_for(underlying, current_expiry):
    today = exact_db.ist_today()
    snapshot = exact_db.get_latest_snapshot_before(underlying, today, current_expiry)
    if snapshot:
        return snapshot
    return None


def _distance(levels, spot):
    for side in ("supports", "resistances"):
        for item in levels.get(side) or []:
            if spot:
                item["distance_pct"] = round(abs(item["strike"] - spot) / spot * 100, 3)
    return levels


def _age_days(snapshot_date):
    if not snapshot_date:
        return None
    try:
        return max(0, (exact_db.ist_today() - date.fromisoformat(snapshot_date)).days)
    except ValueError:
        return None


def _oi_change(levels, snapshot, live_rows):
    live = {row["strike"]: row for row in live_rows}
    saved = {row["strike"]: row for row in (snapshot or {}).get("chain", [])}
    result = {}
    for item in (levels.get("supports") or []) + (levels.get("resistances") or []):
        strike = item["strike"]
        if strike in live and strike in saved:
            result[str(strike)] = {
                "ce": int(live[strike]["ce_oi"] - saved[strike]["ce_oi"]),
                "pe": int(live[strike]["pe_oi"] - saved[strike]["pe_oi"]),
            }
    atm = levels.get("atm_strike")
    if atm in live and atm in saved:
        result["atm"] = {
            "ce": int(live[atm]["ce_oi"] - saved[atm]["ce_oi"]),
            "pe": int(live[atm]["pe_oi"] - saved[atm]["pe_oi"]),
        }
    return result


def _mode():
    try:
        return "analyze" if get_analyze_mode() else "live"
    except Exception:
        return "unknown"


def get_levels(api_key, underlying, now=None):
    """Return the EOD level map plus current spot, ATM and OI change."""
    underlying = str(underlying or "").upper()
    if underlying not in UNDERLYINGS:
        raise LevelsError("Choose BANKNIFTY or NIFTY.")
    if not api_key:
        raise LevelsError("Connect a broker to read the Sahasra exact levels.")

    live = _live_chain(api_key, underlying)
    snapshot = _snapshot_for(underlying, live["expiry"])
    warning = None
    source = "eod_snapshot"
    if snapshot:
        levels = snapshot.get("levels") or {}
        if not levels.get("supports") and not levels.get("resistances"):
            levels = build_level_map(
                snapshot.get("spot") or live["spot"],
                snapshot.get("chain") or [],
                prior_day={
                    "high": snapshot.get("prev_day_high"),
                    "low": snapshot.get("prev_day_low"),
                    "close": snapshot.get("prev_day_close"),
                },
                expiration=snapshot.get("expiry"),
            )
        pcr = levels.get("pcr")
        if pcr is None:
            pcr = compute_pcr(snapshot.get("chain") or live["rows"])
        regime = levels.get("regime") or classify_regime(pcr)
        snapshot_date = snapshot.get("trade_date")
        age = _age_days(snapshot_date)
        if snapshot.get("status") == "partial":
            warning = "The captured chain is partial; a major wall may be missing."
        elif age is not None and age > STALE_AFTER_DAYS:
            warning = "The last complete snapshot is older than expected; capture a fresh one."
        coverage = snapshot.get("coverage") or "window_50"
        levels_warning = levels.get("warning")
        snapshot_status = snapshot.get("status")
    else:
        levels = build_level_map(
            live["spot"],
            live["rows"],
            expiration=live["expiry"],
        )
        pcr = compute_pcr(live["rows"])
        regime = classify_regime(pcr)
        snapshot_date = None
        age = None
        warning = (
            "No end-of-day snapshot exists for this expiry. These levels are built "
            "from the live chain and are provisional."
        )
        coverage = "live_window_15"
        levels_warning = levels.get("warning")
        snapshot_status = "provisional"
        source = "live_provisional"

    levels["spot"] = live["spot"]
    levels["expiry"] = live["expiry"]
    levels["pcr"] = levels.get("pcr") if levels.get("pcr") is not None else pcr
    levels["regime"] = regime
    levels["atm_strike"] = live["atm"]
    levels["atm_ce_symbol"] = next(
        (row["ce_symbol"] for row in live["rows"] if row["strike"] == live["atm"]),
        levels.get("atm_ce_symbol"),
    )
    levels["atm_pe_symbol"] = next(
        (row["pe_symbol"] for row in live["rows"] if row["strike"] == live["atm"]),
        levels.get("atm_pe_symbol"),
    )
    atm_row = next((row for row in live["rows"] if row["strike"] == live["atm"]), None)
    if atm_row:
        levels["atm_lotsize"] = atm_row.get("lotsize")
    _distance(levels, live["spot"])
    levels["oi_change"] = _oi_change(levels, snapshot, live["rows"])
    live_pcr = compute_pcr(live["rows"])
    levels["live_pcr"] = round(live_pcr, 3) if live_pcr is not None else None
    levels.update(
        {
            "underlying": underlying,
            "source": source,
            "snapshot_date": snapshot_date,
            "snapshot_age_days": age,
            "snapshot_status": snapshot_status,
            "coverage": coverage,
            "stale": bool(age is not None and age > STALE_AFTER_DAYS),
            "warning": warning,
            "levels_warning": levels_warning,
            "mode": _mode(),
        }
    )
    return levels


def get_signal(api_key, underlying, now=None):
    """Return the level map plus the last closed 15-minute signal."""
    levels = get_levels(api_key, underlying, now=now)
    today = exact_db.ist_today()
    start = today - timedelta(days=5)
    try:
        ok, response, _ = get_history(
            symbol=levels["underlying"],
            exchange=INDEX_EXCHANGE,
            interval="15m",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=today.strftime("%Y-%m-%d"),
            api_key=api_key,
        )
    except Exception as exc:
        logger.exception("Sahasra exact signal: history failed")
        raise LevelsError("The broker's 15-minute history is unavailable right now.") from exc
    if not ok or not isinstance(response, dict):
        message = response.get("message") if isinstance(response, dict) else None
        raise LevelsError(message or "The broker's 15-minute history is unavailable right now.")
    bars = parse_history_rows(response.get("data") or [])
    result = evaluate_signal(levels, bars, now_ts=(now or datetime.now()).timestamp())
    return {**levels, **result, "bar_count": len(bars)}


def get_snapshot_status():
    """Return recent captures and scheduler state."""
    return {
        "snapshots": exact_db.list_recent_snapshots(12),
        "scheduler": scheduler_status(),
        "today": exact_db.ist_today().isoformat(),
        "underlyings": list(UNDERLYINGS),
    }
