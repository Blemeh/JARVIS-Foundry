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
