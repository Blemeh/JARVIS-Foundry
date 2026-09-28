"""V7 readiness refresh: stale PROBING must not freeze current readiness.

Old resource evidence must not permanently freeze current resource
readiness.  When a registered git worktree moves from dirty/stale to
clean/current, the capability plane must record the fresh evaluation;
when it moves back to unsafe, mutation must block again.  History stays
append-only and mission-pinned baselines are untouched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from jarvis_foundry.control.capabilities import latest_capability_state, set_capability_state
from jarvis_foundry.control.registration import (
    inspect_git_resource,
    refresh_git_resource_readiness,
    registration_event,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import (
    build_plan,
    create_objective,
    rank_objectives,
)
from jarvis_foundry.reconcile import reconcile

WRITE = "write_registered_worktree_files"


def _table_count(database: FoundryDatabase, table: str) -> int:
    with database.connect(readonly=True) as connection:
        row = connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])


def _table_dump(database: FoundryDatabase, table: str) -> list[dict]:
    with database.connect(readonly=True) as connection:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repository(tmp_path: Path, name: str = "repo") -> Path:
    repository = tmp_path / name
    repository.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=repository)
    _git("config", "user.email", "v7-refresh@example.invalid", cwd=repository)
    _git("config", "user.name", "V7 Refresh", cwd=repository)
    (repository / "README.md").write_text("# refresh\n", encoding="utf-8")
    _git("add", "README.md", cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    return repository


def _db_with_probing_resource(tmp_path: Path) -> tuple[FoundryDatabase, Path]:
    """Register a dirty repo so write starts PROBING, like the live defect."""
    repository = _make_repository(tmp_path)
    (repository / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(registration_event(inspect_git_resource(repository, resource_id="resource-1")))
    row = database.row("resources", "resource_id", "resource-1")
    assert json.loads(row["capabilities_json"])[WRITE] == "PROBING"
    return database, repository


def _plane(database: FoundryDatabase) -> str | None:
    latest = latest_capability_state(database, "resource-1", WRITE)
    return (latest or {}).get("state")


def test_stale_probing_becomes_ready_on_clean_observation(tmp_path: Path) -> None:
    database, repository = _db_with_probing_resource(tmp_path)
    set_capability_state(database, "resource-1", WRITE, "PROBING", evidence={"source": "registration"})
    assert _plane(database) == "PROBING"

    (repository / "uncommitted.txt").unlink()  # worktree is clean/current now
    result = refresh_git_resource_readiness(database, "resource-1")

    assert result["refreshed"] is True
    assert _plane(database) == "READY"
    row = database.row("resources", "resource_id", "resource-1")
    assert json.loads(row["capabilities_json"])[WRITE] == "READY"
    # Old PROBING evidence stays auditable.
    states = [
        event.payload.get("state")
        for event in database.events(event_types=("capability.state_changed",))
    ]
    assert "PROBING" in states and "READY" in states
    assert reconcile(database).consistent is True


def test_ready_returns_to_probing_on_unsafe_observation(tmp_path: Path) -> None:
    database, repository = _db_with_probing_resource(tmp_path)
    set_capability_state(database, "resource-1", WRITE, "PROBING", evidence={"source": "registration"})
    (repository / "uncommitted.txt").unlink()
    assert refresh_git_resource_readiness(database, "resource-1")["refreshed"] is True
    assert _plane(database) == "READY"

    (repository / "uncommitted.txt").write_text("dirty again\n", encoding="utf-8")
    result = refresh_git_resource_readiness(database, "resource-1")

    assert result["refreshed"] is True
    assert _plane(database) == "PROBING"
    row = database.row("resources", "resource_id", "resource-1")
    assert json.loads(row["capabilities_json"])[WRITE] == "PROBING"


def test_replay_and_restart_converge_without_duplicates(tmp_path: Path) -> None:
    database, repository = _db_with_probing_resource(tmp_path)
    set_capability_state(database, "resource-1", WRITE, "PROBING", evidence={"source": "registration"})
    (repository / "uncommitted.txt").unlink()
    assert refresh_git_resource_readiness(database, "resource-1")["refreshed"] is True

    events_before = len(database.events())
    second = refresh_git_resource_readiness(database, "resource-1")
    assert second["refreshed"] is False
    assert len(database.events()) == events_before  # unchanged observation is event-silent

    before = json.loads(database.row("resources", "resource_id", "resource-1")["capabilities_json"])
    assert database.rebuild_projections() is not None
    after = json.loads(database.row("resources", "resource_id", "resource-1")["capabilities_json"])
    assert after == before
    assert _plane(database) == "READY"
    assert reconcile(database).consistent is True


def test_incident_owned_state_is_never_overwritten(tmp_path: Path) -> None:
    from jarvis_foundry.control.capabilities import BLOCKING_STATES

    for incident_state in sorted(BLOCKING_STATES):
        database, repository = _db_with_probing_resource(tmp_path / incident_state.lower())
        set_capability_state(database, "resource-1", WRITE, incident_state, evidence={"source": "incident"})
        (repository / "uncommitted.txt").unlink()  # clean, but incident owns the state

        result = refresh_git_resource_readiness(database, "resource-1")

        assert _plane(database) == incident_state
        assert result["refreshed"] is True  # baseline still updated
        row = database.row("resources", "resource_id", "resource-1")
        assert json.loads(row["capabilities_json"])[WRITE] == incident_state


def test_failed_inspection_records_nothing(tmp_path: Path) -> None:
    database, repository = _db_with_probing_resource(tmp_path)
    set_capability_state(database, "resource-1", WRITE, "PROBING", evidence={"source": "registration"})
    events_before = len(database.events())

    missing = refresh_git_resource_readiness(database, "resource-unknown")
    assert missing["refreshed"] is False

    # Point the checkout at a path that never existed: inspection must fail
    # closed without recording anything.
    with database.connect() as connection:
        connection.execute(
            "UPDATE resources SET resolved_root = ?, root_path = ? WHERE resource_id = ?",
            (str(tmp_path / "gone"), str(tmp_path / "gone"), "resource-1"),
        )
    gone = refresh_git_resource_readiness(database, "resource-1")
    assert gone["refreshed"] is False
    assert len(database.events()) == events_before
    assert _plane(database) == "PROBING"


def test_runtime_exhaust_does_not_count_as_dirt(tmp_path: Path) -> None:
    database, repository = _db_with_probing_resource(tmp_path)
    set_capability_state(database, "resource-1", WRITE, "PROBING", evidence={"source": "registration"})
    (repository / "uncommitted.txt").unlink()
    exhaust = repository / ".jarvis" / "owner-goals" / "abc123"
    exhaust.mkdir(parents=True)
    (exhaust / "diagnosis.md").write_text("# diagnosis\n", encoding="utf-8")
    (repository / ".jarvis-result.json").write_text("{}\n", encoding="utf-8")

    result = refresh_git_resource_readiness(database, "resource-1")

    assert result["refreshed"] is True
    assert _plane(database) == "READY"
    # Full status truth is still stored.
    row = database.row("resources", "resource_id", "resource-1")
    stored = json.loads(row["baseline_json"])["status_porcelain"]
    assert any(".jarvis" in line for line in stored)


def test_real_dirt_beside_exhaust_still_blocks(tmp_path: Path) -> None:
    from jarvis_foundry.control.registration import inspect_git_resource as inspect

    repository = _make_repository(tmp_path, name="repo2")
    (repository / ".jarvis").mkdir()
    (repository / ".jarvis" / "note.md").write_text("exhaust\n", encoding="utf-8")
    (repository / "user-change.txt").write_text("real dirt\n", encoding="utf-8")
    resource = inspect(repository, resource_id="resource-2")
    assert resource["capabilities"][WRITE] == "PROBING"
    assert resource["readiness_state"] == "PROBING"


def test_mission_baseline_is_untouched_by_refresh(tmp_path: Path) -> None:
    database, repository = _db_with_probing_resource(tmp_path)
    missions_before = _table_dump(database, "missions")
    contracts_before = _table_dump(database, "mission_step_contracts")
    (repository / "uncommitted.txt").unlink()
    refresh_git_resource_readiness(database, "resource-1")
    assert _table_dump(database, "missions") == missions_before
    assert _table_dump(database, "mission_step_contracts") == contracts_before


def test_parked_objective_becomes_eligible_after_refresh(tmp_path: Path) -> None:
    database, repository = _db_with_probing_resource(tmp_path)
    set_capability_state(database, "resource-1", WRITE, "PROBING", evidence={"source": "registration"})
    create_objective(
        database, "Do bounded work", objective_id="objective-1",
        resource_id="resource-1", required_capabilities=(WRITE,),
    )
    build_plan(database, "objective-1", [{
        "step_id": "execute",
        "description": "Do bounded work",
        "resource_id": "resource-1",
        "required_capabilities": [WRITE],
    }])
    blocked = next(item for item in rank_objectives(database) if item.objective_id == "objective-1")
    assert blocked.eligible is False
    assert any(WRITE in blocker and "PROBING" in blocker for blocker in blocked.blockers)

    (repository / "uncommitted.txt").unlink()
    refresh_git_resource_readiness(database, "resource-1")

    eligible = next(item for item in rank_objectives(database) if item.objective_id == "objective-1")
    assert eligible.eligible is True
    assert eligible.blockers == ()


def test_refresh_creates_no_mission_or_wake(tmp_path: Path) -> None:
    database, repository = _db_with_probing_resource(tmp_path)
    set_capability_state(database, "resource-1", WRITE, "PROBING", evidence={"source": "registration"})
    missions_before = _table_count(database, "missions")
    (repository / "uncommitted.txt").unlink()
    refresh_git_resource_readiness(database, "resource-1")
    assert _table_count(database, "missions") == missions_before
    assert database.events(event_types=("mission.created",)) == []
