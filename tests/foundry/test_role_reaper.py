"""WP4 reaper tests: heartbeat-timeout enforcement for role sessions.

Gate criterion: leases bound every session; heartbeat timeout terminates /
releases stale sessions deterministically. A crashed or lost dispatcher
leaves the run row RUNNING and the ``role:*`` lease expired-or-missing; the
reaper marks the run FAILED (heartbeat_timeout) and releases the lease.
"""
from __future__ import annotations

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.reaper import (
    reap_stale_role_runs,
    recover_stale_mission_state,
    role_lease_key,
)


def mission_database(tmp_path) -> FoundryDatabase:
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
        {"mission_id": "mission-1", "objective": "reaper fixture mission",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["stale sessions are terminated"]},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-1",
    ))
    return database


def start_run(database: FoundryDatabase, run_id: str, *, role: str = "builder",
              mission_id: str = "mission-1", step_id: str | None = None) -> None:
    """Durable RUNNING run row via the real event projection."""
    database.append(Event.create(
        "agent_run.started",
        {
            "run_id": run_id, "mission_id": mission_id, "step_id": step_id,
            "role": role, "provider": "opencode_go", "model": "test-model",
            "context_manifest_hash": "0" * 64, "tool_policy": [],
        },
        source_type="test", source_id="reaper-test",
        idempotency_key=f"agent_run.started:{run_id}:opencode_go",
    ))


def forge_lease(database: FoundryDatabase, key: str, expires_at: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at) VALUES (?, ?, ?, ?)",
            (key, "ghost-owner", expires_at, expires_at),
        )


def failed_event_count(database: FoundryDatabase) -> int:
    with database.connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'agent_run.failed' "
            "AND payload_json LIKE '%heartbeat_timeout%'"
        ).fetchone()[0]


def test_reaper_marks_run_with_expired_lease_failed(tmp_path):
    database = mission_database(tmp_path)
    start_run(database, "run-stale", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")

    reaped = reap_stale_role_runs(database)

    assert reaped == ["run-stale"]
    row = database.row("agent_runs", "run_id", "run-stale")
    assert row["status"] == "FAILED"
    assert row["failure_class"] == "heartbeat_timeout"
    assert failed_event_count(database) == 1
    # the expired lease was released
    assert database.row("leases", "lease_key",
                        role_lease_key("builder", "mission-1", "step-1")) is None


def test_reaper_marks_run_without_any_lease_failed(tmp_path):
    # crashed dispatcher: run row exists, lease never recorded / already gone
    database = mission_database(tmp_path)
    start_run(database, "run-orphan")

    reaped = reap_stale_role_runs(database)

    assert reaped == ["run-orphan"]
    assert database.row("agent_runs", "run_id", "run-orphan")["status"] == "FAILED"
    assert database.row("agent_runs", "run_id", "run-orphan")["failure_class"] == "heartbeat_timeout"


def test_reaper_is_idempotent(tmp_path):
    database = mission_database(tmp_path)
    start_run(database, "run-stale")
    forge_lease(database, role_lease_key("builder", "mission-1", None),
                "2000-01-01T00:00:00Z")

    first = reap_stale_role_runs(database)
    second = reap_stale_role_runs(database)

    assert first == ["run-stale"]
    assert second == []
    assert failed_event_count(database) == 1  # deduped on idempotency key


def test_reaper_skips_run_with_live_lease(tmp_path):
    database = mission_database(tmp_path)
    start_run(database, "run-live")
    forge_lease(database, role_lease_key("builder", "mission-1", None),
                "2999-01-01T00:00:00Z")

    reaped = reap_stale_role_runs(database)

    assert reaped == []
    assert database.row("agent_runs", "run_id", "run-live")["status"] == "RUNNING"
    assert database.row("leases", "lease_key",
                        role_lease_key("builder", "mission-1", None)) is not None


def test_reaper_releases_only_expired_role_leases(tmp_path):
    database = mission_database(tmp_path)
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")
    forge_lease(database, role_lease_key("verifier", "mission-1", "step-1"),
                "2999-01-01T00:00:00Z")

    reap_stale_role_runs(database)

    assert database.row("leases", "lease_key",
                        role_lease_key("builder", "mission-1", "step-1")) is None
    assert database.row("leases", "lease_key",
                        role_lease_key("verifier", "mission-1", "step-1")) is not None


def test_reaper_ignores_completed_and_non_role_leases(tmp_path):
    database = mission_database(tmp_path)
    # completed run: untouched even with an expired-looking absent lease
    start_run(database, "run-done")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE agent_runs SET status = 'COMPLETED' WHERE run_id = 'run-done'"
        )
    # non-role lease (worker lease): never deleted by the role reaper
    forge_lease(database, "worker:run-x", "2000-01-01T00:00:00Z")

    reaped = reap_stale_role_runs(database)

    assert reaped == []
    assert database.row("agent_runs", "run_id", "run-done")["status"] == "COMPLETED"
    assert database.row("leases", "lease_key", "worker:run-x") is not None


