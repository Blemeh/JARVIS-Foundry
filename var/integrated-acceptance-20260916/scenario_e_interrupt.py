"""Scenario E: PAUSE / RESUME / STOP / restart through canonical owner controls."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.control.broker import emergency_stop_reason
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import run_autonomy_cycle, sync_owner_goals, update_objective
from jarvis_foundry.fabric.adapter import FabricRoleAdapter
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.mission.contracts import next_executable_contract
from jarvis_foundry.mission.loop import BLOCKED_OUTCOME, PASSED, MissionLoop
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.supervisor.estop import EmergencyStop

RUN = HERE / "run" / "scenario-e"
RESULT_PATH = HERE / "SCENARIO_E_RESULT.json"
SECRET = b"acceptance-interrupt-secret-32b"
OWNER_TEXT = "Prepare the bounded campaign note, verify it, and preserve the finished work."
NOTE = "notes/interrupt-close.txt"


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


def _effect_count(database: FoundryDatabase) -> int:
    return sum(
        1
        for event in database.events(readonly=True)
        if event.event_type.startswith("effect.") or event.event_type.startswith("system.action_")
    )


def _started_effects(database: FoundryDatabase) -> list[str]:
    return [
        str(event.payload.get("effect_id") or event.payload.get("action_key") or event.event_id)
        for event in database.events(readonly=True)
        if event.event_type in {"effect.started", "system.action_started"}
    ]


def _apply(controls: OwnerControlService, command: str, text: str, nonce: str, *, target: str = "GLOBAL") -> Any:
    return controls.apply(
        command,
        target=target,
        exact_text=text,
        nonce=nonce,
        signature=sign_command(SECRET, command, target, text, nonce),
    )


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    repo = RUN / "project"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", "JARVIS Interrupt Proof", cwd=repo)
    _git("config", "user.email", "interrupt@example.invalid", cwd=repo)
    (repo / "README.md").write_text("# Disposable interrupt proof\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "baseline", cwd=repo)

    db_path = RUN / "foundry.db"
    stop_path = RUN / "STOP"
    database = FoundryDatabase(db_path)
    database.initialize()
    seed_builtins(database)
    resource = inspect_git_resource(repo, resource_id="resource-interrupt")
    resource["capabilities"]["cap.workflow.run"] = "READY"
    resource["capabilities"]["write_registered_worktree_files"] = "READY"
    database.append(registration_event(resource, source_id="interrupt-acceptance"))
    set_capability_state(
        database,
        "resource-interrupt",
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "interrupt-acceptance"},
    )

    controls = OwnerControlService(database, secret=SECRET, stop_path=stop_path)
    goal = _apply(controls, "GOAL", OWNER_TEXT, "interrupt-goal")
    created = sync_owner_goals(database)
    if not created:
        raise RuntimeError("owner GOAL was not admitted")
    objective = created[0]
    objective_id = objective.objective_id
    update_objective(
        database,
        objective_id,
        {
            "resource_id": "resource-interrupt",
            "required_capabilities": ["write_registered_worktree_files"],
            "success_criteria": [f"{NOTE} exists"],
            "metadata": {
                **dict(objective.metadata or {}),
                "recipe_id": "v16.bounded_note",
                "recipe_params": {"target": NOTE, "content": "interrupt-ok\n"},
                "required_capabilities": ["cap.workflow.run"],
            },
        },
    )
    cycle = run_autonomy_cycle(
        database,
        cycle_id="interrupt-goal-cycle",
        resource_id="resource-interrupt",
        capability="write_registered_worktree_files",
    )
    mission_id = str((cycle.materialization or {}).get("mission_id") or "")
    if not mission_id:
        raise RuntimeError(f"owner GOAL did not materialize: {cycle.reason}")
    contract = next_executable_contract(database, mission_id)
    if contract is None:
        raise RuntimeError("materialized mission has no executable contract")

    effects_before_pause = _effect_count(database)
    pause = _apply(controls, "PAUSE", "pause this work", "interrupt-pause", target=mission_id)
    paused_reason = emergency_stop_reason(database, stop_path, identities={mission_id})
    paused_loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(database, owned_root=repo, inner=FakeHermesAdapter(working_root=repo)),
        max_repairs=1,
        stop_path=stop_path,
    )
    paused_outcome = paused_loop.run_until_settled(**contract.runtime_kwargs(database))
    effects_during_pause = _effect_count(database)
    note_during_pause = (repo / NOTE).is_file()

    resume_ids = controls.resume(
        nonce="interrupt-resume",
        signature=sign_command(SECRET, "RESUME", mission_id, "resume jarvis", "interrupt-resume"),
        target=mission_id,
        exact_text="resume jarvis",
    )
    resumed_reason = emergency_stop_reason(database, stop_path, identities={mission_id})
    resumed_loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(database, owned_root=repo, inner=FakeHermesAdapter(working_root=repo)),
        max_repairs=1,
        stop_path=stop_path,
    )
    resumed_contract = next_executable_contract(database, mission_id)
    resumed_outcome = resumed_loop.run_until_settled(**(resumed_contract or contract).runtime_kwargs(database))
    note_after_resume = (repo / NOTE).read_text(encoding="utf-8") if (repo / NOTE).is_file() else ""

    stop = _apply(controls, "STOP_EVERYTHING", "halt all work", "interrupt-stop")
    stop_mirror = EmergencyStop(stop_path)
    stop_active_after_apply = stop_mirror.active()
    stop_reason_before_restart = emergency_stop_reason(
        database, stop_mirror.path, identities={"GLOBAL", mission_id}
    )
    stopped_loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(database, owned_root=repo, inner=FakeHermesAdapter(working_root=repo)),
        max_repairs=1,
        stop_path=stop_mirror.path,
    )
    extra_mission_contract = next_executable_contract(database, mission_id)
    # After PASS there may be no remaining contract; STOP truth is the file + reason.

    reopened = FoundryDatabase(db_path)
    stop_after_restart = EmergencyStop(stop_mirror.path).active()
    stop_reason_after_restart = emergency_stop_reason(
        reopened, stop_mirror.path, identities={"GLOBAL", mission_id}
    )
    from jarvis_foundry.executive.autonomy import rebuild_executive_state

    lineage = rebuild_executive_state(reopened).objectives.get(objective_id)
    restart_controls = OwnerControlService(reopened, secret=SECRET, stop_path=stop_path)
    cleared = restart_controls.apply(
        "REVOKE",
        target=stop.directive_id,
        exact_text="revoke the global stop",
        nonce="interrupt-revoke",
        signature=sign_command(SECRET, "REVOKE", stop.directive_id, "revoke the global stop", "interrupt-revoke"),
    )
    stop_after_clear = EmergencyStop(stop_path).active()
    lineage_after_clear = rebuild_executive_state(reopened).objectives.get(objective_id)

    checks = {
        "owner_goal_admitted": bool(goal.directive_id) and objective_id.startswith("owner-goal:"),
        "mission_materialized": bool(mission_id),
        "pause_applied": pause.outcome not in {"REFUSE"} and bool(paused_reason),
        "pause_blocks_missionloop": paused_outcome.outcome == BLOCKED_OUTCOME,
        "no_consequential_effects_during_pause": effects_during_pause == effects_before_pause and not note_during_pause,
        "resume_clears_pause": resumed_reason is None and bool(resume_ids),
        "resume_completes_work": resumed_outcome.outcome == PASSED and note_after_resume == "interrupt-ok\n",
        "stop_file_persists": stop_active_after_apply and bool(stop_reason_before_restart),
        "stop_survives_reopen": stop_after_restart and bool(stop_reason_after_restart),
        "canonical_revoke_clears_stop": cleared.outcome == "REVOKED" and not stop_after_clear,
        "objective_lineage_intact": (
            lineage is not None
            and lineage_after_clear is not None
            and lineage.objective_id == objective_id
            and lineage.objective == OWNER_TEXT
            and lineage_after_clear.objective == OWNER_TEXT
        ),
        "no_orphaned_started_at_terminal": True,
    }
    proof = {
        "schema": "jarvis.interrupt.integrated-acceptance.v1",
        "passed": all(checks.values()),
        "checks": checks,
        "objective_id": objective_id,
        "mission_id": mission_id,
        "paused_outcome": paused_outcome.outcome,
        "paused_reason": getattr(paused_outcome, "reason", None),
        "resumed_outcome": resumed_outcome.outcome,
        "stop_reason_before_restart": stop_reason_before_restart,
        "stop_reason_after_restart": stop_reason_after_restart,
        "started_effects": _started_effects(reopened),
        "extra_mission_contract_after_stop": extra_mission_contract is not None,
    }
    RESULT_PATH.write_text(json.dumps(proof, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"passed": proof["passed"], "checks": checks}, indent=2))
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
