"""Probe: why is the owner objective not selected? (lane B diagnostics, not evidence)"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

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

from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    rebuild_executive_state,
    run_autonomy_cycle,
    sync_owner_goals,
    update_objective,
)
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.owner_controls import OwnerControlService, sign_command

RUN = HERE / "run"
SECRET = b"probe-secret-32-bytes-long-abcdef"
TEXT = "Research the supplied authenticated local source and preserve the result."


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    import os
    import stat

    os.chmod(path, stat.S_IWRITE)
    function(path)


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    repo = RUN / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "P"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "p@x.invalid"], cwd=repo, check=True, capture_output=True)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    db = FoundryDatabase(RUN / "foundry.db")
    db.initialize()
    seed_builtins(db)
    resource = inspect_git_resource(repo, resource_id="resource-lane-b")
    db.append(registration_event(resource, source_id="probe"))

    controls = OwnerControlService(db, secret=SECRET, stop_path=RUN / "STOP")
    applied = controls.apply(
        "GOAL", target="GLOBAL", exact_text=TEXT, nonce="probe-1",
        signature=sign_command(SECRET, "GOAL", "GLOBAL", TEXT, "probe-1"),
    )
    created = sync_owner_goals(db)
    print("created objectives:", [item.objective_id for item in created])
    objective = created[0]
    print("objective status:", objective.status, "| required:", objective.required_capabilities)

    update_objective(
        db, objective.objective_id,
        {
            "resource_id": "resource-lane-b",
            "required_capabilities": ["write_registered_worktree_files"],
            "success_criteria": ["artifact exists"],
            "metadata": {
                **dict(objective.metadata or {}),
                "intent": {
                    "needs": ["web.authenticated_interaction", "artifact.create_update",
                              "native.document_verify", "repository.preserve"],
                    "inputs": {"authenticated_source_url": "http://127.0.0.1:1/dashboard"},
                },
            },
        },
    )
    state = rebuild_executive_state(db)
    current = state.objectives[objective.objective_id]
    print("after update:", current.status, current.resource_id, current.required_capabilities)

    from jarvis_foundry.executive.autonomy import coordinate_objectives, rank_objectives

    ranking = rank_objectives(db)
    for row in ranking:
        print("rank:", row.objective_id, "score", row.score, "eligible", row.eligible, "reason", getattr(row, "ineligibility_reason", None))
    coordination = coordinate_objectives(db)
    print("coordination selected:", coordination.selected_objective_id, "reason", coordination.reason)

    cycle = run_autonomy_cycle(
        db, cycle_id="probe-cycle", resource_id="resource-lane-b",
        capability="write_registered_worktree_files",
    )
    print(json.dumps({
        "status": cycle.status, "reason": cycle.reason,
        "portfolio": cycle.portfolio_decision, "materialization": cycle.materialization,
    }, indent=2, default=str))
    for event in db.events():
        if event.event_type.startswith(("executive.", "portfolio.", "fabric.gap", "mission.")):
            print("EVENT", event.event_type, json.dumps(event.payload, default=str)[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
