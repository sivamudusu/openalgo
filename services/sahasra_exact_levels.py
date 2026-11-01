"""Pure level engine for the Sahasra exact model.

No database, broker, clock or logging here. The caller supplies the previous
session's chain, the previous day's OHLC, optional swing levels and optional
15-minute rejection counts. The result is a deterministic level map that can be
tested without a running OpenAlgo instance.
"""

from math import floor

#: Weights for the transparent candidate score. They sum to 1.0.
WEIGHT_OI = 0.40
WEIGHT_REJECTION = 0.25
WEIGHT_CONFLUENCE = 0.20
WEIGHT_PROXIMITY = 0.15

#: How far from spot a candidate is considered near enough to matter.
PROXIMITY_BAND = 0.03

#: Strikes closer than this fraction of spot are merged.
MERGE_BAND = 0.0005

#: Top OI candidates kept per side before round, prior-day and swing levels.
TOP_OI_PER_SIDE = 8


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def strike_step(spot):
    """Return (minor, major) round-number steps for an index spot."""
    if spot >= 40000:
        return 100.0, 500.0
    return 50.0, 100.0


def compute_pcr(chain):
    """Window PCR from a list of strike rows with ce_oi and pe_oi."""
    total_put = 0
    total_call = 0
    for row in chain or []:
        total_put += max(0, _int(row.get("pe_oi")))
        total_call += max(0, _int(row.get("ce_oi")))
    if total_call <= 0:
        return None
    return total_put / total_call


def classify_regime(pcr):
    """The model's rule: below 1 is bearish, above 1 is bullish."""
    if pcr is None:
        return "unknown"
    if pcr < 1.0:
        return "bearish"
    if pcr > 1.0:
        return "bullish"
    return "neutral"


def _add_candidate(candidates, strike, *, call_oi=0, put_oi=0, tags=(), rejections=0, tolerance):
    strike = round(_num(strike), 2)
    if strike <= 0:
        return
    existing = None
    for item in candidates:
        if abs(item["strike"] - strike) <= tolerance:
            existing = item
            break
    if existing is None:
        existing = {
            "strike": strike,
            "call_oi": 0,
            "put_oi": 0,
            "tags": set(),
            "rejections": 0,
        }
        candidates.append(existing)
    if call_oi > existing["call_oi"]:
        existing["call_oi"] = _int(call_oi)
        existing["strike"] = strike
    if put_oi > existing["put_oi"]:
        existing["put_oi"] = _int(put_oi)
        existing["strike"] = strike
    existing["tags"].update(tags)
    existing["rejections"] = max(existing["rejections"], _int(rejections))


def _local_peaks(rows, key):
    peaks = set()
    for index in range(1, len(rows) - 1):
        current = _int(rows[index].get(key))
        left = _int(rows[index - 1].get(key))
        right = _int(rows[index + 1].get(key))
        if current > 0 and current >= left and current >= right:
            peaks.add(_num(rows[index].get("strike")))
    return peaks


def _round_levels(spot, minor, major):
    low = spot * (1 - PROXIMITY_BAND)
    high = spot * (1 + PROXIMITY_BAND)
    first = int(floor(low / minor)) * minor
    value = first
    levels = []
    while value <= high:
        if value > 0:
            levels.append((float(value), "round_major" if value % major == 0 else "round_minor"))
        value += minor
    return levels


def _rejection_for(strike, rejection_counts):
    for key, value in (rejection_counts or {}).items():
        try:
            if abs(float(key) - strike) <= 0.01:
                return _int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _trim_public(items, required, limit=6):
    selected = {item["strike"]: item for item in items if item["strike"] in required}
    for item in sorted(items, key=lambda row: (-row["score"], row["strike"]))[:limit]:
        selected[item["strike"]] = item
    return sorted(selected.values(), key=lambda row: row["strike"])


