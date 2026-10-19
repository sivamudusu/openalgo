"""Low-IV helpers ported from agentic_trading_v2 (stateless, no I/O).

Single source of truth for defaults lives in LOW_IV_DEFAULTS, mirroring
agentic_trading_v2/config/strategy.yaml LOW_IV_LONG + scoped exits T4/S12/H15.
"""

LOW_IV_DEFAULTS = {
    "underlying": "BANKNIFTY",
    "index_exchange": "NSE_INDEX",
    "fo_exchange": "NFO",
    "strategy_tag": "LowIV",
    "iv_percentile_max": 25,
    "debounce_ticks": 3,
    "reentry_cooldown_min": 60,
    "max_entry_hour": 14,
    "max_entries_per_day": 0,
    "profit_lock_enabled": True,
    "profit_lock_threshold": 4000,
    "exit_target_pct": 4,
    "exit_stop_pct": 12,
    "exit_max_hold_min": 15,
    "iv_history_len": 30,
    "strike_count": 7,
    # Liquidity / premium guards on the order path (mirror agentic_trading_v2
    # LIQUIDITY MAX_SPREAD + RISK PREMIUM_FLOOR).
    "premium_floor": 50,
    "max_spread_points": 10,
    # Bollinger %B confirmation. %B reads position inside the bands on a
    # 0–1 scale: 0 = lower band (oversold), 0.5 = basis (middle),
    # 1 = upper band (overbought) — the same oscillator broker charts show
    # as "Bollinger Bands %B 20 close 2 <value>".
    "bb_enabled": True,
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_bottom_pctb": 0.20,
    "bb_touch_tol_pct": 0.15,
}

# Full-day IV window guards. Percentile is meaningless before the series has
# statistical depth (first ~15 min of the session are warm-up, not signals).
MIN_IV_SAMPLES = 15

# UI-editable fields: name -> (kind, min, max). Bounds keep fat-finger values
# from silently disabling the strategy (e.g. target 0 or hold 1000m).
SETTINGS_SCHEMA = {
    "iv_percentile_max": ("int", 5, 50),
    "debounce_ticks": ("int", 1, 10),
    "reentry_cooldown_min": ("int", 0, 240),
    "max_entry_hour": ("int", 0, 15),
    "profit_lock_enabled": ("bool", None, None),
    "profit_lock_threshold": ("int", 0, 100000),
    "exit_target_pct": ("float", 0.5, 50.0),
    "exit_stop_pct": ("float", 0.5, 50.0),
    "exit_max_hold_min": ("int", 1, 120),
    "premium_floor": ("float", 0.0, 500.0),
    "max_spread_points": ("float", 1.0, 100.0),
    "max_entries_per_day": ("int", 0, 10),
    "bb_enabled": ("bool", None, None),
    "bb_period": ("int", 10, 50),
    "bb_std": ("float", 1.0, 3.0),
    "bb_bottom_pctb": ("float", 0.0, 0.5),
    "bb_touch_tol_pct": ("float", 0.0, 2.0),
}


def validate_settings(payload: dict) -> tuple[dict, dict]:
    """Validate a UI settings payload. Returns (clean, errors)."""
    clean: dict = {}
    errors: dict = {}
    for key, (kind, lo, hi) in SETTINGS_SCHEMA.items():
        if key not in payload:
            continue
        raw = payload[key]
        try:
            if kind == "bool":
                if isinstance(raw, bool):
                    clean[key] = raw
                elif isinstance(raw, str):
                    clean[key] = raw.strip().lower() in ("1", "true", "yes", "on")
                else:
                    clean[key] = bool(raw)
            elif kind == "int":
                val = int(float(raw))
                if (lo is not None and val < lo) or (hi is not None and val > hi):
                    errors[key] = f"Must be between {lo} and {hi}"
                else:
                    clean[key] = val
            else:
                val = float(raw)
                if (lo is not None and val < lo) or (hi is not None and val > hi):
                    errors[key] = f"Must be between {lo} and {hi}"
                else:
                    clean[key] = round(val, 2)
        except (TypeError, ValueError):
            errors[key] = f"Must be a valid {kind}"
    return clean, errors


def effective_settings(stored: dict | None = None) -> dict:
    """Merge DB-stored settings over defaults. Service stays I/O-free: the
    caller reads the stored dict (database/low_iv_db.get_settings)."""
    cfg = dict(LOW_IV_DEFAULTS)
    for key in SETTINGS_SCHEMA:
        if stored and stored.get(key) is not None:
            cfg[key] = stored[key]
    return cfg


def iv_percentile(current_iv: float, history: list) -> float | None:
    """Percentile rank of current_iv within history (0-100). None if empty."""
    vals = [float(v) for v in (history or []) if v is not None]
    try:
        cur = float(current_iv)
    except (TypeError, ValueError):
        return None
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return 50.0 if cur >= lo else 0.0
    pct = (cur - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, pct))


def evaluate_signal(iv_pct: float | None, below_count: int, config: dict | None = None) -> dict:
    """Pure signal check: below-threshold + debounced => fire."""
    cfg = {**LOW_IV_DEFAULTS, **(config or {})}
    if iv_pct is None:
        return {"fire": False, "reason": "No IV percentile data"}
    if iv_pct >= cfg["iv_percentile_max"]:
        return {
            "fire": False,
            "reason": f"IV percentile {iv_pct:.1f} >= {cfg['iv_percentile_max']}",
        }
    if below_count < cfg["debounce_ticks"]:
        return {
            "fire": False,
            "reason": f"IV {iv_pct:.1f} < {cfg['iv_percentile_max']} but only "
            f"{below_count}/{cfg['debounce_ticks']} confirmations",
        }
    return {
        "fire": True,
        "reason": f"Low IV percentile {iv_pct:.1f} < {cfg['iv_percentile_max']} "
        f"({below_count} consecutive) — buying cheap CE",
        "signal_type": "BUY_CALL",
        "option_type": "CE",
        "confidence": 0.85,
    }


