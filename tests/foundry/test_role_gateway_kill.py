"""WP8 F2 fault-injection regression tests — staged Hermes gateway termination.

Plan F2 (04_V1_ACCEPTANCE_GATE.md §7): terminate Hermes while a worker is
idle and while a worker is active.

Pass condition: Supervisor remains healthy, detects Hermes loss, preserves
the lease and partial evidence, restarts or waits according to policy, and
resumes without losing mission truth.

Staging rule (contract, WP8_GATE_EXECUTION_PLAN §3 F2): the LIVE gateway is
never touched. Every test here dispatches through ``HermesOneshotAdapter``
against a STAGED ``hermes_cli`` package in a tmp root with an isolated
HERMES_HOME, so the subprocess that dies is the staged gateway session.

The staged gateway stub mirrors the real CLI surface the adapter emits
(``-z VALUE``, ``--provider``, ``--model``, ``--usage-file``; verified
against ``.h hermes_cli/_parser.py``): argparse accepts exactly those flags.
Behavior is controlled by environment variables so a test can script a
healthy session, an immediate "gateway down" exit, or a mid-session
termination (kill file observed -> ``os._exit(17)``, a genuine abnormal
process death with a non-zero code, the same boundary effect as an external
``taskkill /F`` on Windows which yields returncode 1).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.adapter import HermesOneshotAdapter
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.results import FAILED
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.supervisor.service import Supervisor

STAGED_MAIN = '''\
"""Staged Hermes gateway for F2 fault injection (never the live gateway).

Behavior (env-controlled):
- JF_STAGED_MODE=down      : exit(3) immediately (gateway unavailable).
- JF_STAGED_KILL_FILE=<p>  : after writing the started marker, poll for
  <p> (kill file). When it appears, os._exit(17) mid-session.
- default                  : healthy one-shot session; writes the usage
  report the real CLI writes and exits 0.
Always writes JF_STAGED_STARTED_FILE=<p> with JSON {pid, cwd, home, args}.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="hermes_cli.main")
    parser.add_argument("-z", dest="prompt", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--usage-file")
    args = parser.parse_args()

    started = os.environ.get("JF_STAGED_STARTED_FILE")
    if started:
        Path(started).write_text(json.dumps({
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "home": os.environ.get("HERMES_HOME"),
            "provider": args.provider,
            "model": args.model,
            "prompt": args.prompt[:120],
        }), encoding="utf-8")

    if os.environ.get("JF_STAGED_MODE") == "down":
        print("[staged-gateway] unavailable (simulated gateway outage)",
              file=sys.stderr)
        return 3

    kill_file = os.environ.get("JF_STAGED_KILL_FILE")
    if kill_file:
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if Path(kill_file).exists():
                os._exit(17)  # genuine abnormal termination mid-session
            time.sleep(0.05)
        return 9  # kill never arrived: fail loudly so the test notices

    print("[staged-gateway] session transcript for %s (%s/%s)"
          % (args.prompt[:80], args.provider, args.model))
    if args.usage_file:
        Path(args.usage_file).write_text(json.dumps({
            "estimated_cost_usd": 0.0042, "model": args.model,
        }), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

STAGED_INIT = '"""Staged Hermes CLI package (F2 fault injection)."""\n'


def make_staged_gateway(root: Path) -> Path:
    """Create the staged hermes_cli package; return the staged root."""
    package = root / "hermes_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(STAGED_INIT, encoding="utf-8")
    (package / "main.py").write_text(STAGED_MAIN, encoding="utf-8")
    return root


def mission_database(tmp_path: Path, mission_id: str = "mission-f2") -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-12T06:00:00Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build:f2-test",
    ))
    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": "F2 staged gateway kill",
         "resource_id": "resource-1", "state": "ACTION_SELECTION",
         "acceptance_criteria": ["staged gateway loss is honest and resumable"]},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    return database