def build_level_map(
    spot,
    chain,
    *,
    prior_day=None,
    swing_levels=None,
    rejection_counts=None,
    expiration=None,
):
    """Build the labelled level map.

    Args:
        spot: Current or snapshot underlying price.
        chain: One row per strike with at least ``strike``, ``ce_oi`` and
            ``pe_oi``. Optional symbols and volumes are passed through.
        prior_day: Optional dict with ``high``, ``low`` and ``close``.
        swing_levels: Optional iterable of recent daily swing prices.
        rejection_counts: Optional mapping of strike to failed 15-minute
            touches.
        expiration: Optional expiry label returned in the result.

    Returns:
        A dict with supports, resistances, primary and major labels, the
        no-trade zone, PCR, regime, ATM symbols and metadata.
    """
    spot = _num(spot)
    rows = []
    for row in chain or []:
        strike = _num(row.get("strike"))
        if strike <= 0:
            continue
        rows.append({**row, "strike": strike})
    rows.sort(key=lambda row: row["strike"])
    if spot <= 0 or not rows:
        return {
            "spot": spot or None,
            "expiry": expiration,
            "pcr": None,
            "regime": "unknown",
            "supports": [],
            "resistances": [],
            "primary_support": None,
            "temporary_support": None,
            "major_support": None,
            "primary_resistance": None,
            "major_resistance": None,
            "no_trade_zone": None,
            "atm_strike": None,
            "atm_ce_symbol": None,
            "atm_pe_symbol": None,
            "atm_lotsize": None,
            "warning": "No option-chain strikes were available.",
        }

    minor, major = strike_step(spot)
    tolerance = max(0.01, spot * MERGE_BAND)
    candidates = []
    call_peaks = _local_peaks(rows, "ce_oi")
    put_peaks = _local_peaks(rows, "pe_oi")

    for row in rows:
        strike = row["strike"]
        call_oi = _int(row.get("ce_oi"))
        put_oi = _int(row.get("pe_oi"))
        rejections = _rejection_for(strike, rejection_counts)
        tags = []
        if call_oi > 0:
            tags.append("call_wall")
        if put_oi > 0:
            tags.append("put_wall")
        if strike in call_peaks:
            tags.append("call_peak")
        if strike in put_peaks:
            tags.append("put_peak")
        _add_candidate(
            candidates,
            strike,
            call_oi=call_oi,
            put_oi=put_oi,
            tags=tags,
            rejections=rejections,
            tolerance=tolerance,
        )

    for strike, tag in _round_levels(spot, minor, major):
        _add_candidate(candidates, strike, tags=(tag,), tolerance=tolerance)

    if prior_day:
        for key, tag in (("high", "pdh"), ("low", "pdl"), ("close", "pdc")):
            level = _num(prior_day.get(key))
            if level > 0:
                _add_candidate(candidates, level, tags=(tag,), tolerance=tolerance)

    for level in swing_levels or []:
        level = _num(level)
        if level > 0:
            _add_candidate(candidates, level, tags=("swing",), tolerance=tolerance)

    supports = []
    resistances = []
    for item in candidates:
        item["side"] = "support" if item["strike"] <= spot else "resistance"
        (supports if item["side"] == "support" else resistances).append(item)

    def score_side(items):
        max_oi = (
            max(
                (item["call_oi"] if item["side"] == "resistance" else item["put_oi"])
                for item in items
            )
            if items
            else 0
        )
        max_rejections = max((item["rejections"] for item in items), default=0)
        for item in items:
            oi = item["call_oi"] if item["side"] == "resistance" else item["put_oi"]
            item["oi"] = oi
            item["oi_norm"] = (oi / max_oi) if max_oi > 0 else 0.0
            item["rejection_norm"] = (
                (item["rejections"] / max_rejections) if max_rejections > 0 else 0.0
            )
            confluence = 0.0
            if "round_major" in item["tags"]:
                confluence = 1.0
            elif "round_minor" in item["tags"]:
                confluence = 0.5
            if item["tags"] & {"pdh", "pdl", "pdc", "swing"}:
                confluence = min(1.0, confluence + 0.5)
            item["confluence"] = confluence
            item["proximity"] = max(0.0, 1 - abs(item["strike"] - spot) / (PROXIMITY_BAND * spot))
            item["score"] = round(
                WEIGHT_OI * item["oi_norm"]
                + WEIGHT_REJECTION * item["rejection_norm"]
                + WEIGHT_CONFLUENCE * confluence
                + WEIGHT_PROXIMITY * item["proximity"],
                4,
            )
            item["distance_pct"] = round(abs(item["strike"] - spot) / spot * 100, 3)
        items.sort(key=lambda item: item["strike"])
        return items

    supports = score_side(supports)
    resistances = score_side(resistances)

    support_pool = sorted(supports, key=lambda item: (-item["put_oi"], -item["score"]))
    put_wall = next((item for item in support_pool if item["put_oi"] > 0), None)
    primary_support = put_wall
    major_support = put_wall
    temporary_support = None
    if supports:
        alternatives = [item for item in supports if item is not primary_support]
        if alternatives:
            temporary_support = max(alternatives, key=lambda item: item["score"])

    max_call = max((item["call_oi"] for item in resistances), default=0)
    threshold = max_call * 0.5
    meaningful = [item for item in resistances if item["call_oi"] >= threshold]
    if meaningful:
        primary_resistance = max(meaningful, key=lambda item: item["score"])
    elif resistances:
        primary_resistance = max(resistances, key=lambda item: (item["call_oi"], item["score"]))
    else:
        primary_resistance = None
    major_resistance = max(resistances, key=lambda item: item["call_oi"]) if max_call > 0 else None

    labels = {}
    if primary_support:
        labels[primary_support["strike"]] = "major_support"
    if temporary_support:
        labels[temporary_support["strike"]] = "temporary_support"
    if primary_resistance:
        labels[primary_resistance["strike"]] = "primary_resistance"
    if major_resistance and major_resistance is not primary_resistance:
        labels[major_resistance["strike"]] = "major_resistance"

    def public_item(item):
        return {
            "strike": item["strike"],
            "label": labels.get(item["strike"], "candidate"),
            "tier": (
                "major"
                if item["strike"]
                in {
                    primary_support["strike"] if primary_support else None,
                    major_resistance["strike"] if major_resistance else None,
                }
                else "primary"
                if item["strike"] in {primary_resistance["strike"] if primary_resistance else None}
                else "watch"
            ),
            "score": item["score"],
            "oi": item["oi"],
            "call_oi": item["call_oi"],
            "put_oi": item["put_oi"],
            "rejections": item["rejections"],
            "tags": sorted(item["tags"]),
            "distance_pct": item["distance_pct"],
        }

    support_list = _trim_public(
        [public_item(item) for item in supports],
        {
            primary_support["strike"] if primary_support else None,
            temporary_support["strike"] if temporary_support else None,
        },
    )
    resistance_list = _trim_public(
        [public_item(item) for item in resistances],
        {
            primary_resistance["strike"] if primary_resistance else None,
            major_resistance["strike"] if major_resistance else None,
        },
    )

    atm_row = min(rows, key=lambda row: abs(row["strike"] - spot))
    atm_ce = atm_row.get("ce_symbol")
    atm_pe = atm_row.get("pe_symbol")
    atm_lotsize = atm_row.get("lotsize")
    if not atm_ce or not atm_pe:
        for row in rows:
            if "ATM" in str((row.get("ce") or {}).get("label", "")):
                atm_ce = (row.get("ce") or {}).get("symbol")
                atm_pe = (row.get("pe") or {}).get("symbol")
                atm_lotsize = (row.get("ce") or {}).get("lotsize")
                break

    pcr = compute_pcr(rows)
    warning = None
    if len(rows) < 20:
        warning = "The captured chain is narrow; a wall may sit outside the window."

    return {
        "spot": spot,
        "expiry": expiration,
        "pcr": round(pcr, 3) if pcr is not None else None,
        "regime": classify_regime(pcr),
        "supports": support_list,
        "resistances": resistance_list,
        "primary_support": primary_support["strike"] if primary_support else None,
        "temporary_support": temporary_support["strike"] if temporary_support else None,
        "major_support": major_support["strike"] if major_support else None,
        "primary_resistance": primary_resistance["strike"] if primary_resistance else None,
        "major_resistance": major_resistance["strike"] if major_resistance else None,
        "no_trade_zone": (
            {
                "low": primary_support["strike"],
                "high": primary_resistance["strike"],
            }
            if primary_support and primary_resistance
            else None
        ),
        "atm_strike": atm_row["strike"],
        "atm_ce_symbol": atm_ce,
        "atm_pe_symbol": atm_pe,
        "atm_lotsize": _int(atm_lotsize) or None,
        "warning": warning,
    }


