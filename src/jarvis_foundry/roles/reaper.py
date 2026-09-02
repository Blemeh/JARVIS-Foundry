"""Deterministic reaper for stale role sessions (WP4).

A crashed or lost dispatcher leaves an ``agent_runs`` row in RUNNING and a
``role:*`` lease that expires without a completion event. The reaper is the
deterministic heartbeat-timeout enforcement: any RUNNING role session whose
lease is missing or expired is marked FAILED (failure_class
``heartbeat_timeout``) via a durable ``agent_run.failed`` event, and expired
``role:*`` leases are released.

Re-running the reaper is idempotent: the ``agent_run.failed`` event dedupes on
its idempotency key and the lease DELETE is naturally repeatable. The reaper
is pure ledger logic (no processes, no Hermes imports), so it can run inside
the independent Supervisor, at startup, or as a one-shot maintenance command.
"""
from __future__ import annotations

import json

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from ..events import Event

ROLE_LEASE_PREFIX = "role:"


def role_lease_key(role: str, mission_id: str, step_id: str | None) -> str:
    """Reconstruct the dispatcher's lease key for a role session."""
    return f"role:{role}:{mission_id}:{step_id or 'start'}"


def reap_stale_role_runs(database: FoundryDatabase, *, now=None) -> list[str]:
    """Mark RUNNING role sessions without a live lease as FAILED and release
    expired role leases. Returns the reaped run ids in deterministic order.

    A session is stale when its dispatcher lease is missing (dispatcher died
    before release, or crashed between start and completion) or expired (the
    session outlived its duration bound without heartbeats/completion).
    """
    current = now or utc_now()
    with database.connect() as connection:
        running = connection.execute(
            "SELECT run_id, role, mission_id, step_id FROM agent_runs WHERE status = 'RUNNING'"
        ).fetchall()
        lease_rows = connection.execute(
            "SELECT lease_key, expires_at FROM leases WHERE lease_key LIKE ?",
            (ROLE_LEASE_PREFIX + "%",),
        ).fetchall()

    live_keys: set[str] = set()
    for row in lease_rows:
        if parse(row["expires_at"]) > current:
            live_keys.add(row["lease_key"])

    reaped: list[str] = []
    for run in sorted(running, key=lambda item: item["run_id"]):
        key = role_lease_key(run["role"], run["mission_id"], run["step_id"])
        if key in live_keys:
            continue
        event = Event.create(
            "agent_run.failed",
            {
                "run_id": run["run_id"],
                "failure_class": "heartbeat_timeout",
                "detail": "session lease expired or missing without a completion event",
            },
            source_type="supervisor",
            source_id="role-reaper",
            idempotency_key=f"agent_run.failed:{run['run_id']}:heartbeat_timeout",
        )
        database.append(event)  # identical re-delivery dedupes on the key
        reaped.append(run["run_id"])

    with database.transaction() as connection:
        connection.execute(
            "DELETE FROM leases WHERE lease_key LIKE ? AND expires_at <= ?",
            (ROLE_LEASE_PREFIX + "%", isoformat(current)),
        )
    return reaped


SESSION_LOST_REASON = "session_lost:heartbeat_timeout:step_reclaimable"
RESUME_AFTER_SESSION_LOSS = "resume_after_session_loss"


def _live_role_lease_keys(database: FoundryDatabase, *, now) -> set[str]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT lease_key FROM leases WHERE lease_key LIKE ? AND expires_at > ?",
            (ROLE_LEASE_PREFIX + "%", isoformat(now)),
        ).fetchall()
    return {row["lease_key"] for row in rows}


