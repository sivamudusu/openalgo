"""Persistence for visual chart orders (draggable bracket orders).

Two tables, both ``vb_`` prefixed. The prefix matters: the platform already
carries strategy, scalping and GTT state, and a future reader must not confuse
an index-triggered visual order with any of them.

- ``vb_bracket``       one row per staged or live visual order
- ``vb_bracket_event`` append-only audit trail for every state change

Timestamps are naive UTC, matching the strategy module's convention. The API
boundary renders them as ISO strings.

All SQLite access goes through ``database.engine_factory.create_db_engine()``
(NullPool), and every helper clears its scoped session before returning. The
engine calls these helpers from background threads as well as request threads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.sql import func

from database.engine_factory import create_db_engine
from utils.logging import get_logger

logger = get_logger(__name__)

engine = create_db_engine()
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()

#: A bracket that still needs the engine's attention after a restart.
LIVE_STATUSES = ("placing", "working", "active", "exiting")
#: A bracket that is finished, one way or another.
TERMINAL_STATUSES = ("closed", "cancelled", "error")
#: Statuses that may be modified from the chart.
EDITABLE_STATUSES = ("placing", "working", "active")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class VisualBracket(Base):
    """One visual order: an option position triggered by an index level."""

    __tablename__ = "vb_bracket"
    __table_args__ = (UniqueConstraint("client_token", name="uq_vb_client_token"),)

    id = Column(Integer, primary_key=True, index=True)
    client_token = Column(String(64), nullable=False)
    mode = Column(String(10), nullable=False, default="analyze")
    strategy = Column(
        String(32), nullable=False, default="VisualBracket", server_default="VisualBracket"
    )
    status = Column(String(12), nullable=False, default="placing", index=True)

    underlying_symbol = Column(String(40), nullable=False, index=True)
    underlying_exchange = Column(String(10), nullable=False)
    fo_exchange = Column(String(10), nullable=False)

    contract_symbol = Column(String(80), nullable=False, index=True)
    contract_exchange = Column(String(10), nullable=False)
    option_type = Column(String(2), nullable=False)
    strike = Column(Float, nullable=False)
    expiry = Column(String(20), nullable=False)
    lot_size = Column(Integer, nullable=False, default=1)
    tick_size = Column(Float, nullable=False, default=0.05)
    freeze_qty = Column(Integer, nullable=True)

    action = Column(String(4), nullable=False)
    product = Column(String(10), nullable=False, default="MIS")
    quantity = Column(Integer, nullable=False, default=0)
    entry_order_type = Column(String(10), nullable=False, default="MARKET")
    entry_limit_price = Column(Float, nullable=True)
    entry_order_id = Column(String(64), nullable=True, index=True)
    entry_fill_price = Column(Float, nullable=True)

    execution_mode = Column(String(40), nullable=False, default="SYNTHETIC_UNDERLYING_TRIGGER")
    bias = Column(String(10), nullable=False)
    click_price = Column(Float, nullable=False)
    entry_reference = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    target_offset = Column(Float, nullable=False, default=0.0)
    stop_offset = Column(Float, nullable=False, default=0.0)
    risk_reward = Column(Float, nullable=False, default=0.0)

    exit_kind = Column(String(12), nullable=True)
    exit_order_id = Column(String(64), nullable=True, index=True)
    exit_reason = Column(String(20), nullable=True)
    error_message = Column(Text, nullable=True)

    filled_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    last_reconciled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class VisualBracketEvent(Base):
    """Append-only audit trail for one bracket."""

    __tablename__ = "vb_bracket_event"

    id = Column(Integer, primary_key=True, index=True)
    bracket_id = Column(Integer, nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


def init_db() -> None:
    """Create the visual bracket tables if they do not exist."""
    from database.db_init_helper import init_db_with_logging

    init_db_with_logging(Base, engine, "Visual Bracket DB", logger)


def _to_dict(row: VisualBracket) -> dict[str, Any]:
    return {
        "id": row.id,
        "client_token": row.client_token,
        "mode": row.mode,
        "strategy": row.strategy,
        "status": row.status,
        "underlying_symbol": row.underlying_symbol,
        "underlying_exchange": row.underlying_exchange,
        "fo_exchange": row.fo_exchange,
        "contract_symbol": row.contract_symbol,
        "contract_exchange": row.contract_exchange,
        "option_type": row.option_type,
        "strike": row.strike,
        "expiry": row.expiry,
        "lot_size": row.lot_size,
        "tick_size": row.tick_size,
        "freeze_qty": row.freeze_qty,
        "action": row.action,
        "product": row.product,
        "quantity": row.quantity,
        "entry_order_type": row.entry_order_type,
        "entry_limit_price": row.entry_limit_price,
        "entry_order_id": row.entry_order_id,
        "entry_fill_price": row.entry_fill_price,
        "execution_mode": row.execution_mode,
        "bias": row.bias,
        "click_price": row.click_price,
        "entry_reference": row.entry_reference,
        "target_price": row.target_price,
        "stop_price": row.stop_price,
        "target_offset": row.target_offset,
        "stop_offset": row.stop_offset,
        "risk_reward": row.risk_reward,
        "exit_kind": row.exit_kind,
        "exit_order_id": row.exit_order_id,
        "exit_reason": row.exit_reason,
        "error_message": row.error_message,
        "filled_at": _iso(row.filled_at),
        "closed_at": _iso(row.closed_at),
        "last_reconciled_at": _iso(row.last_reconciled_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _session_remove() -> None:
    db_session.remove()


def create_bracket(data: dict[str, Any]) -> dict[str, Any] | None:
    """Insert a bracket, or return the existing row for a repeated client token."""
    token = str(data.get("client_token") or "").strip()
    if not token:
        logger.error("create_bracket refused: client_token is required")
        return None

    existing = find_by_client_token(token)
    if existing is not None:
        return existing

    row = VisualBracket(**{k: v for k, v in data.items() if hasattr(VisualBracket, k)})
    try:
        db_session.add(row)
        db_session.commit()
        return _to_dict(row)
    except IntegrityError:
        db_session.rollback()
        return find_by_client_token(token)
    except Exception as e:
        logger.exception("Error creating visual bracket: %s", e)
        db_session.rollback()
        return None
    finally:
        _session_remove()


def find_by_client_token(client_token: str) -> dict[str, Any] | None:
    try:
        row = db_session.query(VisualBracket).filter_by(client_token=client_token).first()
        return _to_dict(row) if row else None
    except Exception as e:
        logger.exception("Error reading visual bracket by token: %s", e)
        db_session.rollback()
        return None
    finally:
        _session_remove()


def get_bracket(bracket_id: int) -> dict[str, Any] | None:
    try:
        row = db_session.query(VisualBracket).filter_by(id=bracket_id).first()
        return _to_dict(row) if row else None
    except Exception as e:
        logger.exception("Error reading visual bracket %s: %s", bracket_id, e)
        db_session.rollback()
        return None
    finally:
        _session_remove()


def find_by_order_id(order_id: str) -> dict[str, Any] | None:
    """A bracket whose entry or exit order carries ``order_id``."""
    if not order_id:
        return None
    try:
        row = (
            db_session.query(VisualBracket)
            .filter(
                (VisualBracket.entry_order_id == order_id)
                | (VisualBracket.exit_order_id == order_id)
            )
            .first()
        )
        return _to_dict(row) if row else None
    except Exception as e:
        logger.exception("Error reading visual bracket by order id: %s", e)
        db_session.rollback()
        return None
    finally:
        _session_remove()


def list_brackets(
    *,
    statuses: tuple[str, ...] | None = None,
    mode: str | None = None,
    underlying_symbol: str | None = None,
    underlying_exchange: str | None = None,
) -> list[dict[str, Any]]:
    try:
        query = db_session.query(VisualBracket)
        if statuses:
            query = query.filter(VisualBracket.status.in_(statuses))
        if mode:
            query = query.filter(VisualBracket.mode == mode)
        if underlying_symbol:
            query = query.filter(VisualBracket.underlying_symbol == underlying_symbol)
        if underlying_exchange:
            query = query.filter(VisualBracket.underlying_exchange == underlying_exchange)
        return [_to_dict(row) for row in query.order_by(VisualBracket.id.asc()).all()]
    except Exception as e:
        logger.exception("Error listing visual brackets: %s", e)
        db_session.rollback()
        return []
    finally:
        _session_remove()


def update_bracket(bracket_id: int, **fields: Any) -> dict[str, Any] | None:
    """Update allow-listed fields on one bracket and return the fresh row."""
    allowed = set(_to_dict_columns())
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return get_bracket(bracket_id)

    patch.setdefault("updated_at", _now())
    try:
        updated = (
            db_session.query(VisualBracket)
            .filter(VisualBracket.id == bracket_id)
            .update(patch, synchronize_session=False)
        )
        db_session.commit()
        return get_bracket(bracket_id) if updated else None
    except Exception as e:
        logger.exception("Error updating visual bracket %s: %s", bracket_id, e)
        db_session.rollback()
        return None
    finally:
        _session_remove()


def claim_exit(bracket_id: int, kind: str, *, statuses: tuple[str, ...] = ("active",)) -> bool:
    """Atomically claim the one exit for a bracket.

    The status check, the ``exit_kind`` write and the transition to ``exiting``
    are one UPDATE, so two rules firing on the same tick cannot both send a
    covering order. The marker is written before the exit order is dispatched,
    never after it returns.
    """
    try:
        claimed = (
            db_session.query(VisualBracket)
            .filter(
                VisualBracket.id == bracket_id,
                VisualBracket.status.in_(statuses),
                VisualBracket.exit_kind.is_(None),
            )
            .update(
                {"exit_kind": kind, "exit_reason": kind, "status": "exiting", "updated_at": _now()},
                synchronize_session=False,
            )
        )
        db_session.commit()
        return claimed == 1
    except Exception as e:
        logger.exception("Error claiming visual bracket exit %s: %s", bracket_id, e)
        db_session.rollback()
        return False
    finally:
        _session_remove()


def release_exit(bracket_id: int, kind: str) -> bool:
    """Release a refused exit so the bracket stays active and can retry.

    A refused dispatch must not leave the bracket looking closed while the
    broker still holds the position, and it must not leave the claim set or the
    bracket would never be watched again.
    """
    try:
        released = (
            db_session.query(VisualBracket)
            .filter(
                VisualBracket.id == bracket_id,
                VisualBracket.status == "exiting",
                VisualBracket.exit_kind == kind,
            )
            .update(
                {"exit_kind": None, "exit_reason": None, "status": "active", "updated_at": _now()},
                synchronize_session=False,
            )
        )
        db_session.commit()
        return released == 1
    except Exception as e:
        logger.exception("Error releasing visual bracket exit %s: %s", bracket_id, e)
        db_session.rollback()
        return False
    finally:
        _session_remove()


def record_event(bracket_id: int, kind: str, detail: dict[str, Any] | None = None) -> None:
    try:
        db_session.add(VisualBracketEvent(bracket_id=bracket_id, kind=kind, detail=detail or {}))
        db_session.commit()
    except Exception as e:
        logger.exception("Error recording visual bracket event: %s", e)
        db_session.rollback()
    finally:
        _session_remove()


def list_events(bracket_id: int, limit: int = 100) -> list[dict[str, Any]]:
    try:
        rows = (
            db_session.query(VisualBracketEvent)
            .filter_by(bracket_id=bracket_id)
            .order_by(VisualBracketEvent.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": row.id,
                "bracket_id": row.bracket_id,
                "kind": row.kind,
                "detail": row.detail or {},
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ]
    except Exception as e:
        logger.exception("Error listing visual bracket events: %s", e)
        db_session.rollback()
        return []
    finally:
        _session_remove()


def _to_dict_columns() -> tuple[str, ...]:
    """Column names accepted by :func:`update_bracket`."""
    return tuple(
        column.name
        for column in VisualBracket.__table__.columns
        if column.name not in {"id", "client_token", "created_at"}
    )
