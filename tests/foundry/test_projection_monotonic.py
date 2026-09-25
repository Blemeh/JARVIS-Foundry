"""V4-S3 (R2b): agent_runs terminal-state monotonicity.

DIR-2026-08-12 item 2: a run marked FAILED must never later become
COMPLETED; a COMPLETED run never regresses to FAILED or RUNNING; the
provider-fallback chain (RUNNING -> FAILED-attempt -> RUNNING ->
COMPLETED) remains legal within one dispatch.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def make_db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def ev(event_type: str, payload: dict, key: str) -> Event:
    return Event.create(event_type, payload, source_type="test",
                        source_id="test-suite", idempotency_key=key)


def started(run_id: str, key: str, manifest_hash: str = "h") -> Event:
    return ev("agent_run.started",
              {"run_id": run_id, "mission_id": "m", "step_id": "s",
               "role": "builder", "provider": "opencode_go",
               "model": "deepseek-v4-flash",
               "context_manifest_hash": manifest_hash, "tool_policy": []},
              key)


def completed(run_id: str, key: str) -> Event:
    return ev("agent_run.completed",
              {"run_id": run_id, "result": {"verdict": "PASS"}, "cost": {}},
              key)


def failed(run_id: str, key: str, failure_class: str = "heartbeat_timeout") -> Event:
    return ev("agent_run.failed",
              {"run_id": run_id, "failure_class": failure_class}, key)


def statuses(database: FoundryDatabase) -> dict[str, str]:
    with database.connect() as connection:
        return dict(connection.execute(
            "SELECT run_id, status FROM agent_runs ORDER BY run_id").fetchall())


def test_failed_run_never_becomes_completed(tmp_path):
    """Reaper marks FAILED; a late completion event must not flip it."""
    database = make_db(tmp_path)
    database.append(started("r1", "s:r1"))
    database.append(failed("r1", "f:r1"))
    database.append(completed("r1", "c:r1"))  # late
    assert statuses(database)["r1"] == "FAILED"


def test_completed_run_never_becomes_failed(tmp_path):
    """A late failure event (e.g. reaper racing) must not regress a
    completed run."""
    database = make_db(tmp_path)
    database.append(started("r2", "s:r2"))
    database.append(completed("r2", "c:r2"))
    database.append(failed("r2", "f:r2"))  # late
    assert statuses(database)["r2"] == "COMPLETED"


def test_completed_run_never_becomes_running(tmp_path):
    """A late started event (e.g. stale re-delivery under a new provider)
    must not reset a completed run to RUNNING."""
    database = make_db(tmp_path)
    database.append(started("r3", "s:r3"))
    database.append(completed("r3", "c:r3"))
    database.append(started("r3", "s2:r3", manifest_hash="h2"))  # late
    assert statuses(database)["r3"] == "COMPLETED"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT provider, context_manifest_hash FROM agent_runs "
            "WHERE run_id = 'r3'").fetchone()
    assert row[0] == "opencode_go" and row[1] == "h"


def test_fallback_chain_restart_remains_legal(tmp_path):
    """The dispatcher's provider fallback walks RUNNING -> FAILED
    (attempt) -> RUNNING -> COMPLETED within one dispatch — the re-start
    from a FAILED attempt row is legal and the completion lands."""
    database = make_db(tmp_path)
    database.append(ev("agent_run.started",
                       {"run_id": "r4", "mission_id": "m", "step_id": "s",
                        "role": "builder", "provider": "opencode_go",
                        "model": "deepseek-v4-flash",
                        "context_manifest_hash": "h", "tool_policy": []},
                       "s:r4:opencode_go"))
    database.append(ev("agent_run.failed",
                       {"run_id": "r4", "failure_class": "provider_unavailable"},
                       "f:r4:provider_unavailable:opencode_go"))
    database.append(ev("agent_run.started",
                       {"run_id": "r4", "mission_id": "m", "step_id": "s",
                        "role": "builder", "provider": "vertex_ai",
                        "model": "deepseek-v4-flash",
                        "context_manifest_hash": "h", "tool_policy": []},
                       "s:r4:vertex_ai"))
    database.append(completed("r4", "c:r4"))
    rows = statuses(database)
    assert rows["r4"] == "COMPLETED"
    with database.connect() as connection:
        row = connection.execute(
            "SELECT provider, failure_class FROM agent_runs "
            "WHERE run_id = 'r4'").fetchone()
    assert row[0] == "vertex_ai"       # the completed provider identity
    assert row[1] is None              # stale attempt failure cleared


def test_failure_result_is_projected_and_cleared_when_run_retries(tmp_path):
    database = make_db(tmp_path)
    database.append(started("r5", "s:r5"))
    database.append(ev(
        "agent_run.failed",
        {
            "run_id": "r5",
            "failure_class": "worker_runtime_failure",
            "result": {
                "status": "FAILED",
                "error_class": "worker_runtime_failure",
                "evidence": ["Hermes exited with code 2"],
            },
        },
        "f:r5:diagnostic",
    ))
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, failure_class, result_json FROM agent_runs "
            "WHERE run_id = 'r5'").fetchone()
    assert row[0] == "FAILED"
    assert row[1] == "worker_runtime_failure"
    assert row[2] == (
        '{"error_class":"worker_runtime_failure",'
        '"evidence":["Hermes exited with code 2"],"status":"FAILED"}'
    )

    database.append(started("r5", "s2:r5", manifest_hash="h2"))
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, failure_class, result_json FROM agent_runs "
            "WHERE run_id = 'r5'").fetchone()
    assert row[0] == "RUNNING"
    assert row[1] is None
    assert row[2] is None
