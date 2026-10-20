"""Integrated V16 campaign / workflow / expansion / process-loss proof."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _repo_root(start: Path) -> Path:
    current = start
    for _ in range(10):
        if (current / "src" / "jarvis_foundry").is_dir() and (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise RuntimeError(f"could not locate JARVIS repo from {start}")


REPO = _repo_root(HERE)
SRC = REPO / "src"
TESTS = REPO / "tests" / "foundry"
for path in (SRC, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    create_objective,
    rebuild_executive_state,
    run_autonomy_cycle,
    update_objective,
)
from jarvis_foundry.executive.campaigns import (
    bind_child_mission,
    campaign_dispatchable_mission_ids,
    checkpoint_campaign,
    create_campaign,
    declare_child_mission,
    record_child_state,
    rebuild_campaign_state,
    reconcile_campaign_resource_capacity,
    startup_resume_pass,
)
from jarvis_foundry.executive.continuous import rebuild_wake_state
from jarvis_foundry.fabric.adapter import FabricRoleAdapter
from jarvis_foundry.fabric.gap_bridge import RESUMED, WAKE_FIRED, rebuild_gap_state
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.mission.contracts import MissionStepContract, next_executable_contract, record_step_contract
from jarvis_foundry.mission.loop import PASSED, MissionLoop
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.results import COMPLETED, VERDICT_FAIL, VERDICT_PASS
from jarvis_foundry.roles.router import ProviderRouteCandidate, RoleRouter
from jarvis_foundry.self_expansion import SelfExpansionEngine, run_isolated_expansion_then_resume

from test_v12_self_expansion_convergence import MISSING, _blueprint

RUN = HERE / "run"
RESULT_PATH = HERE / "RESULT.json"
NOTE = "campaign/v16-bounded-note.txt"
OBJECTIVE_ID = "objective-v16-convergence"
RESOURCE_ID = "resource-v16-convergence"
CAMPAIGN_ID = "campaign-v16-convergence"


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _append(database: FoundryDatabase, event_type: str, payload: dict[str, Any], key: str) -> None:
    database.append(
        Event.create(
            event_type,
            payload,
            source_type="supervisor",
            source_id="v16-convergence",
            idempotency_key=key,
        )
    )


def _create_ready_mission(database: FoundryDatabase, mission_id: str, objective: str, resource_id: str) -> None:
    _append(
        database,
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": objective,
            "resource_id": resource_id,
            "state": "PLANNED",
            "acceptance_criteria": ["bounded child work is independently verified"],
            "next_action": "execute",
        },
        f"mission.created:{mission_id}",
    )
    for state in ("ORIENTING", "ACTION_SELECTION", "READY"):
        _append(
            database,
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": "execute"},
            f"mission.state_changed:{mission_id}:{state}",
        )


def _workflow_contract(mission_id: str, resource_id: str, target: str, content: str) -> MissionStepContract:
    envelope = json.dumps(
        {
            "recipe_id": "v16.bounded_note",
            "params": {"target": target, "content": content},
            "mission_id": mission_id,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    return MissionStepContract(
        mission_id=mission_id,
        step_id="01-workflow",
        version=1,
        resource_id=resource_id,
        capability="cap.workflow.run",
        target_relative_path=target,
        instructions=envelope,
        expected_effects=(f"{target} exists",),
        rollback={"method": "delete_file", "reversible": True},
        risk_class="C1",
        verification_criteria=(f"{target} exists",),
        expected_artifacts=(target,),
        forbidden_paths=(),
        recorded_by="supervisor",
        source="v16-convergence",
        provenance={"recipe_id": "v16.bounded_note"},
    )


def _file_contract(mission_id: str, resource_id: str, target: str, body: str) -> MissionStepContract:
    return MissionStepContract(
        mission_id=mission_id,
        step_id="01-file",
        version=1,
        resource_id=resource_id,
        capability="write_registered_worktree_files",
        target_relative_path=target,
        instructions=body,
        expected_effects=(f"{target} exists",),
        rollback={"method": "delete_file", "reversible": True},
        risk_class="C1",
        verification_criteria=(f"{target} exists", "file body matches repaired-ok"),
        expected_artifacts=(target,),
        forbidden_paths=(),
        recorded_by="supervisor",
        source="v16-convergence",
        provenance={"kind": "repair-child"},
    )


def _scripted_candidate(capability: str) -> ProviderRouteCandidate:
    return ProviderRouteCandidate(
        provider="local-script",
        model="local",
        capabilities=(
            capability,
            "write_registered_worktree_files",
            "code",
            "tools",
            "tests",
            "artifact_inspection",
            "adversarial_verification",
            "adversarial_review",
        ),
        task_classes=("*",),
        tool_support=(),
        health="READY",
        auth_state="READY",
        availability="AVAILABLE",
        readiness="READY",
        data_use_classification="NO_TRAINING",
        noninteractive_allowed=True,
        owner_allowed=True,
        cost_usd=0.0,
        evidence_refs=("v16-scripted-repair",),
    )


class _FailThenRepairAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.received: list[Any] = []

    def dispatch(self, manifest: Any, *, heartbeat) -> Any:
        from jarvis_foundry.roles.results import RoleResult
        from jarvis_foundry.clock import isoformat, utc_now

        heartbeat()
        self.received.append((manifest.role, manifest.attempt, manifest.run_id))
        names = list(manifest.expected_artifacts) or ["repair/note.txt"]
        if manifest.role in {"builder", "repair"}:
            text = "wrong-first-pass\n" if manifest.role == "builder" else "repaired-ok\n"
            for name in names:
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider=manifest.provider,
                model=manifest.model,
                verdict=VERDICT_PASS,
                evidence=("scripted builder/repair wrote owned artifact",),
                started_at=isoformat(utc_now()),
                finished_at=isoformat(utc_now()),
            )
        ok = all((self.root / name).read_text(encoding="utf-8") == "repaired-ok\n" for name in names)
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider=manifest.provider,
            model=manifest.model,
            verdict=VERDICT_PASS if ok else VERDICT_FAIL,
            evidence=("independent file readback",),
            started_at=isoformat(utc_now()),
            finished_at=isoformat(utc_now()),
            error_class=None if ok else "scripted_first_pass_mismatch",
        )


def _event_ids(database: FoundryDatabase, event_type: str, field: str) -> list[str]:
    values: list[str] = []
    for event in database.events(readonly=True):
        if event.event_type != event_type:
            continue
        value = event.payload.get(field)
        if value:
            values.append(str(value))
    return values


def _duplicate_surplus(values: list[str]) -> int:
    return int(sum(count - 1 for count in Counter(values).values() if count > 1))


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    repo = RUN / "project"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", "JARVIS V16 Proof", cwd=repo)
    _git("config", "user.email", "v16-proof@example.invalid", cwd=repo)
    (repo / "README.md").write_text("# V16 disposable campaign\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "baseline", cwd=repo)

    database = FoundryDatabase(RUN / "foundry.db")
    database.initialize()
    seed_builtins(database)
    resource = inspect_git_resource(repo, resource_id=RESOURCE_ID)
    resource["capabilities"]["cap.workflow.run"] = "READY"
    resource["capabilities"]["write_registered_worktree_files"] = "READY"
    database.append(registration_event(resource, source_id="v16-convergence"))

    create_objective(
        database,
        "Keep the disposable campaign note verified and current",
        objective_id=OBJECTIVE_ID,
        resource_id=RESOURCE_ID,
        required_capabilities=("cap.workflow.run",),
        success_criteria=("bounded note exists",),
        budget={"max_missions": 8, "max_retries": 2},
    )
    create_campaign(database, OBJECTIVE_ID, campaign_id=CAMPAIGN_ID, max_parallel_children=8)

    work_mission = "mission-v16-workflow"
    _create_ready_mission(database, work_mission, "write the bounded campaign note", RESOURCE_ID)
    record_step_contract(database, _workflow_contract(work_mission, RESOURCE_ID, NOTE, "campaign child wrote this note\n"))
    update_objective(database, OBJECTIVE_ID, {"mission_ids": [work_mission]})
    declare_child_mission(
        database,
        CAMPAIGN_ID,
        child_id="child-workflow",
        step_id="01-workflow",
        required_capabilities=("cap.workflow.run",),
        isolation_key="shared-v16-note",
        effect_action_key="workflow.run:v16.bounded_note",
    )
    bind_child_mission(database, "child-workflow", work_mission, step_id="01-workflow")
    record_child_state(database, "child-workflow", "READY")

    loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(database, owned_root=repo, inner=FakeHermesAdapter(working_root=repo)),
        max_repairs=1,
        stop_path=RUN / "STOP",
    )
    contract = next_executable_contract(database, work_mission)
    assert contract is not None
    workflow_outcome = loop.run_until_settled(**contract.runtime_kwargs(database))

    for suffix in ("a", "b"):
        mission_id = f"mission-v16-capacity-{suffix}"
        child_id = f"child-capacity-{suffix}"
        _create_ready_mission(database, mission_id, f"capacity child {suffix}", RESOURCE_ID)
        declare_child_mission(
            database,
            CAMPAIGN_ID,
            child_id=child_id,
            isolation_key="shared-v16-resource",
            required_capabilities=("write_registered_worktree_files",),
        )
        bind_child_mission(database, child_id, mission_id)
        record_child_state(database, child_id, "READY")
    first_capacity = reconcile_campaign_resource_capacity(database, {"shared-v16-resource": 1})
    record_child_state(database, "child-capacity-a", "CANCELLED", reason="release shared resource")
    second_capacity = reconcile_campaign_resource_capacity(database, {"shared-v16-resource": 1})
    resource_wakes = [
        wake for wake in rebuild_wake_state(database).values() if wake.get("kind") == "WAIT_FOR_RESOURCE"
    ]

    original_objective = rebuild_executive_state(database).objectives[OBJECTIVE_ID].objective
    update_objective(
        database,
        OBJECTIVE_ID,
        {"required_capabilities": [MISSING, "cap.workflow.run"]},
    )
    gap_cycle = run_autonomy_cycle(
        database,
        cycle_id="v16-gap-before-expansion",
        resource_id=RESOURCE_ID,
        capability="cap.workflow.run",
    )
    engine = SelfExpansionEngine(
        database,
        repo_root=REPO,
        artifact_root=RUN / "adapter-artifacts",
        trial_root=RUN / "artifact-trials",
        resolver=lambda candidate, request: _blueprint(candidate, request, bad_contract=False),
    )
    expansion = None
    expansion_replay = None
    try:
        expansion = run_isolated_expansion_then_resume(
            database,
            objective_id=OBJECTIVE_ID,
            port=engine,
            strategy_id="v16-convergence-expansion",
        )
        expansion_replay = run_isolated_expansion_then_resume(
            database,
            objective_id=OBJECTIVE_ID,
            port=engine,
            strategy_id="v16-convergence-expansion",
        )
    finally:
        engine.close()
    resumed_objective = rebuild_executive_state(database).objectives[OBJECTIVE_ID]

    repair_mission = "mission-v16-repair"
    repair_target = "repair/v16-note.txt"
    _create_ready_mission(database, repair_mission, "repair the bounded note", RESOURCE_ID)
    record_step_contract(database, _file_contract(repair_mission, RESOURCE_ID, repair_target, "repair this note"))
    current_ids = list(rebuild_executive_state(database).objectives[OBJECTIVE_ID].mission_ids)
    if repair_mission not in current_ids:
        update_objective(database, OBJECTIVE_ID, {"mission_ids": [*current_ids, repair_mission]})
    declare_child_mission(
        database,
        CAMPAIGN_ID,
        child_id="child-repair",
        step_id="01-file",
        required_capabilities=("write_registered_worktree_files",),
        max_retries=1,
    )
    bind_child_mission(database, "child-repair", repair_mission, step_id="01-file")
    record_child_state(database, "child-repair", "READY")
    repair_adapter = _FailThenRepairAdapter(repo)
    repair_loop = MissionLoop(
        database,
        RoleRouter(),
        repair_adapter,
        max_repairs=1,
        stop_path=RUN / "STOP",
        provider_candidates=(_scripted_candidate("write_registered_worktree_files"),),
    )
    repair_contract = next_executable_contract(database, repair_mission)
    assert repair_contract is not None
    repair_outcome = repair_loop.run_until_settled(**repair_contract.runtime_kwargs(database))

    checkpoint = checkpoint_campaign(database, CAMPAIGN_ID, reason="process-loss checkpoint")
    before_resume = database.event_count(event_type="campaign.checkpoint_recorded")
    fresh = FoundryDatabase(database.path)
    first_resume = startup_resume_pass(fresh, resume_id="v16-process-loss-1")
    after_first = fresh.event_count(event_type="campaign.checkpoint_recorded")
    second_resume = startup_resume_pass(fresh, resume_id="v16-process-loss-2")
    after_second = fresh.event_count(event_type="campaign.checkpoint_recorded")
    rebuilt = rebuild_campaign_state(fresh)

    duplicate_mission = _duplicate_surplus(_event_ids(fresh, "mission.created", "mission_id"))
    duplicate_effect = _duplicate_surplus(_event_ids(fresh, "effect.requested", "effect_id"))
    duplicate_continuation = _duplicate_surplus(_event_ids(fresh, "campaign.resume_passed", "resume_id"))
    false_success = 0
    if workflow_outcome.outcome == PASSED and not (repo / NOTE).is_file():
        false_success += 1
    if repair_outcome.outcome == PASSED and (repo / repair_target).read_text(encoding="utf-8") != "repaired-ok\n":
        false_success += 1

    checks = {
        "campaign_child_workflow_passed": workflow_outcome.outcome == PASSED and (repo / NOTE).is_file(),
        "workflow_run_recipe": "campaign child wrote this note" in (repo / NOTE).read_text(encoding="utf-8") if (repo / NOTE).is_file() else False,
        "resource_wait": first_capacity.get("waiting") == ["child-capacity-b"],
        "resource_wake": "child-capacity-b" in (second_capacity.get("released") or []),
        "wait_kind_resource": any(wake.get("kind") == "WAIT_FOR_RESOURCE" for wake in resource_wakes) or rebuilt.children["child-capacity-b"].status == "READY",
        "self_expansion_same_objective": resumed_objective.objective == original_objective and resumed_objective.objective_id == OBJECTIVE_ID,
        "self_expansion_resumed": bool(expansion and expansion.resumed_gap_ids) and resumed_objective.status == "ACTIVE",
        "self_expansion_replay_no_duplicate": expansion is not None and expansion_replay is not None and expansion.as_dict() == expansion_replay.as_dict(),
        "failure_repaired_verified": repair_outcome.outcome == PASSED and (repo / repair_target).read_text(encoding="utf-8") == "repaired-ok\n",
        "process_loss_continue_once": after_second == after_first,
        "duplicate_mission_zero": duplicate_mission == 0,
        "duplicate_effect_zero": duplicate_effect == 0,
        "duplicate_continuation_zero": duplicate_continuation == 0,
        "false_success_zero": false_success == 0,
        "gap_cycle_waited": gap_cycle.materialization.get("materialized") is False,
    }
    proof = {
        "schema": "jarvis.v16.converged-real-scenario.v1",
        "passed": all(checks.values()),
        "checks": checks,
        "workflow_outcome": getattr(workflow_outcome, "outcome", None),
        "workflow_reason": getattr(workflow_outcome, "reason", None),
        "repair_outcome": getattr(repair_outcome, "outcome", None),
        "repair_reason": getattr(repair_outcome, "reason", None),
        "capacity": {"first": first_capacity, "second": second_capacity},
        "dispatchable": list(campaign_dispatchable_mission_ids(fresh)),
        "expansion": expansion.as_dict() if expansion is not None else None,
        "objective": resumed_objective.objective,
        "objective_status": resumed_objective.status,
        "resume": {
            "first": getattr(first_resume, "reason", str(first_resume)),
            "second": getattr(second_resume, "reason", str(second_resume)),
            "checkpoint_events": [before_resume, after_first, after_second],
            "checkpoint_id": getattr(checkpoint, "checkpoint_id", None),
        },
        "duplicates": {
            "mission": duplicate_mission,
            "effect": duplicate_effect,
            "continuation": duplicate_continuation,
            "false_success": false_success,
        },
        "gap_ids": sorted(rebuild_gap_state(fresh)),
        "wake_fired": sum(1 for event in fresh.events(readonly=True) if event.event_type == WAKE_FIRED),
        "resumed_events": sum(1 for event in fresh.events(readonly=True) if event.event_type == RESUMED),
    }
    RESULT_PATH.write_text(json.dumps(proof, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"passed": proof["passed"], "checks": checks, "duplicates": proof["duplicates"]}, indent=2))
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
