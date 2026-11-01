"""The Sahasra exact level engine, including the 23 October acceptance fixture."""

from services.sahasra_exact_levels import (
    build_level_map,
    classify_regime,
    compute_pcr,
    count_rejections,
)


def _fixture_chain():
    values = {
        53000: (120000, 250000),
        53500: (150000, 310000),
        54000: (326500, 781440),
        54100: (291080, 713160),
        54200: (328220, 696440),
        54300: (345140, 656940),
        54400: (379960, 649590),
        54500: (431960, 687990),
        54600: (497530, 732360),
        54700: (515860, 693520),
        54800: (544470, 664130),
        54900: (498470, 468120),
        55000: (784100, 212310),
        55100: (775320, 135480),
        55200: (652200, 125730),
        55300: (572300, 89580),
        55400: (501230, 72140),
        55500: (439910, 60720),
        55600: (294530, 48960),
        55700: (321000, 35000),
        56000: (1240000, 20000),
    }
    return [
        {"strike": float(strike), "ce_oi": call, "pe_oi": put}
        for strike, (call, put) in sorted(values.items())
    ]


def _levels():
    return build_level_map(
        54925.7,
        _fixture_chain(),
        prior_day={"high": 55250, "low": 54100, "close": 54300},
        rejection_counts={"55000": 4, "54500": 2, "54000": 1},
    )


def test_23_october_fixture_labels():
    levels = _levels()

    assert levels["primary_support"] == 54000.0
    assert levels["major_support"] == 54000.0
    assert levels["temporary_support"] == 54500.0
    assert levels["primary_resistance"] == 55000.0
    assert levels["major_resistance"] == 56000.0
    assert levels["no_trade_zone"] == {"low": 54000.0, "high": 55000.0}


def test_primary_resistance_is_the_tested_wall_not_the_largest_wall():
    levels = _levels()

    assert levels["primary_resistance"] == 55000.0
    assert levels["major_resistance"] == 56000.0
    assert levels["primary_resistance"] != levels["major_resistance"]


def test_pcr_uses_one_as_the_boundary():
    bearish = [{"strike": 100, "ce_oi": 1000, "pe_oi": 973}]
    bullish = [{"strike": 100, "ce_oi": 1000, "pe_oi": 1001}]
    flat = [{"strike": 100, "ce_oi": 1000, "pe_oi": 1000}]

    assert compute_pcr(bearish) == 0.973
    assert classify_regime(compute_pcr(bearish)) == "bearish"
    assert classify_regime(compute_pcr(bullish)) == "bullish"
    assert classify_regime(compute_pcr(flat)) == "neutral"
    assert classify_regime(None) == "unknown"


def test_no_support_below_spot_is_not_invented_as_a_resistance():
    chain = [
        {"strike": 110.0, "ce_oi": 1000, "pe_oi": 2000},
        {"strike": 120.0, "ce_oi": 500, "pe_oi": 3000},
    ]

    levels = build_level_map(100.0, chain)

    assert levels["primary_support"] is None
    assert all(item["strike"] <= 100 for item in levels["supports"])
    assert levels["primary_resistance"] == 110.0
    assert levels["no_trade_zone"] is None


def test_missing_chain_is_reported_not_guessed():
    levels = build_level_map(100.0, [])

    assert levels["supports"] == []
    assert levels["resistances"] == []
    assert levels["primary_support"] is None
    assert levels["primary_resistance"] is None
    assert levels["warning"]


def test_rejection_counter_counts_failed_touches():
    bars = [
        {"h": 101, "low": 99, "c": 100.5},
        {"h": 101, "low": 99, "c": 100.2},
        {"h": 100, "low": 98, "c": 99.5},
    ]

    counts = count_rejections(bars, [100], tolerance=1.0)

    assert counts[100.0] >= 1
