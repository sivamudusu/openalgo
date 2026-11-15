"""Persistence for the Sahasra signal log.

One table, ``sahasra_signal``. It records every signal the model produced
together with the level map that produced it, and what the signal turned into.

The point is evidence. A rule that has never been measured is a belief, and
the platform's own standard is that a model is judged on fifty or more live
trades rather than on one session's backtest. That needs a durable record of
what was signalled and what happened, so the hit rate can be read off rather
than remembered.

One row per signal. The level map is stored alongside it as JSON because the
map is what made the trade: without it, a hit rate cannot be broken down by
the wall that produced it, by the volatility that was paid, or by which side
of the gamma flip the market was on.

Timestamps are naive UTC, matching the strategy module's convention.

All SQLite access goes through ``database.engine_factory.create_db_engine()``
(NullPool), and every helper clears its scoped session before returning.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from database.engine_factory import create_db_engine
from utils.logging import get_logger

logger = get_logger(__name__)

engine = create_db_engine()
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()

#: The outcomes a signal can end in.
OUTCOME_TARGET = "target"
OUTCOME_STOP = "stop"
OUTCOME_MAX_HOLD = "max_hold"
OUTCOME_RECLAIM = "reclaim"
OUTCOME_CANCELLED = "cancelled"

#: How many closed signals the platform asks for before a model is judged on
#: its live record rather than on a single session.
EVIDENCE_TARGET = 50


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SahasraSignal(Base):
    """One signal the model produced, and what became of it."""

    __tablename__ = "sahasra_signal"

    id = Column(Integer, primary_key=True, index=True)
    underlying = Column(String(16), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=_now)

    #: The setup the state machine read.
    state = Column(String(32), nullable=False)
    #: The leg the plan pointed at.
    side = Column(String(4), nullable=False)
    #: Whether the closed candle confirmed the setup.
    confirmed = Column(Boolean, nullable=False, default=False)

    #: The level map as it stood when the signal fired.
    level_map = Column(JSON, nullable=True)
    #: The market context as it stood when the signal fired.
    market_context = Column(JSON, nullable=True)

    entry_spot = Column(Float, nullable=True)
    entry_premium = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=True)

    #: What ended the trade, and the premium result it produced.
    outcome = Column(String(24), nullable=True)
    outcome_at = Column(DateTime, nullable=True)
    pnl_pct = Column(Float, nullable=True)


def init_db() -> None:
    """Create the table. Safe to call on every start."""
    Base.metadata.create_all(bind=engine)


def record_signal(
    underlying: str,
    state: str,
    side: str,
    *,
    trade_date: date | None = None,
    confirmed: bool = False,
    level_map: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
    entry_spot: float | None = None,
    entry_premium: float | None = None,
    quantity: int | None = None,
) -> int | None:
    """Record a signal and return its row id, or None when it could not be kept.

    A signal that cannot be written must not stop the trade, so the write is
    best effort and the caller carries on either way.
    """
    try:
        row = SahasraSignal(
            underlying=underlying,
            trade_date=trade_date or _now().date(),
            state=state,
            side=side,
            confirmed=bool(confirmed),
            level_map=level_map,
            market_context=market_context,
            entry_spot=entry_spot,
            entry_premium=entry_premium,
            quantity=quantity,
        )
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        signal_id = row.id
        db_session.remove()
        return signal_id
    except IntegrityError:
        db_session.rollback()
        db_session.remove()
        return None
    except Exception:
        logger.exception("Could not record the Sahasra signal")
        db_session.rollback()
        db_session.remove()
        return None


def close_signal(
    signal_id: int,
    outcome: str,
    *,
    pnl_pct: float | None = None,
    outcome_at: datetime | None = None,
) -> bool:
    """Record what a signal turned into."""
    try:
        row = db_session.query(SahasraSignal).filter(SahasraSignal.id == signal_id).first()
        if row is None:
            db_session.remove()
            return False
        row.outcome = outcome
        row.pnl_pct = pnl_pct
        row.outcome_at = outcome_at or _now()
        db_session.commit()
        db_session.remove()
        return True
    except Exception:
        logger.exception("Could not close the Sahasra signal")
        db_session.rollback()
        db_session.remove()
        return False


def signal_evidence(underlying: str, since: date | None = None) -> dict[str, Any]:
    """The live record: how many signals, how many closed, how many reached target.

    The verdict is only given once there are enough closed signals to mean
    something. Below that the numbers are reported as a count and no rate is
    claimed, because a rate computed from a handful of trades reads as a
    result when it is not one.
    """
    since = since or date(1970, 1, 1)
    try:
        rows = (
            db_session.query(SahasraSignal)
            .filter(
                SahasraSignal.underlying == underlying,
                SahasraSignal.trade_date >= since,
            )
            .all()
        )
        closed = [row for row in rows if row.outcome]
        targets = [row for row in closed if row.outcome == OUTCOME_TARGET]
        stops = [row for row in closed if row.outcome == OUTCOME_STOP]
        wins = [row for row in closed if (row.pnl_pct or 0) > 0]
        enough = len(closed) >= EVIDENCE_TARGET
        db_session.remove()
        return {
            "underlying": underlying,
            "signals": len(rows),
            "closed": len(closed),
            "targets": len(targets),
            "stops": len(stops),
            "wins": len(wins),
            "hit_rate": round(len(wins) / len(closed), 4) if closed else None,
            "target_rate": round(len(targets) / len(closed), 4) if closed else None,
            "evidence_target": EVIDENCE_TARGET,
            "enough_evidence": enough,
        }
    except Exception:
        logger.exception("Could not read the Sahasra signal evidence")
        db_session.rollback()
        db_session.remove()
        return {
            "underlying": underlying,
            "signals": 0,
            "closed": 0,
            "targets": 0,
            "stops": 0,
            "wins": 0,
            "hit_rate": None,
            "target_rate": None,
            "evidence_target": EVIDENCE_TARGET,
            "enough_evidence": False,
        }


def recent_signals(underlying: str, limit: int = 50) -> list[dict[str, Any]]:
    """The most recent signals for an underlying, newest first."""
    try:
        rows = (
            db_session.query(SahasraSignal)
            .filter(SahasraSignal.underlying == underlying)
            .order_by(SahasraSignal.id.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        out = [
            {
                "id": row.id,
                "trade_date": row.trade_date.isoformat() if row.trade_date else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "state": row.state,
                "side": row.side,
                "confirmed": row.confirmed,
                "entry_spot": row.entry_spot,
                "outcome": row.outcome,
                "pnl_pct": row.pnl_pct,
            }
            for row in rows
        ]
        db_session.remove()
        return out
    except Exception:
        logger.exception("Could not read the recent Sahasra signals")
        db_session.rollback()
        db_session.remove()
        return []
