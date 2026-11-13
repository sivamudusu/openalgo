"""Resolve a chart click into a tradable index option contract.

The chart's click price is the strike reference, not the current spot, because
that is what the trader pointed at. The resolver validates the whole chain:
supported underlying, live expiry, listed strike, master-contract metadata,
lot size and freeze quantity. Anything it cannot verify is refused here rather
than at the broker.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.option_symbol_service import (
    calculate_offset_strike_from_actual,
    construct_option_symbol,
    find_atm_strike_from_actual,
    find_option_in_database,
    get_available_strikes,
    get_option_exchange,
    validate_option_type,
)
from services.risk.bracket import (
    bias_for,
    risk_reward,
    validate_levels,
)
from services.risk.models import as_float, as_price
from utils.logging import get_logger

logger = get_logger(__name__)

#: Index underlyings the visual tool may trade. Kept here rather than imported
#: from a blueprint so a service never depends on the web layer. Mirrors
#: ``blueprints/scalping.py``; a new index must be added in both places.
SUPPORTED_UNDERLYINGS: dict[str, dict[str, str]] = {
    "NIFTY": {"index_exchange": "NSE_INDEX", "fo_exchange": "NFO"},
    "BANKNIFTY": {"index_exchange": "NSE_INDEX", "fo_exchange": "NFO"},
    "FINNIFTY": {"index_exchange": "NSE_INDEX", "fo_exchange": "NFO"},
    "MIDCPNIFTY": {"index_exchange": "NSE_INDEX", "fo_exchange": "NFO"},
    "NIFTYNXT50": {"index_exchange": "NSE_INDEX", "fo_exchange": "NFO"},
    "SENSEX": {"index_exchange": "BSE_INDEX", "fo_exchange": "BFO"},
    "BANKEX": {"index_exchange": "BSE_INDEX", "fo_exchange": "BFO"},
}

STRIKE_SELECTIONS = ("ATM", "ITM1", "ITM2", "OTM1", "OTM2", "CUSTOM")
MAX_LOTS = 20
MAX_QUANTITY = 100_000
DEFAULT_INDEX_TICK = 0.05
DEFAULT_OPTION_TICK = 0.05


class VisualBracketError(Exception):
    """A refusal that can be shown to the trader as written."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _normalise_action(value: Any) -> str:
    action = str(value or "").strip().upper()
    if action not in ("BUY", "SELL"):
        raise VisualBracketError("Action must be BUY or SELL.")
    return action


def _normalise_selection(value: Any) -> str:
    selection = str(value or "ATM").strip().upper().replace("_", "").replace("-", "")
    if selection not in STRIKE_SELECTIONS:
        raise VisualBracketError("Strike selection must be ATM, ITM1, ITM2, OTM1, OTM2 or CUSTOM.")
    return selection


def _expiry_candidates(value: Any) -> list[str]:
    """Every symbol-shaped expiry a caller's date might mean, most likely first."""
    if value is None or str(value).strip() == "":
        return []

    text = str(value).strip().upper().replace(" ", "")
    candidates: list[str] = []

    def add(item: str) -> None:
        item = item.upper().replace("-", "").replace(" ", "")
        if item and item not in candidates:
            candidates.append(item)

    add(text)

    parsed: datetime | None = None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(str(value).strip(), fmt)
            break
        except ValueError:
            continue

    if parsed is not None:
        add(parsed.strftime("%d%b%y").upper())
        add(parsed.strftime("%d%b%Y").upper())
    else:
        # A 7-character DDMMMYY also has a 9-character DDMMMYYYY spelling, and
        # the master contract may use either depending on the broker.
        if len(text) == 7:
            add(text[:-2] + "20" + text[-2:])
        elif len(text) == 9:
            add(text[:-4] + text[-2:])

    return candidates


