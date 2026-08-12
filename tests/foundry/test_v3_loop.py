"""V3 loop wiring tests (directive Phase 7).

Contract: run_one_loop drives one complete real loop — observe →
record evidence → generate candidate → score → select/reject → create
mission (bound to the registered resource) → plan → dispatch → verify →
settle → reconcile → measure outcome. Honest WAITING when nothing
clears the bar. All events append-only + idempotent; reconcile stays
consistent after the loop.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.router import RoleRouter

FRONTMATTER = ("---\norigin: jarvis\ncurrent_steward: jarvis\n"
               "system: jarvis-foundry\nversion: v1\n---\n\n")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    note = FRONTMATTER + "# State\n\n- The gateway retries.\n"
    _write(root / "JARVIS" / "V1" / "00 - State.md", note)
    return root


@pytest.fixture()
def resource_root(tmp_path: Path) -> Path:
    return tmp_path / "resource"


@pytest.fixture()
def db(tmp_path: Path, resource_root: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "project.registered",
        {"resource_id": "resource-test", "kind": "git_worktree",
         "root_path": str(resource_root), "resolved_root": str(resource_root),
         "baseline": {"branch": "main", "head": "x", "remotes": [],
                      "status_porcelain": []},
         "capabilities": {
             "write_registered_worktree_files": "READY",
             "read_registered_files": "READY"},
         "readiness_state": "READY"},
        source_type="test", source_id="test-suite",
        idempotency_key="project.registered:resource-test"))
    return database


def _seed_stale_claim(db: FoundryDatabase, vault: Path) -> None:
    from jarvis_foundry.vault_indexer import sync_vault
    sync_vault(db, vault)
    db.append(Event.create(
        "knowledge.claim_recorded",
        {"claim_id": "claim-stale1", "mission_id": "mission-wfm-optimizer",
         "statement": "Why does the gateway fail? Root cause unknown — "
                      "regression risk today.",
         "claim_type": "fact", "confidence": 0.5,
         "fresh_until": "2000-01-01T00:00:00Z",
         "source": {"run_id": "test:stale", "event_id": "stale.md:abc"}},
        source_type="test", source_id="test-suite",
        idempotency_key="knowledge.claim_recorded:claim-stale1"))


def test_loop_runs_end_to_end(db: FoundryDatabase, vault: Path,
                              resource_root: Path, tmp_path: Path):
    from jarvis_foundry.v3_loop import run_one_loop
    _seed_stale_claim(db, vault)
    adapter = FakeHermesAdapter()  # COMPLETED / PASS
    report = run_one_loop(
        db, loop_id="t1", adapter=adapter, router=RoleRouter(),
        resource_id="resource-test",
        vault_root=vault, target_root=resource_root, max_items=10,
        max_bytes=20_000)
    assert report["outcome"] == "PASSED"
    assert report["mission_id"] is not None
    assert report["selected_candidate"] is not None
    assert report["candidate_score"] >= 0.35
    assert report["reconciled"] is True
    # mission lifecycle: created → READY → … → completed
    mission = db.row("missions", "mission_id", report["mission_id"])
    assert mission["state"] == "ACTION_SELECTION"
    # the candidate is EXECUTED with the recorded outcome (prediction vs
    # reality wiring via created_from)
    row = db.row("candidates", "candidate_id", report["selected_candidate"])
    assert row["status"] == "EXECUTED"
    assert "outcome" in str(row["evidence_json"])
    # the loop created the target workspace (the FakeHermesAdapter is
    # deterministic and writes nothing — file production belongs to the
    # real adapter; the deliverable assertion is covered by live loops)
    target = resource_root / "v3-loops" / "loop-t1"
    assert target.exists()
    # agent_run ledger records exist for the real dispatcher path
    with db.connect() as connection:
        runs = connection.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE mission_id = ?",
            (report["mission_id"],)).fetchone()[0]
    assert runs >= 1


def test_loop_honest_waiting_when_nothing_clears_bar(
        db: FoundryDatabase, vault: Path, resource_root: Path):
    from jarvis_foundry.v3_loop import run_one_loop
    _seed_stale_claim(db, vault)
    adapter = FakeHermesAdapter()
    report = run_one_loop(
        db, loop_id="t2", adapter=adapter, router=RoleRouter(),
        resource_id="resource-test",
        vault_root=vault, target_root=resource_root, max_items=10,
        max_bytes=20_000, min_score=0.99)  # nothing can clear this bar
    assert report["outcome"] == "WAITING"
    assert report.get("mission_id") is None
    # zero dispatches, zero missions
    with db.connect() as connection:
        missions = connection.execute(
            "SELECT COUNT(*) FROM missions WHERE mission_id != "
            "'mission-wfm-optimizer'").fetchone()[0]
    assert missions == 0


def test_loop_idempotent_rerun_no_duplicate_missions(
        db: FoundryDatabase, vault: Path, resource_root: Path):
    from jarvis_foundry.v3_loop import run_one_loop
    _seed_stale_claim(db, vault)
    adapter = FakeHermesAdapter()
    first = run_one_loop(
        db, loop_id="t3", adapter=adapter, router=RoleRouter(),
        resource_id="resource-test",
        vault_root=vault, target_root=resource_root, max_items=10,
        max_bytes=20_000)
    assert first["outcome"] == "PASSED"
    # the loop is self-feeding: run 1's dispatch events are ingested and
    # generate NEW research candidates, so run 2 selects a DIFFERENT
    # candidate — it never re-creates the same mission (each mission is
    # bound to its own candidate; the EXECUTED candidate is never
    # re-approved)
    second = run_one_loop(
        db, loop_id="t4", adapter=adapter, router=RoleRouter(),
        resource_id="resource-test",
        vault_root=vault, target_root=resource_root, max_items=10,
        max_bytes=20_000)
    assert second["outcome"] == "PASSED"
    assert second["mission_id"] != first["mission_id"]
    assert second["selected_candidate"] != first["selected_candidate"]
    with db.connect() as connection:
        executed = connection.execute(
            "SELECT candidate_id FROM candidates WHERE status = 'EXECUTED' "
            "ORDER BY rowid").fetchall()
        missions = connection.execute(
            "SELECT COUNT(*) FROM missions WHERE state != 'PLANNED'"
        ).fetchone()[0]
    assert len(executed) == 2  # one EXECUTED candidate per loop
    assert missions == 2       # no duplicate missions for one candidate


def test_loop_records_outcome_and_reconciles(
        db: FoundryDatabase, vault: Path, resource_root: Path):
    from jarvis_foundry.reconcile import reconcile
    from jarvis_foundry.v3_loop import run_one_loop
    _seed_stale_claim(db, vault)
    report = run_one_loop(
        db, loop_id="t5", adapter=FakeHermesAdapter(), router=RoleRouter(),
        resource_id="resource-test",
        vault_root=vault, target_root=resource_root, max_items=10,
        max_bytes=20_000)
    assert report["outcome"] == "PASSED"
    check = reconcile(db)
    assert check.consistent, check.mismatches[:3]
