"""V9 Continuous Executive — multi-cycle harness.

Proves the 21 lifecycle transitions plus adversarial conditions from the
Lane 1 MASTER prompt using the real durable engine (no stubs).
"""
from __future__ import annotations
import tempfile, pathlib, subprocess, shutil, json, time
from datetime import datetime, timezone, timedelta
import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import (
    create_objective, build_plan, set_objective_status, rank_objectives,
    rebuild_executive_state, update_objective,
)
from jarvis_foundry.executive.continuous import (
    WakeCondition, create_wake, rebuild_wake_state, is_wake_due, due_wakes,
    tick_wakes, schedule_wait_until, schedule_wait_for_dependency,
    schedule_wait_for_capability, blocker_for, run_continuous_cycle,
    continuous_snapshot, clear_wakes_for_objective, record_strategy_attempt,
    recent_strategies, is_retry_loop, auto_wake_for_blocked,
)
from jarvis_foundry.clock import isoformat

def _db_with_resource(tmp: pathlib.Path, rid="r1"):
    db = FoundryDatabase(tmp / "foundry.db")
    db.initialize()
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], capture_output=True)
    (repo / "f.txt").write_text("hi")
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True)
    from jarvis_foundry.control.registration import inspect_git_resource, registration_event
    from jarvis_foundry.control.capabilities import set_capability_state
    db.append(registration_event(inspect_git_resource(repo, resource_id=rid)))
    set_capability_state(db, rid, "write_registered_worktree_files", "READY", evidence={"source": "test"})
    return db

# 1. Multiple objectives compete
def test_v9_01_competing(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "High", objective_id="high", priority=3.0, resource_id="r1")
    create_objective(db, "Low", objective_id="low", priority=1.0, resource_id="r1")
    rows = rank_objectives(db)
    assert rows[0].objective_id == "high"

# 2-3. Highest becomes blocked, alternate advances
def test_v9_02_blocked_then_alternate(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "High", objective_id="high", priority=3.0, resource_id="r1")
    create_objective(db, "Low", objective_id="low", priority=1.0, resource_id="r1")
    update_objective(db, "high", {"dependencies": ("missing",)})
    rows = rank_objectives(db)
    assert rows[0].objective_id == "low" and rows[0].eligible

# 4-5. Dependency resolves, original resumes via wake
def test_v9_04_dep_resumes(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "A", objective_id="A", resource_id="r1")
    create_objective(db, "B", objective_id="B", resource_id="r1", dependencies=("A",))
    schedule_wait_for_dependency(db, "B", dependency_id="A")
    w = list(rebuild_wake_state(db).values())[0]
    assert not is_wake_due(db, w)
    set_objective_status(db, "A", "COMPLETED", reason="done", evidence_refs=["ev"])
    assert is_wake_due(db, rebuild_wake_state(db)[w["wake_id"]])
    assert w["wake_id"] in tick_wakes(db)
    assert tick_wakes(db) == []
    rows = rank_objectives(db)
    assert any(r.objective_id == "B" and r.eligible for r in rows)

# 6-7. Provider/capability wait
def test_v9_06_capability_wait(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "Needs cap", objective_id="cap", resource_id="r1")
    schedule_wait_for_capability(db, "cap", capability="write_registered_worktree_files", resource_id="r1")
    wakes = rebuild_wake_state(db)
    w = next(v for v in wakes.values() if v.get("capability") == "write_registered_worktree_files")
    assert is_wake_due(db, w)

# 8-10. Strategy loop detection
def test_v9_08_strategy_loop(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "S", objective_id="S", resource_id="r1")
    for _ in range(3):
        record_strategy_attempt(db, "S", "retry_same")
        time.sleep(0.02)
    assert is_retry_loop(db, "S", "retry_same", threshold=3)
    assert not is_retry_loop(db, "S", "other", threshold=3)

