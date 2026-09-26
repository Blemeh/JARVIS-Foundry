from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.project_context import resolve_registered_project_context


def test_registered_project_profile_is_durable_advisory_context(tmp_path: Path) -> None:
    root = tmp_path / "owner-project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "README.md").write_text("# Owner project\n", encoding="utf-8")
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "project.registered",
        {
            "resource_id": "owner-project",
            "kind": "local_repository",
            "root_path": str(root),
            "resolved_root": str(root.resolve()),
            "baseline": {},
            "capabilities": {},
            "readiness_state": "READY",
        },
        source_type="supervisor",
        source_id="fixture",
        idempotency_key="project.registered:owner-project",
    ))

    first = resolve_registered_project_context(
        database, project_id="owner-project", resource_id="owner-project",
        root=root, cycle_id="cycle-one",
    )
    replay = resolve_registered_project_context(
        database, project_id="owner-project", resource_id="owner-project",
        root=root, cycle_id="cycle-two",
    )
    same_cycle = resolve_registered_project_context(
        database, project_id="owner-project", resource_id="owner-project",
        root=root, cycle_id="cycle-one",
    )
    within_poll_interval = resolve_registered_project_context(
        database, project_id="owner-project", resource_id="owner-project",
        root=root, cycle_id="cycle-three", min_interval_s=300,
    )

    assert first["status"] == replay["status"] == "AVAILABLE"
    assert first["authority"] == "ADVISORY_REPOSITORY_DATA_ONLY"
    assert first["lifecycle"] == "FIRST_USE"
    assert replay["lifecycle"] == "UNCHANGED_REPLAY"
    assert first["profile_hash"] == replay["profile_hash"]
    assert same_cycle == first
    assert within_poll_interval == replay
    assert (tmp_path / "state" / "project_profiles").is_dir()
    events = database.events(
        readonly=True, event_types=("executive.project_context_resolved",),
    )
    assert len(events) == 2
    assert all("README" not in str(event.payload) for event in events)

    wrong_root = resolve_registered_project_context(
        database, project_id="owner-project", resource_id="owner-project",
        root=tmp_path, cycle_id="wrong-root",
    )
    assert wrong_root["status"] == "UNAVAILABLE"
    assert len(database.events(
        readonly=True, event_types=("executive.project_context_resolved",),
    )) == 2