def recover_stale_mission_state(database: FoundryDatabase, *, now=None) -> list[str]:
    """Deterministic recovery for missions left stuck in EXECUTING/VERIFYING
    by a killed role session (plan F3: builder killed after mutation, before
    result).

    A crash between the state advance and the session's terminal event leaves
    the mission in EXECUTING or VERIFYING with no live role lease and no
    completion — a state ``propose_next_action`` refuses forever. Recovery:

    1. the reaper (``reap_stale_role_runs``) already marked the dead run
       FAILED (heartbeat_timeout) and released its lease;
    2. here, the mission is transitioned EXECUTING/VERIFYING -> BLOCKED and
       then BLOCKED -> ACTION_SELECTION (preserving ``next_action``) so a
       fresh loop instance re-claims the step with a new run_id — no
       duplicate completion, no fabricated verdict, partial work preserved in
       the worktree for the fresh worker.

    Safety and determinism:
    - a mission with ANY RUNNING run holding a live role lease is untouched
      (a genuinely alive session is never disturbed);
    - deliberate blocks (repair-budget exhaustion, policy denial, commit
      failure) are never auto-resumed: only missions still in EXECUTING/
      VERIFYING are considered, and only the two recovery transitions are
      appended;
    - idempotency keys are INCIDENT-scoped (mission's ``last_event_id`` at
      recovery time, which is unique per stuck-incident), so a SECOND kill of
      the same mission recovers again — mission-scoped keys would dedupe
      across incidents and silently strand the mission (verifier finding
      F3-1);
    - the BLOCKED + ACTION_SELECTION pair is appended atomically in ONE
      transaction (``append_events``), so a crash cannot leave the mission in
      the intermediate BLOCKED state (verifier finding F3-3/F3-4: no
      unscoped ``events`` scan, no provenance ambiguity — the intermediate
      state is never observable).
    """
    current = now or utc_now()
    live_keys = _live_role_lease_keys(database, now=current)
    recovered: list[str] = []
    with database.connect() as connection:
        missions = connection.execute(
            "SELECT mission_id, state, next_action, last_event_id FROM missions "
            "WHERE state IN ('EXECUTING', 'VERIFYING') ORDER BY mission_id"
        ).fetchall()
    for mission in missions:
        mission_id = mission["mission_id"]
        next_action = mission["next_action"]
        with database.connect() as connection:
            running = connection.execute(
                "SELECT run_id, role, step_id FROM agent_runs "
                "WHERE mission_id = ? AND status = 'RUNNING'",
                (mission_id,),
            ).fetchall()
        if any(role_lease_key(r["role"], mission_id, r["step_id"]) in live_keys
               for r in running):
            continue  # a session is genuinely alive; never disturb it
        incident = str(mission["last_event_id"])
        # The state-transition event carries the exact run identity for this
        # in-flight incident. Never borrow an older completed builder from a
        # prior contract: a kill after EXECUTING but before dispatch must go
        # back through action selection, not skip straight to verification.
        with database.connect() as connection:
            transition = connection.execute(
                "SELECT payload_json, idempotency_key FROM events "
                "WHERE event_id = ?",
                (incident,),
            ).fetchone()
            try:
                transition_payload = json.loads(str(transition[0])) \
                    if transition is not None else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                transition_payload = {}
            incident_run_id = str(transition_payload.get("run_id") or "")
            if not incident_run_id and transition is not None:
                key = str(transition["idempotency_key"] or "")
                if ":run-" in key:
                    incident_run_id = key.rsplit(":run-", 1)[1]
            completed_builder = connection.execute(
                "SELECT run_id, step_id FROM agent_runs "
                "WHERE mission_id = ? AND run_id = ? "
                "AND role IN ('builder', 'repair') AND status = 'COMPLETED'",
                (mission_id, incident_run_id),
            ).fetchone() if incident_run_id else None
        if completed_builder is not None:
            # The external worker dispatch already has a durable result. Do
            # not reopen ACTION_SELECTION and dispatch it again. EXECUTING can
            # advance to the existing VERIFYING resume path; VERIFYING is
            # already the exact resumable state and is selected after the
            # dead outer loop releases its singleton lease.
            if str(mission["state"]) == "EXECUTING":
                database.append(Event.create(
                    "mission.state_changed",
                    {
                        "mission_id": mission_id,
                        "state": "VERIFYING",
                        "next_action": next_action,
                        "run_id": incident_run_id,
                        "reason": "resume_after_completed_dispatch",
                    },
                    source_type="supervisor", source_id="role-reaper",
                    idempotency_key=(
                        f"mission.state_changed:{mission_id}:VERIFYING:"
                        f"completed_dispatch:{incident}"
                    ),
                ))
            recovered.append(mission_id)
            continue
        block = Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": "BLOCKED",
             "next_action": next_action, "reason": SESSION_LOST_REASON},
            source_type="supervisor", source_id="role-reaper",
            idempotency_key=(
                f"mission.state_changed:{mission_id}:BLOCKED:session_lost:{incident}"),
        )
        resume = Event.create(
            "mission.state_changed",
            {"mission_id": mission_id, "state": "ACTION_SELECTION",
             "next_action": next_action, "reason": RESUME_AFTER_SESSION_LOSS},
            source_type="supervisor", source_id="role-reaper",
            idempotency_key=(
                f"mission.state_changed:{mission_id}:ACTION_SELECTION:session_lost:{incident}"),
        )
        database.append_events([block, resume])  # atomic: no partial BLOCKED
        recovered.append(mission_id)
    return recovered
