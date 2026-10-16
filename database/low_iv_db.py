# database/low_iv_db.py
"""
Persistence for the /lowiv terminal's strategy settings.

Single-row table (id=1): the operator tunes scoped exits, entry gates and the
Bollinger confirmation from the Low-IV page and every consumer (page auto
evaluator, /python engine via the API-key endpoint) reads the same values.
Mirrors database/scalping_db.py:
- SQLite via NullPool (one connection per op, closed immediately)
- scoped_session registered in app.py teardown_appcontext for FD hygiene
"""

import logging

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.sql import func

from database.engine_factory import create_db_engine

logger = logging.getLogger(__name__)

engine = create_db_engine()

db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


class LowIVSettings(Base):
    """Single-row (id=1) settings for the Low-IV BANKNIFTY strategy."""

    __tablename__ = "low_iv_settings"

    id = Column(Integer, primary_key=True)

    # Entry gates (mirror agentic_trading_v2 LOW_IV_LONG).
    iv_percentile_max = Column(Integer, nullable=False, default=25)
    debounce_ticks = Column(Integer, nullable=False, default=3)
    reentry_cooldown_min = Column(Integer, nullable=False, default=60)
    max_entry_hour = Column(Integer, nullable=False, default=14)
    profit_lock_enabled = Column(Boolean, nullable=False, default=True)
    profit_lock_threshold = Column(Integer, nullable=False, default=4000)

    # Scoped exits (backtest T4/S12/H15).
    exit_target_pct = Column(Float, nullable=False, default=4.0)
    exit_stop_pct = Column(Float, nullable=False, default=12.0)
    exit_max_hold_min = Column(Integer, nullable=False, default=15)

    # Liquidity / premium guards on the order path.
    premium_floor = Column(Float, nullable=False, default=50.0)
    max_spread_points = Column(Float, nullable=False, default=10.0)
    max_entries_per_day = Column(Integer, nullable=False, default=0)

    # Bollinger %B confirmation (0=lower/OS, 0.5=basis, 1=upper/OB).
    bb_enabled = Column(Boolean, nullable=False, default=True)
    bb_period = Column(Integer, nullable=False, default=20)
    bb_std = Column(Float, nullable=False, default=2.0)
    bb_bottom_pctb = Column(Float, nullable=False, default=0.20)
    bb_touch_tol_pct = Column(Float, nullable=False, default=0.15)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LowIVIVSeries(Base):
    """Timestamped ATM-IV samples for the current trading day (IST-naive).

    Both the /lowiv page (30s chain refresh) and the /python engine write
    here, so the IV window survives reloads/restarts and spans 09:15→now
    instead of a 2.5-minute in-memory deque. Garbage prints (IV <= 0 from a
    failed greeks inversion) are rejected at write time, never stored.
    """

    __tablename__ = "low_iv_iv_series"

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime(timezone=True), nullable=False, index=True)
    iv = Column(Float, nullable=False)
    # ATM contract snapshot for future trade replay (entries at ASK,
    # exits at BID). Nullable: rows written before this existed have IV only.
    strike = Column(Integer, nullable=True)
    bid = Column(Float, nullable=True)
    ask = Column(Float, nullable=True)
    # NULL = legacy ATM-mean row (the live signal series). 'CE'/'PE' = one
    # leg of the ±3 replay ladder around ATM.
    option_type = Column(String(2), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


def init_db():
    """Create the low-iv tables if they don't exist."""
    from database.db_init_helper import init_db_with_logging

    init_db_with_logging(Base, engine, "LowIV DB", logger)
    _migrate_add_guard_columns()
    _migrate_add_series_columns()


def _migrate_add_guard_columns():
    """Add order-guard columns introduced after the table's first release.

    Idempotent: skips columns that already exist. New installs get them from
    the model via create_all; existing installs get them here on boot.
    """
    try:
        from sqlalchemy import inspect, text

        inspector = inspect(engine)
        if "low_iv_settings" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("low_iv_settings")}
        wanted = {
            "premium_floor": "FLOAT",
            "max_spread_points": "FLOAT",
            "max_entries_per_day": "INTEGER",
            "bb_bottom_pctb": "FLOAT",
        }
        missing = {k: v for k, v in wanted.items() if k not in existing}
        if not missing:
            return
        with engine.begin() as conn:
            for col, typ in missing.items():
                conn.execute(text(f"ALTER TABLE low_iv_settings ADD COLUMN {col} {typ}"))
        # Backfill defaults on rows created before the columns existed.
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE low_iv_settings SET "
                    "premium_floor=COALESCE(premium_floor,50.0), "
                    "max_spread_points=COALESCE(max_spread_points,10.0), "
                    "max_entries_per_day=COALESCE(max_entries_per_day,0), "
                    "bb_bottom_pctb=COALESCE(bb_bottom_pctb,0.20)"
                )
            )
        logger.info("LowIV DB: added guard columns %s", sorted(missing))
    except Exception:
        logger.exception("LowIV DB: guard-column migration failed")


