"""Shared fixtures for hybrid worker tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def make_repository(tmp_path: Path, name: str = "repo") -> Path:
    repository = tmp_path / name
    repository.mkdir()
    git("init", cwd=repository)
    git("config", "user.email", "jarvis-test@example.invalid", cwd=repository)
    git("config", "user.name", "JARVIS Test", cwd=repository)
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    git("add", "README.md", cwd=repository)
    git("commit", "-m", "baseline", cwd=repository)
    return repository


def mission_database(
    tmp_path: Path,
    *,
    mission_id: str = "mission-1",
    resource_id: str = "resource-1",
    next_action: str | None = "deliver feature X",
) -> tuple[FoundryDatabase, Path]:
    """Start + registered git resource + mission ready to dispatch."""
    repository = make_repository(tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-11T15:33:05Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=resource_id)))
    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": "hybrid worker gate mission",
         "resource_id": resource_id, "state": "PLANNED",
         "acceptance_criteria": ["worker step verified and accepted"],
         "next_action": next_action},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    for state in ("ORIENTING", "ACTION_SELECTION"):
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": next_action},
            source_type="test", source_id="test-suite",
            idempotency_key=f"mission.state_changed:{mission_id}:{state}",
        ))
    set_capability_state(database, resource_id, "git_local_commit", "READY",
                         evidence={"source": "worker-test"})
    return database, repository


def make_controller(
    tmp_path: Path,
    *,
    router=None,
    registry=None,
    stop_path: Path | None = None,
    **kwargs,
):
    from jarvis_foundry.workers.adapters.registry import WorkerRegistry
    from jarvis_foundry.workers.evidence import EvidenceReconciler
    from jarvis_foundry.workers.orchestrator import HybridStepController
    from jarvis_foundry.workers.router import WorkerRouter

    database, repository = mission_database(tmp_path)
    policy = ActionPolicy(database, stop_path or (tmp_path / "STOP"))
    controller = HybridStepController(
        database=database,
        router=router or WorkerRouter(),
        registry=registry or WorkerRegistry(),
        policy=policy,
        stop_path=stop_path or (tmp_path / "STOP"),
        reconciler=EvidenceReconciler(),
        **kwargs,
    )
    return controller, database, repository
