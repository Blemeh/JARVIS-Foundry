"""Integrated V8 convergence scenarios across the canonical authorities.

These tests intentionally cross the lane boundaries.  They do not introduce a
second execution path: every assertion is made against the existing ledger,
capability registry, executive DAG, portfolio bridge, worker loop, and
operational projection.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.capabilities.registry import (
    record_capability_observation,
    register_capability,
)
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    build_plan,
    coordinate_objectives,
    create_objective,
    propose_operational_followups,
    rank_objectives,
    rebuild_executive_state,
    run_autonomy_cycle,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.operational_intelligence import project_operational_intelligence
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER
from jarvis_foundry.supervisor.estop import EmergencyStop


OWNER_SECRET = b"v8-owner-secret-32-bytes-minimum!!"


def _event(
    database: FoundryDatabase,
    event_type: str,
    payload: dict[str, object],
    key: str,
    *,
    occurred_at: str = "2026-08-29T08:00:00+00:00",
) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="v8-integrated-test",
        source_id="v8-full-convergence",
        idempotency_key=key,
        occurred_at=occurred_at,
    ))


def _runtime_db(tmp_path: Path) -> tuple[FoundryDatabase, Path]:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    repository = tmp_path / "resource"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "v8@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "V8 Test"], check=True)
    (repository / "README.md").write_text("# V8 integrated scenario\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run([
        "git", "-C", str(repository), "commit", "-m", "baseline",
    ], check=True, capture_output=True, text=True)
    database.append(registration_event(inspect_git_resource(repository, resource_id="resource-v8")))
    set_capability_state(
        database, "resource-v8", "write_registered_worktree_files", "READY",
        evidence={"source": "v8-integrated-test"},
    )
    return database, repository


def _goal(database: FoundryDatabase, text: str, *, nonce: str = "v8-integrated-goal") -> None:
    service = OwnerControlService(database, secret=OWNER_SECRET, owner_id="owner")
    service.apply(
        "GOAL", target="GLOBAL", exact_text=text, nonce=nonce,
        signature=sign_command(OWNER_SECRET, "GOAL", "GLOBAL", text, nonce),
    )


def test_operational_discovery_becomes_one_deduped_owner_gated_proposal(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    for index in range(3):
        _event(
            database,
            "agent_run.failed",
            {
                "run_id": "run-recurring-provider",
                "mission_id": "mission-recurring-provider",
                "failure_class": "provider_timeout",
                "urgency": 0.7,
                "evidence_refs": [f"failure-proof-{index}"],
            },
            f"failed-recurring-{index}",
            occurred_at=f"2026-08-29T{8 + index:02d}:00:00+00:00",
        )

    projection = project_operational_intelligence(
        database, now="2026-08-29T12:00:00+00:00"
    )
    assert any(
        row["salience"] == "CREATE_WORK" and row["state"] == "OPEN"
        for row in projection["signals"]
    )
    before = database.event_count()
    first = propose_operational_followups(database)
    after_first = database.event_count()
    second = propose_operational_followups(database)
    assert first
    assert second == first
    assert database.event_count() == after_first
    proposal = first[0]
    assert proposal["requires_owner_or_policy"] is True
    assert proposal["auto_create_objective"] is False
    assert proposal["auto_materialize"] is False
    assert proposal["source_event_ids"]
    assert after_first > before

    # A discovery is advisory until owner/policy authority admits it.  A cycle
    # can carry the proposal while remaining genuinely idle.
    cycle = run_autonomy_cycle(database, cycle_id="discovery-only-cycle")
    assert cycle.status == "WAITING"
    assert cycle.objective_id is None
    assert cycle.operational_proposals == (proposal,)
    assert not list(database.events(event_types=("executive.objective_created",)))
    assert not list(database.events(event_types=("executive.mission_requested",)))
    snapshot = build_snapshot(database)
    assert snapshot["discovery"]["operational_intelligence"]["schema"] == "v8.operational-intelligence.v1"
    assert snapshot["executive"]["autonomy"]["operational_followup_proposals"]


def test_capability_and_provider_degradation_remove_executive_eligibility(tmp_path: Path) -> None:
    database, _ = _runtime_db(tmp_path)
    register_capability(
        database,
        capability_id="caps.integrated.code",
        name="Integrated code runner",
        family="terminal",
        surfaces=["shell"],
        effect_class="local-command",
        risk="R1",
        verification_hint="exit code and verifier receipt",
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor",
        source_id="v8-integrated-test",
        provider_id="provider-primary",
        model_id="model-primary",
        permissions=["workspace.read"],
        context={"scope": "candidate"},
        tool_support=["shell"],
        limits={"max_duration_s": 30},
    )
    record_capability_observation(
        database,
        "caps.integrated.code",
        status="HEALTHY",
        auth_state="READY",
        availability="AVAILABLE",
        provider_id="provider-primary",
        model_id="model-primary",
        evidence_refs=["capability-probe-healthy"],
        controller=SUPERVISOR_CONTROLLER,
        source_type="supervisor",
        source_id="v8-integrated-test",
    )
    _event(
        database,
        "provider.health_sample",
        {
            "provider_id": "provider-primary",
            "model": "model-primary",
            "status": "HEALTHY",
            "health": "HEALTHY",
            "auth_state": "READY",
            "availability": "AVAILABLE",
        },
        "provider-primary-healthy",
    )
    set_capability_state(
        database, "resource-v8", "caps.integrated.code", "READY",
        evidence={"source": "capability-probe-healthy"},
    )
    objective = create_objective(
        database,
        "Run the integrated capability",
        objective_id="integrated-capability-objective",
        resource_id="resource-v8",
        required_capabilities=("caps.integrated.code",),
    )
    build_plan(database, objective.objective_id, [{
        "step_id": "execute",
        "description": "Run the integrated capability",
    }])
    healthy = next(row for row in rank_objectives(database) if row.objective_id == objective.objective_id)
    assert healthy.eligible is True

    # The same plan becomes non-executable as soon as the canonical resource
    # capability disappears, without deleting or rewriting the plan.
    set_capability_state(
        database, "resource-v8", "caps.integrated.code", "DEGRADED",
        evidence={"source": "capability-disappeared"},
    )
    degraded_capability = next(
        row for row in rank_objectives(database) if row.objective_id == objective.objective_id
    )
    assert degraded_capability.eligible is False
    assert any("DEGRADED" in blocker for blocker in degraded_capability.blockers)

    # Provider truth is a second, independent gate.  Once the provider sample
    # degrades, executive ranking carries the provider blocker too.
    set_capability_state(
        database, "resource-v8", "caps.integrated.code", "READY",
        source_id="v8-integrated-capability-recovery",
        evidence={"source": "capability-recovered"},
    )
    _event(
        database,
        "provider.health_sample",
        {
            "provider_id": "provider-primary",
            "model": "model-primary",
            "status": "DEGRADED",
            "health": "DEGRADED",
            "auth_state": "READY",
            "availability": "AVAILABLE",
        },
        "provider-primary-degraded",
        occurred_at="2026-08-29T09:00:00+00:00",
    )
    degraded_provider = next(
        row for row in rank_objectives(database) if row.objective_id == objective.objective_id
    )
    assert degraded_provider.eligible is False
    assert any("provider:provider-primary:DEGRADED" in blocker for blocker in degraded_provider.blockers)
    cycle = run_autonomy_cycle(
        database, cycle_id="provider-degraded-cycle", resource_id="resource-v8",
    )
    assert cycle.materialization["materialized"] is False
    assert cycle.status == "WAITING"
    assert not list(database.events(event_types=("executive.mission_requested",)))


def test_cycle_idempotency_replays_recorded_decision_without_duplicate_mission(tmp_path: Path) -> None:
    database, _ = _runtime_db(tmp_path)
    _goal(database, "Create the durable integrated receipt. success: outcome receipt exists")
    first = run_autonomy_cycle(
        database, cycle_id="same-cycle-id", resource_id="resource-v8"
    )
    event_count = database.event_count()
    second = run_autonomy_cycle(
        database, cycle_id="same-cycle-id", resource_id="resource-v8"
    )
    assert second.to_payload() == first.to_payload()
    assert database.event_count() == event_count
    assert len(list(database.events(event_types=("executive.autonomy_cycle_recorded",)))) == 1
    assert len(list(database.events(event_types=("executive.mission_requested",)))) == 1
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 1


def test_competing_objectives_respect_dependency_and_live_owner_reprioritization(
    tmp_path: Path,
) -> None:
    database, _ = _runtime_db(tmp_path)
    high = create_objective(
        database, "High priority shared-resource objective", objective_id="high",
        resource_id="resource-v8", priority=2.0,
    )
    low = create_objective(
        database, "Lower priority shared-resource objective", objective_id="low",
        resource_id="resource-v8", priority=1.0,
    )
    parent = create_objective(database, "Prerequisite", objective_id="parent")
    child = create_objective(
        database, "Blocked dependent objective", objective_id="child",
        dependencies=(parent.objective_id,), resource_id="resource-v8",
    )
    build_plan(database, high.objective_id, [{"step_id": "high-step", "description": "High"}])
    build_plan(database, low.objective_id, [{"step_id": "low-step", "description": "Low"}])
    build_plan(database, child.objective_id, [{"step_id": "child-step", "description": "Child"}])

    child_score = next(row for row in rank_objectives(database) if row.objective_id == "child")
    assert child_score.eligible is False
    assert "dependency:parent:ACTIVE" in child_score.blockers
    initial = coordinate_objectives(database)
    assert initial.selected_objective_id == "high"
    assert any(
        {conflict["left"], conflict["right"]} == {"high", "low"}
        and conflict["winner"] == "high"
        for conflict in initial.conflicts
    )

    service = OwnerControlService(database, secret=OWNER_SECRET, owner_id="owner")
    text = "raise priority for low objective while high work is active"
    service.reprioritize(
        target="low",
        exact_text=text,
        nonce="reprioritize-low",
        signature=sign_command(OWNER_SECRET, "REPRIORITIZE", "low", text, "reprioritize-low"),
    )
    reprioritized = coordinate_objectives(database)
    assert reprioritized.selected_objective_id == "low"
    assert any(
        {conflict["left"], conflict["right"]} == {"high", "low"}
        and conflict["winner"] == "low"
        for conflict in reprioritized.conflicts
    )


def test_failed_mission_updates_memory_operational_state_and_objective_progress(
    tmp_path: Path,
) -> None:
    database, _ = _runtime_db(tmp_path)
    _goal(database, "Create the receipt despite a verifier failure. success: outcome receipt exists")
    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id="resource-v8",
        lab_scripted_hermes=True,
        lab_false_success_step_id="02-execute",
        max_repairs=0,
        executive_autonomy_enabled=True,
        runtime_state_root=str(tmp_path / "runtime"),
    )
    paths = FoundryPaths(tmp_path)
    reports = [
        run_cycle(
            database,
            paths,
            config,
            loop_id=f"integrated-failure-{index}",
            instance_id=f"integrated-worker-{index}",
        )
        for index in range(1, 4)
    ]
    assert any(report["outcome"] in {"BLOCKED", "FAILED"} for report in reports)
    objective = next(iter(rebuild_executive_state(database).objectives.values()))
    assert objective.status in {"FAILED", "ACTIVE"}
    evaluation = rebuild_executive_state(database).progress.get(objective.objective_id)
    assert evaluation is not None
    assert evaluation.failed_steps >= 1 or objective.status == "ACTIVE"

    mission_ids = set(objective.mission_ids)
    projection = project_operational_intelligence(database)
    assert any(
        item["state"] == "OPEN" and item["kind"] in {"FAILED", "BLOCKED"}
        and item["entity_id"] in mission_ids
        for item in projection["signals"]
    )
    assert any(
        any(f"mission:{mission_id}" in item["source_ids"] for mission_id in mission_ids)
        and any(
            marker in item["summary"]
            for marker in ("mission outcome failed", "mission outcome blocked")
        )
        for item in projection["memory"]["items"]
    )


def test_stop_path_blocks_cycle_before_any_executive_mutation(tmp_path: Path) -> None:
    database, _ = _runtime_db(tmp_path)
    _goal(database, "Stop before dispatch. success: receipt exists")
    stop_path = tmp_path / "EMERGENCY_STOP"
    EmergencyStop(stop_path).activate("integrated adversarial stop")
    before = database.event_count()
    with pytest.raises(RuntimeError, match="STOP"):
        run_autonomy_cycle(
            database,
            cycle_id="stop-gated-cycle",
            resource_id="resource-v8",
            stop_path=stop_path,
        )
    assert database.event_count() == before
    assert not list(database.events(event_types=("executive.autonomy_cycle_recorded",)))
