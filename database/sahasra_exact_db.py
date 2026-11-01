"""Persistence for the Sahasra exact model panel.

Three tables, all in ``openalgo.db``:

- ``sahasra_exact_snapshots``: one end-of-day option-chain capture per
  underlying, trade date and expiry.
- ``sahasra_exact_chain``: the captured strikes for one snapshot.
- ``sahasra_exact_context``: the trader's pre-open bias for one session.

The module follows the project's SQLite rules: the canonical engine factory
(NullPool), a scoped session registered in ``utils/db_sessions.py``, and a
``finally: db_session.remove()`` on every public read or write.
"""

import json
from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from database.engine_factory import create_db_engine
from utils.logging import get_logger

logger = get_logger(__name__)

engine = create_db_engine()

db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


def _utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def _json_dumps(value):
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        logger.exception("Sahasra exact DB: could not serialize JSON")
        return "{}"


def _json_loads(value, default):
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


class SahasraExactSnapshot(Base):
    __tablename__ = "sahasra_exact_snapshots"

    id = Column(Integer, primary_key=True)
    underlying = Column(String(20), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    expiry = Column(String(16), nullable=False)
    spot = Column(Float, nullable=True)
    atm_strike = Column(Float, nullable=True)
    pcr = Column(Float, nullable=True)
    total_call_oi = Column(BigInteger, nullable=True)
    total_put_oi = Column(BigInteger, nullable=True)
    coverage = Column(String(24), nullable=False, default="window_50")
    strikes_captured = Column(Integer, nullable=False, default=0)
    prev_day_high = Column(Float, nullable=True)
    prev_day_low = Column(Float, nullable=True)
    prev_day_close = Column(Float, nullable=True)
    levels_json = Column(Text, nullable=True)
    captured_at = Column(DateTime, nullable=False, default=_utc_now)
    source = Column(String(24), nullable=False, default="eod")
    status = Column(String(16), nullable=False, default="complete")
    warning = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "underlying", "trade_date", "expiry", name="uq_sahasra_exact_snapshot_key"
        ),
        Index("ix_sahasra_exact_snapshot_lookup", "underlying", "trade_date", "expiry"),
    )


class SahasraExactChain(Base):
    __tablename__ = "sahasra_exact_chain"

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, nullable=False, index=True)
    strike = Column(Float, nullable=False)
    ce_oi = Column(BigInteger, nullable=False, default=0)
    pe_oi = Column(BigInteger, nullable=False, default=0)
    ce_volume = Column(BigInteger, nullable=False, default=0)
    pe_volume = Column(BigInteger, nullable=False, default=0)
    ce_ltp = Column(Float, nullable=False, default=0.0)
    pe_ltp = Column(Float, nullable=False, default=0.0)
    ce_symbol = Column(String(80), nullable=True)
    pe_symbol = Column(String(80), nullable=True)
    lotsize = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "strike", name="uq_sahasra_exact_chain_strike"),
    )


class SahasraExactContext(Base):
    __tablename__ = "sahasra_exact_context"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, unique=True, index=True)
    cues_json = Column(Text, nullable=True)
    final_bias = Column(String(16), nullable=False, default="unset")
    confidence = Column(String(16), nullable=False, default="low")
    notes = Column(Text, nullable=True)
    captured_at = Column(DateTime, nullable=False, default=_utc_now)
    updated_at = Column(DateTime, nullable=False, default=_utc_now, onupdate=_utc_now)


def init_db():
    """Create the three tables when they do not exist."""
    from database.db_init_helper import init_db_with_logging

    init_db_with_logging(Base, engine, "Sahasra Exact DB", logger)


def _snapshot_dict(row, chain=None):
    if row is None:
        return None
    levels = _json_loads(row.levels_json, {})
    return {
        "id": row.id,
        "underlying": row.underlying,
        "trade_date": row.trade_date.isoformat() if row.trade_date else None,
        "expiry": row.expiry,
        "spot": row.spot,
        "atm_strike": row.atm_strike,
        "pcr": row.pcr,
        "total_call_oi": int(row.total_call_oi or 0),
        "total_put_oi": int(row.total_put_oi or 0),
        "coverage": row.coverage,
        "strikes_captured": int(row.strikes_captured or 0),
        "prev_day_high": row.prev_day_high,
        "prev_day_low": row.prev_day_low,
        "prev_day_close": row.prev_day_close,
        "levels": levels,
        "captured_at": row.captured_at.isoformat() if row.captured_at else None,
        "source": row.source,
        "status": row.status,
        "warning": row.warning,
        "chain": chain or [],
    }


def _chain_rows(snapshot_id):
    rows = (
        db_session.query(SahasraExactChain)
        .filter(SahasraExactChain.snapshot_id == snapshot_id)
        .order_by(SahasraExactChain.strike)
        .all()
    )
    return [
        {
            "strike": row.strike,
            "ce_oi": int(row.ce_oi or 0),
            "pe_oi": int(row.pe_oi or 0),
            "ce_volume": int(row.ce_volume or 0),
            "pe_volume": int(row.pe_volume or 0),
            "ce_ltp": row.ce_ltp,
            "pe_ltp": row.pe_ltp,
            "ce_symbol": row.ce_symbol,
            "pe_symbol": row.pe_symbol,
            "lotsize": row.lotsize,
        }
        for row in rows
    ]