def extract_atm_iv(chain: list) -> float | None:
    """Mean CE+PE IV at the ATM row of an option-chain response. None if absent."""
    if not chain:
        return None
    for row in chain:
        ce = (row or {}).get("ce") or {}
        pe = (row or {}).get("pe") or {}
        if ce.get("label") == "ATM" or pe.get("label") == "ATM":
            ivs = []
            for leg in (ce, pe):
                iv = leg.get("implied_volatility", leg.get("iv"))
                try:
                    if iv is not None:
                        ivs.append(float(iv))
                except (TypeError, ValueError):
                    continue
            if ivs:
                return sum(ivs) / len(ivs)
            return None
    return None


def normalize_expiry(expiry: str) -> str:
    """Normalize expiry to DDMMMYY uppercase (e.g. '10-JUL-25' -> '10JUL25')."""
    return (expiry or "").replace("-", "").replace(" ", "").upper()


def bollinger(closes: list, period: int = 20, std: float = 2.0) -> dict | None:
    """Bollinger Bands for the last two bars. Returns None if too few closes.

    Uses population std (divide by N), the trading-view convention. Result
    carries both the last and previous %B so callers can test "rising".
    """
    vals = []
    for c in closes or []:
        try:
            vals.append(float(c))
        except (TypeError, ValueError):
            continue
    if len(vals) < period + 1:
        return None
    out = []
    for window in (vals[-(period + 1) : -1], vals[-period:]):
        basis = sum(window) / period
        var = sum((x - basis) ** 2 for x in window) / period
        sd = var**0.5
        upper = basis + std * sd
        lower = basis - std * sd
        last = window[-1]
        denom = upper - lower
        pct_b = (last - lower) / denom if denom > 0 else 0.5
        out.append({"basis": basis, "upper": upper, "lower": lower, "pct_b": pct_b})
    prev, last = out
    return {
        "basis": last["basis"],
        "upper": last["upper"],
        "lower": last["lower"],
        "pct_b": last["pct_b"],
        "prev_pct_b": prev["pct_b"],
        "bandwidth_pct": ((last["upper"] - last["lower"]) / last["basis"] * 100)
        if last["basis"]
        else 0.0,
    }


def bb_gate(closes: list, config: dict | None = None) -> dict:
    """Bollinger %B entry gate: %B in the bottom zone AND rising toward 0.5.

    %B scale: 0 = lower band (oversold), 0.5 = basis, 1 = upper band
    (overbought) — the same oscillator broker charts show as
    "Bollinger Bands %B 20 close 2 <value>". Entry needs
    %B <= bb_bottom_pctb (default 0.20) with %B turning up (and the close
    itself up, so a flat %B on a flat line can't pass).
    Disabled via bb_enabled -> pass-through with reason.
    """
    cfg = {**LOW_IV_DEFAULTS, **(config or {})}
    if not cfg.get("bb_enabled", True):
        return {
            "pass": True,
            "reason": "Bollinger gate disabled",
            "at_bottom": None,
            "rising": None,
        }
    period = int(cfg.get("bb_period", 20))
    std = float(cfg.get("bb_std", 2.0))
    bottom = float(cfg.get("bb_bottom_pctb", 0.20))
    bb = bollinger(closes, period, std)
    if bb is None:
        return {
            "pass": False,
            "reason": f"Need {period + 1} closes for Bollinger %B({period},{std})",
            "at_bottom": None,
            "rising": None,
        }
    vals = [float(c) for c in closes if c is not None]
    at_bottom = bb["pct_b"] <= bottom
    rising = vals[-1] > vals[-2] and bb["pct_b"] > bb["prev_pct_b"]
    detail = {k: round(v, 2) for k, v in bb.items()}
    if not at_bottom:
        return {
            "pass": False,
            "reason": f"%B {bb['pct_b']:.2f} above bottom zone {bottom:.2f} (0=OS, 0.5=mid, 1=OB)",
            "at_bottom": at_bottom,
            "rising": rising,
            **detail,
        }
    if not rising:
        return {
            "pass": False,
            "reason": f"%B {bb['pct_b']:.2f} in bottom zone but not yet rising",
            "at_bottom": at_bottom,
            "rising": rising,
            **detail,
        }
    return {
        "pass": True,
        "reason": f"%B {bb['pct_b']:.2f} bottomed and rising toward 0.5",
        "at_bottom": at_bottom,
        "rising": rising,
        **detail,
    }


def combined_signal(
    iv_pct: float | None,
    below_count: int,
    closes: list,
    config: dict | None = None,
) -> dict:
    """Low-IV AND Bollinger gates must both pass to fire."""
    cfg = {**LOW_IV_DEFAULTS, **(config or {})}
    iv = evaluate_signal(iv_pct, below_count, cfg)
    bb = bb_gate(closes, cfg)
    if iv.get("fire") and bb.get("pass"):
        return {
            "fire": True,
            "reason": f"{iv['reason']} + {bb['reason']}",
            "signal_type": "BUY_CALL",
            "option_type": "CE",
            "confidence": 0.85,
            "iv": iv,
            "bb": bb,
        }
    blocker = iv if not iv.get("fire") else bb
    return {
        "fire": False,
        "reason": blocker.get("reason", "Waiting"),
        "iv": iv,
        "bb": bb,
    }
