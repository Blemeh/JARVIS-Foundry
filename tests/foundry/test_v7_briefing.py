"""V7-S8 — owner briefing + command surface (TDD spec).

Gate matrix:
  1. a briefing is generated from the ledger truth (missions, cycles,
     risks, decisions, unknowns, WAITING, release slots, directives) and
     recorded as a durable event;
  2. briefing text renders in English and Albanian (locale parameter);
  3. briefings are read-only views — generating one appends only the
     briefing record, never mutates state;
  4. the command surface records owner commands as owner directives
     (STOP/pause/resume/reprieve) with provenance — supervisor-side reads
     expose them;
  5. briefings are idempotent on identical ledger state (same content
     hash -> same record dedupes).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.reporting.briefing import (
    BriefingError, generate_briefing, latest_briefing, briefing_count,
    record_owner_command, owner_commands,
)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "briefing.db")
    database.initialize()
    return database


def _seed(db: FoundryDatabase) -> None:
    from jarvis_foundry.evolution.records import record_improvement
    record_improvement(db, problem="test improvement", surfaces=["tests"],
                       evidence_refs=["x"], provenance={"source": "test"},
                       hypothesis="h",
                       expected_mechanism="m",
                       baseline_metric={"name": "error_rate", "unit": "rate",
                                        "value": 1.0, "source": "test seed"},
                       mmi={"minimum_delta": 0.5, "direction": "decrease",
                            "unit": "rate"},
                       guardrails={"correctness": "no regression",
                                   "safety": "none", "cost": "none",
                                   "latency": "none"},
                       workload={"ref": "w", "hash": "a" * 64},
                       observation={"requirement": "1 cycle", "cycles": 1},
                       declared_risk="R3", budget={"max_cost_usd": 0.0,
                                                  "capability_scope": []},
                       reversibility={"plan": "revert",
                                      "rollback_threshold": "suite"})
    db.append(Event.create("portfolio.unknown",
                           {"question_id": "q1",
                            "question": "is the key rotated?"},
                           source_type="supervisor", source_id="v7"))


def test_briefing_generated_and_recorded(db: FoundryDatabase) -> None:
    _seed(db)
    text = generate_briefing(db, locale="en",
                             source_type="supervisor", source_id="v7")
    assert "briefing" in text.lower() or "status" in text.lower()
    assert briefing_count(db) == 1
    latest = latest_briefing(db)
    assert latest["locale"] == "en"
    assert "improvement" in latest["text"].lower() or \
        "cycle" in latest["text"].lower()


def test_briefing_renders_in_albanian(db: FoundryDatabase) -> None:
    _seed(db)
    en = generate_briefing(db, locale="en",
                           source_type="supervisor", source_id="v7")
    sq = generate_briefing(db, locale="sq",
                           source_type="supervisor", source_id="v7")
    assert sq != en
    assert any(marker in sq.lower() for marker in
               ("përmbledhja", "supervizorit", "raport", "vëzhgim"))


def test_briefing_is_read_only_view(db: FoundryDatabase) -> None:
    _seed(db)
    before = len(db.events())
    generate_briefing(db, locale="en",
                      source_type="supervisor", source_id="v7")
    after = len(db.events())
    # the only new event is the briefing record itself
    assert after == before + 1
    new_types = {e.event_type for e in db.events()} - {
        "improvement.recorded", "release.manifest_recorded",
        "release.slot_changed", "portfolio.unknown", "briefing.recorded"}
    assert new_types == set()


def test_invalid_locale_refused(db: FoundryDatabase) -> None:
    _seed(db)
    with pytest.raises(BriefingError):
        generate_briefing(db, locale="xx",
                          source_type="supervisor", source_id="v7")


def test_owner_commands_recorded_with_provenance(db: FoundryDatabase) -> None:
    record_owner_command(db, command_id="STOP-1", command="STOP",
                         reason="owner override",
                         source_type="owner-channel", source_id="whatsapp")
    commands = owner_commands(db)
    assert commands[-1]["command"] == "STOP"
    assert commands[-1]["reason"] == "owner override"
    assert commands[-1]["source_id"] == "whatsapp"


def test_identical_state_dedupes_briefings(db: FoundryDatabase) -> None:
    _seed(db)
    generate_briefing(db, locale="en",
                      source_type="supervisor", source_id="v7")
    generate_briefing(db, locale="en",
                      source_type="supervisor", source_id="v7")
    assert briefing_count(db) == 1  # identical content dedupes
    db.append(Event.create("supervisor.heartbeat", {"status": "RUNNING"},
                           source_type="supervisor", source_id="v7"))
    generate_briefing(db, locale="en",
                      source_type="supervisor", source_id="v7")
    assert briefing_count(db) == 2  # changed state -> new briefing