def save_snapshot(header, chain_rows):
    """Insert or replace one snapshot and its strikes. Returns the snapshot id."""
    snapshot_id = None
    try:
        row = (
            db_session.query(SahasraExactSnapshot)
            .filter(
                SahasraExactSnapshot.underlying == header["underlying"],
                SahasraExactSnapshot.trade_date == header["trade_date"],
                SahasraExactSnapshot.expiry == header["expiry"],
            )
            .first()
        )
        if row is None:
            row = SahasraExactSnapshot(
                underlying=header["underlying"],
                trade_date=header["trade_date"],
                expiry=header["expiry"],
            )
            db_session.add(row)
        else:
            db_session.execute(
                delete(SahasraExactChain).where(SahasraExactChain.snapshot_id == row.id)
            )

        for field in (
            "spot",
            "atm_strike",
            "pcr",
            "total_call_oi",
            "total_put_oi",
            "coverage",
            "strikes_captured",
            "prev_day_high",
            "prev_day_low",
            "prev_day_close",
            "levels_json",
            "source",
            "status",
            "warning",
        ):
            if field in header:
                setattr(row, field, header[field])
        row.captured_at = header.get("captured_at") or _utc_now()
        db_session.flush()
        snapshot_id = row.id

        for item in chain_rows:
            db_session.add(
                SahasraExactChain(
                    snapshot_id=snapshot_id,
                    strike=float(item["strike"]),
                    ce_oi=int(item.get("ce_oi") or 0),
                    pe_oi=int(item.get("pe_oi") or 0),
                    ce_volume=int(item.get("ce_volume") or 0),
                    pe_volume=int(item.get("pe_volume") or 0),
                    ce_ltp=float(item.get("ce_ltp") or 0),
                    pe_ltp=float(item.get("pe_ltp") or 0),
                    ce_symbol=item.get("ce_symbol"),
                    pe_symbol=item.get("pe_symbol"),
                    lotsize=item.get("lotsize"),
                )
            )
        db_session.commit()
    except Exception:
        db_session.rollback()
        logger.exception("Sahasra exact DB: failed to save snapshot")
        raise
    finally:
        db_session.remove()
    return snapshot_id


def get_snapshot(underlying, trade_date, expiry=None):
    """Return one snapshot with its chain, or None."""
    try:
        query = db_session.query(SahasraExactSnapshot).filter(
            SahasraExactSnapshot.underlying == underlying,
            SahasraExactSnapshot.trade_date == trade_date,
        )
        if expiry:
            query = query.filter(SahasraExactSnapshot.expiry == expiry)
        row = query.order_by(SahasraExactSnapshot.captured_at.desc()).first()
        if row is None:
            return None
        return _snapshot_dict(row, _chain_rows(row.id))
    finally:
        db_session.remove()


def get_latest_snapshot_before(underlying, before_date, expiry=None):
    """Return the most recent complete snapshot strictly before ``before_date``."""
    try:
        query = db_session.query(SahasraExactSnapshot).filter(
            SahasraExactSnapshot.underlying == underlying,
            SahasraExactSnapshot.trade_date < before_date,
            SahasraExactSnapshot.source == "eod",
            SahasraExactSnapshot.status.in_(("complete", "partial")),
        )
        if expiry:
            query = query.filter(SahasraExactSnapshot.expiry == expiry)
        row = query.order_by(
            SahasraExactSnapshot.trade_date.desc(),
            SahasraExactSnapshot.captured_at.desc(),
        ).first()
        if row is None:
            return None
        return _snapshot_dict(row, _chain_rows(row.id))
    finally:
        db_session.remove()


def latest_snapshot_date(underlying, before_date, expiry=None):
    row = get_latest_snapshot_before(underlying, before_date, expiry)
    return row["trade_date"] if row else None


def has_complete_snapshot(underlying, trade_date):
    try:
        row = (
            db_session.query(SahasraExactSnapshot.id)
            .filter(
                SahasraExactSnapshot.underlying == underlying,
                SahasraExactSnapshot.trade_date == trade_date,
                SahasraExactSnapshot.source == "eod",
                SahasraExactSnapshot.status == "complete",
            )
            .first()
        )
        return row is not None
    finally:
        db_session.remove()


def list_recent_snapshots(limit=12):
    try:
        rows = (
            db_session.query(SahasraExactSnapshot)
            .order_by(
                SahasraExactSnapshot.trade_date.desc(),
                SahasraExactSnapshot.captured_at.desc(),
            )
            .limit(max(1, min(int(limit), 100)))
            .all()
        )
        return [_snapshot_dict(row) for row in rows]
    finally:
        db_session.remove()


def save_context(trade_date, cues, final_bias, confidence, notes):
    """Insert or update one session context row."""
    try:
        row = (
            db_session.query(SahasraExactContext)
            .filter(SahasraExactContext.trade_date == trade_date)
            .first()
        )
        if row is None:
            row = SahasraExactContext(trade_date=trade_date)
            db_session.add(row)
        row.cues_json = _json_dumps(cues or {})
        row.final_bias = final_bias or "unset"
        row.confidence = confidence or "low"
        row.notes = notes or ""
        row.updated_at = _utc_now()
        if row.captured_at is None:
            row.captured_at = row.updated_at
        db_session.commit()
        return get_context(trade_date)
    except Exception:
        db_session.rollback()
        logger.exception("Sahasra exact DB: failed to save context")
        raise
    finally:
        db_session.remove()


def get_context(trade_date):
    """Return one context row as a dict, or None."""
    try:
        row = (
            db_session.query(SahasraExactContext)
            .filter(SahasraExactContext.trade_date == trade_date)
            .first()
        )
        if row is None:
            return None
        return {
            "trade_date": row.trade_date.isoformat() if row.trade_date else None,
            "cues": _json_loads(row.cues_json, {}),
            "final_bias": row.final_bias or "unset",
            "confidence": row.confidence or "low",
            "notes": row.notes or "",
            "captured_at": row.captured_at.isoformat() if row.captured_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    finally:
        db_session.remove()


def ist_today():
    from datetime import timedelta, timezone

    return (datetime.now(UTC) + timedelta(hours=5, minutes=30)).date()


def as_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
