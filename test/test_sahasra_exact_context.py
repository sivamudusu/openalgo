"""Pre-open context is context, and a failed quote stays visibly unavailable."""

from services import sahasra_exact_context_service as context


def test_partial_quote_failure_is_not_turned_into_neutral(monkeypatch):
    context._auto_cache.clear()

    def fake_multiquotes(symbols, api_key):
        return (
            True,
            {
                "results": [
                    {"symbol": "US30", "data": {"ltp": 42000, "prev_close": 41800}},
                    {"symbol": "US500", "error": "permission denied"},
                ]
            },
            200,
        )

    monkeypatch.setattr(context, "get_multiquotes", fake_multiquotes)

    cues = context.fetch_auto_cues("test-key")
    by_key = {cue["key"]: cue for cue in cues}

    assert by_key["US30"]["status"] == "available"
    assert by_key["US30"]["change_pct"] == 0.48
    assert by_key["US500"]["status"] == "unavailable"
    assert by_key["US500"]["value"] is None
    assert by_key["GIFTNIFTY"]["status"] == "unavailable"


def test_no_api_key_reports_unavailable_everywhere():
    context._auto_cache.clear()

    cues = context.fetch_auto_cues(None)

    assert cues
    assert all(cue["status"] == "unavailable" for cue in cues)


def test_alignment_is_advisory():
    assert context.alignment_for("CE", "bullish") == "aligned"
    assert context.alignment_for("PE", "bullish") == "against"
    assert context.alignment_for(None, "bullish") == "unset"
    assert context.alignment_for("CE", "mixed") == "mixed"


def test_manual_values_are_normalised():
    manual = context._normalise_manual(
        {"fed": "Hawkish", "inflation": "in_line", "geopolitics": "bogus", "india_yield": "6.84"}
    )

    assert manual == {
        "fed": "hawkish",
        "inflation": "in_line",
        "geopolitics": "",
        "india_yield": "6.84",
    }
