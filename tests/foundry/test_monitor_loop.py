"""V4-S5 (R6): the continuous loop must use the DURABLE MONITOR REGISTRY
for real monitored sources (DIR-2026-08-12 item 6).

- run_one_loop registers vault/handoffs/repo/system monitors (deduped by
  source_kind+source_ref) and POLLS them through poll_monitor each cycle
  (records last_poll_at + candidates_found);
- repeated polls deduplicate (canonical candidate detection);
- no roots -> no fabricated monitors; honest zero-item polls still
  recorded (the source was polled on schedule).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.v3_loop import _ensure_monitors


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "project.registered",
        {"resource_id": "resource-test", "kind": "git_worktree",
         "root_path": str(tmp_path), "resolved_root": str(tmp_path),
         "baseline": {"branch": "main", "head": "x", "remotes": [],
                      "status_porcelain": []},
         "capabilities": {
             "write_registered_worktree_files": "READY",
             "read_registered_files": "READY"},
         "readiness_state": "READY"},
        source_type="test", source_id="test-suite",
        idempotency_key="project.registered:resource-test"))
    return database


def monitor_rows(db: FoundryDatabase) -> list[dict]:
    with db.connect() as connection:
        return [dict(r) for r in connection.execute(
            "SELECT monitor_id, source_kind, source_ref, enabled, "
            "last_poll_at FROM monitors ORDER BY source_kind").fetchall()]


def test_ensure_monitors_registers_real_sources(db, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    ids = _ensure_monitors(
        db, "mission-1", vault_root=vault, handoff_dir=handoffs,
        repo_paths=(tmp_path / "repo",))
    assert set(ids) == {"vault", "handoffs", "repo", "system"}
    rows = monitor_rows(db)
    assert len(rows) == 4
    kinds = {r["source_kind"] for r in rows}
    assert kinds == {"vault", "handoffs", "repo", "system"}
    assert all(r["enabled"] == 1 for r in rows)


def test_ensure_monitors_dedupes_repeat_registration(db, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    first = _ensure_monitors(db, "mission-1", vault_root=vault)
    second = _ensure_monitors(db, "mission-1", vault_root=vault)
    assert first == second
    assert len(monitor_rows(db)) == 2  # vault + system — no duplicates


def test_ensure_monitors_no_roots_no_fabricated_sources(db):
    ids = _ensure_monitors(db, "mission-1")
    assert ids == {} or set(ids) == {"system"}
    # the ledger is always observed; nothing else is invented
    assert set(ids) <= {"system"}


def test_loop_registers_and_polls_monitors(db, tmp_path):
    from jarvis_foundry.v3_loop import run_one_loop

    vault = tmp_path / "vault"
    vault.mkdir()
    report = run_one_loop(
        db, loop_id="m1", adapter=FakeHermesAdapter(), router=RoleRouter(),
        mission_id="mission-1", resource_id="resource-test",
        vault_root=vault, target_root=tmp_path / "target",
        max_items=5, max_bytes=20_000)
    assert report["outcome"] == "WAITING"  # honest: no candidates
    # the loop still registered + polled the real sources
    assert report["monitors"]["vault"]["polled"] is True
    assert report["monitors"]["system"]["polled"] is True
    rows = monitor_rows(db)
    assert {r["source_kind"] for r in rows} == {"vault", "system"}
    assert all(r["last_poll_at"] is not None for r in rows)
    with db.connect() as connection:
        polls = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.monitor_polled'").fetchone()[0]
    assert polls == 2  # one poll per registered monitor


def test_loop_repeat_runs_dedupe_polls(db, tmp_path):
    from jarvis_foundry.v3_loop import run_one_loop

    vault = tmp_path / "vault"
    vault.mkdir()
    run_one_loop(db, loop_id="r1", adapter=FakeHermesAdapter(),
                 router=RoleRouter(), mission_id="mission-1",
                 resource_id="resource-test", vault_root=vault,
                 target_root=tmp_path / "target",
                 max_items=5, max_bytes=20_000)
    run_one_loop(db, loop_id="r2", adapter=FakeHermesAdapter(),
                 router=RoleRouter(), mission_id="mission-1",
                 resource_id="resource-test", vault_root=vault,
                 target_root=tmp_path / "target",
                 max_items=5, max_bytes=20_000)
    # still exactly two monitors (registration deduped)
    assert len(monitor_rows(db)) == 2
    with db.connect() as connection:
        polls = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.monitor_polled'").fetchone()[0]
        candidates = connection.execute(
            "SELECT COUNT(*) FROM candidates").fetchone()[0]
    assert polls == 4  # one poll per monitor per cycle
    assert candidates == 0  # repeated polls deduplicate (nothing invented)


def test_poll_disabled_monitor_raises(db, tmp_path):
    from jarvis_foundry.discovery_monitors import (
        disable_monitor,
        poll_monitor,
        register_monitor,
    )

    monitor_id = register_monitor(db, "mission-1", "vault", str(tmp_path))
    disable_monitor(db, monitor_id, "test")
    with pytest.raises(ValueError):
        poll_monitor(db, monitor_id, [{"objective": "x"}])