def count_rejections(bars, strikes, tolerance):
    """Count failed 15-minute touches at each strike.

    A resistance touch has a high at or above the strike and a close below it.
    A support touch has a low at or below the strike and a close above it.
    """
    counts = {round(_num(strike), 2): 0 for strike in strikes or []}
    for strike in counts:
        for bar in bars or []:
            low = _num(bar.get("low"))
            high = _num(bar.get("h"))
            close = _num(bar.get("c"))
            if high >= strike - tolerance and close < strike:
                counts[strike] += 1
            elif low <= strike + tolerance and close > strike:
                counts[strike] += 1
    return counts


def swing_levels_from_bars(bars, lookback=10):
    """Return recent daily swing highs and lows from oldest-first OHLC bars."""
    levels = []
    usable = [bar for bar in (bars or []) if _num(bar.get("h")) > 0 and _num(bar.get("low")) > 0]
    start = max(1, len(usable) - max(1, int(lookback)) - 1)
    for index in range(start, len(usable) - 1):
        previous = usable[index - 1]
        current = usable[index]
        following = usable[index + 1]
        if _num(current["h"]) > _num(previous["h"]) and _num(current["h"]) > _num(following["h"]):
            levels.append(_num(current["h"]))
        if _num(current["low"]) < _num(previous["low"]) and _num(current["low"]) < _num(
            following["low"]
        ):
            levels.append(_num(current["low"]))
    return levels[-20:]
