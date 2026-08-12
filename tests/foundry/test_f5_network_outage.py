"""WP8 F5 fault-injection regression tests — network outage during a
provider-required step (04_V1_ACCEPTANCE_GATE.md §7 F5).

Pass condition: local state persists, network work waits with bounded
retry, local eligible work may continue, and reconnection resumes once.

Finding FIND-2026-08-12-04 (2026-08-12): the REAL Hermes CLI reports an
exhausted provider (e.g. ``API call failed after 3 retries: Connection
error.``) on STDOUT with EXIT CODE 0 (verified against .h
agent/conversation_loop.py:5854), so the adapter's ``returncode != 0``
check alone recorded a network outage as COMPLETED with the error text as
"evidence" — a false success that would advance a mission on no evidence.
Repair: ``HermesOneshotAdapter`` classifies the CLI's failure-report
first-line prefixes as ``ProviderUnavailable`` (fallback chain engages;
exhaustion = honest BLOCKED).

Staging rule (contract, WP8_GATE_EXECUTION_PLAN §3 F5): the LIVE gateway
and the real provider are never touched. Every test dispatches through
``HermesOneshotAdapter`` against a STAGED ``hermes_cli`` package in a tmp
root with an isolated HERMES_HOME. The staged stub mirrors the real CLI
surface (``-z``, ``--provider``, ``--model``, ``--usage-file``) and adds
env-controlled failure-report behavior:
- JF_STAGED_MODE=fail_report : print the exact real-CLI failure-report
  line to STDOUT, write the usage file, exit 0 (the F5 finding shape).
- JF_STAGED_FAIL_PROVIDER=<p>  : fail_report only when --provider == <p>;
  healthy otherwise (fallback-chain test).
- JF_STAGED_MODE=down         : exit(3) (non-zero path, F2 semantics).
- default                      : healthy one-shot session, exit 0.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.adapter import HermesOneshotAdapter
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.results import BLOCKED, COMPLETED, FAILED
from jarvis_foundry.roles.router import RoleRouter

STAGED_MAIN = '''\
"""Staged Hermes CLI for F5 network-outage fault injection (never the live
gateway; never the real provider).

Behavior (env-controlled):
- JF_STAGED_MODE=fail_report : print the REAL CLI's failure-report line
  ("API call failed after 3 retries: Connection error.") to STDOUT, write
  the usage file, exit 0 — the exact F5 finding shape (exit-0 error).
- JF_STAGED_FAIL_PROVIDER=<p>: fail_report only when --provider == <p>;
  healthy otherwise (fallback-chain test).
