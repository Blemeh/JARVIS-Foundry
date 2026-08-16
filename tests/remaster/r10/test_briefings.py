"""Briefing category coverage and real English/Albanian correctness."""

from __future__ import annotations

from pathlib import Path

import pytest
from r10_helpers import FIXED_UTC, make_layout, seed_layout

from jarvis_foundry.command_center.briefings import (
    generate_briefing,
)
from jarvis_foundry.command_center.state import build_r10_snapshot

EN_PHRASES = (
    "Supervisor briefing - JARVIS Foundry",
    "Fact",
    "Uncertainty",
    "Recommendation",
    "Action",
    "Cost",
    "Missing evidence",
)

SQ_PHRASES = (
    "Përmbledhja e Supervizorit - JARVIS Foundry",
    "Fakt",
    "Pasiguri",
    "Rekomandim",
    "Veprim",
    "Kosto",
    "Dëshmi e munguar",
)


def _snapshot(tmp_path: Path) -> dict[str, object]:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    return build_r10_snapshot(layout, now=FIXED_UTC)


def test_briefing_has_all_six_categories(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    briefing = generate_briefing(snapshot, "en")
    labels = [category["category"] for category in briefing["categories"]]
    assert set(labels) == {
        "Fact",
        "Uncertainty",
        "Recommendation",
        "Action",
        "Cost",
        "Missing evidence",
    }


def test_english_briefing_is_correct_language(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    briefing = generate_briefing(snapshot, "en")
    assert briefing["lang"] == "en"
    assert briefing["rendered_lang"] == "en"
    text = briefing["text"]
    for phrase in EN_PHRASES:
        assert phrase in text, phrase
    assert "Përmbledhja" not in text


def test_albanian_briefing_is_correct_language(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    briefing = generate_briefing(snapshot, "sq")
    assert briefing["lang"] == "sq"
    assert briefing["rendered_lang"] == "sq"
    text = briefing["text"]
    for phrase in SQ_PHRASES:
        assert phrase in text, phrase
    assert "Supervisor briefing - JARVIS Foundry" not in text
    # The body sentences must be real Albanian, not English strings under
    # Albanian labels (R10-V-102 regression).
    for english_fragment in (
        "Keep the R10 builder evidence package closed",
        "Measured USD total is",
        "No real provider USD receipts exist",
        "Continue the frozen program",
        "accepted missions, ",
        "active capabilities, ",
        "Real STT/TTS receipts:",
        "Health flags:",
        "no recorded next action",
    ):
        assert english_fragment not in text, english_fragment
    for albanian_fragment in (
        "Mbajeni të mbyllur paketën e provave",
        "Totali i matur në USD është",
        "Nuk ka fatura reale USD nga ofruesi",
        "misione të pranuara",
        "aftësi aktive",
        "Faturat reale STT/TTS:",
        "Shenjat e shëndetit:",
    ):
        assert albanian_fragment in text, albanian_fragment


def test_english_and_albanian_share_content_hash(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    english = generate_briefing(snapshot, "en")
    albanian = generate_briefing(snapshot, "sq")
    # Briefings are genuinely per-language content, so hashes differ. The old
    # equality assertion encoded the English-body-under-Albanian-labels bug.
    assert english["content_hash"] != albanian["content_hash"]
    assert english["text"] != albanian["text"]


def test_briefing_evidence_refs_resolve(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    briefing = generate_briefing(snapshot, "en")
    assert briefing["evidence_refs"]
    for ref in briefing["evidence_refs"]:
        record_id = ref.get("record_id")
        assert record_id in snapshot["evidence_index"]


def test_unsupported_briefing_language_raises(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    with pytest.raises(ValueError):
        generate_briefing(snapshot, "de")