def _index_tick_size(underlying: str, exchange: str) -> float:
    try:
        from database.symbol import SymToken
        from database.symbol import db_session as symbol_session

        try:
            row = (
                symbol_session.query(SymToken)
                .filter(SymToken.symbol == underlying, SymToken.exchange == exchange)
                .first()
            )
            tick = as_float(row.tick_size, DEFAULT_INDEX_TICK) if row else DEFAULT_INDEX_TICK
            return tick if tick > 0 else DEFAULT_INDEX_TICK
        finally:
            symbol_session.remove()
    except Exception:
        logger.debug("Could not read index tick size for %s/%s", underlying, exchange)
        return DEFAULT_INDEX_TICK


def _latest_expiry(underlying: str, fo_exchange: str, api_key: str) -> str:
    from services.expiry_service import get_expiry_dates

    ok, response, _code = get_expiry_dates(underlying, fo_exchange, "options", api_key)
    dates = response.get("data") if isinstance(response, dict) else None
    if not ok or not dates:
        raise VisualBracketError(
            f"No live option expiries were found for {underlying}. Download the master contract and try again.",
            404,
        )
    return str(dates[0])


def _resolve_expiry(
    underlying: str,
    fo_exchange: str,
    option_type: str,
    api_key: str,
    requested: Any,
) -> tuple[str, float, dict[str, Any]]:
    raw = (
        requested
        if requested not in (None, "")
        else _latest_expiry(underlying, fo_exchange, api_key)
    )
    candidates = _expiry_candidates(raw)
    if not candidates:
        raise VisualBracketError("Expiry is missing or not a date this platform understands.")

    last_error = "No listed strike was found for this expiry."
    for expiry in candidates:
        strikes = get_available_strikes(underlying, expiry, option_type, fo_exchange)
        if not strikes:
            continue
        return expiry, strikes[0], {"strikes": strikes}
    raise VisualBracketError(f"{last_error} Check the expiry and option type, then try again.", 404)


def _quantity(payload: dict[str, Any], lot_size: int) -> tuple[int, int]:
    lot = max(1, int(lot_size or 1))
    lots_raw = payload.get("lots")
    quantity_raw = payload.get("quantity")

    if lots_raw not in (None, ""):
        try:
            lots = int(lots_raw)
        except (TypeError, ValueError):
            raise VisualBracketError("Lots must be a whole number.") from None
        if lots < 1:
            raise VisualBracketError("Lots must be at least 1.")
        if lots > MAX_LOTS:
            raise VisualBracketError(f"Lots cannot exceed {MAX_LOTS} for one visual order.")
        quantity = lots * lot
    else:
        try:
            quantity = int(quantity_raw or 0)
        except (TypeError, ValueError):
            raise VisualBracketError("Quantity must be a whole number.") from None
        if quantity <= 0:
            raise VisualBracketError("Quantity must be positive.")
        if quantity % lot != 0:
            raise VisualBracketError(f"Quantity must be a whole number of lots ({lot}).")
        lots = quantity // lot

    if quantity > MAX_QUANTITY:
        raise VisualBracketError("Quantity is above the safety limit for one visual order.")
    return quantity, lots