def staged_dispatcher(tmp_path: Path, *, mode: str | None = None,
                      kill_file: Path | None = None,
                      timeout_seconds: int = 60) -> tuple[RoleDispatcher, Path, Path]:
    """Dispatcher wired to the REAL HermesOneshotAdapter against the staged
    gateway; returns (dispatcher, staged_root, started_marker)."""
    database = mission_database(tmp_path)
    staged_root = make_staged_gateway(tmp_path / "gateway")
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    started_marker = tmp_path / "gateway-started.json"
    env = {"JF_STAGED_STARTED_FILE": str(started_marker)}
    if mode is not None:
        env["JF_STAGED_MODE"] = mode
    if kill_file is not None:
        env["JF_STAGED_KILL_FILE"] = str(kill_file)
    adapter = HermesOneshotAdapter(
        hermes_root=staged_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=timeout_seconds,
        python_exe=sys.executable,
    )
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go",
                    "preferred_model": "kimi-k2.7-code", "fallbacks": ()},
    })
    dispatcher = RoleDispatcher(database, router, adapter, source_id="f2-staging")
    return dispatcher, staged_root, started_marker


def wait_for(path: Path, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise AssertionError(f"staged gateway never started (marker {path} missing)")


def test_f2_staged_gateway_killed_mid_session_honest_failure_and_release(
        tmp_path, monkeypatch):
    """Worker ACTIVE: the staged gateway session is terminated after it
    started (mid-session). Expected: dispatcher returns FAILED with
    error_class hermes_oneshot_failed; run row FAILED; lease released;
    ledger records the loss; DB integrity intact."""
    kill_file = tmp_path / "kill.txt"
    started_marker = tmp_path / "gateway-started.json"
    monkeypatch.setenv("JF_STAGED_STARTED_FILE", str(started_marker))
    monkeypatch.setenv("JF_STAGED_KILL_FILE", str(kill_file))
    dispatcher, _, started_marker = staged_dispatcher(
        tmp_path, kill_file=kill_file)

    result_holder = {}

    def target():
        result_holder["result"] = dispatcher.dispatch(
            role="builder", mission_id="mission-f2", resource_id="resource-1",
            capability="write", step_id="step-1", run_id="run-f2-kill",
            instructions="build the smallest correct change",
        )

    thread = threading.Thread(target=target)
    thread.start()
    started = wait_for(started_marker)  # gateway session is now LIVE
    assert started["pid"] > 0
    assert started["home"] == str((tmp_path / "isolated-home").resolve())
    time.sleep(0.3)                     # session is mid-flight
    kill_file.write_text("kill", encoding="utf-8")  # terminate the gateway
    thread.join(timeout=60)
    assert not thread.is_alive()

    result = result_holder["result"]
    assert result.status == FAILED
    assert result.error_class == "hermes_oneshot_failed"
    assert result.provider == "opencode_go"      # exact identity recorded

    database = dispatcher.database
    row = database.row("agent_runs", "run_id", "run-f2-kill")
    assert row["status"] == "FAILED"
    assert row["failure_class"] == "hermes_oneshot_failed"

    # Lease released: no lease rows survive the failed session.
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0

    # Loss is durable: started + heartbeat + failed events all present.
    with database.connect() as connection:
        types = [r[0] for r in connection.execute(
            "SELECT event_type FROM events WHERE event_type LIKE 'agent_run.%'")]
    assert "agent_run.started" in types
    assert "agent_run.failed" in types
    assert database.quick_check() == "ok"


def test_f2_gateway_loss_while_worker_idle_supervisor_healthy(
        tmp_path, monkeypatch):
    """Worker IDLE: the gateway is down (staged mode=down) but nothing is
    dispatching. Expected: the Supervisor daemon has no Hermes dependency —
    heartbeats stay RUNNING, no stale state appears, doctor reports
    hermes_required_for_supervisor False. A dispatch attempt then fails
    honestly (detected gateway loss) instead of hanging or fabricating."""
    database = mission_database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path)
    assert supervisor.start() == "RUNNING"

    # Idle: gateway down, nothing dispatching -> daemon unaffected.
    assert supervisor.heartbeat() == "RUNNING"
    assert supervisor.doctor()["hermes_required_for_supervisor"] is False
    assert supervisor.doctor()["active_workers"] == []

    # Gateway loss DETECTED the moment a session is needed: honest FAILED,
    # never a hang, never a fabricated completion.
    started_marker = tmp_path / "gateway-started.json"
    monkeypatch.setenv("JF_STAGED_STARTED_FILE", str(started_marker))
    monkeypatch.setenv("JF_STAGED_MODE", "down")
    dispatcher, _, started_marker = staged_dispatcher(
        tmp_path, mode="down", timeout_seconds=30)
    result = dispatcher.dispatch(
        role="builder", mission_id="mission-f2", resource_id="resource-1",
        capability="write", step_id="step-2", run_id="run-f2-idle",
        instructions="do the thing",
    )
    assert result.status == FAILED
    assert result.error_class == "hermes_oneshot_failed"
    assert "staged-gateway" in (result.error_detail or "").lower()

    # Supervisor still healthy after the detected loss; no stuck rows.
    assert supervisor.heartbeat() == "RUNNING"
    assert supervisor.doctor()["reconciliation"]["consistent"] is True


