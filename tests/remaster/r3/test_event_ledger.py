from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import (
    Event,
    EventConflict,
    EventIntegrityError,
    payload_hash,
)

KINDS = (
    "mission.created",
    "owner_directive.recorded",
    "action.allowed",
    "agent_run.started",
    "project.registered",
    "supervisor.started",
)


def valid_event(index: int, kind: str, text: str) -> Event:
    payload: dict[str, Any]
    if kind == "mission.created":
        payload = {
            "mission_id": f"m-{index}",
            "objective": text,
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": [text],
        }
    elif kind == "owner_directive.recorded":
        payload = {
            "directive_id": f"d-{index}",
            "command_type": "PAUSE",
            "target": None,
            "exact_text": text,
        }
    elif kind == "action.allowed":
        payload = {
            "action_id": f"a-{index}",
            "mission_id": f"m-{index}",
            "decision": "ALLOWED",
            "reason": text,
            "target": f"/tmp/{index}",
        }
    elif kind == "agent_run.started":
        payload = {
            "run_id": f"r-{index}",
            "mission_id": f"m-{index}",
            "step_id": f"s-{index}",
            "role": "builder",
            "provider": "opencode_go",
            "model": "deepseek-v4-flash",
            "context_manifest_hash": "0" * 64,
            "tool_policy": {"mutate": ["registered_worktree"]},
        }
    elif kind == "project.registered":
        payload = {
            "resource_id": f"res-{index}",
            "kind": "git_worktree",
            "root_path": f"C:/tmp/{index}",
            "resolved_root": f"C:/tmp/{index}",
            "baseline": {"branch": "main", "head": text},
            "capabilities": {"read": "READY"},
            "readiness_state": "READY",
        }
    else:
        payload = {"instance_id": f"i-{index}", "trust_state": "LAB"}
    return Event(
        event_id=f"evt-{index}",
        event_type=kind,
        occurred_at="2026-08-14T00:00:00.000Z",
        recorded_at="2026-08-14T00:00:00.000Z",
        source_type="r3-test",
        source_id="hypothesis",
        correlation_id=f"corr-{index}",
        causation_id=None,
        idempotency_key=f"{kind}:{index}",
        schema_version=1,
        payload=payload,
        payload_hash=payload_hash(payload),
    )


def fresh_database(path: Path) -> FoundryDatabase:
    database = FoundryDatabase(path)
    database.initialize()
    return database


def test_payload_hash_is_content_derived() -> None:
    first = payload_hash({"a": 1})
    second = payload_hash({"a": 2})
    assert first != second
    assert len(first) == 64
    assert len(second) == 64
    event = Event.create(
        "supervisor.started",
        {"instance_id": "i-1", "trust_state": "LAB"},
        source_type="test",
        source_id="r3",
    )
    assert event.payload_hash == payload_hash(event.payload)
    assert event.payload_hash != payload_hash({"instance_id": "i-2", "trust_state": "LAB"})


def test_invalid_event_is_rejected(tmp_path: Path) -> None:
    bad = Event(
        event_id="",
        event_type="invalid",
        occurred_at="2026-08-14T00:00:00.000Z",
        recorded_at="2026-08-14T00:00:00.000Z",
        source_type="test",
        source_id="r3",
        correlation_id="c",
        causation_id=None,
        idempotency_key="bad:1",
        schema_version=1,
        payload={},
        payload_hash=payload_hash({}),
    )
    with pytest.raises(EventIntegrityError):
        bad.validate()
    database = fresh_database(tmp_path / "foundry.db")
    with pytest.raises(EventIntegrityError):
        database.append_events([bad])


def test_duplicate_event_is_idempotent(tmp_path: Path) -> None:
    database = fresh_database(tmp_path / "foundry.db")
    event = valid_event(1, "mission.created", "objective")
    first = database.append(event)
    second = database.append(event)
    assert first[1] is True
    assert second[1] is False
    assert second[0] == first[0]
    assert len(database.events()) == 1


def test_idempotency_conflict_is_rejected(tmp_path: Path) -> None:
    database = fresh_database(tmp_path / "foundry.db")
    database.append(valid_event(1, "mission.created", "objective"))
    conflicting = valid_event(2, "mission.created", "objective")
    conflicting = replace(conflicting, idempotency_key="mission.created:1")
    with pytest.raises(EventConflict):
        database.append(conflicting)


@settings(max_examples=25, deadline=None)
@given(
    indices=st.sets(st.integers(min_value=0, max_value=30), min_size=0, max_size=8).map(sorted),
    text=st.text(max_size=24),
)
def test_append_replay_determinism(
    tmp_path_factory: pytest.TempPathFactory, indices: list[int], text: str
) -> None:
    database = fresh_database(tmp_path_factory.mktemp("ledger") / "foundry.db")
    events = [valid_event(index, KINDS[index % len(KINDS)], f"{text}-{index}") for index in indices]
    results = database.append_events(events)
    assert [row[0] for row in results] == list(range(1, len(events) + 1))
    assert all(row[1] for row in results)
    replayed = database.events()
    assert [event.event_id for event in replayed] == [event.event_id for event in events]
    assert [event.payload for event in replayed] == [event.payload for event in events]
    assert database.verify_event_integrity() == len(events)
    assert database.rebuild_projections() == len(events)
    again = database.append_events(events)
    assert [row[0] for row in again] == [row[0] for row in results]
    assert all(not row[1] for row in again)
    assert len(database.events()) == len(events)
    assert database.quick_check() == "ok"