# ---------------------------------------------------------------------------
# F3 (plan): builder killed after mutation, before result — a mission left
# in EXECUTING/VERIFYING by a dead session must be re-claimable by a fresh
# instance without duplicates (recover_stale_mission_state).
# ---------------------------------------------------------------------------


def advance_mission(database: FoundryDatabase, mission_id: str, *states: str,
                    next_action: str = "deliver feature X", run_id: str | None = None) -> None:
    """Append legal mission.state_changed transitions up to EXECUTING.

    ``run_id`` mirrors the mission loop's run-scoped advance keys: each
    incident (dispatch attempt) appends DISTINCT advance events, which is
    what makes the recovery incident key (mission.last_event_id) unique per
    kill.
    """
    for state in states:
        key = f"mission.state_changed:{mission_id}:{state}"
        if run_id is not None:
            key = f"{key}:run-{run_id}"
        database.append(Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": next_action},
            source_type="test", source_id="reaper-test",
            idempotency_key=key,
        ))


def test_recover_stale_mission_state_resumes_stuck_executing_mission(tmp_path):
    # F3 shape: mission advanced to EXECUTING, builder run RUNNING with an
    # EXPIRED role lease (killed after mutation, before result). The reaper
    # marks the run FAILED; recovery must resume the mission to
    # ACTION_SELECTION (next_action preserved) so a fresh loop re-claims.
    database = mission_database(tmp_path)
    advance_mission(database, "mission-1", "ORIENTING", "ACTION_SELECTION",
                    "READY", "EXECUTING")
    start_run(database, "run-killed", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")

    reaped = reap_stale_role_runs(database)
    assert reaped == ["run-killed"]

    recovered = recover_stale_mission_state(database)

    assert recovered == ["mission-1"]
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert mission["next_action"] == "deliver feature X"
    # The killed run is durably FAILED; the lease was released.
    assert database.row("agent_runs", "run_id", "run-killed")["status"] == "FAILED"
    assert database.row("leases", "lease_key",
                        role_lease_key("builder", "mission-1", "step-1")) is None
    # Both recovery transitions are recorded in the ledger.
    events = database.events()
    payloads = [e.payload for e in events if e.event_type == "mission.state_changed"]
    assert any(p.get("reason") == "session_lost:heartbeat_timeout:step_reclaimable"
               and p["state"] == "BLOCKED" for p in payloads)
    assert any(p.get("reason") == "resume_after_session_loss"
               and p["state"] == "ACTION_SELECTION" for p in payloads)


def test_recover_stale_mission_state_is_idempotent(tmp_path):
    database = mission_database(tmp_path)
    advance_mission(database, "mission-1", "ORIENTING", "ACTION_SELECTION",
                    "READY", "EXECUTING")
    start_run(database, "run-killed", step_id="step-1")

    first = recover_stale_mission_state(database)
    second = recover_stale_mission_state(database)

    assert first == ["mission-1"]
    assert second == []
    # No duplicate recovery transitions in the ledger (the fixture's own
    # ACTION_SELECTION advance is distinct and must not be counted).
    events = [e for e in database.events() if e.event_type == "mission.state_changed"]
    resumes = [e for e in events
               if e.payload.get("reason") == "resume_after_session_loss"
               and e.payload.get("state") == "ACTION_SELECTION"]
    assert len(resumes) == 1


def test_recover_skips_mission_with_live_session(tmp_path):
    # A genuinely alive builder (live lease) must never be disturbed.
    database = mission_database(tmp_path)
    advance_mission(database, "mission-1", "ORIENTING", "ACTION_SELECTION",
                    "READY", "EXECUTING")
    start_run(database, "run-live", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2999-01-01T00:00:00Z")

    recovered = recover_stale_mission_state(database)

    assert recovered == []
    assert database.row("missions", "mission_id", "mission-1")["state"] == "EXECUTING"
    assert database.row("agent_runs", "run_id", "run-live")["status"] == "RUNNING"


def test_recover_never_resumes_deliberate_block(tmp_path):
    # A BLOCKED mission without the session-loss reason (repair budget,
    # policy denial, commit failure) must never be auto-resumed.
    database = mission_database(tmp_path)
    advance_mission(database, "mission-1", "ORIENTING", "ACTION_SELECTION",
                    "READY", "EXECUTING")
    database.append(Event.create(
        "mission.state_changed",
        {"mission_id": "mission-1", "state": "BLOCKED",
         "next_action": "deliver feature X"},
        source_type="test", source_id="reaper-test",
        idempotency_key="mission.state_changed:mission-1:BLOCKED:deliberate",
    ))

    recovered = recover_stale_mission_state(database)

    assert recovered == []
    assert database.row("missions", "mission_id", "mission-1")["state"] == "BLOCKED"


def test_recover_second_incident_same_mission_recovers_again(tmp_path):
    # Verifier finding F3-1 (critical): mission-scoped idempotency keys made
    # a SECOND kill of the same mission a silent no-op — the mission stayed
    # EXECUTING forever while recovery reported success. Keys are now
    # INCIDENT-scoped (mission last_event_id), so recurring kills recover.
    database = mission_database(tmp_path)

    # incident 1
    advance_mission(database, "mission-1", "ORIENTING", "ACTION_SELECTION",
                    "READY", "EXECUTING", run_id="run-1")
    start_run(database, "run-killed-1", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")
    reap_stale_role_runs(database)  # production order: reap, then recover
    assert recover_stale_mission_state(database) == ["mission-1"]
    assert database.row("missions", "mission_id", "mission-1")["state"] == "ACTION_SELECTION"

    # incident 2 (the same mission is killed again, fresh run + lease)
    advance_mission(database, "mission-1", "READY", "EXECUTING", run_id="run-2")
    start_run(database, "run-killed-2", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")
    reap_stale_role_runs(database)
    assert recover_stale_mission_state(database) == ["mission-1"]
    assert database.row("missions", "mission_id", "mission-1")["state"] == "ACTION_SELECTION"


def test_recover_second_incident_changed_next_action_no_conflict(tmp_path):
    # Verifier finding F3-2 (critical): with mission-scoped keys a changed
    # next_action on the second incident raised EventConflict inside the
    # supervisor heartbeat. Incident-scoped keys make the payloads distinct
    # — no conflict, both incidents recover.
    database = mission_database(tmp_path)

    advance_mission(database, "mission-1", "ORIENTING", "ACTION_SELECTION",
                    "READY", "EXECUTING", run_id="run-1")
    start_run(database, "run-killed-1", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")
    reap_stale_role_runs(database)
    assert recover_stale_mission_state(database) == ["mission-1"]

    # second incident with a NEW next_action
    advance_mission(database, "mission-1", "READY", "EXECUTING",
                    next_action="deliver feature Y", run_id="run-2")
    start_run(database, "run-killed-2", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")
    reap_stale_role_runs(database)
    recovered = recover_stale_mission_state(database)  # must not raise
    assert recovered == ["mission-1"]
    mission = database.row("missions", "mission_id", "mission-1")
    assert mission["state"] == "ACTION_SELECTION"
    assert mission["next_action"] == "deliver feature Y"


def test_recover_stale_verifying_mission(tmp_path):
    # Same recovery for a verifier killed mid-verification.
    database = mission_database(tmp_path)
    advance_mission(database, "mission-1", "ORIENTING", "ACTION_SELECTION",
                    "READY", "EXECUTING", "VERIFYING")
    start_run(database, "run-verifier-killed", role="verifier", step_id="step-1")

    recovered = recover_stale_mission_state(database)

    assert recovered == ["mission-1"]
    assert database.row("missions", "mission_id", "mission-1")["state"] == "ACTION_SELECTION"


def test_supervisor_heartbeat_reaps_role_runs_and_recovers_mission(tmp_path):
    # Wiring regression (F3): the supervisor's heartbeat (and start) must
    # reap stale role runs and recover missions left stuck by a killed
    # builder — the deterministic recovery runs inside the daemon, not only
    # in tests.
    from jarvis_foundry.supervisor.service import Supervisor

    database = mission_database(tmp_path)
    advance_mission(database, "mission-1", "ORIENTING", "ACTION_SELECTION",
                    "READY", "EXECUTING")
    start_run(database, "run-killed", step_id="step-1")
    forge_lease(database, role_lease_key("builder", "mission-1", "step-1"),
                "2000-01-01T00:00:00Z")

    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path, instance_id="wiring-check")
    assert supervisor.start() == "RUNNING"
    assert supervisor.heartbeat() == "RUNNING"

    assert database.row("agent_runs", "run_id", "run-killed")["status"] == "FAILED"
    assert database.row("missions", "mission_id", "mission-1")["state"] == "ACTION_SELECTION"
    supervisor.stop()


def test_supervisor_heartbeat_survives_reaper_failure(tmp_path):
    # Verifier finding F3-2 hardening: a raising maintenance path must never
    # take the daemon's heartbeat down — it records a throttled
    # supervisor.degraded event and the loop continues.
    import jarvis_foundry.roles.reaper as reaper_module
    from jarvis_foundry.supervisor.service import Supervisor

    database = mission_database(tmp_path)
    stop_path = tmp_path / "EMERGENCY_STOP"
    supervisor = Supervisor(database, stop_path, instance_id="reaper-fail-check")

    original = reaper_module.reap_stale_role_runs
    reaper_module.reap_stale_role_runs = lambda db, now=None: (_ for _ in ()).throw(
        RuntimeError("injected reaper failure"))
    try:
        assert supervisor.start() == "RUNNING"
        assert supervisor.heartbeat() == "RUNNING"
    finally:
        reaper_module.reap_stale_role_runs = original
        supervisor.stop()

    degraded = [e for e in database.events()
                if e.event_type == "supervisor.degraded"
                and e.payload.get("reason") == "role_reaper_failure"]
    assert len(degraded) == 1
    assert degraded[0].payload["error_class"] == "RuntimeError"