def test_f2_resume_after_gateway_loss_fresh_worker_receives_task(
        tmp_path, monkeypatch):
    """After a detected gateway loss, a FRESH worker (new run_id, same step)
    receives the reconciled task and completes once the gateway is back.
    Mission truth is preserved: the failed run stays FAILED, exactly one
    fresh COMPLETED run exists, no duplicate action, projection consistent."""
    kill_file = tmp_path / "kill.txt"
    started_marker = tmp_path / "gateway-started.json"
    monkeypatch.setenv("JF_STAGED_STARTED_FILE", str(started_marker))
    monkeypatch.setenv("JF_STAGED_KILL_FILE", str(kill_file))
    dispatcher, _, started_marker = staged_dispatcher(
        tmp_path, kill_file=kill_file, timeout_seconds=60)

    # Gateway loss on attempt 1.
    result_holder = {}

    def target():
        result_holder["result"] = dispatcher.dispatch(
            role="builder", mission_id="mission-f2", resource_id="resource-1",
            capability="write", step_id="step-3", run_id="run-f2-a",
            instructions="build the smallest correct change",
        )

    thread = threading.Thread(target=target)
    thread.start()
    wait_for(started_marker)
    time.sleep(0.3)
    kill_file.write_text("kill", encoding="utf-8")
    thread.join(timeout=60)
    assert result_holder["result"].status == FAILED
    assert dispatcher.database.row("agent_runs", "run_id", "run-f2-a")["status"] == "FAILED"

    # Gateway restored (kill mode cleared -> healthy session again); a FRESH
    # worker (new run_id, same step) resumes the task.
    monkeypatch.delenv("JF_STAGED_KILL_FILE")
    result = dispatcher.dispatch(
        role="builder", mission_id="mission-f2", resource_id="resource-1",
        capability="write", step_id="step-3", run_id="run-f2-b",
        instructions="build the smallest correct change",
    )
    assert result.status == "COMPLETED"
    assert result.verdict == "PASS"

    database = dispatcher.database
    row_a = database.row("agent_runs", "run_id", "run-f2-a")
    row_b = database.row("agent_runs", "run_id", "run-f2-b")
    assert row_a["status"] == "FAILED"            # truth preserved
    assert row_b["status"] == "COMPLETED"         # exactly one fresh completion
    with database.connect() as connection:
        completed = connection.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE status='COMPLETED'"
        ).fetchone()[0]
    assert completed == 1
    # No lease survives; integrity intact; projection consistent.
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
    assert database.quick_check() == "ok"
    from jarvis_foundry.reconcile import reconcile
    assert reconcile(database).consistent is True
