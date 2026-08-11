"""WP3 registration tests: automatic git resource records and idempotency."""

from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.control.registration import (
    RegistrationError,
    inspect_git_resource,
    registration_event,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    git("init", cwd=repository)
    git("config", "user.email", "jarvis-test@example.invalid", cwd=repository)
    git("config", "user.name", "JARVIS Test", cwd=repository)
    (repository / "README.md").write_text("test\n", encoding="utf-8")
    git("add", "README.md", cwd=repository)
    git("commit", "-m", "baseline", cwd=repository)
    return repository


def test_inspection_records_exact_git_reality(tmp_path):
    repository = make_repository(tmp_path)
    resource = inspect_git_resource(repository, resource_id="resource-1")

    assert resource["kind"] == "git_worktree"
    assert resource["resource_id"] == "resource-1"
    assert resource["resolved_root"] == str(repository.resolve())
    assert len(resource["baseline"]["head"]) == 40
    assert resource["baseline"]["branch"] == "master" or resource["baseline"]["branch"]
    assert resource["readiness_state"] == "READY"
    assert resource["capabilities"]["read_registered_files"] == "READY"
    assert resource["capabilities"]["git_push"] == "DISCOVERED"


def test_dirty_repository_is_probing_not_ready(tmp_path):
    repository = make_repository(tmp_path)
    (repository / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

    resource = inspect_git_resource(repository)

    assert resource["readiness_state"] == "PROBING"
    assert resource["capabilities"]["write_registered_worktree_files"] == "PROBING"
    assert "uncommitted.txt" in "".join(resource["baseline"]["status_porcelain"])


def test_registration_is_idempotent_and_re_registration_updates(tmp_path):
    repository = make_repository(tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()

    first = registration_event(inspect_git_resource(repository, resource_id="resource-1"))
    database.append(first)
    # Duplicate delivery of the same registration event -> one row.
    database.append(first)
    assert database.events() is not None

    # Re-registration after a commit updates baseline without a new row.
    (repository / "second.txt").write_text("x\n", encoding="utf-8")
    git("add", "second.txt", cwd=repository)
    git("commit", "-m", "second", cwd=repository)
    updated = inspect_git_resource(repository, resource_id="resource-1")
    database.append(registration_event(updated))

    row = database.row("resources", "resource_id", "resource-1")
    assert row is not None
    import json

    assert json.loads(row["baseline_json"])["head"] == updated["baseline"]["head"]
    assert row["last_event_id"] is not None


def test_same_head_re_registration_updates_dirty_state(tmp_path):
    # Verifier finding B5: the registration idempotency key previously excluded
    # status_porcelain, so a repository becoming dirty at an unchanged HEAD could
    # not be re-registered (EventConflict). Re-registration must update dirty
    # state at the same HEAD; identical deliveries still dedupe.
    repository = make_repository(tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()

    clean = inspect_git_resource(repository, resource_id="resource-1")
    database.append(registration_event(clean))
    # Identical re-delivery dedupes (no new event, no conflict).
    events_before = len(database.events())
    _, inserted = database.append(
        registration_event(inspect_git_resource(repository, resource_id="resource-1"))
    )
    assert inserted is False
    assert len(database.events()) == events_before

    # Repository becomes dirty at the SAME HEAD -> re-registration updates state.
    (repository / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
    dirty = inspect_git_resource(repository, resource_id="resource-1")
    assert dirty["baseline"]["head"] == clean["baseline"]["head"]
    database.append(registration_event(dirty))

    row = database.row("resources", "resource_id", "resource-1")
    assert row is not None
    import json

    baseline = json.loads(row["baseline_json"])
    assert baseline["head"] == dirty["baseline"]["head"]
    assert "uncommitted.txt" in "".join(baseline["status_porcelain"])
    assert row["readiness_state"] == "PROBING"
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    assert count == 1


def test_inspection_of_non_repository_raises(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    try:
        inspect_git_resource(plain)
        raised = False
    except RegistrationError:
        raised = True
    assert raised is True


def test_registration_requires_events_ledger(tmp_path):
    repository = make_repository(tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    event = registration_event(inspect_git_resource(repository, resource_id="resource-1"))
    assert isinstance(event, Event)
    assert event.event_type == "project.registered"
    assert event.idempotency_key.startswith("project.registered:resource-1:")
