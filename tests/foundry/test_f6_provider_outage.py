"""WP8 F6 fault-injection regression tests — provider/model outage
(04_V1_ACCEPTANCE_GATE.md §7 F6).

Pass condition: configured eligible fallback is used and recorded, or the
step enters an honest waiting state. No nonexistent model is invented.

Finding FIND-2026-08-12-05 (2026-08-12): a REAL oneshot against a clearly
nonexistent model id returned stdout ``HTTP 401: Model <id> is not
supported`` with EXIT CODE 0; the adapter recorded COMPLETED/PASS with the
error text as evidence (verified live against the real provider endpoint;
the gateway rejects unknown model ids with 401 + body text on stdout).
Repair: the adapter's failure-report classification was extended with the
source-verified exit-0 error prefixes from .h agent/conversation_loop.py
(Invalid API response after / Request payload too large / Context length
exceeded / Model generated invalid tool call / Response truncated due to
output length limit) plus an ``HTTP <status>`` regex, all raising
ProviderUnavailable instead of COMPLETED.

Staging rule: live gateway and real provider never touched; the staged
hermes_cli stub (same pattern as F5) emits the failure lines with exit 0.
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
"""Staged Hermes CLI for F6 provider/model-outage fault injection.

JF_STAGED_LINE=<text>   : print <text> to STDOUT, write the usage file,
                          exit 0 (the exit-0 failure-report shapes).
JF_STAGED_FAIL_PROVIDER=<p>: emit JF_STAGED_LINE only when --provider ==
                          <p>; healthy otherwise (fallback-chain test).
JF_STAGED_MODE=down     : exit(3) (non-zero path).
default                  : healthy one-shot session; exit 0.
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

    if os.environ.get("JF_STAGED_MODE") == "down":
        print("[staged-gateway] unavailable (simulated gateway outage)",
              file=sys.stderr)
        return 3

    fail_provider = os.environ.get("JF_STAGED_FAIL_PROVIDER", "")
    line = os.environ.get("JF_STAGED_LINE", "")
    if line and (not fail_provider or args.provider == fail_provider):
        print(line)
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

STAGED_INIT = '"""Staged Hermes CLI package (F6 fault injection)."""\n'


def make_staged_gateway(root: Path) -> Path:
    package = root / "hermes_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(STAGED_INIT, encoding="utf-8")
    (package / "main.py").write_text(STAGED_MAIN, encoding="utf-8")
    return root


def mission_database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-12T10:00:00Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build:f6-test",
    ))
    database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-f6", "objective": "F6 staged provider outage",
         "resource_id": "resource-1", "state": "ACTION_SELECTION",
         "acceptance_criteria": ["outage is honest; no invented model"]},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-f6",
    ))
    return database


def staged_dispatcher(tmp_path: Path, *, line: str | None = None,
                      fail_provider: str | None = None,
                      fallbacks: tuple[str, ...] = ()) -> tuple[RoleDispatcher, FoundryDatabase]:
    database = mission_database(tmp_path)
    staged_root = make_staged_gateway(tmp_path / "gateway")
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    if line is not None:
        os.environ["JF_STAGED_LINE"] = line
    if fail_provider is not None:
        os.environ["JF_STAGED_FAIL_PROVIDER"] = fail_provider
    adapter = HermesOneshotAdapter(
        hermes_root=staged_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=60, python_exe=sys.executable,
    )
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go",
                    "preferred_model": "model-does-not-exist-xyz-2026",
                    "fallbacks": fallbacks},
    })
    dispatcher = RoleDispatcher(database, router, adapter, source_id="f6-staging")
    return dispatcher, database


def dispatch_one(dispatcher: RoleDispatcher, run_id: str) -> object:
    return dispatcher.dispatch(
        role="builder", mission_id="mission-f6", resource_id="resource-1",
        capability="write", step_id="step-f6", run_id=run_id,
        instructions="f6 staged provider outage",
    )


def state_of(database: FoundryDatabase) -> dict:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT run_id, status, failure_class FROM agent_runs "
            "WHERE run_id LIKE 'run-f6-%' ORDER BY run_id").fetchall()
        leases = connection.execute(
            "SELECT COUNT(*) FROM leases WHERE lease_key LIKE 'role:%'").fetchone()[0]
        events = connection.execute(
            "SELECT event_type, COUNT(*) FROM events "
            "WHERE event_type LIKE 'agent_run.%' GROUP BY event_type").fetchall()
        started = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = 'agent_run.started' "
            "AND idempotency_key LIKE '%run-f6-%'").fetchall()
    models = []
    for (payload,) in started:
        try:
            models.append(json.loads(payload).get("model", ""))
        except (TypeError, ValueError):
            models.append("<unparseable>")
    return {
        "rows": [{"run_id": r[0], "status": r[1], "failure_class": r[2]}
                 for r in rows],
        "role_leases": leases,
        "agent_run_events": {t: c for t, c in events},
        "started_models": models,
    }


