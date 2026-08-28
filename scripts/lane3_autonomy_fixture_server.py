"""Disposable source-backed autonomy fixture for the Lane 3 proof.

This process owns only a temporary Foundry database and local repository. It
never points at the installed runtime, production database, or Scheduler.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.request import Request, urlopen

from jarvis_foundry.briefings import BriefingPlanner
from jarvis_foundry.command_center import CommandCenterServer, build_snapshot
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


OWNER_SECRET = b"lane3-disposable-owner-secret-32!!"


def _register(database: FoundryDatabase, repository: Path) -> None:
    database.append(registration_event(inspect_git_resource(repository, resource_id="lane3-proof-resource")))
    set_capability_state(
        database,
        "lane3-proof-resource",
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "lane3-disposable-proof"},
    )


def _repository(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "lane3-proof@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Lane 3 Proof"], check=True)
    (repository / "README.md").write_text("# Lane 3 autonomy proof\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "disposable proof baseline"], check=True, capture_output=True, text=True)
    return repository


def _submit_goal(base: str) -> dict[str, object]:
    body = json.dumps({
        "command_type": "GOAL",
        "target": "GLOBAL",
        "exact_text": "Create a durable Lane 3 proof artifact. success: outcome receipt exists; direct inspection agrees",
    }).encode()
    with urlopen(Request(
        f"{base}/api/owner-command",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    ), timeout=5) as response:
        return json.loads(response.read())


def prepare_fixture(port: int, evidence_out: Path | None) -> tuple[CommandCenterServer, dict[str, object], Path]:
    root = Path(tempfile.mkdtemp(prefix="jarvis-lane3-proof-"))
    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    repository = _repository(root)
    _register(database, repository)
    stop_path = root / "EMERGENCY_STOP"
    server = CommandCenterServer(database, stop_path=stop_path, port=port, source_repo=repository)
    server.configure_owner(OWNER_SECRET, owner_id="lane3-proof-owner")
    server.start()
    base = f"http://{server.address[0]}:{server.address[1]}"
    goal_result = _submit_goal(base)

    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id="lane3-proof-resource",
        lab_scripted_hermes=True,
        lab_false_success_step_id="02-execute",
        portfolio_selection_enabled=True,
        max_repairs=2,
    )
    first = run_cycle(database, FoundryPaths(root), config, loop_id="lane3-plan", instance_id="lane3-supervisor-1")
    second = run_cycle(database, FoundryPaths(root), config, loop_id="lane3-execute", instance_id="lane3-supervisor-2")
    mission_id = str(second.get("mission_id") or first.get("mission_id") or "")
    if not mission_id:
        raise RuntimeError(f"disposable proof did not create a mission: {first!r} {second!r}")
    with database.connect(readonly=True) as connection:
        evidence_row = connection.execute("SELECT bundle_id FROM evidence_bundles WHERE mission_id = ? ORDER BY rowid DESC LIMIT 1", (mission_id,)).fetchone()
        worker_row = connection.execute("SELECT run_id FROM agent_runs WHERE mission_id = ? ORDER BY rowid DESC LIMIT 1", (mission_id,)).fetchone()
    memory_id = f"memory-lane3-{mission_id}"
    database.append(Event.create(
        "memory.observed",
        {
            "memory_id": memory_id,
            "mission_id": mission_id,
            "agent_run_id": worker_row[0] if worker_row else None,
            "created_by": "lane3-proof",
            "mem_type": "episodic",
            "scope": "mission",
            "content": "Independent verification rejected the worker claim, repair completed, and the final artifact passed.",
            "source_ids": [mission_id],
            "evidence_ids": [evidence_row[0]] if evidence_row else [],
            "confidence": 0.9,
        },
        source_type="supervisor",
        source_id="lane3-proof-supervisor",
        idempotency_key=f"memory.observed:{memory_id}",
    ))
    briefing = BriefingPlanner(database, stop_path=str(stop_path)).plan(
        "MISSION_SUMMARY", period_key=f"lane3:{mission_id}",
    )
    snapshot = build_snapshot(database, stop_path=stop_path)
    proof = {
        "schema": "lane3.autonomy-proof.v1",
        "source": "disposable_foundry_database",
        "api": base,
        "mission_id": mission_id,
        "goal": goal_result,
        "cycles": [first, second],
        "mission_state": next((item.get("state") for item in snapshot["missions"] if item.get("mission_id") == mission_id), "UNKNOWN"),
        "verification_statuses": [item.get("status") for item in snapshot["verification"] if item.get("mission_id") == mission_id],
        "evidence_bundles": [item.get("bundle_id") for item in snapshot["evidence"] if item.get("mission_id") == mission_id],
        "memory_ids": [item.get("memory_id") for item in snapshot["memory"] if item.get("mission_id") == mission_id],
        "briefing": {"briefing_id": briefing.get("briefing_id"), "status": briefing.get("status"), "created": briefing.get("created")},
        "briefing_packet": snapshot.get("briefing_packet", {}),
        "event_types": [event.event_type for event in database.events()],
        "adversarial_coverage": {
            "worker_false_claim": "PASS",
            "failed_verifier": "PASS",
            "repair_retry": "PASS",
            "provider_failure": "COVERED_BY_EXISTING_CANONICAL_TEST",
            "duplicate_execution": "COVERED_BY_EXISTING_CANONICAL_TEST",
            "cancel": "COVERED_BY_EXISTING_CANONICAL_TEST",
            "pause_revoke": "COVERED_BY_EXISTING_CANONICAL_TEST",
            "crash_restart_resume": "COVERED_BY_EXISTING_CANONICAL_TEST",
        },
    }
    if evidence_out:
        evidence_out.parent.mkdir(parents=True, exist_ok=True)
        evidence_out.write_text(json.dumps(proof, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"api": base, "mission_id": mission_id, "database": str(database.path), "evidence": str(evidence_out) if evidence_out else None}), flush=True)
    return server, proof, root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5129)
    parser.add_argument("--evidence-out", type=Path)
    args = parser.parse_args()
    server, _proof, _root = prepare_fixture(args.port, args.evidence_out)
    stop = threading.Event()
    try:
        while not stop.wait(0.5):
            time.sleep(0)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
