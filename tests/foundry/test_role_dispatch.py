"""WP4 role dispatcher tests.

Gate (03 spec §16 WP4): a fake mission dispatches three isolated role
sessions (executive, builder, verifier) and records exact context and
provider identity. Plus: leases, heartbeats, provider fallback, structured
results, and the real-Hermes safety boundary.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.adapter import (
    AdapterConfigurationError,
    FakeHermesAdapter,
    HermesOneshotAdapter,
    ProviderUnavailable,
)
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.manifest import ContextManifest, verify_manifest_hash
from jarvis_foundry.roles.results import BLOCKED, COMPLETED, FAILED, RoleResult
from jarvis_foundry.roles.router import RoleRouter


def mission_database(tmp_path: Path, mission_id: str = "mission-1") -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-08-11T15:33:05Z", "standing_mandate": True},
        source_type="owner", source_id="local-owner",
        idempotency_key="system.start-build",
    ))
    database.append(Event.create(
        "mission.created",
        {"mission_id": mission_id, "objective": "fake mission for WP4 gate",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["three isolated role sessions recorded"]},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    return database


def dispatcher(tmp_path, router=None, adapter=None, **kwargs) -> RoleDispatcher:
    database = mission_database(tmp_path)
    return RoleDispatcher(
        database,
        router or RoleRouter(),
        adapter or FakeHermesAdapter(),
        **kwargs,
    )


def run_row(database: FoundryDatabase, run_id: str) -> dict:
    return database.row("agent_runs", "run_id", run_id)


# ---------------------------------------------------------------------------
# GATE: fake mission dispatches three isolated role sessions and records
# exact context and provider identity.
# ---------------------------------------------------------------------------

def test_gate_fake_mission_three_isolated_role_sessions(tmp_path):
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "executive": {"preferred_provider": "opencode_go", "preferred_model": "deepseek-v4-flash"},
        "builder": {"preferred_provider": "opencode_go", "preferred_model": "kimi-k2.7-code"},
        "verifier": {"preferred_provider": "opencode_go", "preferred_model": "deepseek-v4-flash"},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter, source_id="wp4-gate")

    roles = ["executive", "builder", "verifier"]
    results: dict[str, RoleResult] = {}
    for role in roles:
        results[role] = dispatcher.dispatch(
            role=role,
            mission_id="mission-1",
            resource_id="resource-1",
            capability="local_project_write",
            step_id="step-1",
            run_id=f"run-{role}",
            instructions=f"act as the {role} for the fake mission",
        )

    # Structured results: all COMPLETED with verdict PASS and provider identity.
    for role in roles:
        result = results[role]
        assert result.status == COMPLETED
        assert result.verdict == "PASS"
        assert result.provider == "opencode_go"
        assert result.role == role

    # Durable agent_runs: exactly three rows, one per isolated role session,
    # with exact provider identity and a verifiable context manifest hash.
    for role in roles:
        row = run_row(database, f"run-{role}")
        assert row["status"] == "COMPLETED"
        assert row["role"] == role
        assert row["provider"] == "opencode_go"
        assert row["mission_id"] == "mission-1"
        assert row["context_manifest_hash"] == adapter.received[
            roles.index(role)].content_hash()
        assert len(row["context_manifest_hash"]) == 64

    # Exact context recorded: recompute the manifest the adapter received and
    # prove the durable hash matches it exactly.
    for role in roles:
        received = adapter.received[roles.index(role)]
        assert received.mission_id == "mission-1"
        assert received.resource_id == "resource-1"
        assert received.step_id == "step-1"
        assert received.role == role
        assert verify_manifest_hash(received, run_row(database, f"run-{role}")["context_manifest_hash"])

    # Isolation: three distinct run_ids, three distinct lease-free rows.
    assert {r["run_id"] for r in (run_row(database, f"run-{r}") for r in roles)} == {
        "run-executive", "run-builder", "run-verifier"}

    # Heartbeats recorded for every session.
    for role in roles:
        row = run_row(database, f"run-{role}")
        assert row["heartbeat_at"] is not None

    # Structured result payload is durable in result_json.
    for role in roles:
        row = run_row(database, f"run-{role}")
        payload = json.loads(row["result_json"])
        assert payload["status"] == "COMPLETED"
        assert payload["provider"] == "opencode_go"
        assert payload["role"] == role


def test_gate_records_exact_context_and_provider_identity(tmp_path):
    """The gate phrase: 'records exact context and provider identity'."""
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "vertex_ai", "preferred_model": "gemini-x"},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter)

    dispatcher.dispatch(
        role="builder", mission_id="mission-1", resource_id="resource-1",
        capability="local_project_write", step_id="build-1", run_id="run-b1",
        instructions="build smallest correct change",
    )

    received = adapter.received[0]
    assert received.provider == "vertex_ai"
    assert received.model == "gemini-x"
    assert received.capability == "local_project_write"
    row = run_row(database, "run-b1")
    assert row["provider"] == "vertex_ai"          # exact provider identity
    assert row["model"] == "gemini-x"
    assert row["context_manifest_hash"] == received.content_hash()  # exact context
    assert row["step_id"] == "build-1"


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------

def test_dispatch_acquires_and_releases_lease(tmp_path):
    database = mission_database(tmp_path)
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    dispatcher.dispatch(role="builder", mission_id="mission-1",
                        resource_id="resource-1", capability="write", run_id="run-l1")
    with database.connect() as connection:
        rows = connection.execute("SELECT * FROM leases").fetchall()
    assert rows == []  # released after completion


def test_concurrent_same_step_lease_blocked(tmp_path):
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter)

    first = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                resource_id="resource-1", capability="write",
                                step_id="step-1", run_id="run-c1")
    assert first.status == COMPLETED

    # Simulate a concurrent holder: acquire the same lease key manually with
    # a different owner before dispatching again.
    from jarvis_foundry.supervisor.leases import LeaseManager
    LeaseManager(database).acquire("role:builder:mission-1:step-1", "other-owner", 300)
    second = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 step_id="step-1", run_id="run-c2")
    assert second.status == BLOCKED
    assert second.error_class == "lease_unavailable"
    # No run row is created (dispatch never started), but the denial is
    # durable as an agent_run.failed event with lease_unavailable class.
    with database.connect() as connection:
        denied = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_run.failed' "
            "AND payload_json LIKE '%lease_unavailable%'"
        ).fetchone()[0]
    assert denied >= 1
    assert run_row(database, "run-c2") is None


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------

def test_heartbeat_events_recorded(tmp_path):
    database = mission_database(tmp_path)
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    dispatcher.dispatch(role="executive", mission_id="mission-1",
                        resource_id="resource-1", capability="plan", run_id="run-h1")
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_run.heartbeat'"
        ).fetchone()[0]
    assert count >= 1
    row = run_row(database, "run-h1")
    assert row["heartbeat_at"] is not None


# ---------------------------------------------------------------------------
# Provider fallback
# ---------------------------------------------------------------------------

def test_provider_fallback_on_preferred_failure(tmp_path):
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go",
                    "fallbacks": ("vertex_ai",)},
    })
    adapter = FakeHermesAdapter(fail_provider="opencode_go")
    dispatcher = RoleDispatcher(database, router, adapter)

    result = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 run_id="run-f1")

    assert result.status == COMPLETED
    assert result.provider == "vertex_ai"  # fell back
    row = run_row(database, "run-f1")
    assert row["provider"] == "vertex_ai"
    # The first provider attempt was recorded as a failed run with
    # provider_unavailable, then the fallback completed the same run row.
    assert row["status"] == "COMPLETED"
    # Verifier finding: the completed projection must clear the stale
    # failure_class from the failed attempt (a COMPLETED row never claims
    # provider_unavailable).
    assert row["failure_class"] is None
    with database.connect() as connection:
        failed = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_run.failed'"
        ).fetchone()[0]
    assert failed >= 1
    # Adapter received both manifests with exact provider identity each time.
    assert [m.provider for m in adapter.received] == ["opencode_go", "vertex_ai"]


def test_all_providers_exhausted_blocks(tmp_path):
    class AllDown(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            raise ProviderUnavailable(f"down: {manifest.provider}")

    db2 = mission_database(tmp_path)
    exhausted_router = RoleRouter().clone_with({
        "verifier": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    d2 = RoleDispatcher(db2, exhausted_router, AllDown())
    blocked = d2.dispatch(role="verifier", mission_id="mission-1",
                          resource_id="resource-1", capability="verify",
                          run_id="run-x2")
    assert blocked.status == BLOCKED
    assert blocked.error_class == "provider_unavailable"
    assert "all providers failed" in blocked.error_detail
    row = run_row(db2, "run-x2")
    assert row["status"] == "FAILED"
    assert row["failure_class"] == "provider_unavailable"


def test_adapter_crash_marks_run_failed(tmp_path):
    database = mission_database(tmp_path)

    class Crash(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            raise RuntimeError("boom")

    dispatcher = RoleDispatcher(database, RoleRouter(), Crash())
    result = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 run_id="run-crash")
    assert result.status == FAILED
    assert result.error_class == "adapter_crash"
    row = run_row(database, "run-crash")
    assert row["status"] == "FAILED"
    assert row["failure_class"] == "adapter_crash"


# ---------------------------------------------------------------------------
# Structured results
# ---------------------------------------------------------------------------

def test_verifier_fail_verdict_structured(tmp_path):
    database = mission_database(tmp_path)
    adapter = FakeHermesAdapter(outcome=COMPLETED, verdict="FAIL", evidence=("found a bug",))
    dispatcher = RoleDispatcher(database, RoleRouter(), adapter)
    result = dispatcher.dispatch(role="verifier", mission_id="mission-1",
                                 resource_id="resource-1", capability="verify",
                                 run_id="run-vfail")
    assert result.status == COMPLETED
    assert result.verdict == "FAIL"
    assert result.evidence == ("found a bug",)
    payload = json.loads(run_row(database, "run-vfail")["result_json"])
    assert payload["verdict"] == "FAIL"
    assert payload["evidence"] == ["found a bug"]


# ---------------------------------------------------------------------------
# Real Hermes adapter safety boundary
# ---------------------------------------------------------------------------

def test_hermes_oneshot_adapter_disabled_by_default(tmp_path):
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    adapter = HermesOneshotAdapter(
        hermes_root=hermes_root, hermes_home=tmp_path / "isolated-home",
        enabled=False,
    )
    manifest = ContextManifest(
        run_id="run-1", role="builder", mission_id="mission-1",
        step_id="step-1", resource_id="resource-1", capability="write",
        provider="opencode_go", model="kimi-k2.7-code",
        isolation_group="builder",
    )
    with pytest.raises(ProviderUnavailable):
        adapter.dispatch(manifest, heartbeat=lambda: None)


def test_hermes_oneshot_adapter_rejects_live_hermes_root(tmp_path):
    with pytest.raises(AdapterConfigurationError):
        HermesOneshotAdapter(
            hermes_root=HermesOneshotAdapter.LIVE_HERMES,
            hermes_home=tmp_path / "home",
            enabled=True,
        )
    # Verifier finding (deleg_221bd327): any path INSIDE the live install is
    # equally invalid (parent-containment, mirroring the home check).
    with pytest.raises(AdapterConfigurationError):
        HermesOneshotAdapter(
            hermes_root=HermesOneshotAdapter.LIVE_HERMES / "venv",
            hermes_home=tmp_path / "home",
            enabled=False,
        )


def test_hermes_oneshot_adapter_requires_existing_isolated_home_when_enabled(tmp_path):
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    with pytest.raises(AdapterConfigurationError):
        HermesOneshotAdapter(
            hermes_root=hermes_root,
            hermes_home=tmp_path / "missing-home",
            enabled=True,
        )


def test_hermes_oneshot_adapter_rejects_live_hermes_home(tmp_path):
    # Verifier finding (deleg_f2954000): hermes_home was only checked for
    # existence, never for isolation. The live profile (or anything inside it)
    # must be rejected even when the adapter stays disabled.
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    live_home = HermesOneshotAdapter.LIVE_HERMES_HOME
    for home in (
        live_home,
        live_home / "profiles" / "default",   # child of the live profile
        live_home / "gateway",                # sibling service dir inside live root
    ):
        with pytest.raises(AdapterConfigurationError):
            HermesOneshotAdapter(
                hermes_root=hermes_root, hermes_home=home, enabled=False,
            )


def test_same_run_id_redispatch_replays_identically_and_conflicts_on_change(tmp_path):
    # A crashed caller re-dispatching the SAME run_id with the IDENTICAL
    # context replays from the ledger (no adapter re-run, no new events, one
    # run row); a changed payload under the same run_id conflicts loudly
    # (EventConflict) — first-write integrity (WP3 B6), fail-loud, no
    # corruption.
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter)

    first = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                resource_id="resource-1", capability="write",
                                step_id="step-1", run_id="run-replay",
                                instructions="do the thing")
    assert first.status == COMPLETED
    events_after_first = len(database.events())

    replay = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 step_id="step-1", run_id="run-replay",
                                 instructions="do the thing")
    assert replay.status == COMPLETED
    assert replay.run_id == "run-replay"
    assert len(database.events()) == events_after_first  # pure ledger replay
    assert len(adapter.received) == 1                     # adapter NOT re-run

    from jarvis_foundry.events import EventConflict
    with pytest.raises(EventConflict):
        dispatcher.dispatch(role="builder", mission_id="mission-1",
                            resource_id="resource-1", capability="write",
                            step_id="step-1", run_id="run-replay",
                            instructions="DO A DIFFERENT THING")


def test_provider_diversity_preferred_and_recorded(tmp_path):
    # Provider diversity (builder != verifier) is the standing preference: the
    # router resolves a verifier provider distinct from the builder's, and the
    # durable ledger records both exact provider identities for comparison.
    database = mission_database(tmp_path)
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go",
                    "preferred_model": "kimi-k2.7-code", "fallbacks": ()},
        "verifier": {"preferred_provider": "opencode_go",
                     "preferred_model": "deepseek-v4-flash",
                     "fallbacks": ("vertex_ai",)},
    })
    adapter = FakeHermesAdapter()
    dispatcher = RoleDispatcher(database, router, adapter)

    builder = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                  resource_id="resource-1", capability="write",
                                  step_id="step-1", run_id="run-dv-b")
    verifier_provider = router.diversity_resolution(builder.provider)
    verifier = dispatcher.dispatch(role="verifier", mission_id="mission-1",
                                   resource_id="resource-1", capability="verify",
                                   step_id="step-1", run_id="run-dv-v",
                                   provider_override=verifier_provider)

    assert router.prefer_diversity() is True
    assert builder.provider == "opencode_go"
    assert verifier_provider == "vertex_ai"
    assert verifier.provider == "vertex_ai"
    # Recorded durably with exact provider identity, distinct per role.
    builder_row = run_row(database, "run-dv-b")
    verifier_row = run_row(database, "run-dv-v")
    assert builder_row["provider"] == "opencode_go"
    assert verifier_row["provider"] == "vertex_ai"
    assert builder_row["provider"] != verifier_row["provider"]


def test_hermes_oneshot_command_surface_matches_real_cli(tmp_path, monkeypatch):
    # The real CLI (.h hermes_cli/_parser.py) accepts the prompt as the -z
    # VALUE, pairs --provider with --model, and has NO --role flag. The
    # adapter must emit exactly that surface; role identity rides in the
    # manifest/instructions. Regression: a --role flag or positional prompt
    # would make argparse exit 2 on every real dispatch.
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    adapter = HermesOneshotAdapter(
        hermes_root=hermes_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=60, python_exe="py",
    )
    manifest = ContextManifest(
        run_id="run-1", role="builder", mission_id="mission-1",
        step_id="step-1", resource_id="resource-1", capability="write",
        provider="opencode_go", model="kimi-k2.7-code",
        isolation_group="builder",
        instructions="build the smallest correct change",
    )

    captured = {}

    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env_home"] = env.get("HERMES_HOME")
        captured["timeout"] = timeout
        # Simulate the usage report the real CLI writes.
        import json
        usage_arg = command[command.index("--usage-file") + 1]
        Path(usage_arg).write_text(
            json.dumps({"estimated_cost_usd": 0.0123, "model": "kimi-k2.7-code"}),
            encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr("jarvis_foundry.roles.adapter.subprocess.run", fake_run)
    result = adapter.dispatch(manifest, heartbeat=lambda: None)

    command = captured["command"]
    assert command[0] == "py" and command[1:3] == ["-m", "hermes_cli.main"]
    assert command[3] == "-z"
    assert command[4] == "build the smallest correct change"  # prompt = -z VALUE
    assert "--role" not in command
    assert "--provider" in command and "opencode_go" in command
    assert "--model" in command and "kimi-k2.7-code" in command
    assert "--usage-file" in command
    assert captured["env_home"] == str(isolated_home)  # isolated home, never live
    assert captured["cwd"] == str(hermes_root)         # .h checkout, read-only
    assert captured["timeout"] == 60                   # bounded
    assert result.status == COMPLETED
    assert result.cost_usd == 0.0123                   # usage report parsed


def test_hermes_oneshot_usage_cost_absent_is_zero(tmp_path, monkeypatch):
    hermes_root = tmp_path / ".h"
    hermes_root.mkdir()
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    adapter = HermesOneshotAdapter(
        hermes_root=hermes_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=60,
    )
    manifest = ContextManifest(
        run_id="run-2", role="executive", mission_id="mission-1",
        step_id=None, resource_id="resource-1", capability="plan",
        provider="opencode_go", model="deepseek-v4-flash",
        isolation_group="executive",
    )

    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("jarvis_foundry.roles.adapter.subprocess.run", fake_run)
    result = adapter.dispatch(manifest, heartbeat=lambda: None)
    assert result.status == COMPLETED
    assert result.cost_usd == 0.0


def test_provider_override_for_staging(tmp_path):
    database = mission_database(tmp_path)
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    result = dispatcher.dispatch(role="builder", mission_id="mission-1",
                                 resource_id="resource-1", capability="write",
                                 provider_override="anthropic", run_id="run-p1")
    assert result.provider == "anthropic"
    assert run_row(database, "run-p1")["provider"] == "anthropic"