def test_f6_http_model_not_supported_is_honest_not_completed(tmp_path):
    """The observed 'HTTP 401: Model <id> is not supported' exit-0 line
    must NOT fabricate COMPLETED: honest BLOCKED/provider_unavailable,
    run row FAILED, lease released, configured model recorded (the
    system never invents a model)."""
    line = "HTTP 401: Model model-does-not-exist-xyz-2026 is not supported"
    dispatcher, database = staged_dispatcher(tmp_path, line=line)
    try:
        result = dispatch_one(dispatcher, "run-f6-a")
        assert result.status == BLOCKED, result
        assert result.error_class == "provider_unavailable", result
        state = state_of(database)
        assert state["rows"] == [{"run_id": "run-f6-a", "status": "FAILED",
                                  "failure_class": "provider_unavailable"}], state
        assert state["role_leases"] == 0, state
        assert state["started_models"] == ["model-does-not-exist-xyz-2026"], state
        assert "agent_run.completed" not in state["agent_run_events"], state
        assert database.quick_check() == "ok"
    finally:
        os.environ.pop("JF_STAGED_LINE", None)


def test_f6_context_length_report_is_honest_not_completed(tmp_path):
    """The 'Context length exceeded' exit-0 failure shape is classified."""
    dispatcher, database = staged_dispatcher(
        tmp_path, line="Context length exceeded: max compression attempts (3) reached.")
    try:
        result = dispatch_one(dispatcher, "run-f6-b")
        assert result.status == BLOCKED, result
        assert result.error_class == "provider_unavailable", result
        assert "agent_run.completed" not in state_of(database)["agent_run_events"]
    finally:
        os.environ.pop("JF_STAGED_LINE", None)


def test_f6_invalid_api_response_report_is_honest_not_completed(tmp_path):
    """The 'Invalid API response after N retries' exit-0 shape is classified."""
    dispatcher, database = staged_dispatcher(
        tmp_path, line="Invalid API response after 3 retries: malformed JSON")
    try:
        result = dispatch_one(dispatcher, "run-f6-c")
        assert result.status == BLOCKED, result
        assert result.error_class == "provider_unavailable", result
        assert "agent_run.completed" not in state_of(database)["agent_run_events"]
    finally:
        os.environ.pop("JF_STAGED_LINE", None)


def test_f6_normal_answer_no_false_positive(tmp_path):
    """A normal model answer must still COMPLETE (classifier precision)."""
    dispatcher, database = staged_dispatcher(tmp_path)
    result = dispatch_one(dispatcher, "run-f6-d")
    assert result.status == COMPLETED, result
    assert result.verdict == "PASS", result
    state = state_of(database)
    assert state["rows"][0]["status"] == "COMPLETED", state
    assert state["role_leases"] == 0, state


def test_f6_fallback_used_and_recorded_with_real_adapter(tmp_path):
    """With an eligible fallback configured, the exit-0 failure on the
    preferred provider engages the fallback (recorded) then COMPLETES."""
    dispatcher, database = staged_dispatcher(
        tmp_path, line="HTTP 401: Model model-does-not-exist-xyz-2026 is not supported",
        fail_provider="opencode-go", fallbacks=("vertex_ai",))
    try:
        result = dispatch_one(dispatcher, "run-f6-e")
        assert result.status == COMPLETED, result
        assert result.provider == "vertex_ai", result
        state = state_of(database)
        events = state["agent_run_events"]
        assert events.get("agent_run.failed", 0) >= 1, events
        assert events.get("agent_run.completed", 0) == 1, events
        assert database.quick_check() == "ok"
    finally:
        os.environ.pop("JF_STAGED_LINE", None)
        os.environ.pop("JF_STAGED_FAIL_PROVIDER", None)


def test_f6_nonzero_exit_still_honest_oneshot_failure(tmp_path):
    """Non-zero exit semantics preserved (F2): FAILED hermes_oneshot_failed."""
    dispatcher, database = staged_dispatcher(tmp_path)
    try:
        os.environ["JF_STAGED_MODE"] = "down"
        result = dispatch_one(dispatcher, "run-f6-f")
        assert result.status == FAILED, result
        assert result.error_class == "hermes_oneshot_failed", result
        assert state_of(database)["role_leases"] == 0
    finally:
        os.environ.pop("JF_STAGED_MODE", None)