def _migrate_add_series_columns():
    """Add strike/bid/ask replay columns to low_iv_iv_series (idempotent).

    Early rows keep NULL quotes (IV-only); new writes fill them. No backfill
    possible — the quotes were never captured.
    """
    try:
        from sqlalchemy import inspect, text

        inspector = inspect(engine)
        if "low_iv_iv_series" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("low_iv_iv_series")}
        wanted = {"strike": "INTEGER", "bid": "FLOAT", "ask": "FLOAT", "option_type": "VARCHAR(2)"}
        missing = {k: v for k, v in wanted.items() if k not in existing}
        if not missing:
            return
        with engine.begin() as conn:
            for col, typ in missing.items():
                conn.execute(text(f"ALTER TABLE low_iv_iv_series ADD COLUMN {col} {typ}"))
        logger.info("LowIV DB: added series columns %s", sorted(missing))
    except Exception:
        logger.exception("LowIV DB: series-column migration failed")


def get_settings() -> dict:
    """Return the stored settings row as a dict, or {} when never saved."""
    try:
        row = db_session.query(LowIVSettings).filter(LowIVSettings.id == 1).first()
    finally:
        db_session.remove()
    if not row:
        return {}
    return {
        "iv_percentile_max": row.iv_percentile_max,
        "debounce_ticks": row.debounce_ticks,
        "reentry_cooldown_min": row.reentry_cooldown_min,
        "max_entry_hour": row.max_entry_hour,
        "profit_lock_enabled": bool(row.profit_lock_enabled),
        "profit_lock_threshold": row.profit_lock_threshold,
        "exit_target_pct": row.exit_target_pct,
        "exit_stop_pct": row.exit_stop_pct,
        "exit_max_hold_min": row.exit_max_hold_min,
        "premium_floor": row.premium_floor,
        "max_spread_points": row.max_spread_points,
        "max_entries_per_day": row.max_entries_per_day,
        "bb_enabled": bool(row.bb_enabled),
        "bb_period": row.bb_period,
        "bb_std": row.bb_std,
        "bb_bottom_pctb": row.bb_bottom_pctb,
        "bb_touch_tol_pct": row.bb_touch_tol_pct,
    }


def upsert_settings(clean: dict) -> dict:
    """Upsert the single settings row. `clean` must already be validated."""
    try:
        row = db_session.query(LowIVSettings).filter(LowIVSettings.id == 1).first()
        if not row:
            row = LowIVSettings(id=1)
            db_session.add(row)
        for key, value in clean.items():
            if hasattr(row, key):
                setattr(row, key, value)
        db_session.commit()
        result = get_settings()
    except Exception:
        db_session.rollback()
        logger.exception("LowIV DB: failed to save settings")
        raise
    finally:
        db_session.remove()
    return result


def _ist_today_start():
    """IST midnight (naive) — series timestamps are stored IST-naive."""
    from datetime import datetime

    import pytz

    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def _clean_quote(value) -> float | None:
    """Positive finite quote or None (never store garbage marks)."""
    import math

    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) and num > 0 else None


