"""Server-side engine for visual chart orders.

One process-wide engine owns every active visual bracket. It watches the
underlying index through the shared ``RiskTickFeed``, judges target and stop
with the pure ``services/risk`` core, and sends exactly one market exit when a
level is breached.

The safety shape is deliberate:

* a bracket's mode is fixed at creation, and every write goes through
  ``dispatch`` in that mode;
* the exit is claimed atomically in the database *before* any order is sent;
* a refused or failed exit releases the claim and leaves the bracket active;
* recovery reconciles against the broker's order and position books before it
  rearms or retries.
"""

from __future__ import annotations

import atexit
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from database import visual_bracket_db as store
from services.risk.bracket import Bias, bias_side, risk_reward, validate_levels
from services.risk.models import PositionRisk
from services.risk.position import evaluate_position
from services.visual_bracket import dispatch, resolver
from utils.logging import get_logger

logger = get_logger(__name__)

ENGINE_STRATEGY = "VisualBracket"
ALLOWED_STRATEGIES = {"VisualBracket", "Manual"}
TERMINAL_STATUSES = set(store.TERMINAL_STATUSES)
EDITABLE_STATUSES = set(store.EDITABLE_STATUSES)
EXIT_RETRY_SEC = 5.0
RECONCILE_SEC = 15.0
PLACING_STALE_SEC = 120.0
DEFAULT_INDEX_TICK = 0.05

_FILLED_STATUSES = {"complete", "completed", "filled", "executed", "traded"}
_REJECTED_STATUSES = {"rejected", "cancelled", "canceled", "expired"}
_WORKING_STATUSES = {
    "open",
    "new",
    "pending",
    "trigger pending",
    "triggered",
    "amended",
    "partially filled",
    "partiallyfilled",
}


