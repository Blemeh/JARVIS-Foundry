"""Lane B probe: where does the mission verifier step route for an
intent-resolved deterministic local action?

Reproduces the self-expansion resume on a disposable runtime and dumps the
durable facts the repair must key on:

  - the intent-resolved contract (source / capability / step / instructions)
  - what the Fabric resolves that capability to (`resolve_fabric_action`)
  - whether the general-work verifier-replay resolver claims the step
  - the recorded `fabric.spine.result` receipt for the builder step
  - the verifier agent run row, its provider/error, and the verdicts

Read-only w.r.t. the lane's committed proofs: writes only under probe/.

Disposable runtime only. Does not touch live JARVIS, main, origin, or the
live DB.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
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
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.control.registration import inspect_git_resource, registration_event  # noqa: E402
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.executive.autonomy import (  # noqa: E402
    run_autonomy_cycle,
    sync_owner_goals,
    update_objective,
)
from jarvis_foundry.fabric.adapter import (  # noqa: E402
    FabricRoleAdapter,
    resolve_fabric_action,
    resolve_general_work_dispatch,
)
from jarvis_foundry.fabric.loop import run_intent, seed_builtins  # noqa: E402
from jarvis_foundry.fabric.model import TaskIntent  # noqa: E402
from jarvis_foundry.fabric.executors import ExecutorContext  # noqa: E402
from jarvis_foundry.mission.contracts import latest_contract, next_executable_contract  # noqa: E402
from jarvis_foundry.mission.loop import MissionLoop  # noqa: E402
from jarvis_foundry.owner_controls import OwnerControlService, sign_command  # noqa: E402
from jarvis_foundry.roles.adapter import FakeHermesAdapter  # noqa: E402
from jarvis_foundry.roles.router import RoleRouter  # noqa: E402

RUN = HERE / "run"
RESULT = HERE / "VERIFY_ROUTING_PROBE.json"
SECRET = b"purpose-recovery-ii-lane-b-verify-routing-probe"
GOAL_TEXT = "Compute and record the integrity digest of the supplied evidence file."
NEED_ACTION = "hash.file.sha256"


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, 0o700)
    function(path)


def _rows(database: FoundryDatabase, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with database.connect(readonly=True) as connection:
        try:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]
        except Exception as error:  # noqa: BLE001
            return [{"__error__": f"{type(error).__name__}: {error}"}]


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    repo = RUN / "project"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", "JARVIS Lane B Verify Routing Probe", cwd=repo)
    _git("config", "user.email", "lane-b@example.invalid", cwd=repo)
    evidence = repo / "evidence.bin"
    evidence.write_bytes(b"lane-b-verify-routing-probe-evidence")
    _git("add", "evidence.bin", cwd=repo)
    _git("commit", "-m", "evidence", cwd=repo)
    expected_sha = hashlib.sha256(evidence.read_bytes()).hexdigest()

    database = FoundryDatabase(RUN / "foundry.db")
    database.initialize()
    seed_builtins(database)
    resource = inspect_git_resource(repo, resource_id="resource-lane-b-probe")
    database.append(registration_event(resource, source_id="lane-b-probe"))

    out: dict[str, Any] = {"schema": "jarvis.lane-b.verify-routing-probe.v1"}
    controls = OwnerControlService(database, secret=SECRET, stop_path=RUN / "STOP")
    controls.apply(
        "GOAL", target="GLOBAL", exact_text=GOAL_TEXT, nonce="lane-b-probe-1",
        signature=sign_command(SECRET, "GOAL", "GLOBAL", GOAL_TEXT, "lane-b-probe-1"),
    )
    created = sync_owner_goals(database)
    objective = created[0]
    update_objective(
        database, objective.objective_id,
        {
            "resource_id": "resource-lane-b-probe",
            "required_capabilities": ["write_registered_worktree_files"],
            "success_criteria": ["the evidence digest is computed and independently verified"],
            "metadata": {
                **dict(objective.metadata or {}),
                "intent": {
                    "needs": ["compute.file_hash"],
                    "inputs": {"file_to_hash": str(evidence)},
                    "success_criteria": ["the evidence digest is computed and independently verified"],
                },
            },
        },
    )

    run_autonomy_cycle(
        database, cycle_id="probe-cycle-1",
        resource_id="resource-lane-b-probe",
        capability="write_registered_worktree_files",
    )
    ctx = ExecutorContext(database=database, owned_root=repo)
    acquisition_run = run_intent(
        database, ctx,
        TaskIntent(task_id="lane-b-probe", required_actions=(NEED_ACTION,)),
        {NEED_ACTION: {"path": str(evidence)}},
    )
    out["acquisition"] = {
        "claimed": acquisition_run["results"][0].get("claimed"),
        "verified": acquisition_run["results"][0].get("verified"),
        "capability_id": acquisition_run["results"][0].get("capability_id"),
    }

    cycle_two = run_autonomy_cycle(
        database, cycle_id="probe-cycle-2",
        resource_id="resource-lane-b-probe",
        capability="write_registered_worktree_files",
    )
    mission_id = str((cycle_two.materialization or {}).get("mission_id") or "")
    out["cycle_two"] = {
        "status": cycle_two.status,
        "materialized": bool((cycle_two.materialization or {}).get("materialized")),
        "mission_id": mission_id,
    }
    contract = next_executable_contract(database, mission_id) if mission_id else None
    if contract is None:
        out["error"] = "no executable contract"
        RESULT.write_text(json.dumps(out, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(json.dumps(out, indent=2, default=str))
        return 1

    out["contract"] = {
        "step_id": contract.step_id,
        "capability": contract.capability,
        "source": contract.source,
        "resource_id": contract.resource_id,
        "target_relative_path": contract.target_relative_path,
        "instructions": contract.instructions,
        "verification_criteria": list(contract.verification_criteria),
        "expected_artifacts": list(contract.expected_artifacts),
    }
    resolved_action = resolve_fabric_action(contract.capability, database)
    out["resolved_action"] = {
        "contract_capability": contract.capability,
        "fabric_action_kind": resolved_action,
        "resolve_verification_capability": resolve_fabric_action("verification", database),
    }
    out["general_work_verifier_replay"] = (
        None
        if resolve_general_work_dispatch(
            database, role="verifier", mission_id=mission_id,
            step_id=contract.step_id, capability="verification",
        ) is None
        else list(
            resolve_general_work_dispatch(
                database, role="verifier", mission_id=mission_id,
                step_id=contract.step_id, capability="verification",
            )
        )
    )
    out["latest_contract_by_step"] = {
        "by_step": (
            None if latest_contract(database, mission_id, step_id=contract.step_id,
                                    allow_invalid=False) is None
            else latest_contract(database, mission_id, step_id=contract.step_id,
                                allow_invalid=False).source
        ),
        "without_step": (
            None if latest_contract(database, mission_id, allow_invalid=False) is None
            else latest_contract(database, mission_id, allow_invalid=False).source
        ),
    }
    out["spec_for_capability"] = _rows(
        database, "SELECT * FROM capability_specs WHERE capability_id = ?",
        ("cap.hash.file.sha256",),
    )

    adapter = FabricRoleAdapter(
        database, owned_root=repo,
        inner=FakeHermesAdapter(working_root=repo),
        operator_executable_allowlist=(),
    )
    loop = MissionLoop(database, RoleRouter(), adapter, max_repairs=1, stop_path=RUN / "STOP")
    outcome = loop.run_until_settled(**contract.runtime_kwargs(database))
    out["mission_outcome"] = outcome.outcome

    spine_events = [
        dict(event.payload) for event in database.events(event_types=("fabric.spine.result",))
    ]
    out["spine_results"] = [
        {
            "step_id": item.get("step_id"),
            "action_kind": item.get("action_kind"),
            "capability_id": item.get("capability_id"),
            "claimed": item.get("claimed"),
            "verified": item.get("verified"),
            "success": item.get("success"),
            "state": item.get("state"),
            "effect_id": item.get("effect_id"),
            "receipt_id": item.get("receipt_id"),
            "payload_keys": sorted(item.keys()),
            "observed_keys": sorted((item.get("observed") or {}).keys())
            if isinstance(item.get("observed"), dict) else type(item.get("observed")).__name__,
            "observed": item.get("observed"),
        }
        for item in spine_events
        if str(item.get("mission_id") or "") == mission_id
    ]
    out["agent_runs"] = [
        {
            "run_id": row.get("run_id"),
            "role": row.get("role"),
            "capability": row.get("capability"),
            "step_id": row.get("step_id"),
            "status": row.get("status"),
            "provider": row.get("provider"),
            "model": row.get("model"),
            "error_class": row.get("error_class"),
            "error_detail": row.get("error_detail"),
            "verdict": row.get("verdict"),
        }
        for row in _rows(
            database,
            "SELECT * FROM agent_runs WHERE mission_id = ? ORDER BY rowid",
            (mission_id,),
        )
    ]
    out["verdicts"] = _rows(
        database,
        "SELECT * FROM verification_verdicts WHERE mission_id = ? ORDER BY rowid",
        (mission_id,),
    )
    out["verdict_columns"] = [
        row.get("name") for row in _rows(database, "PRAGMA table_info(verification_verdicts)")
    ]
    out["verification_bindings"] = _rows(
        database,
        "SELECT * FROM verification_bindings WHERE mission_id = ? ORDER BY rowid",
        (mission_id,),
    )
    out["event_types_for_mission"] = sorted({
        str(event.event_type)
        for event in database.events()
        if isinstance(event.payload, dict)
        and (
            str(event.payload.get("mission_id") or "") == mission_id
            or str(event.correlation_id or "") == mission_id
        )
    })
    out["expected_sha"] = expected_sha

    RESULT.write_text(json.dumps(out, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({
        "outcome": out.get("mission_outcome"),
        "contract": out.get("contract"),
        "resolved_action": out.get("resolved_action"),
        "general_work_verifier_replay": out.get("general_work_verifier_replay"),
        "latest_contract_by_step": out.get("latest_contract_by_step"),
        "spine_results": out.get("spine_results"),
        "agent_runs": out.get("agent_runs"),
        "verdicts": out.get("verdicts"),
        "event_types_for_mission": out.get("event_types_for_mission"),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
