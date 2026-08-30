"""Cross-lane proof for the isolated V9 convergence candidate.

The lane suites prove their own contracts.  These scenarios deliberately
cross the seams while keeping every effect inside a pytest-owned disposable
database and git worktree.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

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
    create_objective,
    observe_mission_outcome,
    rebuild_executive_state,
)
from jarvis_foundry.executive.continuous import (
    is_wake_due,
    rebuild_wake_state,
    run_continuous_cycle,
)
from jarvis_foundry.fabric import (
    ExecutorContext,
    VerifyVerdict,
    execute_action,
    execute_for_spine,
    seed_builtins,
)
from jarvis_foundry.mission.verification import (
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.world_model import WorldModel, writeback_mission_outcome


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _runtime(tmp_path: Path) -> tuple[FoundryDatabase, Path]:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    repo = tmp_path / "resource"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "v9-integration@example.invalid", cwd=repo)
    _git("config", "user.name", "V9 integration", cwd=repo)
    (repo / "README.md").write_text("V9 disposable resource\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "baseline", cwd=repo)
    db.append(registration_event(inspect_git_resource(repo, resource_id="resource-v9")))
    set_capability_state(
        db,
        "resource-v9",
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "v9-full-convergence-test"},
    )
    return db, repo


def _register_provider_capability(db: FoundryDatabase, *, health: str) -> None:
    register_capability(
        db,
        capability_id="caps.v9.provider-bound",
        name="V9 provider-bound action",
        family="integration",
        surfaces=["file"],
        effect_class="file-write",
        risk="R1",
        verification_hint="read-back hash",
        controller="supervisor",
        source_type="supervisor",
        source_id="v9-full-convergence-test",
        provider_id="provider-v9",
        model_id="model-v9",
        permissions=["workspace.write"],
        context={"scope": "disposable"},
        tool_support=["filesystem"],
        limits={"max_duration_s": 30},
    )
    record_capability_observation(
        db,
        "caps.v9.provider-bound",
        status=health,
        auth_state="READY",
        availability="AVAILABLE" if health == "HEALTHY" else "DEGRADED",
        provider_id="provider-v9",
        model_id="model-v9",
        evidence_refs=[f"capability-{health.lower()}"],
        controller="supervisor",
        source_type="supervisor",
        source_id="v9-full-convergence-test",
    )
    set_capability_state(
        db,
        "resource-v9",
        "caps.v9.provider-bound",
        "READY",
        evidence={"source": "v9-full-convergence-test"},
    )
    db.append(Event.create(
        "provider.health_sample",
        {
            "provider_id": "provider-v9",
            "model": "model-v9",
            "health": health,
            "status": health,
            "auth_state": "READY",
            "availability": "AVAILABLE" if health == "HEALTHY" else "DEGRADED",
        },
        source_type="supervisor",
        source_id="v9-full-convergence-test",
        idempotency_key=f"provider.health_sample:v9:{health}",
    ))


def test_world_signal_to_executive_mission_fabric_effect_and_truthful_projection(
    tmp_path: Path,
) -> None:
    db, repo = _runtime(tmp_path)
    objective = create_objective(
        db,
        "Deliver the world-signalled receipt",
        objective_id="objective-v9-chain",
        resource_id="resource-v9",
        priority=3.0,
        required_capabilities=("write_registered_worktree_files",),
    )
    # The explicit objective identity is the only admissible world->executive
    # routing key; the World lane remains advisory and does not create work.
    wm = WorldModel(db)
    wm.entities.record(
        "OWNER_OBJECTIVE",
        objective.objective,
        entity_id=objective.objective_id,
        attributes={"source": "v9-chain"},
        authority="OWNER_STATEMENT",
    )
    observation = wm.observe(
        "connector:health_check",
        "health-v9-chain",
        {
            "objective_id": objective.objective_id,
            "provider_id": "provider-v9",
            "health": "degraded",
            "reason": "rate_limited",
        },
    )
    signal = wm.signals.get(observation["signals"][0]["signal_id"])
    assert signal is not None
    assert objective.objective_id in signal["affected_entities"]

    # The Executive admits one durable wake and one materialized mission.
    result = run_continuous_cycle(db, cycle_id="v9-chain-cycle")
    assert result["status"] == "MISSION_MATERIALIZED"
    assert result["triggered_wakes"]
    mission_id = str(result["autonomy_cycle"]["materialization"]["mission_id"])
    assert mission_id
    wakes = rebuild_wake_state(db)
    assert any(
        wake["objective_id"] == objective.objective_id
        and wake["status"] == "TRIGGERED"
        for wake in wakes.values()
    )
    assert len([e for e in db.events(event_types=("mission.created",))]) == 1

    # Capability Fabric executes the same disposable mission identity and
    # journals an actual read-back effect; no live path is involved.
    seed_builtins(db)
    ctx = ExecutorContext(database=db, owned_root=repo)
    receipt = execute_for_spine(
        db,
        ctx,
        mission_id=mission_id,
        step_id="v9-fabric-effect",
        action_kind="file.write",
        payload={"target": "world-signalled.txt", "content": "verified V9\n"},
    )
    assert receipt["success"] is True
    assert receipt["verified"] == VerifyVerdict.MATCH.value
    assert (repo / "world-signalled.txt").read_text(encoding="utf-8") == "verified V9\n"

    # A false COMPLETED claim is rejected by the Fabric verifier and cannot
    # masquerade as a successful effect.
    false = execute_action(
        db,
        ctx,
        type("Intent", (), {"task_id": "v9-false", "required_actions": ("file.write",), "preferred": None, "unattended": False})(),
        "file.write",
        {"target": "missing-by-claim.txt", "content": "never written"},
        claim_override={"claimed": "COMPLETED", "target": "missing-by-claim.txt", "content_hash": "wrong"},
    )
    assert false.success is False
    assert false.verified is VerifyVerdict.MISMATCH

    # Command Center exposes the same ledger-backed wake, mission and world
    # signal state; it is a projection, not another authority.
    snapshot = build_snapshot(db)
    assert snapshot["executive"]["continuous"]["wake_state"]
    assert any(m["mission_id"] == mission_id for m in snapshot["missions"])
    assert any(s["signal_id"] == signal["signal_id"] for s in snapshot["world_intelligence"]["signals"])


def test_provider_wait_recovery_survives_restart_and_materializes_once(
    tmp_path: Path,
) -> None:
    db, _repo = _runtime(tmp_path)
    _register_provider_capability(db, health="DEGRADED")
    objective = create_objective(
        db,
        "Wait for provider recovery",
        objective_id="objective-v9-provider",
        resource_id="resource-v9",
        required_capabilities=("caps.v9.provider-bound",),
    )
    build_plan(
        db,
        objective.objective_id,
        [{
            "step_id": "execute",
            "description": objective.objective,
            "required_capabilities": ("caps.v9.provider-bound",),
            "resource_id": "resource-v9",
        }],
    )

    parked = run_continuous_cycle(db, cycle_id="v9-provider-park")
    assert parked["status"] == "WAITING"
    provider_wakes = [
        wake for wake in rebuild_wake_state(db).values()
        if wake.get("objective_id") == objective.objective_id
        and wake.get("kind") == "WAIT_FOR_PROVIDER"
    ]
    assert len(provider_wakes) == 1
    wake = provider_wakes[0]
    assert wake["status"] == "ACTIVE"
    assert is_wake_due(db, wake) is False

    # Re-open the database before recovery.  The durable wait remains parked,
    # and replaying the cycle does not generate a second wake or mission.
    restarted = FoundryDatabase(tmp_path / "foundry.db")
    parked_again = run_continuous_cycle(restarted, cycle_id="v9-provider-park-replay")
    assert parked_again["status"] == "WAITING"
    assert len([
        w for w in rebuild_wake_state(restarted).values()
        if w.get("objective_id") == objective.objective_id
        and w.get("kind") == "WAIT_FOR_PROVIDER"
    ]) == 1
    assert len(list(restarted.events(event_types=("mission.created",)))) == 0

    # A later positive provider sample releases exactly that wake.
    record_capability_observation(
        restarted,
        "caps.v9.provider-bound",
        status="HEALTHY",
        auth_state="READY",
        availability="AVAILABLE",
        provider_id="provider-v9",
        model_id="model-v9",
        evidence_refs=["provider-recovered"],
        controller="supervisor",
        source_type="supervisor",
        source_id="v9-full-convergence-test",
    )
    restarted.append(Event.create(
        "provider.health_sample",
        {
            "provider_id": "provider-v9",
            "model": "model-v9",
            "health": "HEALTHY",
            "status": "HEALTHY",
            "auth_state": "READY",
            "availability": "AVAILABLE",
        },
        source_type="supervisor",
        source_id="v9-full-convergence-test",
        idempotency_key="provider.health_sample:v9:recovery",
    ))
    recovered = run_continuous_cycle(restarted, cycle_id="v9-provider-recovered")
    assert recovered["status"] == "MISSION_MATERIALIZED"
    assert wake["wake_id"] in recovered["triggered_wakes"]
    assert len(list(restarted.events(event_types=("mission.created",)))) == 1

    replay = run_continuous_cycle(restarted, cycle_id="v9-provider-recovered")
    assert replay["status"] == recovered["status"]
    assert len(list(restarted.events(event_types=("mission.created",)))) == 1


def test_terminal_mission_updates_world_then_executive_and_replans_without_duplicate_writeback(
    tmp_path: Path,
) -> None:
    db, _repo = _runtime(tmp_path)
    objective = create_objective(
        db,
        "Replan after a failed mission",
        objective_id="objective-v9-replan",
        resource_id="resource-v9",
    )
    build_plan(db, objective.objective_id, [{"step_id": "execute", "description": objective.objective}])
    # Materialize through the one Executive/portfolio bridge.
    cycle = run_continuous_cycle(db, cycle_id="v9-replan-materialize")
    mission_id = str(cycle["autonomy_cycle"]["materialization"]["mission_id"])
    assert mission_id

    # Verifier truth, not a caller's status claim, makes this a failed
    # mission outcome.  The terminal verdict is the same canonical evidence
    # source that MissionLoop uses in production.
    start_verification(
        db,
        verdict_id="verdict-v9-fail",
        mission_id=mission_id,
        step_id="execute",
        builder_run_id="builder-v9-fail",
        verifier_run_id="verifier-v9-fail",
        independence={"builder_provider": "provider-a", "verifier_provider": "provider-b"},
        criteria=["provider response is independently checked"],
    )
    record_verification_outcome(
        db,
        verdict_id="verdict-v9-fail",
        outcome="FAIL",
        findings=["provider timeout"],
        evidence={"observed": "timeout"},
        repair_requirements=["use alternate provider"],
        reason="provider timeout",
    )

    failure = writeback_mission_outcome(
        db,
        mission_id,
        "FAILED",
        {"state": "FAILED", "reason": "provider timeout"},
        evidence_refs=("verdict-v9-fail",),
    )
    assert failure["deduplicated"] is False
    duplicate = writeback_mission_outcome(
        db,
        mission_id,
        "FAILED",
        {"state": "FAILED", "reason": "provider timeout"},
        evidence_refs=("verdict-v9-fail",),
    )
    assert duplicate["deduplicated"] is True
    assert len([
        e for e in db.events(event_type_prefixes=("world.observation_recorded",))
        if e.idempotency_key.endswith(f"{mission_id}:FAILED")
    ]) == 1
    assert db.row("world_entities", "entity_id", mission_id)["state"] == "STALE"

    observed = observe_mission_outcome(db, mission_id)
    assert observed["status"] in {"FAILED", "BLOCKED"}
    continuation = rebuild_executive_state(db).continuations[objective.objective_id]
    assert continuation.action in {"RETRY", "ESCALATE", "WAIT"}
    if continuation.action == "RETRY":
        assert continuation.next_step_id == "execute"

        # The retry is a new mission identity, uses the reopened step, and
        # cannot be considered successful until the independent verifier
        # records a PASS.  This closes the failure -> alternate route -> retry
        # -> verified success seam across Executive, Mission, Verifier and
        # World Intelligence.
        retry_cycle = run_continuous_cycle(db, cycle_id="v9-replan-retry")
        assert retry_cycle["status"] == "MISSION_MATERIALIZED"
        retry_mission_id = str(
            retry_cycle["autonomy_cycle"]["materialization"]["mission_id"]
        )
        assert retry_mission_id and retry_mission_id != mission_id
        start_verification(
            db,
            verdict_id="verdict-v9-retry-pass",
            mission_id=retry_mission_id,
            step_id="execute",
            builder_run_id="builder-v9-retry",
            verifier_run_id="verifier-v9-retry",
            independence={"builder_provider": "provider-a", "verifier_provider": "provider-b"},
            criteria=["alternate provider response is independently checked"],
        )
        record_verification_outcome(
            db,
            verdict_id="verdict-v9-retry-pass",
            outcome="PASS",
            findings=["alternate provider response verified"],
            evidence={"observed": "alternate provider success"},
            reason="alternate strategy succeeded",
        )
        success = writeback_mission_outcome(
            db,
            retry_mission_id,
            "COMPLETED",
            {"state": "COMPLETED", "strategy": "alternate_provider"},
            evidence_refs=("verdict-v9-retry-pass",),
        )
        assert success["deduplicated"] is False
        assert db.row("world_entities", "entity_id", retry_mission_id)["state"] == "RESOLVED"
        assert observe_mission_outcome(db, retry_mission_id)["status"] == "COMPLETED"

    # Strategy history forces a materially different next route rather than a
    # storm of identical retries; the existing Executive gate records the
    # escalation durably.
    from jarvis_foundry.executive.continuous import (
        is_retry_loop,
        next_strategy_options,
        record_strategy_attempt,
    )
    for _ in range(3):
        record_strategy_attempt(db, objective.objective_id, "retry_same")
    assert is_retry_loop(db, objective.objective_id, "retry_same")
    assert "alternate_provider" in next_strategy_options(db, objective.objective_id)