- JF_STAGED_MODE=down        : exit(3) immediately (non-zero path).
- default                     : healthy one-shot session; exit 0.
"""
import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="hermes_cli.main")
    parser.add_argument("-z", dest="prompt", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--usage-file")
    args = parser.parse_args()

    fail_provider = os.environ.get("JF_STAGED_FAIL_PROVIDER", "")
    failed = os.environ.get("JF_STAGED_MODE") == "fail_report" or (
        fail_provider and args.provider == fail_provider)

    if os.environ.get("JF_STAGED_MODE") == "down":
        print("[staged-gateway] unavailable (simulated gateway outage)",
              file=sys.stderr)
        return 3

    if failed:
        # Exact shape the real CLI emits when the provider is unreachable:
        # stdout failure report + exit code 0 (conversation_loop.py:5854).
        print("API call failed after 3 retries: Connection error.")
        if args.usage_file:
            Path(args.usage_file).write_text(json.dumps({
                "estimated_cost_usd": 0.0, "model": args.model,
            }), encoding="utf-8")
        return 0

    print("[staged-gateway] session transcript for %s (%s/%s)"
          % (args.prompt[:80], args.provider, args.model))
    print("VERDICT: PASS")  # V4-S2: healthy sessions emit the structured verdict
    if args.usage_file:
        Path(args.usage_file).write_text(json.dumps({
            "estimated_cost_usd": 0.0042, "model": args.model,
        }), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

STAGED_INIT = '"""Staged Hermes CLI package (F5 fault injection)."""\n'


def make_staged_gateway(root: Path) -> Path:
    """Create the staged hermes_cli package; return the staged root."""
    package = root / "hermes_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(STAGED_INIT, encoding="utf-8")
    (package / "main.py").write_text(STAGED_MAIN, encoding="utf-8")
    return root


def mission_database(tmp_path: Path, mission_id: str = "mission-f5") -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-12T10:00:00Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build:f5-test",
    ))
    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": "F5 staged network outage",
         "resource_id": "resource-1", "state": "ACTION_SELECTION",
         "acceptance_criteria": ["outage is honest, bounded, and resumable"]},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    return database


def staged_dispatcher(
    tmp_path: Path, *,
    mode: str | None = None,
    fail_provider: str | None = None,
    fallbacks: tuple[str, ...] = (),
) -> tuple[RoleDispatcher, FoundryDatabase]:
    """Dispatcher wired to the REAL HermesOneshotAdapter against the staged
    gateway; returns (dispatcher, database)."""
    database = mission_database(tmp_path)
    staged_root = make_staged_gateway(tmp_path / "gateway")
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    env: dict[str, str] = {}
    if mode is not None:
        env["JF_STAGED_MODE"] = mode
    if fail_provider is not None:
        env["JF_STAGED_FAIL_PROVIDER"] = fail_provider
    if env:
        os.environ.update(env)  # subprocess inherits; restored by the test
    adapter = HermesOneshotAdapter(
        hermes_root=staged_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=60,
        python_exe=sys.executable,
    )
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go",
                    "preferred_model": "kimi-k2.7-code",
                    "fallbacks": fallbacks},
    })
    dispatcher = RoleDispatcher(database, router, adapter, source_id="f5-staging")
    return dispatcher, database


def dispatch_one(dispatcher: RoleDispatcher, run_id: str) -> object:
    return dispatcher.dispatch(
        role="builder", mission_id="mission-f5", resource_id="resource-1",
        capability="write", step_id="step-f5", run_id=run_id,
        instructions="f5 staged network outage",
    )


def run_rows(database: FoundryDatabase) -> list[dict]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT run_id, status, failure_class FROM agent_runs "
            "WHERE run_id LIKE 'run-f5-%' ORDER BY run_id").fetchall()
        leases = connection.execute(
            "SELECT COUNT(*) FROM leases WHERE lease_key LIKE 'role:%'").fetchone()[0]
        events = connection.execute(
            "SELECT event_type, COUNT(*) FROM events "
            "WHERE event_type LIKE 'agent_run.%' GROUP BY event_type").fetchall()
    return {
        "rows": [{"run_id": r[0], "status": r[1], "failure_class": r[2]}
                 for r in rows],
        "role_leases": leases,
        "agent_run_events": {t: c for t, c in events},
    }


def test_f5_exit0_failure_report_is_honest_provider_unavailable(tmp_path):
    """Network outage (CLI reports provider failure with exit 0) must NOT
    fabricate COMPLETED: honest BLOCKED/provider_unavailable, run row
    FAILED, lease released, exactly one attempt (bounded)."""
    dispatcher, database = staged_dispatcher(tmp_path, mode="fail_report")
    result = dispatch_one(dispatcher, "run-f5-a")
    try:
        assert result.status == BLOCKED, result
        assert result.error_class == "provider_unavailable", result
        assert result.verdict == "BLOCKED", result
        state = run_rows(database)
        assert state["rows"] == [{"run_id": "run-f5-a", "status": "FAILED",
                                  "failure_class": "provider_unavailable"}], state
        assert state["role_leases"] == 0, state
        events = state["agent_run_events"]
        assert events.get("agent_run.started", 0) == 1, events  # bounded: 1 attempt
        assert events.get("agent_run.failed", 0) == 2, events  # per-provider + exhausted
        assert "agent_run.completed" not in events, events  # never a false success
        assert database.quick_check() == "ok"
    finally:
        for key in ("JF_STAGED_MODE", "JF_STAGED_FAIL_PROVIDER"):
            os.environ.pop(key, None)


def test_f5_reconnection_resumes_once_after_restore(tmp_path):
    """Outage -> honest blocked; endpoint restored -> a FRESH dispatch
    COMPLETES and the failed run row stays FAILED (truth preserved)."""
    dispatcher, database = staged_dispatcher(tmp_path, mode="fail_report")
    first = dispatch_one(dispatcher, "run-f5-b1")
    assert first.status == BLOCKED
    try:
        os.environ["JF_STAGED_MODE"] = ""
        second = dispatch_one(dispatcher, "run-f5-b2")
        assert second.status == COMPLETED, second
        state = run_rows(database)
        by_id = {r["run_id"]: r for r in state["rows"]}
        assert by_id["run-f5-b1"]["status"] == "FAILED", state  # outage truth kept
        assert by_id["run-f5-b2"]["status"] == "COMPLETED", state
        assert state["role_leases"] == 0, state
        assert database.quick_check() == "ok"
    finally:
        os.environ.pop("JF_STAGED_MODE", None)


def test_f5_configured_fallback_used_and_recorded_on_outage(tmp_path):
    """With an eligible fallback configured, an outage on the preferred
    provider engages the fallback (recorded in events), then COMPLETES."""
    dispatcher, database = staged_dispatcher(
        tmp_path, fail_provider="opencode-go", fallbacks=("vertex_ai",))
    result = dispatch_one(dispatcher, "run-f5-c")
    try:
        assert result.status == COMPLETED, result
        assert result.provider == "vertex_ai", result  # fallback served
        state = run_rows(database)
        events = state["agent_run_events"]
        assert events.get("agent_run.failed", 0) >= 1, events  # preferred failed
        assert events.get("agent_run.completed", 0) == 1, events
        assert state["rows"][0]["status"] == "COMPLETED", state
        assert database.quick_check() == "ok"
    finally:
        os.environ.pop("JF_STAGED_FAIL_PROVIDER", None)


def test_f5_healthy_exit0_transcript_still_completed(tmp_path):
    """No false positive: a healthy exit-0 session still COMPLETES."""
    dispatcher, database = staged_dispatcher(tmp_path)
    result = dispatch_one(dispatcher, "run-f5-d")
    assert result.status == COMPLETED, result
    assert result.verdict == "PASS", result
    assert "session transcript" in (result.evidence[0] if result.evidence else ""), result
    state = run_rows(database)
    assert state["rows"][0]["status"] == "COMPLETED", state
    assert state["role_leases"] == 0, state


def test_f5_nonzero_exit_still_honest_oneshot_failure(tmp_path):
    """F2 semantics preserved: a non-zero exit is still a FAILED
    hermes_oneshot_failed run, never reclassified."""
    dispatcher, database = staged_dispatcher(tmp_path, mode="down")
    result = dispatch_one(dispatcher, "run-f5-e")
    try:
        assert result.status == FAILED, result
        assert result.error_class == "hermes_oneshot_failed", result
        state = run_rows(database)
        assert state["rows"][0]["failure_class"] == "hermes_oneshot_failed", state
        assert state["role_leases"] == 0, state
    finally:
        os.environ.pop("JF_STAGED_MODE", None)


def test_f5_billing_block_report_is_provider_unavailable(tmp_path):
    """The billing variant of the CLI's exit-0 failure report is also
    classified as provider unavailability (never COMPLETED)."""
    database = mission_database(tmp_path)
    staged_root = make_staged_gateway(tmp_path / "gateway2")
    (staged_root / "hermes_cli" / "main.py").write_text(
        STAGED_MAIN.replace(
            'print("API call failed after 3 retries: Connection error.")',
            'print("Billing or credits exhausted: insufficient credits")'),
        encoding="utf-8")
    isolated_home = tmp_path / "isolated-home-2"
    isolated_home.mkdir()
    os.environ["JF_STAGED_MODE"] = "fail_report"  # trigger the fail branch
    try:
        adapter = HermesOneshotAdapter(
            hermes_root=staged_root, hermes_home=isolated_home,
            enabled=True, timeout_seconds=60, python_exe=sys.executable,
        )
        router = RoleRouter().clone_with({
            "builder": {"preferred_provider": "opencode_go",
                        "preferred_model": "kimi-k2.7-code", "fallbacks": ()},
        })
        dispatcher = RoleDispatcher(database, router, adapter, source_id="f5-staging-2")
        result = dispatcher.dispatch(
            role="builder", mission_id="mission-f5", resource_id="resource-1",
            capability="write", step_id="step-f5", run_id="run-f5-f",
            instructions="billing block staged")
        assert result.status == BLOCKED, result
        assert result.error_class == "provider_unavailable", result
        state = run_rows(database)
        assert "agent_run.completed" not in state["agent_run_events"], state
        assert database.quick_check() == "ok"
    finally:
        os.environ.pop("JF_STAGED_MODE", None)