def _status(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso_now() -> str:
    return _now().isoformat()


def _age_seconds(iso_value: Any) -> float:
    try:
        created = datetime.fromisoformat(str(iso_value))
    except (TypeError, ValueError):
        return 0.0
    if created.tzinfo is not None:
        created = created.astimezone(UTC).replace(tzinfo=None)
    return max(0.0, (_now() - created).total_seconds())


def _message(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("message") or "The broker refused the order.")
    return str(response or "The broker refused the order.")


class VisualBracketEngine:
    """Singleton state machine for visual chart orders."""

    _instance: VisualBracketEngine | None = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> VisualBracketEngine:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_once()
            return cls._instance

    def _init_once(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[int, dict[str, Any]] = {}
        self._by_underlying: dict[tuple[str, str], set[int]] = {}
        self._last_exit_attempt: dict[int, float] = {}
        self._exit_inflight: set[int] = set()
        self._subscribed_ids: set[int] = set()
        self._started = False
        self._reconciling = False
        self._recovered = False
        self._stop_event = threading.Event()
        self._reconciler_thread: threading.Thread | None = None
        self._price_unsubscribe = None
        self._order_updates_subscribed = False
        atexit.register(self.stop)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Bring the engine up. Idempotent."""
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
        self._recovered = False

        store.init_db()
        try:
            from services.strategy_module.tick_feed import get_risk_tick_feed

            self._price_unsubscribe = get_risk_tick_feed().add_price_hook(self.on_price)
        except Exception:
            logger.exception("Visual bracket engine could not register its price hook")

        try:
            from utils.event_bus import bus

            bus.subscribe(
                "order.update",
                self.on_order_update,
                name="VisualBracketOrderUpdates",
                critical=True,
            )
            self._order_updates_subscribed = True
        except Exception:
            logger.exception("Visual bracket engine could not subscribe to order updates")

        self._reconciler_thread = threading.Thread(
            target=self._reconcile_loop, name="visual-bracket-reconcile", daemon=True
        )
        self._reconciler_thread.start()
        self._recovered = self.recover()
        logger.info("Visual bracket engine started")

    def stop(self) -> None:
        """Release the feed hook, subscriptions and reconciler. Idempotent."""
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._stop_event.set()
            self._recovered = False
            thread = self._reconciler_thread
            self._reconciler_thread = None

        if self._price_unsubscribe is not None:
            try:
                self._price_unsubscribe()
            except Exception:
                pass
            self._price_unsubscribe = None
        if self._order_updates_subscribed:
            try:
                from utils.event_bus import bus

                bus.unsubscribe("order.update", self.on_order_update)
            except Exception:
                pass
            self._order_updates_subscribed = False

        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

        with self._lock:
            self._active.clear()
            self._by_underlying.clear()
            self._last_exit_attempt.clear()
            self._exit_inflight.clear()
            subscribed_ids = set(self._subscribed_ids)
            self._subscribed_ids.clear()
        for bracket_id in subscribed_ids:
            self._unsubscribe_id(bracket_id)

        try:
            atexit.unregister(self.stop)
        except Exception:
            pass

    def is_running(self) -> bool:
        with self._lock:
            return self._started

    # --------------------------------------------------------------- place / modify

    def place(
        self, payload: dict[str, Any], api_key: str, mode: str
    ) -> tuple[bool, dict[str, Any], int]:
        """Validate, resolve and place one visual bracket."""
        execution_mode = str(
            payload.get("execution_mode") or "SYNTHETIC_UNDERLYING_TRIGGER"
        ).upper()
        if execution_mode != "SYNTHETIC_UNDERLYING_TRIGGER":
            return (
                False,
                {
                    "status": "error",
                    "message": "This version supports underlying-triggered visual orders only.",
                },
                400,
            )

        strategy = str(payload.get("strategy") or ENGINE_STRATEGY).strip()
        if strategy not in ALLOWED_STRATEGIES:
            return (
                False,
                {
                    "status": "error",
                    "message": "This order source is not supported by the visual order tool.",
                },
                400,
            )

        token = str(payload.get("client_token") or "").strip() or uuid.uuid4().hex
        existing = store.find_by_client_token(token)
        if existing is not None:
            if existing["status"] in TERMINAL_STATUSES:
                return (
                    False,
                    {
                        "status": "error",
                        "message": "This visual order attempt has already finished. Place it again.",
                    },
                    409,
                )
            return True, {"status": "success", "data": existing}, 200

        resolved = resolver.resolve_contract(payload, api_key)
        row_data = {
            **resolved,
            "client_token": token,
            "mode": mode,
            "strategy": strategy,
            "status": "placing",
            "execution_mode": execution_mode,
            "entry_order_type": "MARKET",
            "entry_limit_price": None,
        }
        row = store.create_bracket(row_data)
        if row is None:
            return (
                False,
                {"status": "error", "message": "The visual order could not be saved."},
                500,
            )
        if row["status"] != "placing" or row["entry_order_id"]:
            # A concurrent request already moved it on; return what exists.
            return True, {"status": "success", "data": row}, 200

        ok, response, code = dispatch.place_entry(self._entry_order_data(row), api_key, mode)
        if not ok:
            message = _message(response)
            updated = store.update_bracket(row["id"], status="cancelled", error_message=message)
            store.record_event(row["id"], "place_failed", {"message": message})
            self._emit(updated or row)
            return False, response, code

        order_id = dispatch.response_order_id(response)
        if not order_id:
            message = (
                "The order was sent but the broker did not return an order id. "
                "Check the order book before trying again."
            )
            updated = store.update_bracket(row["id"], status="error", error_message=message)
            store.record_event(row["id"], "place_unconfirmed", {"message": message})
            self._emit(updated or row)
            return False, {"status": "error", "message": message}, 502

        updated = store.update_bracket(row["id"], status="working", entry_order_id=order_id)
        store.record_event(
            row["id"],
            "placed",
            {
                "order_id": order_id,
                "symbol": row["contract_symbol"],
                "quantity": row["quantity"],
            },
        )
        if updated is None:
            return (
                False,
                {
                    "status": "error",
                    "message": "The order was placed but the visual order could not be updated.",
                },
                500,
            )

        self._subscribe(updated)
        self._emit(updated)

        # A fast fill can beat the order-update event. Ask once, now.
        try:
            snapshot = dispatch.order_status(order_id, api_key, mode)
            if snapshot and self._is_filled(snapshot):
                self._activate(updated, snapshot.get("average_price"))
        except Exception:
            logger.debug("Visual bracket immediate fill check failed", exc_info=True)

        return True, {"status": "success", "data": store.get_bracket(row["id"]) or updated}, 200

    def modify(
        self, bracket_id: int, payload: dict[str, Any], api_key: str
    ) -> tuple[bool, dict[str, Any], int]:
        """Update the entry reference, target or stop on a live visual order."""
        row = store.get_bracket(bracket_id)
        if row is None:
            return False, {"status": "error", "message": "Visual order not found."}, 404
        if row["status"] not in EDITABLE_STATUSES:
            return (
                False,
                {
                    "status": "error",
                    "message": "This visual order is finished and cannot be changed.",
                },
                409,
            )

        bias = Bias(row["bias"])
        entry = row["entry_reference"]
        target = payload.get("target_price", row["target_price"])
        stop = payload.get("stop_price", row["stop_price"])
        problems = validate_levels(bias, entry, target, stop, DEFAULT_INDEX_TICK)
        if problems:
            return False, {"status": "error", "message": " ".join(problems)}, 400

        updated = store.update_bracket(
            bracket_id,
            entry_reference=float(entry),
            target_price=float(target),
            stop_price=float(stop),
            target_offset=abs(float(target) - float(entry)),
            stop_offset=abs(float(entry) - float(stop)),
            risk_reward=risk_reward(entry, target, stop),
            error_message=None,
        )
        if updated is None:
            return (
                False,
                {"status": "error", "message": "The visual order could not be updated."},
                500,
            )

        with self._lock:
            if bracket_id in self._active:
                self._active[bracket_id] = updated
        store.record_event(
            bracket_id,
            "modified",
            {
                "entry_reference": updated["entry_reference"],
                "target_price": updated["target_price"],
                "stop_price": updated["stop_price"],
            },
        )
        self._emit(updated)
        return True, {"status": "success", "data": updated}, 200

    def cancel(self, bracket_id: int, api_key: str) -> tuple[bool, dict[str, Any], int]:
        """Cancel a working entry. An active position must be exited instead."""
        row = store.get_bracket(bracket_id)
        if row is None:
            return False, {"status": "error", "message": "Visual order not found."}, 404
        if row["status"] in TERMINAL_STATUSES:
            return True, {"status": "success", "data": row}, 200
        if row["status"] == "active":
            return (
                False,
                {
                    "status": "error",
                    "message": "The entry has filled. Exit the position instead of cancelling it.",
                },
                409,
            )
        if not row["entry_order_id"]:
            return (
                False,
                {
                    "status": "error",
                    "message": "The entry order is still being placed. Wait for the broker acknowledgement.",
                },
                409,
            )

        ok, response, code = dispatch.cancel_entry(row["entry_order_id"], api_key, row["mode"])
        if not ok:
            return False, response, code

        net = self._open_position(row, api_key)
        if net is None:
            updated = store.update_bracket(
                bracket_id,
                status="error",
                error_message=(
                    "The entry order was cancelled, but the position book could not be read. "
                    "Check the position before placing another order."
                ),
            )
            store.record_event(
                bracket_id, "cancel_unconfirmed", {"order_id": row["entry_order_id"]}
            )
            self._emit(updated or row)
            return (
                False,
                {
                    "status": "error",
                    "message": "The entry order was cancelled, but the position could not be verified. Check the position book.",
                },
                502,
            )
        if net != 0:
            self._activate(row, row.get("entry_fill_price"))
            return (
                True,
                {
                    "status": "success",
                    "message": "The entry had already filled. The visual order is now watching it.",
                    "data": store.get_bracket(bracket_id) or row,
                },
                200,
            )

        updated = store.update_bracket(bracket_id, status="cancelled", error_message=None)
        self._forget(bracket_id)
        self._unsubscribe(row)
        store.record_event(bracket_id, "cancelled", {"order_id": row["entry_order_id"]})
        self._emit(updated or row)
        return True, {"status": "success", "data": updated or row}, 200

    def exit(self, bracket_id: int, api_key: str) -> tuple[bool, dict[str, Any], int]:
        """Ask the engine to flatten an active bracket at market."""
        row = store.get_bracket(bracket_id)
        if row is None:
            return False, {"status": "error", "message": "Visual order not found."}, 404
        if row["status"] != "active":
            return (
                False,
                {
                    "status": "error",
                    "message": "Only an active visual order can be exited.",
                },
                409,
            )
        if not self._request_exit(bracket_id, "manual"):
            return (
                False,
                {
                    "status": "error",
                    "message": "An exit is already in progress for this visual order.",
                },
                409,
            )
        return (
            True,
            {
                "status": "success",
                "message": "Exit requested. The position will be squared off at market.",
                "data": store.get_bracket(bracket_id) or row,
            },
            202,
        )

    def list_brackets(self, mode: str | None = None) -> list[dict[str, Any]]:
        # ``error`` is included so a bracket that needs a person stays visible
        # on the chart instead of disappearing into the database.
        return store.list_brackets(statuses=store.LIVE_STATUSES + ("error",), mode=mode)

    # ------------------------------------------------------------------ tick path

    def on_price(self, symbol: str, exchange: str, ltp: float) -> None:
        """Evaluate every active bracket that watches this underlying."""
        key = (str(symbol), str(exchange))
        with self._lock:
            ids = list(self._by_underlying.get(key, ()))

        for bracket_id in ids:
            with self._lock:
                row = self._active.get(bracket_id)
            if row is None:
                continue
            try:
                risk = PositionRisk(
                    identifier=str(bracket_id),
                    side=bias_side(Bias(row["bias"])),
                    entry_price=float(row["entry_reference"]),
                    quantity=float(row["quantity"]),
                    stop_price=float(row["stop_price"]),
                    target_price=float(row["target_price"]),
                )
                decision = evaluate_position(risk, ltp)
            except Exception:
                logger.exception("Visual bracket evaluation failed for %s", bracket_id)
                continue
            if decision.breached:
                reason = decision.reason.value if decision.reason else "sl"
                self._request_exit(bracket_id, reason)

    def _request_exit(self, bracket_id: int, kind: str) -> bool:
        """Start one exit worker, rate-limited per bracket."""
        now = time.monotonic()
        with self._lock:
            if bracket_id not in self._active:
                return False
            if bracket_id in self._exit_inflight:
                return False
            if now - self._last_exit_attempt.get(bracket_id, 0.0) < EXIT_RETRY_SEC:
                return False
            self._last_exit_attempt[bracket_id] = now
            self._exit_inflight.add(bracket_id)
        threading.Thread(
            target=self._exit_worker,
            args=(bracket_id, kind),
            name=f"visual-bracket-exit-{bracket_id}",
            daemon=True,
        ).start()
        return True

    def _exit_worker(self, bracket_id: int, kind: str) -> None:
        try:
            self._exit_worker_inner(bracket_id, kind)
        finally:
            with self._lock:
                self._exit_inflight.discard(bracket_id)

    def _exit_worker_inner(self, bracket_id: int, kind: str) -> None:
        api_key = self._background_api_key()
        if api_key is None:
            logger.error("Visual bracket exit %s deferred: no active API key", bracket_id)
            return

        row = store.get_bracket(bracket_id)
        if row is None:
            return

        if row["status"] == "exiting" and row.get("exit_kind"):
            # A restart or a previous worker already owns the claim. Retry the
            # dispatch without claiming a second time: the marker is the lock.
            kind = row["exit_kind"]
        else:
            if not store.claim_exit(bracket_id, kind):
                return
            row = store.get_bracket(bracket_id)
            if row is None:
                return

        net = dispatch.net_position(
            row["contract_symbol"],
            row["contract_exchange"],
            row["product"],
            api_key,
            row["mode"],
        )
        if net is None:
            self._exit_failed(row, "The broker position book could not be read.")
            return
        if net == 0:
            self._close(row, kind, "Position was already flat.")
            return

        action = "SELL" if net > 0 else "BUY"
        ok, response, _code, order_ids = dispatch.reducing_exit(
            row["contract_symbol"],
            row["contract_exchange"],
            row["product"],
            action,
            abs(net),
            api_key,
            row["mode"],
            lot_size=row["lot_size"],
            freeze_qty=row["freeze_qty"],
            strategy=row.get("strategy") or ENGINE_STRATEGY,
        )
        if not ok:
            self._exit_failed(row, _message(response))
            return

        order_id = order_ids[0] if order_ids else None
        updated = store.update_bracket(
            bracket_id,
            exit_order_id=order_id,
            exit_reason=kind,
            error_message=None,
        )
        store.record_event(
            bracket_id,
            "exit_sent",
            {
                "reason": kind,
                "action": action,
                "quantity": abs(net),
                "order_ids": order_ids,
            },
        )
        self._emit(updated or row)

    def _exit_failed(self, row: dict[str, Any], message: str) -> None:
        bracket_id = row["id"]
        fresh = store.get_bracket(bracket_id) or row
        kind = fresh.get("exit_kind") or fresh.get("exit_reason") or "sl"
        store.release_exit(bracket_id, kind)
        store.update_bracket(bracket_id, error_message=message)
        store.record_event(bracket_id, "exit_failed", {"message": message, "reason": kind})
        logger.error("Visual bracket exit failed for %s: %s", bracket_id, message)
        self._emit(store.get_bracket(bracket_id) or fresh)

    def _close(self, row: dict[str, Any], reason: str, detail: str = "") -> None:
        bracket_id = row["id"]
        updated = store.update_bracket(
            bracket_id,
            status="closed",
            closed_at=_now(),
            exit_reason=reason,
            error_message=None,
        )
        self._forget(bracket_id)
        self._unsubscribe(row)
        store.record_event(
            bracket_id,
            "closed",
            {"reason": reason, "detail": detail},
        )
        self._emit(updated or row)

    # --------------------------------------------------------------- order updates

    def on_order_update(self, event: Any) -> None:
        order_id = getattr(event, "orderid", None)
        if not order_id and isinstance(event, dict):
            order_id = event.get("orderid")
        if not order_id:
            return

        row = store.find_by_order_id(str(order_id))
        if row is None:
            return

        status = _status(getattr(event, "order_status", None))
        filled_qty = _int(getattr(event, "filled_quantity", 0))
        average = _float(getattr(event, "average_price", 0.0))
        reason = str(getattr(event, "rejection_reason", "") or "")

        if row["entry_order_id"] == str(order_id) and row["status"] in ("placing", "working"):
            if status in _FILLED_STATUSES or filled_qty > 0:
                self._activate(row, average)
            elif status in _REJECTED_STATUSES:
                self._cancel_from_event(row, reason or "The broker rejected the entry order.")
            return

        if row["exit_order_id"] == str(order_id) and row["status"] == "exiting":
            if status in _FILLED_STATUSES or filled_qty > 0:
                self._close(row, row.get("exit_reason") or "exit")
            elif status in _REJECTED_STATUSES:
                self._exit_failed(row, reason or "The broker rejected the exit order.")

    def _activate(self, row: dict[str, Any], fill_price: Any) -> None:
        bracket_id = row["id"]
        if bracket_id in self._active:
            return
        fields: dict[str, Any] = {"status": "active", "filled_at": _now(), "error_message": None}
        price = _float(fill_price)
        if price > 0:
            fields["entry_fill_price"] = price
        updated = store.update_bracket(bracket_id, **fields)
        if updated is None:
            return
        self._remember(updated)
        self._subscribe(updated)
        store.record_event(
            bracket_id, "filled", {"entry_fill_price": updated.get("entry_fill_price")}
        )
        self._emit(updated)

    def _cancel_from_event(self, row: dict[str, Any], message: str) -> None:
        bracket_id = row["id"]
        net = self._open_position(row, self._background_api_key())
        if net is None:
            updated = store.update_bracket(
                bracket_id,
                status="error",
                error_message=(
                    "The entry order is no longer working, but the position book could not be read. "
                    "Check the position before placing another order."
                ),
            )
            store.record_event(bracket_id, "cancel_unconfirmed", {"message": message})
            self._emit(updated or row)
            return
        if net != 0:
            self._activate(row, row.get("entry_fill_price"))
            return
        updated = store.update_bracket(bracket_id, status="cancelled", error_message=message)
        self._forget(bracket_id)
        self._unsubscribe(row)
        store.record_event(bracket_id, "cancelled", {"message": message})
        self._emit(updated or row)

    # -------------------------------------------------------------------- recovery

    def recover(self) -> bool:
        """Rebuild live state after a restart and reconcile with the broker."""
        api_key = self._background_api_key()
        if api_key is None:
            return False

        rows = store.list_brackets(statuses=store.LIVE_STATUSES)
        if not rows:
            return True
        logger.info("Visual bracket recovery: %d live row(s)", len(rows))

        for row in rows:
            try:
                self._recover_row(row, api_key)
            except Exception:
                logger.exception("Visual bracket recovery failed for %s", row.get("id"))
        return True

    def _recover_row(self, row: dict[str, Any], api_key: str) -> None:
        status = row["status"]

        if status == "placing":
            if not row["entry_order_id"]:
                if _age_seconds(row.get("created_at")) >= PLACING_STALE_SEC:
                    store.update_bracket(
                        row["id"],
                        status="error",
                        error_message=(
                            "The order was never confirmed by the broker. "
                            "Check the order book before placing it again."
                        ),
                    )
                    self._emit(store.get_bracket(row["id"]) or row)
                return
            status = "working"

        if status == "working":
            snapshot = dispatch.order_status(row["entry_order_id"], api_key, row["mode"])
            if snapshot and self._is_filled(snapshot):
                self._activate(row, snapshot.get("average_price"))
                return
            if snapshot and _status(snapshot.get("order_status")) in _REJECTED_STATUSES:
                self._cancel_from_event(
                    row,
                    str(
                        snapshot.get("rejection_reason") or "The entry order is no longer working."
                    ),
                )
                return
            self._subscribe(row)
            return

        net = dispatch.net_position(
            row["contract_symbol"],
            row["contract_exchange"],
            row["product"],
            api_key,
            row["mode"],
        )

        if status == "active":
            if net == 0:
                self._close(row, row.get("exit_reason") or "recovered", "Position is flat.")
            elif net is None:
                self._remember(row)
                self._subscribe(row)
                logger.warning(
                    "Visual bracket %s recovered without a readable position book", row["id"]
                )
            else:
                self._remember(row)
                self._subscribe(row)
            return

        if status == "exiting":
            if net == 0:
                self._close(row, row.get("exit_reason") or "recovered", "Exit completed.")
            elif net is not None:
                self._subscribe(row)
                if row.get("exit_order_id"):
                    # The claim and the exit order both survived the restart.
                    # Retry only when the order itself is no longer working.
                    self._request_retry_claimed(row["id"])
                else:
                    self._needs_attention(
                        row,
                        "An exit was sent but its order id was not recorded. "
                        "Check the order book and square off the position if it is still open.",
                    )

    def _retry_claimed_exit(self, bracket_id: int) -> None:
        api_key = self._background_api_key()
        if api_key is None:
            return
        row = store.get_bracket(bracket_id)
        if row is None or row["status"] != "exiting":
            return
        self._exit_worker(bracket_id, row.get("exit_reason") or "recovered")

    # ---------------------------------------------------------------- reconciler

    def _reconcile_loop(self) -> None:
        while not self._stop_event.wait(RECONCILE_SEC):
            try:
                self._reconcile_once()
            except Exception:
                logger.exception("Visual bracket reconciliation failed")

    def _reconcile_once(self) -> None:
        with self._lock:
            if self._reconciling:
                return
            self._reconciling = True
        try:
            api_key = self._background_api_key()
            if api_key is None:
                return
            if not self._recovered:
                self._recovered = self.recover()
                if not self._recovered:
                    return
            rows = store.list_brackets(statuses=store.LIVE_STATUSES)
            for row in rows:
                try:
                    self._reconcile_row(row, api_key)
                except Exception:
                    logger.exception("Visual bracket reconcile failed for %s", row.get("id"))
        finally:
            with self._lock:
                self._reconciling = False

    def _reconcile_row(self, row: dict[str, Any], api_key: str) -> None:
        if row["status"] == "placing" and not row["entry_order_id"]:
            if _age_seconds(row.get("created_at")) >= PLACING_STALE_SEC:
                updated = store.update_bracket(
                    row["id"],
                    status="error",
                    error_message=(
                        "The order was never confirmed by the broker. "
                        "Check the order book before placing it again."
                    ),
                )
                self._emit(updated or row)
            return

        if row["status"] == "working":
            snapshot = dispatch.order_status(row["entry_order_id"], api_key, row["mode"])
            if snapshot and self._is_filled(snapshot):
                self._activate(row, snapshot.get("average_price"))
            elif snapshot and _status(snapshot.get("order_status")) in _REJECTED_STATUSES:
                self._cancel_from_event(
                    row,
                    str(
                        snapshot.get("rejection_reason") or "The entry order is no longer working."
                    ),
                )
            return

        if row["status"] not in ("active", "exiting"):
            return

        net = dispatch.net_position(
            row["contract_symbol"],
            row["contract_exchange"],
            row["product"],
            api_key,
            row["mode"],
        )
        if net == 0:
            self._close(row, row.get("exit_reason") or "reconciled", "Position is flat.")
            return
        if net is None:
            return

        if row["status"] == "active":
            self._remember(row)
            return

        if not row.get("exit_order_id"):
            self._needs_attention(
                row,
                "An exit was sent but its order id was not recorded. "
                "Check the order book and square off the position if it is still open.",
            )
            return

        snapshot = dispatch.order_status(row["exit_order_id"], api_key, row["mode"])
        if snapshot and _status(snapshot.get("order_status")) in _REJECTED_STATUSES:
            self._exit_failed(
                row,
                str(snapshot.get("rejection_reason") or "The exit order was rejected."),
            )
            return
        if snapshot and _status(snapshot.get("order_status")) in _WORKING_STATUSES:
            return
        if snapshot is None:
            return
        self._request_retry_claimed(row["id"])

    def _needs_attention(self, row: dict[str, Any], message: str) -> None:
        """Stop guessing and leave the row visible for a person to resolve."""
        updated = store.update_bracket(row["id"], status="error", error_message=message)
        store.record_event(row["id"], "needs_attention", {"message": message})
        self._emit(updated or row)

    def _request_retry_claimed(self, bracket_id: int) -> None:
        now = time.monotonic()
        with self._lock:
            if bracket_id in self._exit_inflight:
                return
            if now - self._last_exit_attempt.get(bracket_id, 0.0) < EXIT_RETRY_SEC:
                return
            self._last_exit_attempt[bracket_id] = now
            self._exit_inflight.add(bracket_id)
        threading.Thread(
            target=self._retry_claimed_exit,
            args=(bracket_id,),
            name=f"visual-bracket-retry-{bracket_id}",
            daemon=True,
        ).start()

    # -------------------------------------------------------------------- helpers

    def _entry_order_data(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "strategy": row.get("strategy") or ENGINE_STRATEGY,
            "symbol": row["contract_symbol"],
            "exchange": row["contract_exchange"],
            "action": row["action"],
            "pricetype": "MARKET",
            "product": row["product"],
            "quantity": row["quantity"],
        }

    def _owner(self, bracket_id: int) -> str:
        return f"visual_bracket:{bracket_id}"

    def _subscribe(self, row: dict[str, Any]) -> None:
        try:
            from services.strategy_module.tick_feed import get_risk_tick_feed

            get_risk_tick_feed().add_subscription(
                self._owner(row["id"]),
                [(row["underlying_symbol"], row["underlying_exchange"])],
            )
            with self._lock:
                self._subscribed_ids.add(row["id"])
        except Exception:
            logger.exception("Visual bracket could not subscribe %s", row.get("id"))

    def _unsubscribe(self, row: dict[str, Any]) -> None:
        self._unsubscribe_id(row.get("id"))

    def _unsubscribe_id(self, bracket_id: Any) -> None:
        if bracket_id is None:
            return
        try:
            from services.strategy_module.tick_feed import get_risk_tick_feed

            get_risk_tick_feed().remove_subscription(self._owner(int(bracket_id)))
        except Exception:
            logger.debug("Visual bracket could not unsubscribe %s", bracket_id)
        with self._lock:
            self._subscribed_ids.discard(int(bracket_id))

    def _remember(self, row: dict[str, Any]) -> None:
        if row is None or row.get("status") != "active":
            return
        key = (row["underlying_symbol"], row["underlying_exchange"])
        with self._lock:
            self._active[row["id"]] = row
            self._by_underlying.setdefault(key, set()).add(row["id"])

    def _forget(self, bracket_id: int) -> None:
        with self._lock:
            row = self._active.pop(bracket_id, None)
            self._last_exit_attempt.pop(bracket_id, None)
            self._exit_inflight.discard(bracket_id)
            if row is None:
                return
            key = (row["underlying_symbol"], row["underlying_exchange"])
            ids = self._by_underlying.get(key)
            if ids is not None:
                ids.discard(bracket_id)
                if not ids:
                    self._by_underlying.pop(key, None)

    def _open_position(self, row: dict[str, Any], api_key: str | None) -> int | None:
        """Net position for a bracket, or None when it cannot be verified."""
        if api_key is None:
            return None
        return dispatch.net_position(
            row["contract_symbol"],
            row["contract_exchange"],
            row["product"],
            api_key,
            row["mode"],
        )

    def _background_api_key(self) -> str | None:
        try:
            from database.auth_db import get_first_available_api_key

            return get_first_available_api_key()
        except Exception:
            logger.exception("Visual bracket could not resolve an API key")
            return None

    def _emit(self, row: dict[str, Any] | None) -> None:
        if row is None:
            return
        try:
            from extensions import emit_from_any_thread

            emit_from_any_thread("bracket_update", row)
        except Exception:
            logger.debug("Visual bracket socket emit skipped", exc_info=True)

    def _is_filled(self, snapshot: dict[str, Any]) -> bool:
        status = _status(snapshot.get("order_status"))
        filled = _int(snapshot.get("filled_quantity"))
        return status in _FILLED_STATUSES or filled > 0


def _float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


_engine: VisualBracketEngine | None = None
_engine_lock = threading.Lock()


def get_visual_bracket_engine() -> VisualBracketEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = VisualBracketEngine()
        return _engine


def start_visual_bracket_engine() -> None:
    get_visual_bracket_engine().start()


def stop_visual_bracket_engine() -> None:
    get_visual_bracket_engine().stop()
