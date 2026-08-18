"""R10 forward-port gate: EN/SQ briefings with six semantic categories."""

from __future__ import annotations

import pytest

from jarvis_foundry.convergence.briefings_l10n import (
    CATEGORY_ACTION,
    CATEGORY_COST,
    CATEGORY_FACT,
    CATEGORY_MISSING_EVIDENCE,
    CATEGORY_RECOMMENDATION,
    CATEGORY_UNCERTAINTY,
    render_briefing,
    semantic_items,
)


def _snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-08-16T00:00:00Z",
        "missions": [
            {
                "mission_id": "m1",
                "state": "ACTION_SELECTION",
                "objective": "repair export lineage",
                "next_action": "dispatch repair B",
                "record_ref": "evt-m1",
            }
        ],
        "timeline": [{"event_type": "mission.dispatched"}],
        "costs": {
            "measured_actual_total_usd": "UNKNOWN",
            "measured_estimated_total_usd": "UNKNOWN",
            "unknown_measurements": 1,
        },
        "recovery": {
            "provider_or_connector_unknowns": ["connector.browser"],
            "blocked_missions": ["m2"],
            "recovery_events": [],
        },
        "source_of_truth": {"ledger_head_sequence": 5},
    }


def test_six_semantic_categories_present() -> None:
    items = semantic_items(_snapshot())
    ids = {item["category"] for item in items}
    assert ids == {
        CATEGORY_FACT,
        CATEGORY_UNCERTAINTY,
        CATEGORY_RECOMMENDATION,
        CATEGORY_ACTION,
        CATEGORY_COST,
        CATEGORY_MISSING_EVIDENCE,
    }
    assert all("en" in item["text"] and "sq" in item["text"] for item in items)


def test_english_and_albanian_briefings_are_correct_language() -> None:
    snapshot = _snapshot()
    en = render_briefing(snapshot, "en")
    sq = render_briefing(snapshot, "sq")
    assert en["lang"] == "en"
    assert sq["lang"] == "sq"
    assert "Supervisor briefing - JARVIS Foundry" in en["title"]
    assert "Përmbledhja e Supervizorit - JARVIS Foundry" in sq["title"]
    assert en["categories"][0]["category"] == "Fact"
    assert sq["categories"][0]["category"] == "Fakt"
    # R10-V-102 regression: Albanian body must not carry English template text.
    sq_joined = " ".join(item["text"] for item in sq["categories"])
    assert "Vazhdo veprimin tjetër" in sq_joined or "Kosto:" in sq_joined


def test_unsupported_language_rejected() -> None:
    with pytest.raises(ValueError):
        render_briefing(_snapshot(), "de")
