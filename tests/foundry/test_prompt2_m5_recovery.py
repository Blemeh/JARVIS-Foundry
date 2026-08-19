"""Prompt 2 M5: owned-worker crash after a real partial worktree mutation."""
from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.deployment.lab_hermes import OWNED_PID_NAME, PARTIAL_BYTES
from jarvis_foundry.mission.contracts import MissionStepContract, record_step_contract
from jarvis_foundry.mission.loop import PASSED
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle

from worker_test_helpers import mission_database


def _contract(step_id: str, artifact: str, token: str) -> MissionStepContract:
    return MissionStepContract(
        mission_id="mission-1",
        step_id=step_id,
        version=1,
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        target_relative_path=artifact,
        instructions=f"create {artifact} containing {token.strip()}",
        expected_effects=(f"{artifact} exists",),
        rollback={"method": "git_reset_soft", "reversible": True},
        risk_class="C1",
        verification_criteria=(f"{artifact} contains {token.strip()}",),
        expected_artifacts=(artifact,),
        forbidden_paths=("owner-private",),
        recorded_by="supervisor",
        source="prompt2-m5",
        provenance={"kernel": "worker-crash"},
    )


def _kill_owned_worker(repository: Path, *, seen: dict[str, bool]) -> None:
    artifact = repository / "step_a.md"
    pid_path = repository / OWNED_PID_NAME
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if artifact.is_file() and artifact.read_text(encoding="utf-8") == PARTIAL_BYTES:
            seen["partial"] = True
            if pid_path.is_file():
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                seen["pid"] = pid
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                seen["killed"] = True
                return
        time.sleep(0.05)


def test_owned_worker_crash_after_partial_write_repairs_once(tmp_path):
    database, repository = mission_database(
        tmp_path, next_action="create step_a.md containing STEP-A")
    set_capability_state(
        database, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "prompt2-m5"})
    record_step_contract(database, _contract("step-a", "step_a.md", "STEP-A"))
    cfg = WorkerRuntimeConfig(
        enabled=True,
        lab_scripted_hermes=True,
        lab_crash_after_write=True,
        lab_crash_step_id="step-a",
        runtime_state_root=str(tmp_path / "state"),
        max_repairs=2,
        role_signals={
            "builder": {"bounded": True, "explicit_acceptance": True, "reversible": True},
            "verifier": {"bounded": True, "explicit_acceptance": True},
            "repair": {"ambiguous": True, "root_cause_required": True, "recovery": True},
        },
    )
    seen: dict[str, bool] = {}
    killer = threading.Thread(target=_kill_owned_worker, args=(repository,), kwargs={"seen": seen})
    killer.start()
    reports = []
    for index in range(6):
        reports.append(run_cycle(
            database, FoundryPaths(tmp_path), cfg,
            loop_id=f"crash-{index}", instance_id=f"i-{index}"))
        mission = database.row("missions", "mission_id", "mission-1")
        if mission["state"] == "COMPLETE":
            break
    killer.join(timeout=5)
    assert seen.get("partial") is True
    assert seen.get("killed") is True
    assert (repository / "step_a.md").read_text(encoding="utf-8") == "STEP-A\n"
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "COMPLETE"
    types = [event.event_type for event in database.events()]
    assert "verification.failed" in types
    failed = [event for event in database.events() if event.event_type == "verification.failed"]
    assert any(
        "recover partial worker mutation and re-verify" in (event.payload.get("repair_requirements") or [])
        for event in failed
    )
    assert "verification.passed" in types
    assert PASSED in [item["outcome"] for item in reports]
    with database.connect(readonly=True) as connection:
        leases = list(connection.execute("SELECT lease_key FROM leases"))
    assert not any(str(row[0]).startswith("worker:lab-crash:") for row in leases)
    writes = [event for event in database.events() if event.event_type == "verification.passed"]
    assert len(writes) == 1