# 11-12. Follow-up becomes due, wakes automatically
def test_v9_11_time_wake(tmp_path):
    db = _db_with_resource(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    schedule_wait_until(db, "timed", not_before=past)
    assert len(due_wakes(db)) == 1
    due_id = due_wakes(db)[0]["wake_id"]
    assert tick_wakes(db) == [due_id]
    assert tick_wakes(db) == []
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    schedule_wait_until(db, "future", not_before=future)
    assert not any(w.get("objective_id") == "future" for w in due_wakes(db))

# 13-14. Owner changes priority midstream, portfolio reprioritizes
def test_v9_13_priority_change(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "A", objective_id="A", priority=1.0, resource_id="r1")
    create_objective(db, "B", objective_id="B", priority=3.0, resource_id="r1")
    assert rank_objectives(db)[0].objective_id == "B"
    update_objective(db, "B", {"priority": 0.5})
    assert rank_objectives(db)[0].objective_id == "A"

# 15-16. Restart wakes survive
def test_v9_15_restart_survives(tmp_path):
    db = _db_with_resource(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    schedule_wait_until(db, "r_obj", not_before=future)
    from jarvis_foundry.database import FoundryDatabase as FDB
    db2 = FDB(tmp_path / "foundry.db")
    assert any(v.get("objective_id") == "r_obj" for v in rebuild_wake_state(db2).values())

# 18-19. STOP blocks
def test_v9_18_stop_blocks(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "X", objective_id="X", resource_id="r1")
    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("stop", encoding="utf-8")
    result = run_continuous_cycle(db, cycle_id="c-stop")
    assert result["status"] == "OWNER_BLOCKED"
    stop.unlink()

# 20-21. Honest idle + idempotent replay
def test_v9_20_idle_and_idempotent(tmp_path):
    db = _db_with_resource(tmp_path)
    r1 = run_continuous_cycle(db, cycle_id="idle-1")
    assert r1["status"] in ("WAITING", "BLOCKED", "IDLE") or "no objective" in r1.get("reason", "").lower()
    r2 = run_continuous_cycle(db, cycle_id="idle-1")
    assert r2["status"] == r1["status"]
    assert r2["reason"] == r1["reason"]

# Adversarial: no storm
def test_v9_no_storm(tmp_path):
    db = _db_with_resource(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    for _ in range(10):
        schedule_wait_until(db, "storm", not_before=past)
    assert len([w for w in rebuild_wake_state(db).values() if w.get("objective_id") == "storm"]) == 1

# Adversarial: clear wakes for terminal
def test_v9_clear_terminal(tmp_path):
    db = _db_with_resource(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    schedule_wait_until(db, "clr", not_before=future)
    future2 = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    schedule_wait_until(db, "clr", not_before=future2)
    assert len([w for w in rebuild_wake_state(db).values() if w.get("objective_id") == "clr" and w.get("status") == "ACTIVE"]) == 2
    cleared = clear_wakes_for_objective(db, "clr")
    assert len(cleared) == 2
    assert len([w for w in rebuild_wake_state(db).values() if w.get("objective_id") == "clr" and w.get("status") == "ACTIVE"]) == 0

# Snapshot surfaces
def test_v9_snapshot_surfaces(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "Snap", objective_id="snap", resource_id="r1")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    schedule_wait_until(db, "snap", not_before=future)
    snap = continuous_snapshot(db)
    assert "continuous" in snap
    assert "wake_state" in snap["continuous"]
    assert "exec_states" in snap["continuous"]
    assert snap["continuous"]["exec_states"]["snap"] == "WAITING"

# Blocker model
def test_v9_blocker_model(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "Blk", objective_id="blk", resource_id="r1", dependencies=("missing",))
    schedule_wait_for_dependency(db, "blk", dependency_id="missing")
    b = blocker_for(db, "blk")
    assert b is not None
    assert b.kind in ("WAITING", "BLOCKED")

# Command center exposes continuous
def test_v9_command_center(tmp_path):
    db = _db_with_resource(tmp_path)
    create_objective(db, "CC", objective_id="cc", resource_id="r1")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    schedule_wait_until(db, "cc", not_before=future)
    from jarvis_foundry.command_center import build_snapshot
    snap = build_snapshot(db)
    assert "continuous" in snap["executive"]
    assert "wake_state" in snap["executive"]["continuous"]

# Deadline urgency escalates
def test_v9_deadline(tmp_path):
    db = _db_with_resource(tmp_path)
    past_deadline = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00","Z")
    future_deadline = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat().replace("+00:00","Z")
    create_objective(db, "Overdue", objective_id="overdue", resource_id="r1", deadline=past_deadline)
    create_objective(db, "Future", objective_id="future_dl", resource_id="r1", deadline=future_deadline)
    from jarvis_foundry.executive.continuous import deadline_urgency
    assert deadline_urgency(db, "overdue") > deadline_urgency(db, "future_dl")
    assert deadline_urgency(db, "overdue") == 1.0
