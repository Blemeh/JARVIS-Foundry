"""Concurrent stranded-mission recovery must not duplicate its transitions.

P3-D2 makes recovery run from two places that can overlap in wall-clock time:
the Supervisor's maintenance pass and the worker loop's cycle start. Both call
the same canonical function, so the question worth proving is whether a
double invocation can append duplicate recovery transitions or double-advance
the mission.
"""
from __future__ import annotations

import threading

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.reaper import (
    recover_stale_mission_state,
    role_lease_key,
)


def _fixture(tmp_path) -> tuple[FoundryDatabase, str]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-strand", "objective": "concurrency fixture",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["recovered once"]},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-strand",
    ))
    for state in ("ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": "mission-strand", "state": state,
             "next_action": "deliver feature X"},
            source_type="test", source_id="test-suite",
            idempotency_key=f"mission.state_changed:mission-strand:{state}",
        ))
    database.append(Event.create(
        "agent_run.started",
        {"run_id": "run-stale", "mission_id": "mission-strand",
         "step_id": "step-1", "role": "builder", "provider": "opencode_go",
         "model": "test-model", "context_manifest_hash": "0" * 64,
         "tool_policy": []},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.started:run-stale:opencode_go",
    ))
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (role_lease_key("builder", "mission-strand", "step-1"),
             "ghost-owner", "2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z"),
        )
    return database, "mission-strand"


def _resume_count(database: FoundryDatabase) -> int:
    return len([
        e for e in database.events()
        if e.event_type == "mission.state_changed"
        and e.payload.get("reason") == "resume_after_session_loss"
        and e.payload.get("state") == "ACTION_SELECTION"
    ])


def test_concurrent_recovery_records_exactly_one_resume(tmp_path):
    """Two overlapping recovery calls must recover the mission exactly once.

    The MATERIAL invariant is the one under test: exactly one resume transition
    reaches the ledger and the mission ends in ACTION_SELECTION exactly once.
    Twenty recorded trials show that holds (0 duplicate transitions).

    The RETURN VALUE is deliberately NOT asserted to be exclusive. Two callers
    can both observe the mission still stranded before either has appended, so
    both may legitimately report it as recovered while only one transition
    lands (16 of 20 trials showed both reporting). That is an advisory return,
    not a safety property, and asserting exclusivity would be asserting
    behaviour the contract does not promise.
    """
    database, mission_id = _fixture(tmp_path)

    results: list[list[str]] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(recover_stale_mission_state(database))
        except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"recovery raised under concurrency: {errors}"
    assert _resume_count(database) == 1, (
        "concurrent recovery appended more than one resume transition")
    assert database.row("missions", "mission_id", mission_id)["state"] == "ACTION_SELECTION"
    # At least one recoverer must report it; exclusivity is not promised.
    claimed = sum(1 for batch in results if mission_id in batch)
    assert claimed >= 1, (
        "neither recoverer reported the mission despite it recovering "
        f"(results={results})")


def test_repeated_sequential_recovery_is_idempotent(tmp_path):
    """Calling recovery again after it succeeded must be a no-op."""
    database, mission_id = _fixture(tmp_path)
    first = recover_stale_mission_state(database)
    second = recover_stale_mission_state(database)
    third = recover_stale_mission_state(database)
    assert first == [mission_id]
    assert second == [] and third == []
    assert _resume_count(database) == 1
    assert database.row("missions", "mission_id", mission_id)["state"] == "ACTION_SELECTION"
