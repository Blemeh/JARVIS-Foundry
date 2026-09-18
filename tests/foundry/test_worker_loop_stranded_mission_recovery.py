"""A stranded EXECUTING mission must be recovered by the loop that owns the lease.

Observed live defect (P3-D2, 2026-09-14): after a Supervisor self-restart,
mission-65b4968b76e6 sat in EXECUTING with no dispatch. Neither canonical
selector returns EXECUTING (dispatchable_contract_missions and
select_dispatchable_mission both accept only
ACTION_SELECTION/REPAIRING/READY/VERIFYING) and propose_next_action raises
ProposalError for it.

The Supervisor's own recovery in _reap_and_recover() is gated on the worker-loop
lease NOT being live, so a running one-shot loop - the only thing that could
pick the mission up - keeps the gate closed indefinitely. Recovery therefore has
to happen inside the loop, which already holds the singleton lease.
"""
from __future__ import annotations

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.proposal import ProposalError, propose_next_action
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.roles.reaper import (
    recover_stale_mission_state,
    role_lease_key,
)
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


def _fixture(tmp_path) -> tuple[FoundryDatabase, FoundryPaths, str]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-stranded", "objective": "stranded fixture",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["recovered"]},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-stranded",
    ))
    for state in ("ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": "mission-stranded", "state": state,
             "next_action": "deliver feature X"},
            source_type="test", source_id="test-suite",
            idempotency_key=f"mission.state_changed:mission-stranded:{state}",
        ))
    database.append(Event.create(
        "agent_run.started",
        {"run_id": "run-killed", "mission_id": "mission-stranded",
         "step_id": "step-1", "role": "builder", "provider": "opencode_go",
         "model": "test-model", "context_manifest_hash": "0" * 64,
         "tool_policy": []},
        source_type="test", source_id="test-suite",
        idempotency_key="agent_run.started:run-killed:opencode_go",
    ))
    # The killed session's lease is expired; nothing else holds a role lease.
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (role_lease_key("builder", "mission-stranded", "step-1"),
             "ghost-owner", "2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z"),
        )
    return database, FoundryPaths(tmp_path), "mission-stranded"


def test_executing_mission_is_not_selectable_or_proposable(tmp_path):
    """Precondition: the stranded state is genuinely unreachable by the selectors."""
    database, _paths, mission_id = _fixture(tmp_path)
    try:
        propose_next_action(database, mission_id)
    except ProposalError:
        pass
    else:  # pragma: no cover - documents the defect precondition
        raise AssertionError("EXECUTING should not be proposable")


def test_worker_loop_recovers_a_stranded_executing_mission(tmp_path):
    """The loop that owns the singleton lease must clear the stranded state."""
    database, paths, mission_id = _fixture(tmp_path)
    config = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        runtime_state_root=str(tmp_path / "state"),
    )

    run_cycle(database, paths, config,
              loop_id="stranded-loop", instance_id="stranded-instance")

    mission = database.row("missions", "mission_id", mission_id)
    assert mission["state"] != "EXECUTING", (
        "the worker loop left the mission stranded in EXECUTING; with the "
        "Supervisor recovery gated on a non-live loop lease, nothing else can "
        "ever move it")
    assert mission["next_action"] == "deliver feature X"
    assert database.row("agent_runs", "run_id", "run-killed")["status"] == "FAILED"


def test_recovery_stays_idempotent_across_cycles(tmp_path):
    """A second cycle must not append duplicate recovery transitions."""
    database, paths, mission_id = _fixture(tmp_path)
    config = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        runtime_state_root=str(tmp_path / "state"),
    )
    run_cycle(database, paths, config,
              loop_id="stranded-loop-1", instance_id="stranded-instance-1")
    run_cycle(database, paths, config,
              loop_id="stranded-loop-2", instance_id="stranded-instance-2")

    resumes = [
        e for e in database.events()
        if e.event_type == "mission.state_changed"
        and e.payload.get("reason") == "resume_after_session_loss"
        and e.payload.get("state") == "ACTION_SELECTION"
    ]
    assert len(resumes) == 1, "recovery must not duplicate its transitions"


def test_recovery_never_touches_a_live_session(tmp_path):
    """A mission whose role lease is live must be left alone."""
    database, _paths, mission_id = _fixture(tmp_path)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE leases SET expires_at = ? WHERE lease_key = ?",
            ("2999-01-01T00:00:00Z",
             role_lease_key("builder", mission_id, "step-1")),
        )
    recovered = recover_stale_mission_state(database)
    assert recovered == []
    assert database.row("missions", "mission_id", mission_id)["state"] == "EXECUTING"
