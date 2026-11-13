"""Order dispatch for visual bracket orders.

The bracket's ``mode`` is fixed when the order is placed. Every write goes
through this module so a global analyzer toggle can never divert a live
position's exit into the sandbox, where it would report success while the real
position stayed open.

Reads have the same split: a live bracket reads the live position and order
books directly through the broker token, even while the platform is in analyze
mode. The sandbox bracket reads the sandbox managers directly.
"""

from __future__ import annotations

from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

STRATEGY = "VisualBracket"
LIVE = "live"
SANDBOX = "sandbox"


def response_order_id(response: Any) -> str | None:
    """The order id from a place/modify response, in either supported shape."""
    if not isinstance(response, dict):
        return None
    order_id = response.get("orderid")
    if not order_id and isinstance(response.get("data"), dict):
        order_id = response["data"].get("orderid")
    return str(order_id) if order_id else None


def _original_data(order_data: dict[str, Any], api_key: str) -> dict[str, Any]:
    return {**order_data, "apikey": api_key}


def _live_auth(api_key: str) -> tuple[str | None, str | None]:
    from database.auth_db import get_auth_token_broker

    token, broker = get_auth_token_broker(api_key)
    if not token or not broker:
        return None, None
    return token, broker


def place_entry(
    order_data: dict[str, Any], api_key: str, mode: str
) -> tuple[bool, dict[str, Any], int]:
    """Place the option entry in the bracket's own mode."""
    if mode == SANDBOX:
        from services.sandbox_service import sandbox_place_order

        return sandbox_place_order(order_data, api_key, _original_data(order_data, api_key))

    from services.place_order_service import place_order_with_auth

    token, broker = _live_auth(api_key)
    if token is None:
        return (
            False,
            {
                "status": "error",
                "message": "Your broker session is not active. Log in to the broker and try again.",
            },
            401,
        )
    return place_order_with_auth(
        order_data,
        token,
        broker,
        _original_data(order_data, api_key),
        force_live=True,
    )


def place_exit(
    order_data: dict[str, Any], api_key: str, mode: str
) -> tuple[bool, dict[str, Any], int]:
    """Place one market exit chunk. Same mode guarantee as the entry."""
    return place_entry(order_data, api_key, mode)


def modify_entry(
    order_data: dict[str, Any], api_key: str, mode: str
) -> tuple[bool, dict[str, Any], int]:
    """Modify a working entry order.

    The v1 entry is MARKET, so this is unused today. It refuses rather than
    guessing when the platform's mode has moved away from the bracket's mode.
    """
    if mode == SANDBOX:
        from services.sandbox_service import sandbox_modify_order

        return sandbox_modify_order(order_data, api_key, _original_data(order_data, api_key))

    from database.settings_db import get_analyze_mode

    if get_analyze_mode():
        return (
            False,
            {
                "status": "error",
                "message": "Switch OpenAlgo back to Live mode before changing a live order.",
            },
            409,
        )

    from services.modify_order_service import modify_order

    return modify_order(order_data, api_key=api_key)


def cancel_entry(orderid: str, api_key: str, mode: str) -> tuple[bool, dict[str, Any], int]:
    """Cancel a working entry order in the bracket's own mode."""
    if mode == SANDBOX:
        from services.sandbox_service import sandbox_cancel_order

        return sandbox_cancel_order(
            {"orderid": orderid}, api_key, {"orderid": orderid, "apikey": api_key}
        )

    from database.settings_db import get_analyze_mode

    if get_analyze_mode():
        return (
            False,
            {
                "status": "error",
                "message": "Switch OpenAlgo back to Live mode before cancelling a live order.",
            },
            409,
        )

    from services.cancel_order_service import cancel_order

    return cancel_order({"orderid": orderid}, api_key=api_key)


def net_position(
    symbol: str,
    exchange: str,
    product: str,
    api_key: str,
    mode: str,
) -> int | None:
    """Signed net quantity for one contract, or None when the book is unreadable."""
    if mode == SANDBOX:
        from services.sandbox_service import sandbox_get_positions

        ok, response, _code = sandbox_get_positions(api_key, {"apikey": api_key})
    else:
        from services.positionbook_service import get_positionbook

        token, broker = _live_auth(api_key)
        if token is None:
            return None
        ok, response, _code = get_positionbook(auth_token=token, broker=broker)

    if not ok or not isinstance(response, dict):
        return None

    rows = response.get("data")
    if not isinstance(rows, list):
        return None

    total = 0
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(row.get("exchange") or "").upper() != exchange.upper():
            continue
        if product and str(row.get("product") or "").upper() != product.upper():
            continue
        try:
            total += int(float(row.get("quantity", row.get("netqty", 0)) or 0))
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else 0


def order_status(orderid: str, api_key: str, mode: str) -> dict[str, Any] | None:
    """The broker's snapshot of one order, or None when it cannot be read."""
    if mode == SANDBOX:
        from services.sandbox_service import sandbox_get_order_status

        ok, response, _code = sandbox_get_order_status(
            {"orderid": orderid}, api_key, {"orderid": orderid, "apikey": api_key}
        )
    else:
        from services.orderstatus_service import get_order_status

        token, broker = _live_auth(api_key)
        if token is None:
            return None
        ok, response, _code = get_order_status(
            {"orderid": orderid}, auth_token=token, broker=broker
        )

    if not ok or not isinstance(response, dict):
        return None
    data = response.get("data")
    return data if isinstance(data, dict) else response


def exit_chunks(quantity: int, lot_size: int, freeze_qty: int | None) -> list[int]:
    """Split an exit into whole-lot chunks that respect the exchange freeze."""
    if quantity <= 0:
        return []
    lot = max(1, int(lot_size or 1))
    if quantity % lot != 0:
        return []
    freeze = int(freeze_qty or 0)
    chunk = (freeze // lot) * lot if freeze > 0 else quantity
    if chunk <= 0:
        chunk = quantity
    return [min(chunk, quantity - start) for start in range(0, quantity, chunk)]


def reducing_exit(
    symbol: str,
    exchange: str,
    product: str,
    action: str,
    quantity: int,
    api_key: str,
    mode: str,
    lot_size: int = 1,
    freeze_qty: int | None = None,
    strategy: str = STRATEGY,
) -> tuple[bool, dict[str, Any], int, list[str]]:
    """Flatten a position with freeze-safe, whole-lot market orders.

    Returns the first failure when a chunk fails. Some chunks may already have
    filled; the caller must re-read the position book before retrying rather
    than resending the original quantity.
    """
    chunks = exit_chunks(quantity, lot_size, freeze_qty)
    if not chunks:
        return (
            False,
            {
                "status": "error",
                "message": f"Exit quantity {quantity} is not a whole number of lots ({lot_size}).",
            },
            400,
            [],
        )

    order_ids: list[str] = []
    for chunk in chunks:
        order_data = {
            "strategy": strategy or STRATEGY,
            "symbol": symbol,
            "exchange": exchange,
            "action": action,
            "pricetype": "MARKET",
            "product": product,
            "quantity": chunk,
        }
        ok, response, code = place_exit(order_data, api_key, mode)
        if not ok:
            return ok, response, code, order_ids
        order_id = response_order_id(response)
        if order_id:
            order_ids.append(order_id)

    return True, {"status": "success", "orderids": order_ids}, 200, order_ids