def resolve_contract(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    """Resolve and validate one visual bracket request.

    Raises :class:`VisualBracketError` with a trader-readable message for every
    refusal. The returned dict is everything the database row and the order
    payload need.
    """
    underlying = str(payload.get("underlying") or "").strip().upper()
    cfg = SUPPORTED_UNDERLYINGS.get(underlying)
    if cfg is None:
        supported = ", ".join(SUPPORTED_UNDERLYINGS)
        raise VisualBracketError(f"Visual orders support these indices only: {supported}.")

    index_exchange = str(payload.get("index_exchange") or cfg["index_exchange"]).strip().upper()
    if index_exchange != cfg["index_exchange"]:
        raise VisualBracketError(
            f"{underlying} is quoted on {cfg['index_exchange']}, not {index_exchange}."
        )
    fo_exchange = cfg["fo_exchange"] or get_option_exchange(index_exchange)

    action = _normalise_action(payload.get("action"))
    try:
        option_type = validate_option_type(payload.get("option_type"))
    except ValueError as e:
        raise VisualBracketError(str(e)) from None

    bias = bias_for(action, option_type)
    if bias is None:
        raise VisualBracketError("Choose Buy CE, Buy PE, Sell CE or Sell PE.")

    click_price = as_price(payload.get("click_price"))
    if click_price is None:
        raise VisualBracketError("The chart did not report a usable click price.")
    entry_reference = as_price(payload.get("entry_reference")) or click_price
    target_price = as_price(payload.get("target_price"))
    stop_price = as_price(payload.get("stop_price"))
    index_tick = _index_tick_size(underlying, index_exchange)

    problems = validate_levels(bias, entry_reference, target_price, stop_price, index_tick)
    if problems:
        raise VisualBracketError(" ".join(problems))

    expiry, _first_strike, _meta = _resolve_expiry(
        underlying, fo_exchange, option_type, api_key, payload.get("expiry")
    )
    strikes = get_available_strikes(underlying, expiry, option_type, fo_exchange)
    atm_strike = find_atm_strike_from_actual(click_price, strikes)
    if atm_strike is None:
        raise VisualBracketError(
            f"No strikes were found near {click_price} for {underlying} {expiry}.", 404
        )

    selection = _normalise_selection(payload.get("strike_selection"))
    if selection == "CUSTOM":
        strike = as_price(payload.get("strike"))
        if strike is None:
            raise VisualBracketError("Enter a strike for a custom strike.")
        if strike not in strikes:
            raise VisualBracketError(
                f"Strike {strike} is not listed for {underlying} {expiry} {option_type}."
            )
    else:
        strike = calculate_offset_strike_from_actual(atm_strike, selection, option_type, strikes)
        if strike is None:
            raise VisualBracketError(
                f"{selection} is outside the listed strikes for {underlying} {expiry}."
            )

    contract_symbol = construct_option_symbol(underlying, expiry, strike, option_type)
    details = find_option_in_database(contract_symbol, fo_exchange)
    if not details:
        raise VisualBracketError(
            f"{contract_symbol} is not in the master contract. Update the master contract and try again.",
            404,
        )

    lot_size = int(details.get("lotsize") or 1)
    quantity, lots = _quantity(payload, lot_size)
    product = str(payload.get("product") or "MIS").strip().upper()
    if product not in ("MIS", "NRML"):
        raise VisualBracketError("Product must be MIS or NRML for an option order.")

    try:
        from database.qty_freeze_db import get_freeze_qty_for_option

        freeze_qty = int(get_freeze_qty_for_option(contract_symbol, fo_exchange) or 0)
    except Exception:
        logger.debug("Freeze quantity unavailable for %s", contract_symbol)
        freeze_qty = 0

    tick_size = as_float(details.get("tick_size"), DEFAULT_OPTION_TICK)
    if tick_size <= 0:
        tick_size = DEFAULT_OPTION_TICK

    target_offset = abs(float(target_price) - float(entry_reference))
    stop_offset = abs(float(entry_reference) - float(stop_price))

    return {
        "underlying_symbol": underlying,
        "underlying_exchange": index_exchange,
        "fo_exchange": fo_exchange,
        "contract_symbol": contract_symbol,
        "contract_exchange": fo_exchange,
        "option_type": option_type,
        "strike": float(strike),
        "expiry": expiry,
        "lot_size": lot_size,
        "lots": lots,
        "quantity": quantity,
        "tick_size": tick_size,
        "index_tick_size": index_tick,
        "freeze_qty": freeze_qty,
        "action": action,
        "product": product,
        "bias": bias.value,
        "click_price": float(click_price),
        "entry_reference": float(entry_reference),
        "target_price": float(target_price),
        "stop_price": float(stop_price),
        "target_offset": target_offset,
        "stop_offset": stop_offset,
        "risk_reward": risk_reward(entry_reference, target_price, stop_price),
    }