def record_iv(iv: float, strike=None, bid=None, ask=None, legs=None) -> int | None:
    """Store one ATM-IV sample + contract snapshot. Returns samples-today
    count, or None if rejected.

    Rejects non-positive / non-finite / absurd (>200) prints — a zero IV is a
    failed inversion, not cheap volatility, and must never enter the window.
    `legs` is the ±3 replay ladder ([{strike, option_type, iv, bid, ask}],
    max 20); each leg is validated identically and stored as its own row.
    The live signal reads ATM-mean rows only (option_type IS NULL), so legs
    can never pollute the percentile window.
    History is KEPT across days (365-day retention) so the series becomes a
    backtest dataset; the live signal always scopes to today via
    get_today_series().
    """
    import math

    try:
        value = float(iv)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0 or value > 200:
        return None
    try:
        strike_int = int(strike) if strike is not None else None
    except (TypeError, ValueError):
        strike_int = None
    try:
        from datetime import datetime, timedelta

        import pytz

        now = datetime.now(pytz.timezone("Asia/Kolkata")).replace(tzinfo=None)
        start = _ist_today_start()
        cutoff = (now - timedelta(days=365)).replace(hour=0, minute=0, second=0, microsecond=0)
        db_session.query(LowIVIVSeries).filter(LowIVIVSeries.ts < cutoff).delete()
        db_session.add(
            LowIVIVSeries(
                ts=now,
                iv=value,
                strike=strike_int,
                bid=_clean_quote(bid),
                ask=_clean_quote(ask),
            )
        )
        for leg in (legs or [])[:20]:
            if not isinstance(leg, dict):
                continue
            try:
                leg_iv = float(leg.get("iv"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(leg_iv) or leg_iv <= 0 or leg_iv > 200:
                continue
            try:
                leg_strike = int(leg.get("strike"))
            except (TypeError, ValueError):
                continue
            leg_type = (leg.get("option_type") or "").strip().upper()
            if leg_type not in ("CE", "PE"):
                continue
            db_session.add(
                LowIVIVSeries(
                    ts=now,
                    iv=leg_iv,
                    strike=leg_strike,
                    bid=_clean_quote(leg.get("bid")),
                    ask=_clean_quote(leg.get("ask")),
                    option_type=leg_type,
                )
            )
        # Absolute backstop (far above any realistic yearly volume).
        overflow = db_session.query(LowIVIVSeries).count() - 1000000
        if overflow > 0:
            stale_ids = [
                r.id
                for r in db_session.query(LowIVIVSeries.id)
                .order_by(LowIVIVSeries.id.asc())
                .limit(overflow)
                .all()
            ]
            db_session.query(LowIVIVSeries).filter(LowIVIVSeries.id.in_(stale_ids)).delete(
                synchronize_session=False
            )
        db_session.commit()
        count = (
            db_session.query(LowIVIVSeries)
            .filter(
                LowIVIVSeries.ts >= start,
                LowIVIVSeries.option_type.is_(None),
            )
            .count()
        )
    except Exception:
        db_session.rollback()
        logger.exception("LowIV DB: failed to record IV sample")
        raise
    finally:
        db_session.remove()
    return count


def get_today_series() -> list:
    """Today's ATM-mean IV samples as [(ts, iv)], ascending — the live signal
    window. Leg rows (option_type set) are excluded. Defensive filter kept
    at read time too, so legacy rows can never poison the percentile."""
    import math

    try:
        start = _ist_today_start()
        rows = (
            db_session.query(LowIVIVSeries)
            .filter(
                LowIVIVSeries.ts >= start,
                LowIVIVSeries.option_type.is_(None),
            )
            .order_by(LowIVIVSeries.ts.asc())
            .all()
        )
    finally:
        db_session.remove()
    return [
        (r.ts, r.iv)
        for r in rows
        if isinstance(r.iv, (int, float)) and math.isfinite(r.iv) and 0 < r.iv <= 200
    ]


def get_today_series_full() -> list:
    """Today's samples as dicts (ts/iv/strike/bid/ask), ascending — the future
    backtest feed: entries replayable at ASK, exits at BID."""
    import math

    try:
        start = _ist_today_start()
        rows = (
            db_session.query(LowIVIVSeries)
            .filter(LowIVIVSeries.ts >= start)
            .order_by(LowIVIVSeries.ts.asc())
            .all()
        )
    finally:
        db_session.remove()
    out = []
    for r in rows:
        if not (isinstance(r.iv, (int, float)) and math.isfinite(r.iv) and 0 < r.iv <= 200):
            continue
        out.append(
            {
                "ts": r.ts.isoformat() if r.ts else None,
                "iv": r.iv,
                "strike": r.strike,
                "bid": r.bid,
                "ask": r.ask,
                "option_type": r.option_type,
            }
        )
    return out
