"""Disposable proof of the production owner-facing execution spine.

The proof uses the real local HTTP owner endpoint and a Supervisor-owned
worker-loop child.  The deterministic Lab adapter is fault injection only; the
result is therefore PROVEN_ISOLATED_ONLY and must never be reported as a real
provider proof.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import Request, urlopen

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.lab_hermes import OWNED_PID_NAME
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.service import WORKER_LOOP_RUN_ID, Supervisor


RESOURCE_ID = "resource-full-product-isolated-proof"


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True, capture_output=True, text=True,
    )


def _setup(home: Path) -> tuple[FoundryDatabase, Path]:
    repository = home / "disposable-repository"
    repository.mkdir(parents=True)
    _git(repository, "init")
    _git(repository, "config", "user.email", "jarvis-proof@example.invalid")
    _git(repository, "config", "user.name", "JARVIS Isolated Proof")
    (repository / "README.md").write_text(
        "# Disposable JARVIS product proof\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "baseline")
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=RESOURCE_ID)))
    set_capability_state(
        database, RESOURCE_ID, "write_registered_worktree_files", "READY",
        evidence={"source": "full-product-isolated-spine-proof"},
    )
    return database, repository


def _write_config(
    path: Path, home: Path, *,
    false_success_step: str | None = None,
    crash_step: str | None = None,
) -> None:
    lines = [
        "enabled: true",
        "status: ACTIVE",
        f"resource_id: {RESOURCE_ID}",
        "provider: opencode_go",
        "model: deepseek-v4-flash",
        "lab_scripted_hermes: true",
        f"runtime_state_root: {json.dumps(str(home / 'state'))}",
        "executive:",
        "  portfolio_selection_enabled: true",
        "verification:",
        "  max_repairs: 2",
    ]
    if false_success_step:
        lines.append(f"lab_false_success_step_id: {false_success_step}")
    if crash_step:
        lines.extend([
            "lab_crash_after_write: true",
            f"lab_crash_step_id: {crash_step}",
        ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _supervisor(
    database: FoundryDatabase, home: Path, config: Path, instance: str,
) -> Supervisor:
    service = Supervisor(
        database, home / "EMERGENCY_STOP",
        instance_id=instance,
        spawn_worker_loop=True,
        worker_loop_config=config,
        command_center_port=0,
        # The proof intentionally drives several heartbeats per second;
        # production runs on the normal 60-second cadence.
        ledger_heartbeat_seconds=0,
    )
    status = service.start()
    if status != "RUNNING":
        raise RuntimeError(f"Supervisor did not start: {status}")
    return service


def _submit(supervisor: Supervisor, objective: str) -> dict:
    if supervisor._command_center is None:
        raise RuntimeError("Supervisor did not own Command Center")
    host, port = supervisor._command_center.address
    request = Request(
        f"http://{host}:{port}/api/owner-command",
        data=json.dumps({
            "command_type": "GOAL",
            "target": "GLOBAL",
            "exact_text": objective,
        }).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def _run_one_owned_cycle(supervisor: Supervisor, *, timeout: float = 40) -> None:
    database = supervisor.database
    before_cycles = sum(
        database.event_count(event_type=event_type)
        for event_type in ("worker_loop.cycle_started", "worker_loop.frozen")
    )
    heartbeat_status = supervisor.heartbeat()
    if heartbeat_status == "FROZEN":
        return
    deadline = time.monotonic() + timeout
    observed_alive = False
    while time.monotonic() < deadline:
        status = supervisor.workers.status(WORKER_LOOP_RUN_ID)
        observed_alive = observed_alive or status.alive
        after_cycles = sum(
            database.event_count(event_type=event_type)
            for event_type in ("worker_loop.cycle_started", "worker_loop.frozen")
        )
        if not status.alive and (observed_alive or after_cycles > before_cycles):
            return
        time.sleep(0.1)
    raise TimeoutError("Supervisor-owned worker-loop cycle did not settle")


def _missions(database: FoundryDatabase) -> list[dict]:
    with database.connect(readonly=True) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM missions ORDER BY rowid").fetchall()]


def _verdicts(database: FoundryDatabase, mission_id: str, step_id: str) -> list[str]:
    with database.connect(readonly=True) as connection:
        return [str(row[0]) for row in connection.execute(
            "SELECT status FROM verification_verdicts "
            "WHERE mission_id = ? AND subject_id = ? ORDER BY rowid",
            (mission_id, step_id),
        ).fetchall()]


def _run_owned_crash_cycle(
    supervisor: Supervisor, repository: Path, *, timeout: float = 60,
) -> int:
    """Inject one exact kill against the Lab adapter's owned child PID."""
    supervisor.heartbeat()
    pid_path = repository / OWNED_PID_NAME
    deadline = time.monotonic() + 20
    owned_pid = 0
    while time.monotonic() < deadline:
        if pid_path.is_file():
            owned_pid = int(pid_path.read_text(encoding="utf-8").strip())
            if owned_pid > 0:
                break
        time.sleep(0.05)
    if owned_pid <= 0:
        raise TimeoutError("owned crash-test worker PID was not published")
    os.kill(owned_pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not supervisor.workers.status(WORKER_LOOP_RUN_ID).alive:
            return owned_pid
        time.sleep(0.1)
    raise TimeoutError("worker-loop did not settle after owned child kill")


def run_proof(home: Path) -> dict:
    home = home.resolve()
    if home.exists():
        raise FileExistsError(
            f"refusing to overwrite existing proof home: {home}")
    home.mkdir(parents=True)
    database, repository = _setup(home)
    config = home / "worker-routing.yaml"
    proofs: dict[str, bool] = {}
    details: dict[str, object] = {}

    # Mission 1: real HTTP admission, restart between steps, deliberate false
    # success, automatic repair, fresh verification, and completion.
    _write_config(config, home, false_success_step="02-execute")
    first = _supervisor(database, home, config, "proof-supervisor-a")
    applied = _submit(
        first,
        "Create restart-continuity evidence. success: durable outcome receipt exists",
    )
    proofs["command_center_submit"] = applied.get("status") == "APPLIED"
    _run_one_owned_cycle(first)
    mission_1 = _missions(database)[0]
    proofs["step_a_selected_and_passed"] = (
        mission_1["state"] == "ACTION_SELECTION"
        and _verdicts(database, mission_1["mission_id"], "01-plan") == ["PASS"]
    )
    first.stop()
    second = _supervisor(database, home, config, "proof-supervisor-b")
    _run_one_owned_cycle(second)
    mission_1 = _missions(database)[0]
    proofs["supervisor_restart_continuation"] = mission_1["state"] == "COMPLETE"
    proofs["false_success_rejected_and_repaired"] = (
        _verdicts(database, mission_1["mission_id"], "02-execute")
        == ["FAIL", "PASS"]
    )

    # Mission 2: STOP between verified steps. A frozen cycle must not dispatch
    # step B; RESUME then continues without an owner "continue" command.
    applied = _submit(
        second,
        "Create stop-resume evidence. success: durable outcome receipt exists",
    )
    proofs["second_goal_admitted"] = applied.get("status") == "APPLIED"
    _run_one_owned_cycle(second)
    mission_2 = _missions(database)[1]
    before_runs = database.event_count(event_type="agent_run.started")
    EmergencyStop(home / "EMERGENCY_STOP").activate("isolated STOP proof")
    _run_one_owned_cycle(second)
    after_runs = database.event_count(event_type="agent_run.started")
    proofs["stop_blocks_dispatch"] = (
        before_runs == after_runs and mission_2["state"] == "ACTION_SELECTION")
    EmergencyStop(home / "EMERGENCY_STOP").clear()
    _run_one_owned_cycle(second)
    mission_2 = _missions(database)[1]
    proofs["resume_continues"] = mission_2["state"] == "COMPLETE"
    second.stop()

    # Mission 3: the owned builder dies after a partial write. MissionLoop
    # persists failure, repairs from current state, verifies, and completes.
    _write_config(config, home, crash_step="02-execute")
    third = _supervisor(database, home, config, "proof-supervisor-c")
    applied = _submit(
        third,
        "Create crash-recovery evidence. success: durable outcome receipt exists",
    )
    proofs["third_goal_admitted"] = applied.get("status") == "APPLIED"
    _run_one_owned_cycle(third)
    owned_crash_pid = _run_owned_crash_cycle(third, repository)
    mission_3 = _missions(database)[2]
    proofs["worker_crash_repaired"] = (
        mission_3["state"] == "COMPLETE"
        and _verdicts(database, mission_3["mission_id"], "02-execute")[-1:] == ["PASS"]
    )
    _run_one_owned_cycle(third)
    third.stop()

    events = database.events()
    snapshot = build_snapshot(database, stop_path=home / "EMERGENCY_STOP")
    proofs["durable_ui_projection"] = (
        len(snapshot["missions"]) == 3
        and all(item["state"] == "COMPLETE" for item in snapshot["missions"])
    )
    proofs["no_duplicate_missions"] = len(_missions(database)) == 3
    proofs["useful_wait_after_completion"] = any(
        event.event_type == "worker_loop.waiting" for event in events)
    details.update({
        "home": str(home),
        "database": str(database.path),
        "repository": str(repository),
        "mission_ids": [item["mission_id"] for item in _missions(database)],
        "owned_crash_pid": owned_crash_pid,
        "event_count": len(events),
        "trust_state": "LAB",
        "capability_classification": "PROVEN_ISOLATED_ONLY",
    })
    verdict = "PASS" if all(proofs.values()) else "FAIL"
    result = {"verdict": verdict, "proofs": proofs, "details": details}
    output = home / "evidence" / "FULL_PRODUCT_ISOLATED_SPINE_PROOF.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    result["artifact"] = str(output)
    result["artifact_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: full_product_isolated_spine_proof.py <disposable-home>",
              file=sys.stderr)
        return 2
    result = run_proof(Path(sys.argv[1]))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
