"""Owner-facing Command Center read model.

The command center is deliberately read-only: every field is reconstructed
from the event ledger and its deterministic projections.  It never stores a
second operational truth and it never starts a worker, sender, or connector.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .database import FoundryDatabase
from .owner_controls import (
    OwnerAuthenticationError,
    OwnerControlService,
    OwnerReplayError,
    sign_command,
)
from .owner_directives import owner_control_state, owner_directive_snapshot
from .decision_queue import build_owner_attention_queue
from .result_traceability import build_result_traceability
from .operational_intelligence import project_operational_intelligence
from .supervisor.estop import EmergencyStop
from .mission.verification import accepted_verifications
from .v3_teams import DEFAULT_REPAIR_BUDGET
from .migrations import SCHEMA_VERSION


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _rows(connection, query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, args).fetchall()]


def build_health_snapshot(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    controls_configured: bool = False,
    runtime_home: str | Path | None = None,
    source_repo: str | Path | None = None,
) -> dict[str, Any]:
    """Build the cheap liveness/readiness projection.

    Health polling must not materialize missions, timeline, discovery,
    briefings, or the full Command Center packet.  It reads only the durable
    Supervisor control row, a bounded event count, SQLite quick-check, and
    immutable release pointers.  Operational/full state remains owned by
    :func:`build_snapshot` and its explicit API routes.
    """
    from .deployment.release import (
        read_current, read_previous, read_release_json, release_authority_report,
        release_dir,
    )
    from .deployment.runtime_identity import (
        parity_state, process_matches_home, release_sha_for_id,
        supervisor_processes,
    )
    from .supervisor.control import inspect_control
    from .trust import trust_status

    now = _now_iso()
    database_path = Path(database.path)
    observed_home = Path(runtime_home or database_path.parent).expanduser().resolve()
    if not database_path.is_file():
        return {
            "status": "DEGRADED", "ready": False, "generated_at": now,
            "reason": "database missing", "database": str(database_path),
            "controls_configured": controls_configured,
            "canonical_source_sha": None,
            "installed_release_sha": None,
            "runtime_release_sha": None,
            "parity_state": "MISSING_RUNTIME",
            "release_authority": {"status": "DATABASE_MISSING", "consistent": False},
            "schema": {"live": 0, "package": None},
            "release": {
                "current": None, "previous": None, "status": "UNKNOWN",
                "canonical_source_sha": None, "installed_release_sha": None,
                "runtime_release_sha": None, "parity_state": "MISSING_RUNTIME",
                "release_authority": {"status": "DATABASE_MISSING", "consistent": False},
            },
        }
    try:
        control = inspect_control(database, stop_path=str(stop_path) if stop_path else None)
        with command_center_readonly_connection(database) as connection:
            state_row = connection.execute(
                "SELECT status, trust_state, last_heartbeat_at "
                "FROM supervisor_state WHERE singleton = 1").fetchone()
            event_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            schema_row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        quick_check = database.quick_check(readonly=True)
    except Exception as error:  # noqa: BLE001 - health is fail-closed
        return {
            "status": "DEGRADED", "ready": False, "generated_at": now,
            "reason": f"health read failed: {type(error).__name__}",
            "controls_configured": controls_configured,
            "canonical_source_sha": None,
            "installed_release_sha": None,
            "runtime_release_sha": None,
            "parity_state": "UNKNOWN",
            "release_authority": {"status": "UNKNOWN", "consistent": False},
        }
    current = read_current()
    previous = read_previous()
    release_status = "UNKNOWN"
    release_manifest: dict[str, Any] | None = None
    if current:
        manifest_path = release_dir(current) / "RELEASE.json"
        try:
            release_manifest = read_release_json(manifest_path)
            release_status = "VALID" if str(release_manifest.get("source_sha")) == current else "DEGRADED"
        except (OSError, ValueError):
            release_status = "DEGRADED"
    canonical_source_sha: str | None = None
    canonical_source_origin: str | None = None
    source_root = source_repo or os.environ.get("JARVIS_SOURCE_REPO")
    if source_root:
        import subprocess

        source_path = Path(source_root).expanduser().resolve()
        if (source_path / ".git").exists():
            try:
                result = subprocess.run(
                    ["git", "-C", str(source_path), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                if result.returncode == 0:
                    canonical_source_sha = result.stdout.strip() or None
                    if canonical_source_sha:
                        canonical_source_origin = "source_checkout"
            except (OSError, subprocess.TimeoutExpired):
                canonical_source_sha = None
    state = dict(state_row) if state_row else {}
    # ``release_id`` is event/control provenance, not a column on the compact
    # supervisor_state projection.  Reading a non-existent column here would
    # make every health poll fail closed with OperationalError and hide the
    # very release identity the endpoint is meant to expose.
    recorded_release_id = str(getattr(control, "release_id", "") or "").strip() or None
    if recorded_release_id:
        state["release_id"] = recorded_release_id
    runtime_release_sha = current if (
        current and release_manifest and state.get("release_id")
        and str(state.get("release_id")) == str(release_manifest.get("release_id"))
    ) else None
    all_processes = supervisor_processes()
    scoped_processes = [
        item for item in all_processes if process_matches_home(item, observed_home)
    ]
    active_processes = [
        item for item in scoped_processes
        if item.get("identity_status", "VALID") == "VALID" and item.get("live", True)
    ]
    stale_processes = [item for item in scoped_processes if item not in active_processes]
    # A headless immutable release may intentionally have no mutable source
    # checkout configured.  Its validated manifest still carries the exact
    # source identity used to build the release, so use that identity only
    # after one live Supervisor is proven; duplicates stay fail-closed.
    if (canonical_source_sha is None and len(active_processes) == 1
            and release_status == "VALID" and release_manifest is not None
            and current):
        manifest_sha = str(release_manifest.get("source_sha") or "").strip().lower()
        if manifest_sha == str(current).lower():
            canonical_source_sha = manifest_sha
            canonical_source_origin = "immutable_release_manifest"
    runtime_shas = [item.get("release_sha") for item in active_processes]
    # The process command line often omits the immutable SHA (the headless
    # launcher resolves CURRENT internally).  If the one identity-validated
    # process is also the durable control owner, resolve its recorded release
    # id through the immutable manifest; never guess from the package version.
    if (len(active_processes) == 1
            and not any(runtime_shas)
            and control.pid == active_processes[0].get("pid")):
        recorded_sha = release_sha_for_id(state.get("release_id"))
        if recorded_sha:
            runtime_shas = [recorded_sha]
    runtime_release_sha = (
        next(iter({str(value).lower() for value in runtime_shas if value}))
        if len({str(value).lower() for value in runtime_shas if value}) == 1 else None
    )
    parity = parity_state(
        canonical_sha=canonical_source_sha,
        installed_sha=current,
        runtime_shas=runtime_shas,
        supervisor_count=len(active_processes),
    )
    authority = release_authority_report(foundry_home=observed_home)
    live_schema = int(schema_row[0] if schema_row else 0)
    stop_active = bool(stop_path and EmergencyStop(stop_path).active())
    runtime_status = str(control.status or state.get("status") or "UNKNOWN").upper()
    if stop_active or runtime_status == "BLOCKED":
        status, reason = "BLOCKED", "emergency STOP or runtime block is active"
    elif runtime_status not in {"RUNNING", "FROZEN"}:
        status, reason = "DEGRADED", f"Supervisor control status is {runtime_status}"
    elif quick_check != "ok":
        status, reason = "DEGRADED", f"SQLite quick_check is {quick_check}"
    elif live_schema != SCHEMA_VERSION:
        status, reason = "DEGRADED", (
            f"schema mismatch: live={live_schema} package={SCHEMA_VERSION}")
    elif release_status != "VALID":
        status, reason = "DEGRADED", f"release identity status is {release_status}"
    elif authority.get("status") != "CONSISTENT":
        status, reason = "DEGRADED", (
            f"release authority is {authority.get('status')}")
    elif parity != "MATCH":
        status, reason = "DEGRADED", f"source/installed/runtime parity is {parity}"
    elif not controls_configured:
        status, reason = "DEGRADED", "owner authentication secret is not configured"
    else:
        status, reason = "OK", "liveness and readiness proven"
    trust = trust_status(database)
    return {
        "status": status,
        "ready": status == "OK",
        "degraded": status != "OK",
        "generated_at": now,
        "reason": reason,
        "stop_active": stop_active,
        "controls_configured": controls_configured,
        "supervisor_status": runtime_status,
        "trust_state": state.get("trust_state", "UNKNOWN"),
        "trust": trust,
        "heartbeat_at": state.get("last_heartbeat_at") or control.observed_at,
        "pid": control.pid,
        "creation_identity": control.creation_identity,
        "runtime_instance_id": control.instance_id,
        "canonical_source_sha": canonical_source_sha,
        "canonical_source_origin": canonical_source_origin,
        "installed_release_sha": current,
        "runtime_release_sha": runtime_release_sha,
        "parity_state": parity,
        "release_authority": authority,
        "event_count": event_count,
        "database": str(database_path),
        "database_quick_check": quick_check,
        "schema": {"live": int(schema_row[0] if schema_row else 0), "package": SCHEMA_VERSION},
        "release": {
            "current": current,
            "previous": previous,
            "canonical_source_sha": canonical_source_sha,
            "canonical_source_origin": canonical_source_origin,
            "source_sha": release_manifest.get("source_sha") if release_manifest else None,
            "installed_release_sha": current,
            "runtime_sha": runtime_release_sha,
            "runtime_release_sha": runtime_release_sha,
            "release_id": release_manifest.get("release_id") if release_manifest else None,
            "status": release_status,
            "parity_state": parity,
            "release_authority": authority,
            "supervisor_count": len(active_processes),
            "active_supervisor_processes": active_processes,
            "stale_supervisor_processes": stale_processes,
        },
    }


def build_operational_status_snapshot(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    controls_configured: bool = False,
    runtime_home: str | Path | None = None,
    source_repo: str | Path | None = None,
) -> dict[str, Any]:
    """Build bounded operational status without materializing the UI packet.

    ``/api/health`` is intentionally a liveness/readiness probe.  This
    companion status projection adds small aggregate views useful to an
    operator (mission/worker/lease/directive counts and a bounded tail of
    event types) while keeping the expensive full Command Center snapshot on
    its explicit route.
    """
    health = build_health_snapshot(
        database,
        stop_path=stop_path,
        controls_configured=controls_configured,
        runtime_home=runtime_home,
        source_repo=source_repo,
    )
    if not Path(database.path).is_file():
        return {
            "status": health.get("status", "DEGRADED"),
            "generated_at": health.get("generated_at", _now_iso()),
            "health": health,
            "mission_counts": {},
            "worker_counts": {},
            "active_lease_count": 0,
            "directive_counts": {},
            "recent_events": [],
        }
    try:
        with command_center_readonly_connection(database) as connection:
            def grouped(table: str, column: str) -> dict[str, int]:
                rows = connection.execute(
                    f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}"  # noqa: S608 - fixed table/column constants
                ).fetchall()
                return {str(row[0] or "UNKNOWN"): int(row[1]) for row in rows}

            mission_counts = grouped("missions", "state")
            worker_counts = grouped("agent_runs", "status")
            directive_counts = grouped("directives", "active")
            active_lease_count = int(connection.execute(
                # Lease timestamps are canonical ISO-8601 (``T``/``Z``),
                # while SQLite's CURRENT_TIMESTAMP uses a space separator.
                # Lexical comparison therefore counts an expired same-day
                # lease as live.  julianday parses both forms and treats
                # malformed timestamps as non-live instead of inflating the
                # operator status.
                "SELECT COUNT(*) FROM leases "
                "WHERE julianday(expires_at) >= julianday('now')"
            ).fetchone()[0])
            recent_events = [
                dict(row) for row in connection.execute(
                    "SELECT event_id, event_type, recorded_at, source_type, source_id "
                    "FROM events ORDER BY sequence DESC LIMIT 20"
                ).fetchall()
            ]
    except Exception as error:  # noqa: BLE001 - status fails closed
        return {
            "status": "DEGRADED",
            "generated_at": health.get("generated_at", _now_iso()),
            "reason": f"operational status read failed: {type(error).__name__}",
            "health": health,
            "mission_counts": {},
            "worker_counts": {},
            "active_lease_count": 0,
            "directive_counts": {},
            "recent_events": [],
        }
    return {
        "status": health.get("status", "DEGRADED"),
        "health": health,
        "generated_at": health.get("generated_at", _now_iso()),
        "reason": health.get("reason"),
        "supervisor_status": health.get("supervisor_status", "UNKNOWN"),
        "trust_state": health.get("trust_state", "UNKNOWN"),
        "stop_active": health.get("stop_active", False),
        "mission_counts": mission_counts,
        "worker_counts": worker_counts,
        "active_lease_count": active_lease_count,
        "directive_counts": directive_counts,
        "recent_events": recent_events,
        "release": health.get("release", {}),
        "schema": health.get("schema", {}),
        "parity_state": health.get("parity_state", "UNKNOWN"),
        "release_authority": health.get("release_authority", {}),
    }


@contextmanager
def command_center_readonly_connection(database: FoundryDatabase):
    """Yield a mechanically read-only SQLite handle for Command Center reads.

    ``mode=ro`` is enforced by :meth:`FoundryDatabase.connect`; this helper
    keeps every read-model query on that path and fails with ``FileNotFoundError``
    when a configured live database is absent instead of creating a replacement.
    """
    with database.connect(readonly=True) as connection:
        yield connection


def _record_ref(kind: str, identifier: Any, event_id: Any = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"kind": kind, "id": identifier}
    if event_id:
        ref["event_id"] = event_id
    return ref


def _event_refs(payload: dict[str, Any], event_id: str) -> list[dict[str, Any]]:
    refs = [_record_ref("event", event_id, event_id)]
    mappings = {
        "mission_id": "mission", "run_id": "agent_run", "bundle_id": "evidence",
        "verdict_id": "verification", "candidate_id": "candidate",
        "measurement_id": "cost_measurement", "briefing_id": "briefing",
        "interaction_id": "voice_interaction", "directive_id": "directive",
        "artifact_hash": "artifact",
    }
    for key, kind in mappings.items():
        if payload.get(key) is not None:
            refs.append(_record_ref(kind, payload[key], event_id))
    if isinstance(payload.get("artifact_hashes"), dict):
        for name, digest in payload["artifact_hashes"].items():
            refs.append({"kind": "artifact", "id": name, "sha256": digest, "event_id": event_id})
    if payload.get("evidence"):
        refs.append({"kind": "evidence", "value": payload["evidence"], "event_id": event_id})
    return refs


def _worker_lease_matches(lease: dict[str, Any], worker: dict[str, Any]) -> bool:
    """Return whether a lease belongs to this durable worker run.

    Role dispatch leases are owned by the run id, while the lower-level
    worker lease is owned by its ``worker:<run_id>`` key.  Matching both the
    key and owner prevents an unrelated retry for the same mission/step from
    making a terminal historical run look current.
    """
    run_id = str(worker.get("run_id") or "")
    if not run_id:
        return False
    lease_key = str(lease.get("lease_key") or "")
    owner_id = str(lease.get("owner_id") or "")
    role_key = (
        f"role:{worker.get('role')}:{worker.get('mission_id')}"
        f":{worker.get('step_id') or 'start'}"
    )
    worker_key = f"worker:{run_id}"
    return (lease_key == role_key and owner_id == run_id) or (
        lease_key == worker_key and owner_id == worker_key
    )


def _current_failed_workers(
    workers: list[dict[str, Any]],
    leases: list[dict[str, Any]],
    stale_leases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select worker failures that still describe the current runtime.

    ``FAILED`` is a terminal ledger outcome, not a current health signal by
    itself.  A failed run remains a current crash only while its own lease is
    still present (including an expired lease, which is separately surfaced
    as stale).  Conversely, a ``RUNNING`` projection with no live own lease
    is an unresolved crash.  Historical terminal failures remain visible in
    ``workers`` and are never deleted or rewritten.
    """
    stale_ids = {
        (str(lease.get("lease_key") or ""), str(lease.get("owner_id") or ""))
        for lease in stale_leases
    }

    def has_lease(worker: dict[str, Any], *, live_only: bool) -> bool:
        return any(
            _worker_lease_matches(lease, worker)
            and (
                not live_only
                or (str(lease.get("lease_key") or ""), str(lease.get("owner_id") or ""))
                not in stale_ids
            )
            for lease in leases
        )

    return [
        worker for worker in workers
        if (
            worker.get("status") == "FAILED"
            and has_lease(worker, live_only=False)
        ) or (
            worker.get("status") == "RUNNING"
            and not has_lease(worker, live_only=True)
        )
    ]


def decision_timeline(database: FoundryDatabase, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return a compact trace of material ledger decisions and outcomes."""
    limit = max(1, min(int(limit), 2000))
    events = list(
        reversed(
            database.events(
                readonly=True,
                limit=max(1000, limit * 20),
                newest_first=True,
                exclude_event_types=(
                    "supervisor.heartbeat",
                    "worker_loop.cycle_started",
                    "worker_loop.waiting",
                    "agent_run.heartbeat",
                ),
            )
        )
    )
    material = []
    for sequence, event in enumerate(events, start=1):
        payload = dict(event.payload) if isinstance(event.payload, dict) else {}
        event_type = str(event.event_type or "")
        material.append({
            "sequence": sequence,
            "event_id": event.event_id,
            "event_type": event_type,
            "occurred_at": event.occurred_at,
            "recorded_at": event.recorded_at,
            "source": {"type": event.source_type, "id": event.source_id},
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "summary": payload.get("reason") or payload.get("objective") or payload.get("status") or event_type,
            "decision": payload.get("decision") or payload.get("verdict") or payload.get("outcome"),
            # State transitions are material lifecycle evidence. Keep these
            # bounded scalar fields in the projection so consumers can expose
            # repair/retry without reconstructing or guessing from prose.
            "state": payload.get("state") if isinstance(payload.get("state"), str) else None,
            "next_action": payload.get("next_action") if isinstance(payload.get("next_action"), str) else None,
            "rejected_alternative": payload.get("rejection_reason") or payload.get("reason") if "reject" in event_type else None,
            "evidence_refs": _event_refs(payload, event.event_id),
        })
    return material[-limit:]


def _safe_table(
    connection, table: str, query: str, args: tuple[Any, ...] = (),
    *, errors: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    try:
        return _rows(connection, query, args)
    except Exception as error:
        # A V4 UI pointed at an older read-only candidate reports NOT_TESTED
        # rather than turning a missing additive table into fabricated state.
        if errors is not None:
            errors.append({
                "projection": table,
                "status": "UNAVAILABLE",
                "error_class": type(error).__name__,
                "reason": str(error)[:300],
            })
        return []


def _bounded_event_rows(
    connection: sqlite3.Connection,
    patterns: tuple[str, ...],
    *,
    limit: int,
) -> list[tuple[int, str, str, str, str]]:
    """Read a bounded newest-first slice without exposing raw payloads."""
    clauses = " OR ".join("event_type LIKE ?" for _ in patterns)
    try:
        return [
            (int(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]))
            for row in connection.execute(
                "SELECT sequence, event_id, event_type, occurred_at, "
                "payload_json FROM events WHERE " + clauses +
                " ORDER BY sequence DESC LIMIT ?",
                (*patterns, int(limit)),
            ).fetchall()
        ]
    except Exception:
        # A legacy schema or malformed optional projection is an explicit
        # empty/unknown surface, never a fabricated success state.
        return []


def _effect_truth_projection(
    connection: sqlite3.Connection, *, limit: int = 100,
) -> dict[str, Any]:
    """Project V5 terminal effect truth for owner-facing read models.

    Only the durable attempt, lineage, and system-action state is exposed;
    provider request bodies and before/after blobs stay outside this surface.
    External payloads remain untrusted observations, not authority.
    """
    rows = _bounded_event_rows(
        connection,
        ("connector.attempt_recorded", "correlation.lineage_recorded"),
        limit=max(1, min(int(limit) * 3, 600)),
    )
    attempts: dict[str, dict[str, Any]] = {}
    lineage: dict[str, dict[str, Any]] = {}
    for sequence, event_id, event_type, occurred_at, raw in rows:
        payload, malformed = _load_json_object(raw)
        if malformed:
            continue
        if event_type == "connector.attempt_recorded":
            key = str(payload.get("attempt_id") or event_id)
            if key in attempts:
                continue
            attempts[key] = {
                "attempt_id": payload.get("attempt_id"),
                "mission_id": payload.get("mission_id"),
                "resource_id": payload.get("resource_id"),
                "effect_ticket_id": payload.get("effect_ticket_id"),
                "connector_id": payload.get("connector_id"),
                "action_kind": payload.get("action_kind"),
                "outcome": payload.get("outcome", "UNKNOWN"),
                "error_code": payload.get("error_code"),
                "provider_ref": payload.get("provider_ref"),
                "evidence_refs": list(payload.get("evidence_refs") or []),
                "reconciled": bool(payload.get("reconciled", False)),
                "source_event_id": event_id,
                "source_sequence": sequence,
                "occurred_at": occurred_at,
            }
        else:
            key = str(payload.get("lineage_id") or event_id)
            if key in lineage:
                continue
            lineage[key] = {
                "lineage_id": payload.get("lineage_id"),
                "resource_id": payload.get("resource_id"),
                "mission_id": payload.get("mission_id"),
                "connector_request_id": payload.get("connector_request_id"),
                "effect_ticket_id": payload.get("effect_ticket_id"),
                "external_object_id": payload.get("external_object_id"),
                "outcome": payload.get("outcome", "UNKNOWN"),
                "confidence": payload.get("confidence", 0.0),
                "ambiguity_reason": payload.get("ambiguity_reason"),
                "evidence_refs": list(payload.get("evidence_refs") or []),
                "source_refs": list(payload.get("source_refs") or []),
                "source_event_id": event_id,
                "source_sequence": sequence,
                "occurred_at": occurred_at,
            }
    actions = _safe_table(
        connection,
        "system_actions",
        "SELECT action_key, system_id, target_ref, kind, status, "
        "last_event_id, updated_at FROM system_actions "
        "ORDER BY updated_at DESC LIMIT ?",
        (max(1, min(int(limit), 200)),),
    )
    attempt_rows = list(attempts.values())[: max(1, int(limit))]
    lineage_rows = list(lineage.values())[: max(1, int(limit))]
    statuses = [str(row.get("outcome") or "UNKNOWN").upper()
                for row in attempt_rows + lineage_rows]
    statuses.extend(str(row.get("status") or "UNKNOWN").upper()
                    for row in actions)
    unknown = sum(status in {"UNKNOWN", "NOT_TESTED"} for status in statuses)
    failures = sum(status in {"FAILED", "FROZEN", "BLOCKED"}
                   for status in statuses)
    pending = sum(status in {"STARTED", "PROBING", "PENDING"}
                  for status in statuses)
    return {
        "schema": "v5.effect-truth.v1",
        "status": "AVAILABLE" if (attempt_rows or lineage_rows or actions)
        else "EMPTY",
        "source": "append_only_event_ledger",
        "external_payloads": "UNTRUSTED",
        "attempts": attempt_rows,
        "lineage": lineage_rows,
        "actions": actions,
        "summary": {
            "attempts": len(attempt_rows),
            "lineage": len(lineage_rows),
            "actions": len(actions),
            "unknown": unknown,
            "failures_or_frozen": failures,
            "pending": pending,
        },
    }


def _evolution_truth_projection(
    connection: sqlite3.Connection, *, limit: int = 100,
) -> dict[str, Any]:
    """Project V6 evidence/gate signals without granting UI authority."""
    rows = _bounded_event_rows(
        connection,
        (
            "improvement.%", "measurement.%", "experiment.%",
            "shadow.%", "benchmark.%", "verifier.%", "eligibility.%",
            "cycle.%", "feedback.%", "evolution.%",
        ),
        limit=max(1, min(int(limit), 400)),
    )
    records: list[dict[str, Any]] = []
    eligible = 0
    for sequence, event_id, event_type, occurred_at, raw in rows:
        payload, malformed = _load_json_object(raw)
        if malformed:
            continue
        if event_type == "verifier.run_recorded":
            if payload.get("trusted_independent_pass") is True and \
                    payload.get("all_criteria_pass") is True:
                status = "PASS"
            elif payload.get("all_criteria_pass") is False:
                status = "FAIL"
            else:
                status = "NOT_TESTED"
        elif event_type == "eligibility.evaluated":
            status = str(payload.get("decision") or "UNKNOWN").upper()
            eligible += status == "ELIGIBLE"
        else:
            status = str(payload.get("verdict") or payload.get("outcome")
                         or payload.get("status") or payload.get("decision")
                         or "RECORDED").upper()
        records.append({
            "sequence": sequence,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "status": status,
            "improvement_id": payload.get("improvement_id"),
            "candidate_release_id": payload.get("candidate_release_id"),
            "verification_id": payload.get("verification_id"),
            "evidence_refs": list(payload.get("evidence_refs") or []),
        })
    counts: dict[str, int] = {}
    for record in records:
        status = str(record["status"])
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema": "v6.evolution-truth.v1",
        "status": "AVAILABLE" if records else "EMPTY",
        "source": "governed_evidence_and_independent_verdicts",
        "ui_authority": "NONE",
        "records": records[: max(1, int(limit))],
        "summary": {
            "records": len(records),
            "eligible_decisions": eligible,
            "by_status": counts,
        },
    }


def _portfolio_projection(database: FoundryDatabase, *, limit: int = 10) -> dict[str, Any]:
    """Project V7 portfolio decisions, incidents, and procedure state.

    Additive read-only view over portfolio.*, incident.*, and
    operation.procedure.* events. Every field traces to the ledger; no
    simulated values.
    """
    latest: dict[str, Any] | None = None
    recent: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    portfolio_events = database.events(
        readonly=True,
        event_types=(
            "portfolio.selection_recorded",
            "portfolio.outcome_recorded",
            "incident.detected",
            "incident.transitioned",
        ),
    )
    for event in portfolio_events:
        if event.event_type == "portfolio.selection_recorded":
            payload = dict(event.payload)
            entry = {
                "decision_id": payload.get("decision_id"),
                "decision": payload.get("decision"),
                "selected_class": payload.get("selected_class"),
                "entity_id": payload.get("entity_id"),
                "reason": payload.get("reason"),
                "why_now": payload.get("why_now"),
                "expected_value": payload.get("expected_value"),
                "uncertainty": payload.get("uncertainty"),
                "capacity_check": payload.get("capacity_check"),
                "wake_on": list(payload.get("wake_on") or []),
                "revisit_at": payload.get("revisit_at"),
                "alternatives": list(payload.get("alternatives") or [])[:5],
                "recorded_at": event.recorded_at,
            }
            if latest is None:
                latest = entry
            if len(recent) < limit:
                recent.append(entry)
        elif event.event_type == "portfolio.outcome_recorded":
            if len(outcomes) < limit:
                outcomes.append({
                    "outcome_id": event.payload.get("outcome_id"),
                    "entity_id": event.payload.get("entity_id"),
                    "kind": event.payload.get("kind"),
                    "status": event.payload.get("status"),
                    "repair_count": event.payload.get("repair_count"),
                    "recorded_at": event.recorded_at,
                })
    incidents: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    for event in portfolio_events:
        if event.event_type == "incident.detected":
            statuses[str(event.payload.get("incident_id"))] = "DETECTED"
            incidents.append({
                "incident_id": event.payload.get("incident_id"),
                "kind": event.payload.get("kind"),
                "severity": event.payload.get("severity"),
                "correlation_key": event.payload.get("correlation_key"),
                "status": "DETECTED",
                "detected_at": event.recorded_at,
            })
        elif event.event_type == "incident.transitioned":
            incident_id = str(event.payload.get("incident_id") or "")
            statuses[incident_id] = str(event.payload.get("status") or "")
    for incident in incidents:
        incident["status"] = statuses.get(
            str(incident["incident_id"]), incident["status"])
    return {
        "latest_decision": latest,
        "recent_decisions": recent,
        "outcomes": outcomes,
        "incidents": incidents[:limit],
        "open_incident_count": sum(
            1 for i in incidents
            if i["status"] not in {"RECOVERED", "BLOCKED", "ESCALATED",
                                   "UNKNOWN"}),
    }


def _postv7_projection(
    database: FoundryDatabase,
    *,
    portfolio: dict[str, Any],
    effect_truth: dict[str, Any],
    limit: int = 100,
) -> dict[str, Any]:
    """Expose Post-V7 seams without creating a second source of truth.

    Account state, procedure bindings, incidents, and portfolio decisions are
    reconstructed from the canonical ledger.  A missing live account or
    procedure event is reported as an explicit boundary, never as activation.
    Credential references and raw provider payloads are intentionally omitted.
    """
    try:
        from .connectors.account_activation import activation_records
        from .procedure_library.definitions import build_catalog

        account_rows: list[dict[str, Any]] = []
        for record in activation_records(database):
            row = record.to_dict()
            for secret_key in (
                "credential_reference", "last_probe", "last_reconcile",
                "last_write_eligibility",
            ):
                row.pop(secret_key, None)
            account_rows.append(row)
        if not account_rows:
            account_status = "UNCONFIGURED"
        elif all(str(row.get("readiness")).upper() == "READY" for row in account_rows):
            account_status = "READY"
        else:
            account_status = "DEGRADED"

        catalog = build_catalog()
        proof_counts: dict[str, int] = {}
        for definition in catalog.definitions:
            status = str(definition.proof_status)
            proof_counts[status] = proof_counts.get(status, 0) + 1

        # Prefix predicates on the append-only ledger are not indexed on all
        # historical installations.  Read a bounded newest window first and
        # filter in memory so a Command Center refresh cannot rescan the full
        # ledger as it grows.
        window_limit = max(400, min(max(1, int(limit)), 1000))
        ledger_window = database.events(
            readonly=True,
            limit=window_limit,
            newest_first=True,
        )
        procedure_events: list[dict[str, Any]] = []
        for event in ledger_window:
            if not event.event_type.startswith(("procedure.library.", "operation.procedure.")):
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            procedure_events.append({
                "event_id": event.event_id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "procedure_id": payload.get("procedure_id") or payload.get("library_procedure_id"),
                "mission_id": payload.get("mission_id"),
                "step_id": payload.get("step_id"),
                "outcome": payload.get("outcome") or payload.get("status"),
                "verifier_id": (payload.get("verification") or {}).get("verifier_id")
                if isinstance(payload.get("verification"), dict) else None,
            })

        manual_count = database.event_count(
            readonly=True,
            event_type="manual_continuation_required",
        )
        return {
            "account_readiness": {
                "status": account_status,
                "owner_activation_required": not account_rows
                or account_status != "READY",
                "records": account_rows,
                "real_credentials": False,
                "real_external_effects": False,
            },
            "procedure_library": {
                "status": "LIVE_BOUND" if procedure_events else "SOURCE_ONLY",
                "catalog_count": len(catalog.definitions),
                "proof_status_counts": proof_counts,
                "events": procedure_events,
                "trusted_count": proof_counts.get("TRUSTED", 0),
            },
            "incidents": list(portfolio.get("incidents") or []),
            "open_incident_count": int(portfolio.get("open_incident_count") or 0),
            "autonomy": {
                "latest_decision": portfolio.get("latest_decision"),
                "wait": portfolio.get("latest_decision")
                if (portfolio.get("latest_decision") or {}).get("decision") == "WAIT"
                else None,
                "outcomes": list(portfolio.get("outcomes") or [])[:limit],
                "manual_continuation_required": manual_count,
            },
            "effects": dict(effect_truth.get("summary") or {}),
            "fixture_leakage": False,
            "manual_continuation_required": manual_count,
            "source": "canonical_append_only_ledger",
        }
    except Exception as error:  # additive read model must fail closed
        return {
            "status": "NOT_AVAILABLE",
            "reason": type(error).__name__,
            "source": "canonical_append_only_ledger",
            "account_readiness": {
                "status": "NOT_TESTED",
                "owner_activation_required": True,
                "records": [],
                "real_credentials": False,
                "real_external_effects": False,
            },
            "procedure_library": {
                "status": "NOT_TESTED",
                "catalog_count": 0,
                "proof_status_counts": {},
                "events": [],
                "trusted_count": 0,
            },
            "incidents": [],
            "open_incident_count": None,
            "autonomy": {
                "latest_decision": None,
                "wait": None,
                "outcomes": [],
                "manual_continuation_required": None,
            },
            "effects": {},
            "fixture_leakage": False,
            "manual_continuation_required": None,
        }


def _executive_projection(database: FoundryDatabase, *, limit: int = 25) -> dict[str, Any]:
    """Project Executive Intelligence decisions from the durable ledger.

    Read-only view over executive.* events: latest decision (with rationale,
    rejected/deferred alternatives, factor breakdown), its prediction, the
    outcome calibration that followed, and bounded recent history. No UI-only
    truth: every field traces to a ledger event.
    """
    decisions: list[dict[str, Any]] = []
    predictions: dict[str, dict[str, Any]] = {}
    calibrations: list[dict[str, Any]] = []
    activation_epoch: str | None = None
    admission_authority: str = "UNKNOWN"
    for event in database.events(readonly=True, event_type_prefixes=("executive.",)):
        etype = str(event.event_type or "")
        payload = event.payload if isinstance(event.payload, dict) else {}
        if etype == "executive.activation_recorded":
            epoch = payload.get("epoch")
            if isinstance(epoch, str) and epoch:
                activation_epoch = epoch
        elif etype in ("executive.admission_authority_enabled",
                       "executive.admission_authority_disabled"):
            admission_authority = "ENABLED" if etype.endswith("enabled") else "DISABLED"
        elif etype == "executive.prediction_recorded":
            predictions[str(payload.get("decision_id") or "")] = {
                "candidate_id": payload.get("candidate_id"),
                "prediction": _json_value(payload.get("prediction")),
            }
        elif etype == "executive.calibration_recorded":
            calibrations.append({
                "decision_id": payload.get("decision_id"),
                "candidate_id": payload.get("candidate_id"),
                "mission_id": payload.get("mission_id"),
                "outcome": payload.get("outcome"),
                "repair_count": payload.get("repair_count"),
                "actual": _json_value(payload.get("actual")),
                "calibration_version": payload.get("calibration_version"),
                "event_id": event.event_id,
            })
        elif etype == "executive.decision_recorded":
            decisions.append({
                "decision_id": payload.get("decision_id"),
                "decision": payload.get("decision"),
                "reason": payload.get("reason"),
                "decision_version": payload.get("decision_version"),
                "candidate_id": payload.get("candidate_id"),
                "mission_id": payload.get("mission_id"),
                "active_goals": payload.get("active_goals") or [],
                "factor_breakdown": _json_value(payload.get("factor_breakdown")),
                "eligibility": _json_value(payload.get("eligibility")),
                "rejected": payload.get("rejected") or [],
                "deferred": payload.get("deferred") or [],
                "selected_score": _json_value(payload.get("selected_score")),
                "wait": _json_value(payload.get("wait")),
                "calibration_version": payload.get("calibration_version"),
                "event_id": event.event_id,
                "occurred_at": event.occurred_at,
            })
    by_decision: dict[str, dict[str, Any]] = {}
    for record in reversed(decisions):
        key = str(record.get("decision_id") or "")
        if key and key not in by_decision:
            by_decision[key] = record
    # ``decisions`` is oldest->newest; ``by_decision`` was built newest-first
    # (first occurrence of each id wins), so the NEWEST decision is at the
    # front. Bound from the front and expose newest-first ordering.
    ordered = list(by_decision.values())[:limit]
    calibration_by_decision: dict[str, dict[str, Any]] = {}
    for cal in calibrations:
        key = str(cal.get("decision_id") or "")
        if key:
            calibration_by_decision[key] = cal
    latest = ordered[0] if ordered else None
    if latest is not None:
        key = str(latest.get("decision_id") or "")
        latest["prediction"] = predictions.get(key)
        latest["calibration"] = calibration_by_decision.get(key)
    return {
        "activation_epoch": activation_epoch,
        "admission_authority": admission_authority,
        "latest_decision": latest,
        "recent_decisions": ordered,
        "calibrations": calibrations[-limit:],
    }


def _load_json_object(raw: Any) -> tuple[dict[str, Any], bool]:
    """Parse a JSON object column; malformed input degrades honestly."""
    if raw is None or raw == "":
        return {}, False
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}, True
    if isinstance(parsed, dict):
        return parsed, False
    if isinstance(parsed, list):
        return {"__items__": parsed}, False
    return {}, True


def _load_json_list(raw: Any) -> tuple[list[Any], bool]:
    """Parse a JSON array column; malformed input degrades honestly."""
    if raw is None or raw == "":
        return [], False
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return [], True
    if isinstance(parsed, list):
        return parsed, False
    return [], True


# Event types the discovery projection reads. Bounded per-type scans use the
# events_type_sequence index; the full ledger is never iterated.
_DISCOVERY_EVENT_TYPES = (
    "worker_loop.cycle_started",
    "portfolio.evaluated",
    "executive.decision_recorded",
    "executive.decision_materialized",
    "executive.materialization_blocked",
    "executive.calibration_recorded",
    "mission.completed",
    "discovery.cycle_completed",
    "discovery.candidate_scored",
    "discovery.candidate_rejected",
    "discovery.candidate_outcome_recorded",
)
_DISCOVERY_SCAN_WINDOW = 400
DISCOVERY_PROJECTION_SCHEMA = 1
DISCOVERY_MAX_CYCLES = 10
DISCOVERY_MAX_CANDIDATES = 25
DISCOVERY_MAX_OUTCOMES = 50


def _recent_events(
    connection: sqlite3.Connection, event_types: tuple[str, ...],
    *, limit: int = _DISCOVERY_SCAN_WINDOW,
) -> list[dict[str, Any]]:
    """Newest-first bounded read of specific event types (parameterized)."""
    placeholders = ",".join("?" for _ in event_types)
    return [
        {
            "sequence": row[0], "event_type": row[1],
            "occurred_at": row[2], "payload": row[3],
        }
        for row in connection.execute(
            "SELECT sequence, event_type, occurred_at, payload_json "
            f"FROM events WHERE event_type IN ({placeholders}) "
            "ORDER BY sequence DESC LIMIT ?",
            (*event_types, int(limit)),
        ).fetchall()
    ]


def _empty_discovery(state: str, *, cycle_enabled: str = "UNREGISTERED") -> dict[str, Any]:
    return {
        "schema": DISCOVERY_PROJECTION_SCHEMA,
        "state": state,
        "flag_state": cycle_enabled,
        "policy_version": None,
        "latest_cycle": None,
        "cycles": [],
        "candidates_recent": [],
        "selected": None,
        "portfolio_summary": {"eligible": None, "deferred": None, "rejected": None},
        "active_team": [],
        "verifier": None,
        "outcomes": [],
        "maintenance_due": None,
        "next_action": None,
    }


def _discovery_projection(
    connection: sqlite3.Connection, *,
    cycle_enabled: bool | None = None,
    hold_active: bool = False,
    limit_cycles: int = DISCOVERY_MAX_CYCLES,
    limit_candidates: int = DISCOVERY_MAX_CANDIDATES,
    limit_outcomes: int = DISCOVERY_MAX_OUTCOMES,
) -> dict[str, Any]:
    """Additive ledger-backed ``snapshot.discovery`` section (spec §3.13).

    Read-only over canonical V3 rows/events; deterministic newest-first
    ordering; malformed/legacy data degrades to explicit partial/unavailable
    fields instead of manufacturing success. ``cost_window`` is deliberately
    absent: no canonical shape for it exists at this schema.
    """
    flag_state = "UNREGISTERED" if cycle_enabled is None else ("ON" if cycle_enabled else "OFF")
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not {"candidates", "candidate_outcomes", "scoring_policy_registry"}.issubset(tables):
        # Legacy schema without the additive V3 projections stays honest.
        return _empty_discovery("UNSUPPORTED_SCHEMA", cycle_enabled=flag_state)

    candidate_total = int(connection.execute(
        "SELECT COUNT(*) FROM candidates").fetchone()[0])
    relevant_events = _recent_events(connection, _DISCOVERY_EVENT_TYPES)
    if candidate_total == 0 and not relevant_events:
        return _empty_discovery("EMPTY_LEDGER", cycle_enabled=flag_state)

    partial = False
    policy_version = None
    policy_row = connection.execute(
        "SELECT policy_version FROM scoring_policy_registry "
        "WHERE active = 1 ORDER BY updated_at DESC LIMIT 1").fetchone()
    if policy_row is None:
        policy_row = connection.execute(
            "SELECT policy_version FROM scoring_policy_registry "
            "ORDER BY updated_at DESC LIMIT 1").fetchone()
    if policy_row is not None:
        policy_version = str(policy_row[0])

    # ---- bounded newest-first event joins ---------------------------------
    started_by_loop: dict[str, str] = {}
    portfolio_by_loop: dict[str, dict[str, Any]] = {}
    decision_by_id: dict[str, dict[str, Any]] = {}
    materialized_by_decision: dict[str, str] = {}
    blocked_by_decision: dict[str, str] = {}
    calibration_by_candidate: dict[tuple[str, str], float] = {}
    completed_by_mission: dict[str, str] = {}
    score_event_by_candidate: dict[str, dict[str, Any]] = {}
    rejection_by_candidate: dict[str, dict[str, str]] = {}
    for item in relevant_events:
        payload, bad = _load_json_object(item["payload"])
        partial = partial or bad
        etype = item["event_type"]
        if etype == "worker_loop.cycle_started":
            loop_id = str(payload.get("loop_id") or "")
            if loop_id and loop_id not in started_by_loop:
                started_by_loop[loop_id] = str(item["occurred_at"])
        elif etype == "portfolio.evaluated":
            loop_id = str(payload.get("loop_id") or "")
            portfolio_by_loop.setdefault(loop_id, payload)
        elif etype == "executive.decision_recorded":
            decision_id = str(payload.get("decision_id") or "")
            decision_by_id.setdefault(decision_id, payload)
        elif etype == "executive.decision_materialized":
            decision_id = str(payload.get("decision_id") or "")
            mission_id = payload.get("mission_id")
            if decision_id and mission_id:
                materialized_by_decision.setdefault(decision_id, str(mission_id))
        elif etype == "executive.materialization_blocked":
            decision_id = str(payload.get("decision_id") or "")
            if decision_id:
                blocked_by_decision.setdefault(
                    decision_id, str(payload.get("block_reason") or ""))
        elif etype == "executive.calibration_recorded":
            key = (str(payload.get("candidate_id") or ""),
                   str(payload.get("mission_id") or ""))
            delta = payload.get("delta")
            if all(key) and isinstance(delta, (int, float)):
                calibration_by_candidate.setdefault(key, float(delta))
        elif etype == "mission.completed":
            mission_id = str(payload.get("mission_id") or "")
            outcome = payload.get("outcome")
            if mission_id and outcome:
                completed_by_mission.setdefault(mission_id, str(outcome))
        elif etype == "discovery.candidate_scored":
            candidate_id = str(payload.get("candidate_id") or "")
            if candidate_id:
                score_event_by_candidate.setdefault(candidate_id, payload)
        elif etype == "discovery.candidate_rejected":
            candidate_id = str(payload.get("candidate_id") or "")
            if candidate_id:
                rejection_by_candidate.setdefault(candidate_id, {
                    "code": str(payload.get("reason_code")
                                or payload.get("filter_or_score") or ""),
                    "detail": str(payload.get("reason") or ""),
                })

    # ---- cycles ------------------------------------------------------------
    cycle_rows = connection.execute(
        "SELECT payload_json, recorded_at FROM events "
        "WHERE event_type = 'discovery.cycle_completed' "
        "ORDER BY sequence DESC LIMIT ?",
        (int(limit_cycles),),
    ).fetchall()
    cycles: list[dict[str, Any]] = []
    for row in cycle_rows:
        payload, bad = _load_json_object(row[0])
        partial = partial or bad
        loop_id = str(payload.get("loop_id") or "")
        portfolio = portfolio_by_loop.get(loop_id)
        decision_payload = decision_by_id.get(str(payload.get("decision_id") or ""))
        wait_payload, _wait_bad = _load_json_object(
            (decision_payload or {}).get("wait"))
        eligible = rejected = scored = deferred_count = None
        if portfolio is not None:
            eligible = int(portfolio.get("eligible_count", 0) or 0)
            rejected = int(portfolio.get("rejected_count", 0) or 0)
            deferred_count = int(portfolio.get("deferred_count", 0) or 0)
            scored = eligible + rejected + deferred_count
        wait_kind = None
        if decision_payload is not None and decision_payload.get("decision") == "WAIT":
            wait_kind = wait_payload.get("wait_kind") or None
        cycles.append({
            "loop_id": loop_id,
            "started_at": started_by_loop.get(loop_id),
            "completed_at": str(row[1]),
            "outcome": payload.get("outcome"),
            "decision_id": payload.get("decision_id"),
            "mission_id": payload.get("mission_id"),
            "candidate_id": payload.get("candidate_id"),
            "observed": int(payload.get("observed", 0) or 0),
            "candidates_created": int(payload.get("candidates_created", 0) or 0),
            "scored": scored,
            "eligible": eligible,
            "rejected": rejected,
            "degraded_sources": list(payload.get("degraded_sources") or []),
            "reason": payload.get("reason"),
            "wait_kind": wait_kind,
        })

    # ---- candidates --------------------------------------------------------
    candidate_rows = connection.execute(
        "SELECT candidate_id, fingerprint, candidate_type, objective,"
        " expected_outcome, status, created_from, superseded_by, expires_at,"
        " evidence_json, scoring_policy_version, updated_at"
        " FROM candidates ORDER BY updated_at DESC, candidate_id DESC LIMIT ?",
        (int(limit_candidates),),
    ).fetchall()
    ids = [str(row[0]) for row in candidate_rows]
    score_rows: dict[str, tuple[float, str | None]] = {}
    if ids:
        placeholders = ",".join("?" for _ in ids)
        score_rows = {
            str(r[0]): (float(r[1]), r[2])
            for r in connection.execute(
                "SELECT candidate_id, score, scoring_policy_version FROM"
                " candidate_scores WHERE candidate_id IN"
                f" ({placeholders})",
                ids,
            )
        } if "scoring_policy_version" in {
            c[1] for c in connection.execute("PRAGMA table_info(candidate_scores)")
        } else {
            str(r[0]): (float(r[1]), None)
            for r in connection.execute(
                "SELECT candidate_id, score FROM candidate_scores"
                f" WHERE candidate_id IN ({placeholders})",
                ids,
            )
        }
    lineage_by_candidate: dict[str, str] = {}
    mission_states: dict[str, str] = {}
    for m_row in connection.execute(
            "SELECT mission_id, created_from, state FROM missions").fetchall():
        mission_states[str(m_row[0])] = str(m_row[2])
        created_from = str(m_row[1] or "")
        if created_from.startswith("candidate:"):
            lineage_by_candidate[created_from.split(":", 1)[1]] = str(m_row[0])

    candidates_recent: list[dict[str, Any]] = []
    for row in candidate_rows:
        (candidate_id, fingerprint, candidate_type, objective, expected_outcome,
         status, created_from, superseded_by, expires_at, evidence_raw,
         row_policy, updated_at) = row
        evidence_items, bad = _load_json_list(evidence_raw)
        partial = partial or bad
        cooldown_until = None
        for entry in evidence_items:
            if isinstance(entry, dict) and entry.get("cooldown_until"):
                cooldown_until = str(entry["cooldown_until"])
                break
        score_entry: dict[str, Any] | None = None
        total = score_rows.get(str(candidate_id))
        scored_event = score_event_by_candidate.get(str(candidate_id))
        trace, trace_bad = _load_json_object((scored_event or {}).get("trace"))
        dimensions = trace.get("dimensions")
        if dimensions is None and scored_event is not None:
            dimensions = scored_event.get("dimensions")
        if total is not None or scored_event is not None:
            total_value: float | None = None
            if total is not None:
                total_value = float(total[0])
            elif isinstance((scored_event or {}).get("score"), (int, float)):
                total_value = float(scored_event["score"])  # type: ignore[index]
            score_entry = {
                "total": total_value,
                "policy_version": (total[1] if total else None)
                or (scored_event or {}).get("policy_version")
                or row_policy,
                "dimensions": dimensions,
                "calibration_adjust": float(trace.get("calibration_adjust", 0.0) or 0.0),
                "thresholds": trace.get("thresholds"),
            }
            partial = partial or trace_bad
        rejection = rejection_by_candidate.get(str(candidate_id))
        if rejection is None and status == "REJECTED":
            for entry in evidence_items:
                if isinstance(entry, dict) and entry.get("rejection_reason"):
                    rejection = {"code": "", "detail": str(entry["rejection_reason"])}
                    break
        if status == "REJECTED" and rejection is None:
            rejection = {"code": "", "detail": ""}
        candidates_recent.append({
            "candidate_id": str(candidate_id),
            "fingerprint": fingerprint,
            "type": candidate_type,
            "objective": objective,
            "expected_outcome": expected_outcome,
            "status": status,
            "created_from": created_from,
            "updated_at": str(updated_at),
            "expires_at": expires_at,
            "evidence_ref_count": sum(1 for e in evidence_items if isinstance(e, dict)),
            "score": score_entry,
            "rejection": rejection,
            "superseded_by": superseded_by,
            "deferred_until": cooldown_until,
            "mission_id": lineage_by_candidate.get(str(candidate_id)),
            "partial": bad or trace_bad,
        })

    # ---- selected decision --------------------------------------------------
    selected: dict[str, Any] | None = None
    for decision_id, payload in decision_by_id.items():
        if payload.get("decision") != "SELECT":
            continue
        selected_score, ss_bad = _load_json_object(payload.get("selected_score"))
        rejected_alt = [
            {
                "candidate_id": str(item.get("candidate_id") or ""),
                "total_score": (
                    float(item["composite"])
                    if isinstance(item.get("composite"), (int, float)) else None),
                "reason": str(item.get("reason") or ""),
            }
            for item in (payload.get("rejected") or [])
            if isinstance(item, dict)
        ]
        deferred_alt = [
            {"candidate_id": str(item.get("candidate_id") or ""),
             "reason": str(item.get("reason") or "")}
            for item in (payload.get("deferred") or [])
            if isinstance(item, dict)
        ]
        selected = {
            "decision_id": decision_id,
            "candidate_id": payload.get("candidate_id"),
            "total_score": selected_score.get("composite"),
            "comparative_rationale": None,
            "reason": str(payload.get("reason") or ""),
            "rejected_alternatives": rejected_alt,
            "deferred_alternatives": deferred_alt,
            "mission_id": materialized_by_decision.get(decision_id),
            "block_reason": blocked_by_decision.get(decision_id),
        }
        partial = partial or ss_bad
        break

    selected_mission = (selected or {}).get("mission_id")

    # ---- team + verifier for the selected mission ---------------------------
    active_team: list[dict[str, Any]] = []
    verifier: dict[str, Any] | None = None
    if selected_mission:
        team_rows = connection.execute(
            "SELECT run_id, role, provider, model, status, mission_id"
            " FROM agent_runs WHERE mission_id = ?"
            " ORDER BY started_at DESC LIMIT 20",
            (selected_mission,),
        ).fetchall()
        active_team = [
            {"run_id": str(r[0]), "role": str(r[1]), "provider": r[2],
             "model": r[3], "status": str(r[4]), "mission_id": str(r[5])}
            for r in team_rows
        ]
        verdict_rows = connection.execute(
            "SELECT verdict_id, verdict, status, verifier_run_id, builder_run_id,"
            " independence_json FROM verification_verdicts"
            " WHERE mission_id = ? ORDER BY rowid DESC LIMIT 5",
            (selected_mission,),
        ).fetchall()
        accepted_for_selected = {
            str(row[0]) for row in connection.execute(
                "SELECT json_extract(payload_json, '$.verdict_id') FROM events "
                "WHERE event_type = 'verification.accepted' "
                "AND json_extract(payload_json, '$.mission_id') = ?",
                (selected_mission,),
            ).fetchall()
            if row[0]
        }
        repair_rounds = int(connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts"
            " WHERE mission_id = ? AND verdict = 'FAIL'",
            (selected_mission,),
        ).fetchone()[0])
        for v_row in verdict_rows:
            verdict = v_row[1] or (None if v_row[2] == "STARTED" else v_row[2])
            if verdict is None:
                continue
            independence, ind_bad = _load_json_object(v_row[5])
            partial = partial or ind_bad
            verifier = {
                "mission_id": selected_mission,
                "verdict": str(verdict),
                "acceptance_status": (
                    "ACCEPTED" if str(v_row[0]) in accepted_for_selected
                    else "PENDING"
                ),
                "fresh_session": independence.get("fresh_session"),
                "independence": independence.get("independence"),
                "provider": independence.get("provider"),
                "builder_run_id": v_row[4],
                "verifier_run_id": v_row[3],
                "repair_rounds_used": repair_rounds,
                "repair_budget": DEFAULT_REPAIR_BUDGET,
            }
            break

    # ---- outcomes ------------------------------------------------------------
    outcome_rows = connection.execute(
        "SELECT candidate_id, mission_id, window_kind, predicted_value_json,"
        " actual_value_json, status, confidence, updated_at"
        " FROM candidate_outcomes ORDER BY updated_at DESC LIMIT ?",
        (int(limit_outcomes),),
    ).fetchall()
    outcomes: list[dict[str, Any]] = []
    for row in outcome_rows:
        predicted, p_bad = _load_json_object(row[3])
        actual_raw, a_bad = _load_json_object(row[4])
        partial = partial or p_bad or a_bad
        status = str(row[5])
        actual: dict[str, Any] | None
        if status == "UNKNOWN":
            actual = None  # honest UNKNOWN, never a manufactured value
        else:
            actual = {k: v for k, v in actual_raw.items() if k != "__items__"} \
                if actual_raw else {}
        delta = calibration_by_candidate.get((str(row[0]), str(row[1])))
        outcomes.append({
            "candidate_id": str(row[0]),
            "mission_id": str(row[1]),
            "window": str(row[2]),
            "predicted": {k: v for k, v in predicted.items() if k != "__items__"},
            "actual": actual,
            "status": status,
            "confidence": float(row[6] or 0.0),
            "delta": delta,
        })

    # ---- honest continuation / next action -----------------------------------
    next_action = None
    latest_cycle = cycles[0] if cycles else None
    if hold_active:
        next_action = "HOLD: owner veto/STOP active"
    elif latest_cycle is not None:
        mission_id = latest_cycle.get("mission_id")
        if latest_cycle.get("outcome") == "COMPILED" and mission_id:
            state = mission_states.get(str(mission_id))
            if state in ("COMPLETE", "FAILED", "BLOCKED"):
                if completed_by_mission.get(str(mission_id)) == "PASSED" and \
                        any(o["mission_id"] == mission_id for o in outcomes):
                    next_action = "RUN_NEXT_CYCLE"
                elif completed_by_mission.get(str(mission_id)) == "PASSED":
                    next_action = f"RECONCILE_OUTCOME:{mission_id}"
                else:
                    next_action = f"WAIT: mission {mission_id} terminal ({state})"
            else:
                next_action = f"EXECUTE_MISSION:{mission_id}"
        else:
            next_action = "WAIT"

    return {
        "schema": DISCOVERY_PROJECTION_SCHEMA,
        "state": "PARTIAL" if partial else "FRESH",
        "flag_state": flag_state,
        "policy_version": policy_version,
        "latest_cycle": latest_cycle,
        "cycles": cycles,
        "candidates_recent": candidates_recent,
        "selected": selected,
        "portfolio_summary": {
            "eligible": (cycles[0]["eligible"] if cycles else None),
            "deferred": (cycles[0]["scored"] - cycles[0]["eligible"]
                         - cycles[0]["rejected"]
                         if cycles and cycles[0]["scored"] is not None else None),
            "rejected": (cycles[0]["rejected"] if cycles else None),
        },
        "active_team": active_team,
        "verifier": verifier,
        "outcomes": outcomes,
        "maintenance_due": None,
        "next_action": next_action,
    }


def build_snapshot(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    now: str | None = None,
    timeline_limit: int = 200,
    discovery_cycle_enabled: bool | None = None,
) -> dict[str, Any]:
    """Build one complete snapshot inside a shared read-only context."""
    with database.read_snapshot():
        return _build_snapshot(
            database,
            stop_path=stop_path,
            now=now,
            timeline_limit=timeline_limit,
            discovery_cycle_enabled=discovery_cycle_enabled,
        )


def _build_snapshot(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    now: str | None = None,
    timeline_limit: int = 200,
    discovery_cycle_enabled: bool | None = None,
) -> dict[str, Any]:
    """Build the complete JSON-safe Command Center projection.

    ``discovery_cycle_enabled`` carries the registered runtime flag
    (``WorkerRuntimeConfig.discovery_cycle_enabled``). ``None`` (the default)
    means no registered value was available and the projection reports the
    honest ``UNREGISTERED`` state; callers with a live config pass its value.
    """
    generated_at = now or _now_iso()
    projection_errors: list[dict[str, str]] = []
    with command_center_readonly_connection(database) as connection:
        head = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0]
        event_count = int(head or 0)
        schema_row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        live_schema = int(schema_row[0] if schema_row else 0)
        supervisor_row = connection.execute(
            "SELECT * FROM supervisor_state WHERE singleton = 1"
        ).fetchone()
        supervisor = dict(supervisor_row) if supervisor_row else {"status": "UNKNOWN", "trust_state": "UNKNOWN"}
        system_state_row = connection.execute(
            "SELECT * FROM system_state WHERE singleton = 1"
        ).fetchone()
        system_state = dict(system_state_row) if system_state_row else {"start_active": None}
        missions = _rows(connection, "SELECT * FROM missions ORDER BY updated_at DESC LIMIT 100")
        workers = _rows(connection, "SELECT * FROM agent_runs ORDER BY updated_at DESC LIMIT 100")
        # The UI worker list is intentionally bounded, but health must not
        # lose an active run merely because older terminal history fills that
        # window.  Select all RUNNING rows plus FAILED rows that still have a
        # durable role/worker lease; historical failures stay in ``workers``
        # without becoming health inputs.
        runtime_workers = _rows(
            connection,
            """
            SELECT ar.* FROM agent_runs ar
            WHERE ar.status = 'RUNNING'
               OR EXISTS (
                   SELECT 1 FROM leases l
                   WHERE l.lease_key = 'role:' || ar.role || ':' || ar.mission_id || ':' || COALESCE(ar.step_id, 'start')
               )
               OR EXISTS (
                   SELECT 1 FROM leases l
                   WHERE l.lease_key = 'worker:' || ar.run_id
               )
            ORDER BY ar.updated_at DESC
            """,
        )
        evidence = _rows(connection, "SELECT * FROM evidence_bundles ORDER BY updated_at DESC LIMIT 100")
        verdicts = _rows(connection, "SELECT * FROM verification_verdicts ORDER BY updated_at DESC LIMIT 100")
        accepted_ids = set(accepted_verifications(database))
        for verdict in verdicts:
            verdict["acceptance_status"] = (
                "ACCEPTED" if str(verdict.get("verdict_id") or "") in accepted_ids
                else "PENDING"
            )
        resources = _rows(connection, "SELECT * FROM resources ORDER BY updated_at DESC LIMIT 100")
        directives = _rows(connection, "SELECT * FROM directives WHERE active = 1 ORDER BY updated_at DESC")
        leases = _rows(connection, "SELECT * FROM leases ORDER BY heartbeat_at DESC")
        budgets = _rows(connection, "SELECT * FROM budgets ORDER BY provider")
        systems = _safe_table(connection, "systems", "SELECT * FROM systems ORDER BY updated_at DESC", errors=projection_errors)
        costs = _safe_table(connection, "cost_measurements", "SELECT * FROM cost_measurements ORDER BY recorded_at DESC LIMIT 200", errors=projection_errors)
        measured_run_ids = {str(c.get("run_id")) for c in costs if c.get("run_id")}
        # Existing agent_run cost payloads are authoritative measurements when
        # present.  They are read into the V4 view without rewriting the
        # underlying run projection or inventing an amount when absent.
        for run in workers:
            if str(run.get("run_id")) in measured_run_ids:
                continue
            raw = run.get("cost_json")
            parsed = _json_value(raw) if raw else {}
            if isinstance(parsed, dict) and parsed:
                costs.append({
                    "measurement_id": f"agent-run:{run.get('run_id')}",
                    "mission_id": run.get("mission_id"), "run_id": run.get("run_id"),
                    "provider": run.get("provider"), "model": run.get("model"),
                    "status": "MEASURED" if any(key in parsed for key in ("actual_cost_usd", "estimated_cost_usd", "usd", "cost_usd")) else "UNKNOWN",
                    "input_tokens": parsed.get("input_tokens"), "output_tokens": parsed.get("output_tokens"), "total_tokens": parsed.get("total_tokens"),
                    "estimated_cost_usd": parsed.get("estimated_cost_usd"),
                    "actual_cost_usd": parsed.get("actual_cost_usd", parsed.get("usd", parsed.get("cost_usd"))),
                    "duration_ms": parsed.get("duration_ms"), "resource_json": parsed,
                    "failure_class": run.get("failure_class"), "evidence_json": {"run_id": run.get("run_id")},
                    "recorded_at": run.get("finished_at") or run.get("updated_at"),
                    "last_event_id": run.get("last_event_id"), "updated_at": run.get("updated_at"),
                })
        briefings = _safe_table(connection, "briefing_outbox", "SELECT * FROM briefing_outbox ORDER BY updated_at DESC LIMIT 100", errors=projection_errors)
        voices = _safe_table(connection, "voice_interactions", "SELECT * FROM voice_interactions ORDER BY updated_at DESC LIMIT 100", errors=projection_errors)
        candidates = _safe_table(connection, "candidates", "SELECT * FROM candidates ORDER BY updated_at DESC LIMIT 200", errors=projection_errors)
        memories = _safe_table(
            connection, "tier_c_memories",
            "SELECT memory_id, state, mem_type, scope, mission_id, agent_run_id, "
            "created_by, content, confidence, source_ids_json, evidence_ids_json, "
            "last_verified, updated_at, last_event_id "
            "FROM tier_c_memories ORDER BY updated_at DESC LIMIT 100",
            errors=projection_errors)
        procedures = _safe_table(
            connection, "tier_c_procedures",
            "SELECT procedure_id, state, scope, updated_at, last_event_id "
            "FROM tier_c_procedures ORDER BY updated_at DESC LIMIT 100",
            errors=projection_errors)
        steps = _safe_table(
            connection, "mission_step_contracts",
            "SELECT contract_id, mission_id, step_id, version, capability, "
            "last_event_id, updated_at FROM mission_step_contracts "
            "ORDER BY updated_at DESC LIMIT 100", errors=projection_errors)
        vault_files = _safe_table(
            connection, "vault_files",
            "SELECT rel_path, content_sha256, status, updated_at, last_event_id "
            "FROM vault_files WHERE status = 'ACTIVE' ORDER BY rel_path LIMIT 500",
            errors=projection_errors)
        effect_truth = _effect_truth_projection(connection)
        evolution_truth = _evolution_truth_projection(connection)
        # Keep discovery on the same bounded read handle.  An active owner
        # veto is already available here; STOP and pause only alter the honest
        # next-action text and are applied after the control projection below.
        discovery_projection = _discovery_projection(
            connection,
            cycle_enabled=None if discovery_cycle_enabled is None
            else bool(discovery_cycle_enabled),
            hold_active=any(
                d.get("command_type") in {"VETO_ACTION", "STOP_MISSION", "STOP_EVERYTHING"}
                for d in directives
            ),
        )

    stop = EmergencyStop(stop_path).read() if stop_path is not None else None
    owner_directives = owner_directive_snapshot(database, now=generated_at)
    try:
        from .deployment.release import read_current, read_previous
        release_identity = {
            "current": read_current(),
            "previous": read_previous(),
            "source": "immutable_release_pointers",
        }
    except Exception as error:  # noqa: BLE001 - CC read model must not fail closed on layout
        release_identity = {
            "current": None, "previous": None,
            "status": "NOT_AVAILABLE", "reason": type(error).__name__,
        }
    active_vetoes = [d for d in directives if d.get("command_type") in {"VETO_ACTION", "STOP_MISSION", "STOP_EVERYTHING"}]
    goals = [d for d in directives if d.get("command_type") == "GOAL"]
    constraints = [d for d in directives if d.get("command_type") in {"CONSTRAINT", "ADD_CONSTRAINT"}]
    reprioritizations = [d for d in directives if d.get("command_type") == "REPRIORITIZE"]
    redirects = [d for d in directives if d.get("command_type") == "REDIRECT"]
    stale_leases = [l for l in leases if l.get("expires_at") and str(l["expires_at"]) < generated_at]
    blocked = [m for m in missions if m.get("state") == "BLOCKED"]
    crashed_workers = _current_failed_workers(runtime_workers, leases, stale_leases)
    runtime_degraded = supervisor.get("status") == "DEGRADED" or bool(
        stale_leases or crashed_workers
    )
    control_state = owner_control_state(
        owner_directives,
        legacy_active=directives,
        stop_record=stop,
        supervisor_status=supervisor.get("status"),
        degraded=runtime_degraded,
        active_work_count=sum(
            1 for worker in runtime_workers if worker.get("status") == "RUNNING"
        ),
        blocked_work_count=len(blocked),
    )
    paused = bool(control_state["paused"])
    hold_active = bool(active_vetoes) or bool(stop) or paused
    if hold_active:
        discovery_projection = dict(discovery_projection)
        discovery_projection["next_action"] = "HOLD: owner veto/STOP active"
    decision_queue = build_owner_attention_queue(
        database,
        now=generated_at,
        limit=100,
    )
    result_traceability = build_result_traceability(
        database,
        now=generated_at,
        limit=100,
    )
    recovery_events = [e for e in decision_timeline(database, limit=timeline_limit)
                       if any(token in e["event_type"] for token in ("recover", "reap", "rollback", "degraded", "blocked"))]
    release_events = [e for e in decision_timeline(database, limit=timeline_limit)
                      if any(token in e["event_type"] for token in ("release", "canary", "rollback"))]
    measured_costs = [c for c in costs if c.get("status") == "MEASURED"]
    actual_values = [float(c["actual_cost_usd"]) for c in measured_costs if c.get("actual_cost_usd") is not None]
    estimated_values = [float(c["estimated_cost_usd"]) for c in measured_costs if c.get("estimated_cost_usd") is not None]
    actual_total: float | str = sum(actual_values) if actual_values else "UNKNOWN"
    estimated_total: float | str = sum(estimated_values) if estimated_values else "UNKNOWN"
    unknown_costs = [c for c in costs if c.get("status") != "MEASURED"]

    def with_ref(row: dict[str, Any], kind: str, key: str) -> dict[str, Any]:
        value = dict(row)
        value["record_ref"] = _record_ref(kind, row.get(key), row.get("last_event_id"))
        for field in ("acceptance_json", "tool_policy_json", "cost_json", "criteria_json", "evidence_json", "resource_json", "artifact_hashes_json", "source_ids_json", "evidence_ids_json", "builder_claim_json", "uncertainty_json", "repair_requirements_json", "missing_proof_json", "result_json"):
            if field in value:
                value[field[:-5] if field.endswith("_json") else field] = _json_value(value[field])
        return value

    portfolio_projection = _portfolio_projection(database, limit=10)
    executive_projection = _executive_projection(database, limit=25)
    # V8 Executive Autonomy is an additive subprojection of the established
    # executive decision surface.  Both views are derived from the same
    # ledger; the autonomy service is the sole owner of objectives/plans and
    # the legacy projection remains the sole owner of V3 decision history.
    try:
        from .executive.autonomy import executive_autonomy_snapshot
        autonomy_projection = executive_autonomy_snapshot(database)
    except Exception as error:  # noqa: BLE001 - read model fails closed
        autonomy_projection = {
            "status": "UNKNOWN",
            "reason": f"{type(error).__name__}: {error}",
            "objectives": {},
            "plans": {},
            "priorities": [],
            "continuations": {},
            "escalations": {},
            "coordination": {},
            "progress": {},
            "mission_requests": {},
            "operational_followup_proposals": {},
            "latest_cycle": {},
            "briefing": None,
        }
    executive_projection = dict(executive_projection)
    executive_projection["autonomy"] = autonomy_projection
    # V9 Continuous Executive: additive wake/condition subprojection
    try:
        from .executive.continuous import continuous_snapshot
        continuous_full = continuous_snapshot(database)
        continuous_projection = continuous_full.get("continuous", continuous_full)
    except Exception as error:  # noqa: BLE001 - read model fails closed
        continuous_projection = {"status": "UNKNOWN", "reason": f"{type(error).__name__}: {error}", "wake_state": {}, "exec_states": {}, "due_wakes": []}
    executive_projection["continuous"] = continuous_projection
    # V13 Chief of Staff is a read-only semantic subprojection. It can expose
    # project truth here, but candidate admission, wakes, campaigns, missions,
    # effects, and verification remain with their established authorities.
    try:
        from .chief_of_staff import chief_of_staff_read_projection
        chief_of_staff_projection = chief_of_staff_read_projection(
            database,
            now=generated_at,
        )
    except Exception as error:  # noqa: BLE001 - read model fails closed
        chief_of_staff_projection = {
            "contract": "jarvis.chief-of-staff.v1",
            "schema": 40,
            "generated_at": generated_at,
            "status": "UNKNOWN",
            "reason": f"{type(error).__name__}: {error}",
            "projects": [],
            "priorities": [],
            "owner_attention": [],
            "briefing": [],
            "source_event_refs": [],
        }
    executive_projection["chief_of_staff"] = chief_of_staff_projection
    postv7_projection = _postv7_projection(
        database,
        portfolio=portfolio_projection,
        effect_truth=effect_truth,
        limit=100,
    )
    # Preserve the established top-level snapshot contract.  Post-V7 fields
    # are an additive, ledger-backed subprojection of the existing portfolio
    # surface so older consumers retain their exact key set.
    portfolio_projection = dict(portfolio_projection)
    portfolio_projection["postv7"] = postv7_projection

    operational_intelligence = project_operational_intelligence(
        database,
        now=generated_at,
        stop_path=stop_path,
    )
    discovery_surface = dict(discovery_projection)
    # Keep the established top-level contract stable.  Operational
    # intelligence is an additive sub-surface alongside the existing V3
    # discovery projection, not a second Command Center authority.
    discovery_surface["operational_intelligence"] = operational_intelligence
    # V9 World Intelligence is an additive understanding layer.  It exposes
    # current state, history, signals, predictions, and gaps without
    # becoming a second scheduler or mission engine.
    try:
        from jarvis_foundry.world_model.read_model import project_world_intelligence
        world_intelligence = project_world_intelligence(database, limit=50)
    except Exception as _wi_err:  # noqa: BLE001 - read model fails closed
        world_intelligence = {
            "counts": {},
            "signals": [],
            "entities": [],
            "predictions": [],
            "gaps": [],
            "patterns": [],
            "error": f"{type(_wi_err).__name__}: {_wi_err}",
        }

    try:
        from .fabric.catalog import catalog_snapshot
        fabric_catalog = catalog_snapshot(database)
        capability_fabric = {
            "status": "AVAILABLE" if fabric_catalog["count"] else "EMPTY",
            "authority": "CAPABILITY_FABRIC_CATALOG",
            "source": "fabric.catalog.catalog_snapshot",
            **fabric_catalog,
        }
    except Exception as error:  # noqa: BLE001 - read model fails closed
        capability_fabric = {
            "status": "UNAVAILABLE",
            "authority": "CAPABILITY_FABRIC_CATALOG",
            "source": "fabric.catalog.catalog_snapshot",
            "reason": f"{type(error).__name__}: {error}",
            "capabilities": [],
            "count": 0,
        }

    snapshot_payload = {
        "generated_at": generated_at,
        "source_of_truth": {"kind": "append_only_event_ledger", "ledger_head_sequence": event_count, "event_count": event_count},
        "supervisor": supervisor,
        "system_state": system_state,
        "stop": {"active": bool(stop), "record": stop, "source": "sentinel_or_durable_control"},
        "controls": {
            "goals": [with_ref(d, "directive", "directive_id") for d in goals],
            "constraints": [with_ref(d, "directive", "directive_id") for d in constraints],
            "vetoes": [with_ref(d, "directive", "directive_id") for d in active_vetoes],
            "reprioritizations": [with_ref(d, "directive", "directive_id") for d in reprioritizations],
            "redirects": [with_ref(d, "directive", "directive_id") for d in redirects],
            "paused": paused,
            "resume_available": control_state["resume_available"],
            "runtime_state": control_state["status"],
            "state_reason": control_state["reason"],
            "state_source": control_state["source"],
            "state_evidence": control_state,
            "decision_queue_open_count": decision_queue["open_count"],
            "stop_effective_without_hermes_ui_models_network": True,
            "history": owner_directives["records"],
            "unknown": owner_directives["unknown"],
            "history_count": owner_directives["history_count"],
            "active_count": owner_directives["active_count"],
        },
        "owner_directives": owner_directives,
        "decision_queue": decision_queue,
        "result_traceability": result_traceability,
        "missions": [with_ref(m, "mission", "mission_id") for m in missions],
        "workers": [with_ref(w, "agent_run", "run_id") for w in workers],
        "evidence": [with_ref(e, "evidence", "bundle_id") for e in evidence],
        "verification": [with_ref(v, "verification", "verdict_id") for v in verdicts],
        "health": {
            "supervisor_status": supervisor.get("status", "UNKNOWN"),
            "trust_state": supervisor.get("trust_state", "UNKNOWN"),
            "heartbeat_at": supervisor.get("last_heartbeat_at"),
            "schema": {"live": live_schema, "package": SCHEMA_VERSION},
            "active_leases": len(leases) - len(stale_leases),
            "stale_leases": [with_ref(l, "lease", "lease_key") for l in stale_leases],
            "crashed_workers": [with_ref(w, "agent_run", "run_id") for w in crashed_workers],
            "degraded": supervisor.get("status") == "DEGRADED" or bool(
                stale_leases or crashed_workers or projection_errors),
            "projection_errors": projection_errors,
        },
        "resources": [with_ref(r, "resource", "resource_id") for r in resources],
        "systems": [with_ref(s, "system", "system_id") for s in systems],
        "candidates": [with_ref(c, "candidate", "candidate_id") for c in candidates],
        "mission_steps": [with_ref(s, "mission_step", "contract_id") for s in steps],
        "memory": [with_ref(m, "memory", "memory_id") for m in memories],
        "vault": {
            "files": [with_ref(f, "vault_file", "rel_path") for f in vault_files],
            "count": len(vault_files),
        },
        "procedures": [with_ref(p, "procedure", "procedure_id") for p in procedures],
        "release_identity": release_identity,
        "releases_canaries_rollbacks": release_events or {"status": "NOT_AVAILABLE", "evidence_refs": []},
        "recovery": {
            "blocked_missions": [with_ref(m, "mission", "mission_id") for m in blocked],
            "stale_leases": [with_ref(l, "lease", "lease_key") for l in stale_leases],
            "recovery_events": recovery_events,
            "rollback_state": release_events or {"status": "NOT_AVAILABLE", "evidence_refs": []},
            "reclaimable_work": [with_ref(m, "mission", "mission_id") for m in blocked],
            "provider_or_connector_unknowns": [c for c in costs if c.get("status") in {"UNKNOWN", "NOT_AVAILABLE", "NOT_TESTED"}],
        },
        "costs": {
            "measurements": [with_ref(c, "cost_measurement", "measurement_id") for c in costs],
            "measured_actual_total_usd": actual_total if measured_costs else "UNKNOWN",
            "measured_estimated_total_usd": estimated_total if measured_costs else "UNKNOWN",
            "unknown_measurements": len(unknown_costs),
            "budgets": budgets,
        },
        "briefings": [with_ref(b, "briefing", "briefing_id") for b in briefings],
        "voice": [with_ref(v, "voice_interaction", "interaction_id") for v in voices],
        "effect_truth": effect_truth,
        "evolution_truth": evolution_truth,
        "timeline": decision_timeline(database, limit=timeline_limit),
        "executive": executive_projection,
        "portfolio": portfolio_projection,
        "discovery": discovery_surface,
        "capability_fabric": capability_fabric,
    }
    # Keep the pre-V9 top-level snapshot shape byte-compatible for databases
    # with no World Intelligence records.  Once the advisory world layer has
    # real state (or an explicit projection error), expose its additive
    # surface so consumers can distinguish an empty legacy database from a
    # populated V9 world model without introducing a second authority.
    world_counts = world_intelligence.get("counts") if isinstance(world_intelligence, dict) else None
    world_has_state = bool(
        isinstance(world_counts, dict)
        and any(int(value or 0) > 0 for value in world_counts.values() if isinstance(value, (int, float)))
    ) or any(
        bool(world_intelligence.get(key))
        for key in ("signals", "entities", "predictions", "gaps", "patterns", "error")
    ) if isinstance(world_intelligence, dict) else False
    if world_has_state:
        snapshot_payload["world_intelligence"] = world_intelligence
    # The packet is an additive read-only export contract.  Import lazily to
    # avoid a module cycle: the packet builder reuses this snapshot function
    # when called independently.
    from .briefing_packet import build_briefing_packet
    try:
        packet_clock = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        packet_clock = None
    snapshot_payload["briefing_packet"] = build_briefing_packet(
        database,
        now=packet_clock,
        stop_path=str(stop_path) if stop_path is not None else None,
        snapshot=snapshot_payload,
    )
    # Trust is an additive health subprojection.  Keep the established
    # top-level Command Center contract stable for existing consumers while
    # exposing the evidence-backed lifecycle under ``health``.
    from .trust import trust_status
    snapshot_payload["health"]["trust_assessment"] = trust_status(database)
    return snapshot_payload


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "::1"}


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]")


def _vault_root_from_db(database: FoundryDatabase) -> Path | None:
    for env_key in ("OBSIDIAN_VAULT", "JARVIS_OBSIDIAN_VAULT"):
        env_root = os.environ.get(env_key)
        if env_root:
            candidate = Path(env_root).expanduser().resolve()
            if candidate.is_dir():
                return candidate
    try:
        with database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT root FROM vault_sync_state WHERE root IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 1",
            ).fetchone()
    except sqlite3.OperationalError:
        # A database from before the vault integration must report an honest
        # unavailable projection instead of turning the local UI into a 500.
        return None
    if not row:
        return None
    root = Path(str(row[0])).expanduser().resolve()
    if not root.is_dir():
        return None
    # If sync rooted at a subfolder, prefer the Obsidian vault root when present.
    for parent in [root, *root.parents]:
        if (parent / ".obsidian").is_dir():
            return parent
    return root


def _wikilink_targets(body: str) -> list[str]:
    targets: list[str] = []
    for match in _WIKILINK_RE.finditer(body):
        raw = match.group(1).split("|", 1)[0].strip()
        if "/" in raw:
            raw = raw.rsplit("/", 1)[-1]
        if raw:
            targets.append(raw)
    return targets


def _vault_label_index(rel_paths: list[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for rel in rel_paths:
        stem = Path(rel).stem
        index[stem] = rel
        index[stem.lower()] = rel
        index[rel] = rel
        index[rel.lower()] = rel
    return index


def _resolve_vault_target(target: str, index: dict[str, str]) -> str | None:
    candidates = [
        target,
        target.lower(),
        Path(target).stem,
        Path(target).stem.lower(),
        f"{target}.md",
        f"{target}.md".lower(),
    ]
    for candidate in candidates:
        hit = index.get(candidate)
        if hit:
            return hit
    return None


def build_vault_graph(database: FoundryDatabase, vault_root: Path) -> dict[str, Any]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT rel_path FROM vault_files WHERE status = 'ACTIVE' ORDER BY rel_path",
        ).fetchall()
    rel_paths = [str(row[0]) for row in rows]
    index = _vault_label_index(rel_paths)
    nodes = [
        {"id": rel, "label": Path(rel).stem, "rel_path": rel}
        for rel in rel_paths
    ]
    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rel in rel_paths:
        note_path = (vault_root / rel).resolve()
        try:
            note_path.relative_to(vault_root.resolve())
        except ValueError:
            continue
        if not note_path.is_file():
            continue
        try:
            body = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for target in _wikilink_targets(body):
            resolved = _resolve_vault_target(target, index)
            if not resolved or resolved == rel:
                continue
            edge = (rel, resolved)
            if edge in seen:
                continue
            seen.add(edge)
            links.append({"source": rel, "target": resolved})
    return {
        "vault_root": str(vault_root),
        "nodes": nodes,
        "links": links,
        "count": len(nodes),
    }


def read_vault_note(vault_root: Path, rel_path: str, *, allowed: set[str]) -> dict[str, Any]:
    rel = rel_path.replace("\\", "/").lstrip("/")
    if rel not in allowed:
        raise FileNotFoundError(rel)
    note_path = (vault_root / rel).resolve()
    try:
        note_path.relative_to(vault_root.resolve())
    except ValueError:
        raise FileNotFoundError(rel)
    if not note_path.is_file():
        raise FileNotFoundError(rel)
    content = note_path.read_text(encoding="utf-8")
    return {
        "rel_path": rel,
        "label": Path(rel).stem,
        "content": content,
        "vault_root": str(vault_root),
        "absolute_path": str(note_path),
    }


class _CommandCenterHandler(BaseHTTPRequestHandler):
    database: FoundryDatabase
    stop_path: Path | None
    owner_secret: bytes | None
    owner_id: str = "local-owner"
    runtime_home: Path | None = None
    source_repo: Path | None = None
    timeline_limit: int = 200
    discovery_cycle_enabled: bool | None = None
    static_root: Path | None = None

    def _write_body(self, body: bytes) -> bool:
        """Write an HTTP body while treating a client navigation as normal.

        Command Center pages poll and navigate between projections. A browser
        may cancel a still-streaming snapshot during navigation; that is not a
        runtime failure and must not emit a traceback or change ledger truth.
        """
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            return False
        return True

    def _serve_static(self, route: str) -> bool:
        root = self.static_root
        if root is None:
            return False
        requested = "index.html" if route == "/" else unquote(route.lstrip("/"))
        candidate = (root / requested).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self.send_error(404)
            return True
        if not candidate.is_file():
            candidate = root / "index.html"
        if not candidate.is_file():
            self.send_error(404)
            return True
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Cache-Control", "no-cache" if candidate.name == "index.html"
            else "public, max-age=31536000, immutable")
        self._cors_headers()
        self.end_headers()
        self._write_body(body)
        return True

    # The server binds a fresh handler subclass, so this cache is isolated per
    # CommandCenterServer while concurrent polls still share one bounded read.
    _snapshot_cache_at: float = 0.0
    _snapshot_cache_payload: dict[str, Any] | None = None
    _snapshot_cache_ledger_head: int | None = None
    _snapshot_cache_lock = threading.Lock()
    SNAPSHOT_CACHE_SEC = 60.0
    # Heartbeats keep runtime health current but do not alter the expensive
    # mission/objective/world projection.  They must not defeat the bounded
    # product snapshot cache on every scheduler tick.
    SNAPSHOT_CACHE_IGNORED_EVENTS = (
        "supervisor.heartbeat",
        "worker_loop.cycle_started",
        "worker_loop.waiting",
        "agent_run.heartbeat",
    )

    @classmethod
    def _invalidate_snapshot_cache(cls) -> None:
        with cls._snapshot_cache_lock:
            cls._snapshot_cache_payload = None
            cls._snapshot_cache_at = 0.0
            cls._snapshot_cache_ledger_head = None

    def _cached_snapshot(self) -> dict[str, Any]:
        handler_cls = type(self)
        now = time.monotonic()
        try:
            with self.database.read_snapshot() as read_context:
                ledger_head = read_context.projection_head(
                    handler_cls.SNAPSHOT_CACHE_IGNORED_EVENTS
                )
        except (OSError, sqlite3.Error):
            ledger_head = None
        cached = handler_cls._snapshot_cache_payload
        if (
            cached is not None
            and now - handler_cls._snapshot_cache_at < handler_cls.SNAPSHOT_CACHE_SEC
            and ledger_head is not None
            and ledger_head == handler_cls._snapshot_cache_ledger_head
        ):
            return self._fresh_runtime_health(cached)
        with handler_cls._snapshot_cache_lock:
            now = time.monotonic()
            cached = handler_cls._snapshot_cache_payload
            if (
                cached is not None
                and now - handler_cls._snapshot_cache_at < handler_cls.SNAPSHOT_CACHE_SEC
                and ledger_head is not None
                and ledger_head == handler_cls._snapshot_cache_ledger_head
            ):
                return self._fresh_runtime_health(cached)
            payload = build_snapshot(
                self.database,
                stop_path=self.stop_path,
                timeline_limit=self.timeline_limit,
                discovery_cycle_enabled=self.discovery_cycle_enabled,
            )
            handler_cls._snapshot_cache_payload = payload
            handler_cls._snapshot_cache_at = now
            handler_cls._snapshot_cache_ledger_head = ledger_head
            return payload

    def _fresh_runtime_health(self, cached: dict[str, Any]) -> dict[str, Any]:
        """Refresh only cheap runtime fields on a cached product snapshot."""
        try:
            with command_center_readonly_connection(self.database) as connection:
                state_row = connection.execute(
                    "SELECT status, trust_state, last_heartbeat_at "
                    "FROM supervisor_state WHERE singleton = 1"
                ).fetchone()
                ledger_row = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0), COUNT(*) FROM events"
                ).fetchone()
                leases = connection.execute(
                    "SELECT lease_key, owner_id, expires_at, heartbeat_at "
                    "FROM leases ORDER BY heartbeat_at DESC"
                ).fetchall()
            health = dict(cached.get("health") or {})
            state = dict(state_row) if state_row else {}
            now_iso = _now_iso()
            stale = [
                dict(row) for row in leases
                if row["expires_at"] and str(row["expires_at"]) < now_iso
            ]
            health.update({
                "supervisor_status": state.get(
                    "status", health.get("supervisor_status", "UNKNOWN")
                ),
                "trust_state": state.get(
                    "trust_state", health.get("trust_state", "UNKNOWN")
                ),
                "heartbeat_at": state.get(
                    "last_heartbeat_at", health.get("heartbeat_at")
                ),
                "active_leases": max(0, len(leases) - len(stale)),
                "stale_leases": stale,
                "degraded": (
                    state.get("status") == "DEGRADED"
                    or bool(stale)
                    or bool(health.get("crashed_workers"))
                    or bool(health.get("projection_errors"))
                ),
            })
            refreshed = dict(cached)
            refreshed["health"] = health
            source = dict(refreshed.get("source_of_truth") or {})
            source.update({
                "ledger_head_sequence": int(ledger_row[0] or 0),
                "event_count": int(ledger_row[1] or 0),
            })
            refreshed["source_of_truth"] = source
            return refreshed
        except (OSError, sqlite3.Error):
            return cached

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        if not route.startswith("/api/") and self._serve_static(route):
            return
        if route == "/assets/Tektur.ttf":
            body = (Path(__file__).with_name("assets") / "Tektur.ttf").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "font/ttf")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._write_body(body)
            return
        if route.startswith("/api/record/"):
            parts = route.split("/")
            if len(parts) != 5 or parts[2] != "record" or parts[3] not in {"mission", "event"} or not parts[4]:
                self.send_error(404)
                return
            snapshot = self._cached_snapshot()
            record_kind, record_id = parts[3], unquote(parts[4])
            if record_kind == "mission":
                record = next((item for item in snapshot["missions"] if str(item.get("mission_id")) == record_id), None)
            else:
                record = next((item for item in snapshot["timeline"] if str(item.get("event_id")) == record_id), None)
            if record is None:
                self._json_response({"status": "NOT_FOUND", "kind": record_kind, "id": record_id}, status=404)
                return
            self._json_response({"status": "FOUND", "kind": record_kind, "record": record}, status=200)
            return
        if route == "/api/health":
            health = build_health_snapshot(
                self.database, stop_path=self.stop_path,
                controls_configured=bool(self.owner_secret),
                runtime_home=self.runtime_home,
                source_repo=self.source_repo)
            self._json_response(health, status=200)
            return
        if route == "/api/status":
            status = build_operational_status_snapshot(
                self.database,
                stop_path=self.stop_path,
                controls_configured=bool(self.owner_secret),
                runtime_home=self.runtime_home,
                source_repo=self.source_repo)
            try:
                from .deployment.status import production_status
                deployment_status = production_status(
                    home=Path(self.database.path).parent,
                    source_repo=self.source_repo)
            except Exception as error:  # noqa: BLE001 - status stays truthful
                deployment_status = {
                    "status": "UNKNOWN",
                    "health": {"status": "UNKNOWN", "reason": str(error)},
                }
            status.update({
                "deployment_health": deployment_status.get("health", {}),
                "release_authority": deployment_status.get("release_authority", {}),
                "parity": deployment_status.get("parity", {}),
                "process_truth": deployment_status.get("process_truth", {}),
                "scheduler": deployment_status.get("scheduler", {}),
                "deployment_status": deployment_status,
            })
            self._json_response(status, status=200)
            return
        if route == "/api/autonomy":
            query = parse_qs(urlparse(self.path).query)
            try:
                from .autonomy_projection import build_autonomy_projection
                projection = build_autonomy_projection(
                    self.database,
                    candidate_filter=str(query.get("filter", ["all"])[0]),
                    candidate_limit=int(query.get("limit", ["50"])[0]),
                    timeline_limit=int(query.get("timeline_limit", ["100"])[0]),
                )
            except (TypeError, ValueError) as error:
                self._json_response(
                    {"status": "BAD_REQUEST", "reason": str(error)}, status=400,
                )
                return
            except Exception as error:  # noqa: BLE001 - optional projection stays truthful
                self._json_response(
                    {
                        "status": "UNAVAILABLE",
                        "reason": f"{type(error).__name__}: {error}",
                    },
                    status=503,
                )
                return
            self._json_response(projection, status=200)
            return
        if route in {"/api/executive", "/api/executive/objectives", "/api/executive/briefing"}:
            try:
                from .executive.autonomy import executive_autonomy_snapshot
                with self.database.read_snapshot():
                    projection = executive_autonomy_snapshot(self.database)
            except Exception as error:  # noqa: BLE001 - additive API stays truthful
                projection = {"status": "UNAVAILABLE", "reason": f"{type(error).__name__}: {error}"}
            if route.endswith("/objectives"):
                projection = {
                    "objectives": projection.get("objectives", {}),
                    "priorities": projection.get("priorities", []),
                    "progress": projection.get("progress", {}),
                }
            elif route.endswith("/briefing"):
                projection = {"briefing": projection.get("briefing"), "escalations": projection.get("escalations", {}), "continuations": projection.get("continuations", {})}
            self._json_response(projection, status=200)
            return
        if route == "/api/timeline":
            try:
                with self.database.read_snapshot():
                    timeline = decision_timeline(
                        self.database, limit=self.timeline_limit
                    )
            except Exception as error:  # noqa: BLE001 - bounded read stays honest
                self._json_response(
                    {"status": "UNAVAILABLE", "reason": f"{type(error).__name__}: {error}"},
                    status=503,
                )
                return
            self._json_response(timeline, status=200)
            return
        if route == "/api/vault/graph":
            vault_root = _vault_root_from_db(self.database)
            if vault_root is None:
                self._json_response(
                    {"status": "NOT_AVAILABLE", "reason": "vault root unknown", "nodes": [], "links": []},
                    status=503,
                )
                return
            try:
                graph = build_vault_graph(self.database, vault_root)
            except sqlite3.OperationalError:
                self._json_response(
                    {"status": "NOT_AVAILABLE", "reason": "vault index unavailable", "nodes": [], "links": []},
                    status=503,
                )
                return
            self._json_response(graph, status=200)
            return
        if route == "/api/vault/note":
            vault_root = _vault_root_from_db(self.database)
            if vault_root is None:
                self._json_response({"status": "NOT_AVAILABLE", "reason": "vault root unknown"}, status=503)
                return
            rel_path = unquote(parse_qs(urlparse(self.path).query).get("path", [""])[0])
            if not rel_path:
                self._json_response({"status": "BAD_REQUEST", "reason": "path required"}, status=400)
                return
            try:
                with self.database.connect(readonly=True) as connection:
                    allowed = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT rel_path FROM vault_files WHERE status = 'ACTIVE'",
                        ).fetchall()
                    }
            except sqlite3.OperationalError:
                self._json_response(
                    {"status": "NOT_AVAILABLE", "reason": "vault index unavailable"}, status=503,
                )
                return
            try:
                note = read_vault_note(vault_root, rel_path, allowed=allowed)
            except FileNotFoundError:
                self._json_response({"status": "NOT_FOUND", "rel_path": rel_path}, status=404)
                return
            except (OSError, UnicodeError):
                self._json_response({"status": "NOT_AVAILABLE", "rel_path": rel_path}, status=503)
                return
            self._json_response(note, status=200)
            return
        if route not in {"/", "/api/command-center"}:
            self.send_error(404)
            return
        snapshot = self._cached_snapshot()
        if route == "/":
            body = self._html(snapshot).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            body = json.dumps(snapshot, ensure_ascii=True).encode()
            content_type = "application/json; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self._write_body(body)

    def do_POST(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        if route not in {"/api/controls", "/api/owner-command"}:
            self.send_error(404)
            return
        if not self.owner_secret:
            self._json_response({"status": "BLOCKED", "reason": "owner authentication secret is not configured"}, status=503)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("bounded JSON body required")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            if route == "/api/owner-command" and not _is_loopback(self.client_address[0]):
                raise OwnerAuthenticationError("owner-command is localhost-only")
            service = OwnerControlService(self.database, secret=self.owner_secret, stop_path=self.stop_path, owner_id=self.owner_id)
            command_type = str(payload.get("command_type", ""))
            target = payload.get("target")
            exact_text = str(payload.get("exact_text", ""))
            if route == "/api/owner-command":
                nonce = secrets.token_hex(16)
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
                signature = sign_command(
                    self.owner_secret,
                    command_type,
                    target,
                    exact_text,
                    nonce,
                    supersedes=payload.get("supersedes_directive_id"),
                    expires_at=payload.get("expires_at"),
                    metadata=metadata,
                )
            else:
                nonce = str(payload.get("nonce", ""))
                signature = str(payload.get("signature", ""))
            if command_type == "RESUME":
                event_ids = service.resume(
                    target=str(target or "GLOBAL"), nonce=nonce,
                    signature=signature, exact_text=exact_text or "resume jarvis")
                result: dict[str, Any] = {
                    "status": "APPLIED", "command_type": command_type,
                    "target": target or "GLOBAL", "event_ids": event_ids,
                }
            else:
                applied = service.apply(
                    command_type,
                    target=target,
                    exact_text=exact_text,
                    nonce=nonce,
                    signature=signature,
                    directive_id=payload.get("directive_id"),
                    supersedes_directive_id=payload.get("supersedes_directive_id"),
                    expires_at=payload.get("expires_at"),
                    metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
                )
                result = {
                    "status": "APPLIED",
                    "command_type": applied.command_type,
                    "directive_id": applied.directive_id,
                    "event_id": applied.event_id,
                    "superseded_directive_id": applied.superseded_directive_id,
                    "outcome": applied.outcome,
                    "details": applied.details,
                }
            # Owner controls append ledger events; do not leave the UI serving
            # a pre-command snapshot during the bounded cache window.
            type(self)._invalidate_snapshot_cache()
            self._json_response(result, status=200)
        except OwnerReplayError as error:
            self._json_response({"status": "REJECTED", "reason": str(error)}, status=409)
        except OwnerAuthenticationError as error:
            self._json_response({"status": "REJECTED", "reason": str(error)}, status=401)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._json_response({"status": "MALFORMED", "reason": str(error)}, status=400)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")

    def _json_response(self, value: dict[str, Any], *, status: int) -> None:
        body = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    @staticmethod
    def _html(snapshot: dict[str, Any]) -> str:
        """Dependency-free telemetry observatory; all values come from durable state."""
        stop = snapshot["stop"]["active"]
        health = snapshot["health"]
        cost = snapshot["costs"]
        controls = snapshot["controls"]
        missions = snapshot["missions"][:20]
        timeline_events = snapshot["timeline"][-16:][::-1]
        unknown = "UNKNOWN"

        def safe(value: Any, fallback: str = unknown) -> str:
            return escape(str(value if value not in (None, "") else fallback))

        def tone(value: Any) -> str:
            normalized = str(value or unknown).upper()
            if normalized in {"PASS", "PASSED", "RUNNING", "READY", "CLEAR", "COMPLETED", "MEASURED", "EXECUTING"}:
                return "nominal"
            if normalized in {"BLOCKED", "FAILED", "FAIL", "ACTIVE", "STOPPED", "FROZEN"}:
                return "critical"
            if normalized in {"VERIFYING", "UNKNOWN", "NOT_TESTED", "NOT_AVAILABLE", "DEGRADED"}:
                return "caution"
            return "neutral"

        mission_cards = "".join(
            f"""<article class='mission-thread {tone(m.get('state'))}'>
              <div class='mission-thread-head'><a class='record-link' href='/api/record/mission/{quote(str(m.get("mission_id", "")), safe="")}' aria-label='Open durable mission record for {safe(m.get("mission_id"))}'>{safe(m.get("mission_id"))}</a><span class='status {tone(m.get("state"))}'><i></i>{safe(m.get("state"))}</span></div>
              <h3>{safe(m.get("objective"))}</h3>
              <div class='mission-thread-foot'><span>next vector</span><strong>{safe(m.get("next_action"))}</strong></div>
              <div class='mission-progress' aria-label='Mission progress measurement unavailable'><span>progress UNKNOWN / state-derived signal only</span></div>
            </article>"""
            for m in missions
        ) or "<article class='mission-thread empty'>No durable missions recorded. JARVIS is honestly waiting.</article>"

        timeline = "".join(
            f"""<li class='timeline-event {tone(e.get("summary"))}'>
              <div class='timeline-node' aria-hidden='true'></div>
              <div class='event-head'><a href='/api/record/event/{quote(str(e.get("event_id", "")), safe="")}' aria-label='Open durable event record {safe(e.get("event_id"))}'>EVT-{int(e["sequence"]):04d}</a><time>{safe(str(e.get("recorded_at", ""))[11:19], "--:--:--")}</time></div>
              <strong>{safe(e.get("event_type"))}</strong>
              <p>{safe(e.get("summary"), "No summary recorded")}</p>
              <span class='evidence-count'>{len(e.get("evidence_refs", []))} linked record{'s' if len(e.get("evidence_refs", [])) != 1 else ''}</span>
            </li>"""
            for e in timeline_events
        ) or "<li class='empty'>No ledger events recorded.</li>"

        goal = controls["goals"][0].get("exact_text") if controls["goals"] else "No active owner goal"
        constraint_count = len(controls["constraints"])
        active_workers = sum(1 for worker in snapshot["workers"] if worker.get("status") == "RUNNING")
        verified = sum(
            1 for verdict in snapshot["verification"]
            if verdict.get("status") == "PASS"
            and verdict.get("acceptance_status") == "ACCEPTED"
        )
        blocked = len(snapshot["recovery"]["blocked_missions"])
        briefing_status = snapshot["briefings"][0].get("status") if snapshot["briefings"] else "NOT_AVAILABLE"
        voice_status = snapshot["voice"][0].get("status") if snapshot["voice"] else "NOT_TESTED"
        cost_value = cost.get("measured_actual_total_usd", unknown)
        cost_display = f"${float(cost_value):.2f}" if isinstance(cost_value, (float, int)) else safe(cost_value)
        stop_label = "ACTIVE" if stop else "CLEAR"
        degraded_label = "DEGRADED" if health.get("degraded") else "NOMINAL"
        generated = safe(snapshot["generated_at"])
        identity = snapshot.get("release_identity") or {}
        current_sha = identity.get("current")
        previous_sha = identity.get("previous")
        current_disp = safe(current_sha[:12] if isinstance(current_sha, str) and current_sha else None)
        previous_disp = safe(previous_sha[:12] if isinstance(previous_sha, str) and previous_sha else None)

        return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='color-scheme' content='dark'>
<title>JARVIS Command Center</title>
<style>
@font-face{{font-family:"Tektur";src:url('/assets/Tektur.ttf') format('truetype');font-weight:400 900;font-display:swap}}
:root{{
  --void:#02040a;--panel:#07101c;--panel-raised:#0b1928;--line:#1a3751;--line-hot:#2a6382;
  --text:#effcff;--muted:#9fc5d7;--dim:#7093a7;--signal:#8affef;--cyan:#66d9ff;
  --violet:#9b8cff;--amber:#ffd071;--red:#ff7189;--focus:0 0 0 3px rgba(102,217,255,.34)
}}
*{{box-sizing:border-box}}
html{{background:var(--void);scroll-behavior:smooth}}
body{{margin:0;min-width:320px;background:var(--void);color:var(--text);font:500 14px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(ellipse at 50% 9%,rgba(79,167,255,.20),transparent 30%),radial-gradient(ellipse at 88% 70%,rgba(115,73,255,.10),transparent 34%),linear-gradient(180deg,rgba(255,255,255,.025),transparent 22%);z-index:-1}}
::selection{{background:var(--signal);color:#03221c}}
a{{color:inherit;text-decoration-color:var(--line-hot);text-underline-offset:4px}}
a:hover{{color:var(--signal)}}
:focus-visible{{outline:none;box-shadow:var(--focus);border-radius:4px}}
.shell{{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}}
.rail{{position:sticky;top:0;height:100vh;padding:24px 18px;border-right:1px solid var(--line);background:linear-gradient(180deg,#030710,#061323 58%,#050913);display:flex;flex-direction:column}}
.brand{{display:flex;align-items:center;gap:12px;padding:4px 6px 28px}}
.brand-mark{{width:34px;height:34px;position:relative;display:grid;place-items:center;border:1px solid var(--signal);clip-path:polygon(20% 0,80% 0,100% 20%,100% 80%,80% 100%,20% 100%,0 80%,0 20%);box-shadow:0 0 30px rgba(138,255,239,.18)}}
.brand-mark::before{{content:"";width:9px;height:9px;border:2px solid var(--signal);border-radius:50%;box-shadow:0 0 18px rgba(138,255,239,.65)}}
.brand strong,.identity h1,.command-copy h2,.goal-panel blockquote,.panel-head h2{{font-family:"Tektur","Segoe UI",sans-serif}}.brand strong{{display:block;font-size:15px;letter-spacing:.08em}}.brand small{{color:var(--muted);font-size:10px;letter-spacing:.16em}}
nav{{display:grid;gap:5px}}nav a{{display:flex;align-items:center;gap:11px;padding:10px 11px;color:var(--muted);text-decoration:none;border-radius:8px;border:1px solid transparent}}
nav a::before{{content:"";width:6px;height:6px;border:1px solid currentColor;transform:rotate(45deg)}}nav a.active{{color:var(--text);background:var(--panel);border-color:var(--line)}}nav a.active::before{{background:var(--signal);border-color:var(--signal)}}
.rail-foot{{margin-top:auto;padding:14px 10px 4px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}}.rail-foot b{{display:block;color:var(--signal);margin-bottom:3px}}
.main{{min-width:0}}
.topbar{{min-height:72px;display:flex;align-items:center;justify-content:space-between;gap:20px;padding:14px 30px;border-bottom:1px solid var(--line);background:rgba(2,4,10,.72);position:sticky;top:0;z-index:10;backdrop-filter:blur(18px)}}
.identity h1{{font-size:17px;margin:0;letter-spacing:.06em}}.identity p{{margin:2px 0 0;color:var(--muted);font-size:11px}}
.top-signals{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}}
.status{{display:inline-flex;align-items:center;gap:7px;white-space:nowrap;font:700 10px/1.2 ui-monospace,"Cascadia Mono",monospace;letter-spacing:.07em;text-transform:uppercase}}
.status i{{width:6px;height:6px;border-radius:50%;background:var(--dim)}}.status.nominal{{color:var(--signal)}}.status.nominal i{{background:var(--signal);box-shadow:0 0 9px rgba(100,244,210,.6)}}.status.caution{{color:var(--amber)}}.status.caution i{{background:var(--amber)}}.status.critical{{color:var(--red)}}.status.critical i{{background:var(--red)}}.status.neutral{{color:var(--muted)}}
.stop-state{{border:1px solid var(--line);padding:8px 12px;border-radius:8px;background:var(--panel)}}.stop-state.critical{{border-color:rgba(255,107,107,.55)}}
.content{{padding:28px 30px 48px;max-width:1680px;margin:0 auto}}
.downlink{{position:relative;border:1px solid var(--line-hot);background:linear-gradient(145deg,rgba(12,30,50,.95),rgba(3,9,18,.98) 75%);clip-path:polygon(0 18px,18px 0,calc(100% - 18px) 0,100% 18px,100% calc(100% - 18px),calc(100% - 18px) 100%,18px 100%,0 calc(100% - 18px));box-shadow:0 28px 80px rgba(0,0,0,.46),0 0 90px rgba(66,145,255,.08)}}
.downlink::before,.downlink::after{{content:"";position:absolute;z-index:2;pointer-events:none;background:var(--signal)}}.downlink::before{{height:1px;width:82px;top:0;left:28px}}.downlink::after{{width:1px;height:82px;top:28px;left:0}}
.calibration{{height:26px;border-bottom:1px solid var(--line);background:repeating-linear-gradient(90deg,transparent 0 23px,var(--line-hot) 23px 24px);position:relative}}.calibration::after{{content:"JARVIS / OWNER DOWNLINK / LEDGER PROJECTION";position:absolute;left:26px;top:6px;background:var(--panel);padding:0 10px;color:var(--dim);font:700 9px ui-monospace,monospace;letter-spacing:.12em}}
.mission-control{{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(300px,.55fr);border-bottom:1px solid var(--line)}}
.command-field{{min-height:300px;border-right:1px solid var(--line);background:linear-gradient(145deg,rgba(12,29,48,.85),rgba(5,14,27,.5) 72%);padding:34px 34px 28px;position:relative;overflow:hidden}}
.command-field::after{{content:"";position:absolute;right:-28px;top:-112px;width:380px;height:380px;border:1px solid rgba(102,217,255,.16);border-radius:50%;box-shadow:0 0 0 18px rgba(102,217,255,.035),0 0 0 78px rgba(102,217,255,.028),0 0 0 142px rgba(155,140,255,.022)}}
.command-copy{{position:relative;z-index:1;max-width:760px}}.command-copy h2{{font-size:43px;line-height:1.02;letter-spacing:-.035em;margin:10px 0 14px;max-width:16ch;text-shadow:0 0 30px rgba(102,217,255,.16)}}.command-copy>p{{color:var(--muted);max-width:58ch;margin:0 0 24px}}
.signal-label{{font:700 10px/1.2 ui-monospace,"Cascadia Mono",monospace;letter-spacing:.14em;color:var(--signal);text-transform:uppercase}}
.directive-box{{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:12px;max-width:760px;background:#050b10;border:1px solid var(--line);border-radius:10px;padding:11px 13px}}
.directive-box svg{{width:17px;height:17px;stroke:var(--cyan)}}.directive-box input{{min-width:0;border:0;background:transparent;color:var(--muted);font:inherit;outline:0}}.directive-box button{{border:1px solid var(--line);background:var(--panel-raised);color:var(--dim);border-radius:7px;padding:8px 12px;font:700 10px ui-monospace,monospace;letter-spacing:.07em}}.directive-box button:disabled{{cursor:not-allowed}}
.preview-note{{display:flex;align-items:center;gap:8px;margin-top:10px;color:var(--amber);font-size:11px}}
.goal-panel{{padding:28px 24px;display:flex;flex-direction:column;justify-content:space-between;background:linear-gradient(180deg,rgba(11,26,46,.62),rgba(5,12,23,.34))}}
.goal-panel blockquote{{font-size:22px;line-height:1.25;margin:14px 0 22px;color:var(--text)}}.goal-meta{{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding-top:16px;border-top:1px solid var(--line)}}.datum span{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.1em}}.datum b{{display:block;margin-top:4px;font:700 15px ui-monospace,monospace}}
  .core-orbit{{width:172px;height:172px;margin:2px auto 12px;position:relative;display:grid;place-items:center;border:1px solid rgba(102,217,255,.32);border-radius:50%;box-shadow:0 0 42px rgba(102,217,255,.11),inset 0 0 36px rgba(102,217,255,.08);animation:core-pulse 5.6s ease-in-out 2}}.core-orbit::before,.core-orbit::after{{content:"";position:absolute;border:1px solid rgba(155,140,255,.28);border-radius:50%;transform:rotate(34deg)}}.core-orbit::before{{inset:20px -14px;animation:orbit-drift 7s ease-in-out 2}}.core-orbit::after{{inset:-14px 20px;transform:rotate(-34deg);animation:orbit-drift-reverse 7s ease-in-out 2}}.core-orbit .core{{width:66px;height:66px;border:1px solid var(--signal);border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle,rgba(138,255,239,.22),rgba(9,23,39,.88) 64%);box-shadow:0 0 24px rgba(138,255,239,.32),inset 0 0 16px rgba(138,255,239,.3);font:700 9px ui-monospace,monospace;letter-spacing:.1em;color:var(--signal);z-index:1;animation:core-glow 2.8s ease-in-out 4}}.core-orbit .orbit-dot{{position:absolute;width:7px;height:7px;border-radius:50%;background:var(--cyan);box-shadow:0 0 12px var(--cyan);top:10px;left:83px}}.core-orbit .orbit-dot.two{{top:auto;left:auto;right:17px;bottom:41px;background:var(--violet);box-shadow:0 0 12px var(--violet)}}
.telemetry{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border-bottom:1px solid var(--line);background:rgba(5,15,28,.82)}}
.telemetry article{{padding:17px 19px;min-width:0}}.telemetry article+article{{border-left:1px solid var(--line)}}.telemetry span{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.11em}}.telemetry strong{{display:block;font:650 24px/1.1 ui-monospace,"Cascadia Mono",monospace;margin:7px 0 4px;overflow-wrap:anywhere}}.telemetry small{{color:var(--dim);font-size:10px}}
.operations{{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(340px,.65fr);align-items:start}}
.panel{{background:rgba(5,14,26,.82);overflow:hidden}}.operations>.panel:first-child{{border-right:1px solid var(--line)}}
.panel-head{{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:18px 20px;border-bottom:1px solid var(--line)}}.panel-head h2{{font-size:14px;letter-spacing:.06em;margin:0}}.panel-head p{{margin:2px 0 0;color:var(--muted);font-size:11px}}.panel-head a{{font:700 10px ui-monospace,monospace;color:var(--cyan)}}
 .mission-list{{display:grid;gap:1px}}.mission-thread{{position:relative;padding:18px 20px 16px;border-bottom:1px solid var(--line);background:linear-gradient(90deg,rgba(102,217,255,.025),transparent 72%);overflow:hidden}}.mission-thread::before{{content:"";position:absolute;left:0;top:0;bottom:0;width:1px;background:var(--cyan);opacity:.55}}.mission-thread.critical::before{{background:var(--red)}}.mission-thread.caution::before{{background:var(--amber)}}.mission-thread-head,.mission-thread-foot{{display:flex;align-items:center;justify-content:space-between;gap:16px}}.mission-thread h3{{font-family:"Tektur","Segoe UI",sans-serif;font-size:17px;line-height:1.18;font-weight:600;margin:10px 0 14px;max-width:62ch}}.mission-thread-foot{{justify-content:flex-start;color:var(--dim);font:700 9px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase}}.mission-thread-foot strong{{color:var(--muted);font-size:10px;letter-spacing:0;text-transform:none;font-weight:500}}.mission-progress{{margin-top:14px;color:var(--dim);font:700 9px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase}}.mission-progress span{{display:block}}
.timeline-panel{{max-height:720px;overflow:auto}}.timeline-list{{list-style:none;margin:0;padding:7px 20px 20px 46px}}.timeline-event{{position:relative;padding:14px 0 16px;border-bottom:1px solid var(--line)}}.timeline-event:last-child{{border-bottom:0}}.timeline-event::before{{content:"";position:absolute;left:-24px;top:0;bottom:0;width:1px;background:var(--line)}}.timeline-node{{position:absolute;left:-28px;top:20px;width:9px;height:9px;border:2px solid var(--signal);background:var(--panel);transform:rotate(45deg)}}.timeline-event:first-child .timeline-node{{animation:signal-breathe 2.8s ease-in-out 2}}.timeline-event.caution .timeline-node{{border-color:var(--amber)}}.timeline-event.critical .timeline-node{{border-color:var(--red)}}.event-head{{display:flex;justify-content:space-between;gap:8px;font:700 9px ui-monospace,monospace;color:var(--dim)}}.event-head a{{color:var(--cyan);text-decoration:none}}.timeline-event strong{{display:block;font:650 11px ui-monospace,monospace;margin:6px 0 3px;color:var(--text)}}.timeline-event p{{margin:0;color:var(--muted);font-size:11px;line-height:1.45}}.evidence-count{{display:inline-block;margin-top:8px;color:var(--signal);font:700 9px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.06em}}
.footer{{display:flex;justify-content:space-between;gap:20px;padding:15px 26px;color:var(--dim);font-size:10px;border-top:1px solid var(--line)}}.footer a{{color:var(--muted)}}
 @keyframes signal-breathe{{0%,100%{{box-shadow:0 0 0 rgba(138,255,239,0)}}50%{{box-shadow:0 0 12px rgba(138,255,239,.42)}}}}@keyframes core-pulse{{0%,100%{{box-shadow:0 0 42px rgba(102,217,255,.11),inset 0 0 36px rgba(102,217,255,.08)}}50%{{box-shadow:0 0 72px rgba(102,217,255,.24),inset 0 0 52px rgba(102,217,255,.14)}}}}@keyframes core-glow{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.035)}}}}@keyframes orbit-drift{{0%,100%{{transform:rotate(34deg) scale(1)}}50%{{transform:rotate(39deg) scale(1.035)}}}}@keyframes orbit-drift-reverse{{0%,100%{{transform:rotate(-34deg) scale(1)}}50%{{transform:rotate(-39deg) scale(1.035)}}}}
@media(max-width:1120px){{.shell{{grid-template-columns:78px minmax(0,1fr)}}.rail{{padding:22px 12px}}.brand-copy,nav span,.rail-foot span{{display:none}}.brand{{justify-content:center}}nav a{{justify-content:center;padding:12px}}.mission-control,.operations{{grid-template-columns:1fr}}.command-field,.operations>.panel:first-child{{border-right:0;border-bottom:1px solid var(--line)}}.telemetry{{grid-template-columns:repeat(3,1fr)}}.telemetry article:nth-child(4){{border-left:0;border-top:1px solid var(--line)}}.telemetry article:nth-child(5){{border-top:1px solid var(--line)}}}}
@media(max-width:700px){{.shell{{display:block}}.rail{{position:static;height:auto;padding:10px 14px;display:flex;flex-direction:row;align-items:center;border-right:0;border-bottom:1px solid var(--line)}}.brand{{padding:0;margin-right:auto}}.brand-copy{{display:block}}nav{{display:flex;overflow:auto}}nav a{{padding:9px}}nav a:not(.active){{display:none}}.rail-foot{{display:none}}.topbar{{padding:11px 14px;align-items:center}}.identity p{{display:none}}.top-signals{{gap:7px}}.top-signals>.status:not(.stop-state){{display:none}}.stop-state{{padding:7px 9px}}.content{{padding:14px 10px 28px}}.downlink{{clip-path:polygon(0 12px,12px 0,calc(100% - 12px) 0,100% 12px,100% calc(100% - 12px),calc(100% - 12px) 100%,12px 100%,0 calc(100% - 12px))}}.calibration::after{{left:12px;font-size:8px}}.mission-control{{display:block}}.goal-panel{{border-top:1px solid var(--line)}}.command-field{{min-height:0;padding:26px 18px 22px}}.command-copy h2{{font-size:32px}}.directive-box{{grid-template-columns:auto minmax(0,1fr);overflow:hidden}}.directive-box input{{text-overflow:ellipsis}}.directive-box button{{display:none}}.telemetry{{grid-template-columns:1fr 1fr}}.telemetry article:nth-child(n){{border-left:0;border-top:1px solid var(--line)}}.telemetry article:nth-child(odd){{border-right:1px solid var(--line)}}.telemetry article:first-child,.telemetry article:nth-child(2){{border-top:0}}.operations{{display:block}}.timeline-panel{{max-height:none}}.panel-head{{padding:15px 16px}}.mission-thread{{padding:17px 16px}}.mission-thread h3{{font-size:16px}}.footer{{display:block}}.footer span{{display:block;margin-bottom:7px}}}}
 @media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}.timeline-node,.core-orbit,.core-orbit::before,.core-orbit::after,.core-orbit .core{{animation:none}}.timeline-node{{box-shadow:0 0 0 2px rgba(138,255,239,.12)}}}}
</style>
</head>
<body>
<!--
THESIS: JARVIS is a living command core for an autonomous institution, not a dashboard waiting for clicks.
OWN-WORLD: Deep space-black, electric cyan and violet orbit light, cinematic radial depth, spatial telemetry planes, and ledger-linked signals.
STORY: The owner speaks an intent, the JARVIS core reveals the active system posture, and mission vectors radiate outward into evidence.
FIRST VIEWPORT: A cinematic command core anchors an Owner Link, current goal, orbit telemetry, mission vectors, and a scrollable evidence stream.
FORM: 2100 autonomous command deck, user-pinned Iron Man JARVIS direction; seed f99526de was overruled by the explicit brief. Signature motion is a living core pulse and orbit drift.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
-->
<div class='shell'>
  <aside class='rail'>
    <div class='brand'><div class='brand-mark' aria-hidden='true'></div><div class='brand-copy'><strong>JARVIS</strong><small>COMMAND CENTER</small></div></div>
    <nav aria-label='Primary navigation'>
      <a class='active' href='#overview'><span>Command</span></a>
      <a href='#missions'><span>Missions</span></a>
      <a href='#timeline'><span>Timeline</span></a>
      <a href='/api/command-center'><span>Evidence API</span></a>
    </nav>
    <div class='rail-foot'><b>LIVE FOUNDRY</b><span>CURRENT {current_disp}<br>PREVIOUS {previous_disp}</span></div>
  </aside>
  <main class='main'>
    <header class='topbar'>
      <div class='identity'><h1>Command Center</h1><p>Append-only operational projection · generated {generated}</p></div>
      <div class='top-signals'>
        <span class='status {tone(health.get("supervisor_status"))}'><i></i>Supervisor {safe(health.get("supervisor_status"))}</span>
        <span class='stop-state status {tone(stop_label)}'><i></i>STOP {stop_label}</span>
      </div>
    </header>
    <div class='content' id='overview'>
      <div class='downlink'>
      <div class='calibration' aria-hidden='true'></div>
      <section class='mission-control' aria-labelledby='owner-link-title'>
        <div class='command-field'>
          <div class='command-copy'>
            <h2 id='owner-link-title'>What should JARVIS move next?</h2>
            <p>Goals, constraints, vetoes, pause, resume, and deterministic STOP enter one audited control path. Routine operation continues without becoming an approval queue.</p>
            <div class='directive-box' aria-label='Owner communication preview'>
              <svg viewBox='0 0 24 24' fill='none' aria-hidden='true'><path d='M12 3v18M8 7.5C8 5.6 9.8 4 12 4s4 1.6 4 3.5V12c0 2.2-1.8 4-4 4s-4-1.8-4-4V7.5Z' stroke-width='1.6'/><path d='M5 12a7 7 0 0 0 14 0M9 21h6' stroke-width='1.6'/></svg>
              <input value='Owner console preview — authentication is not configured' readonly aria-label='Owner directive preview status'>
              <button type='button' disabled>CHANNEL LOCKED</button>
            </div>
            <div class='preview-note'><span class='status caution'><i></i>{safe(voice_status)}</span> Real English STT/TTS remains explicitly gated.</div>
          </div>
        </div>
        <aside class='goal-panel' aria-label='Current owner intent'>
          <div><div class='core-orbit' aria-hidden='true'><span class='orbit-dot'></span><span class='orbit-dot two'></span><span class='core'>JARVIS</span></div><blockquote>{safe(goal)}</blockquote></div>
          <div class='goal-meta'><div class='datum'><span>Constraints</span><b>{constraint_count:02d}</b></div><div class='datum'><span>Trust state</span><b>{safe(health.get("trust_state"))}</b></div></div>
        </aside>
      </section>
      <section class='telemetry' aria-label='Operational cross-check'>
        <article><span>System posture</span><strong class='status {tone(degraded_label)}'><i></i>{degraded_label}</strong><small>{len(health.get("stale_leases", []))} stale leases</small></article>
        <article><span>Active workers</span><strong>{active_workers:02d}</strong><small>{len(snapshot["workers"])} durable run records</small></article>
        <article><span>Verified</span><strong>{verified:02d}</strong><small>independent verdicts passed</small></article>
        <article><span>Blocked work</span><strong>{blocked:02d}</strong><small>reclaimable mission records</small></article>
        <article><span>Measured cost</span><strong>{cost_display}</strong><small>{cost.get("unknown_measurements", 0)} unknown measurements</small></article>
      </section>
      <div class='operations'>
        <section class='panel' id='missions'>
          <header class='panel-head'><div><h2>Mission operations</h2><p>Current intent, execution state, and next action from durable records.</p></div><span class='status {tone(briefing_status)}'><i></i>Briefing {safe(briefing_status)}</span></header>
          <div class='mission-list'>{mission_cards}</div>
        </section>
        <aside class='panel timeline-panel' id='timeline'>
          <header class='panel-head'><div><h2>Decision signal</h2><p>Newest ledger events first.</p></div><a href='/api/timeline'>OPEN JSON</a></header>
          <ol class='timeline-list'>{timeline}</ol>
        </aside>
      </div>
      <footer class='footer'><span>Ledger head {snapshot['source_of_truth']['ledger_head_sequence']} · {snapshot['source_of_truth']['event_count']} append-only events</span><a href='/api/command-center'>Inspect complete durable projection</a></footer>
      </div>
    </div>
  </main>
</div>
</body>
</html>"""

    def log_message(self, *_args: Any) -> None:
        return


class CommandCenterServer:
    """Local-only HTTP projection, optionally owned by the Supervisor."""

    def __init__(
        self, database: FoundryDatabase, *,
        stop_path: str | Path | None = None,
        host: str = "127.0.0.1", port: int = 0,
        discovery_cycle_enabled: bool | None = None,
        static_root: str | Path | None = None,
        runtime_home: str | Path | None = None,
        source_repo: str | Path | None = None,
    ) -> None:
        handler = type("BoundCommandCenterHandler", (_CommandCenterHandler,), {})
        handler.database = database
        handler.stop_path = Path(stop_path).resolve() if stop_path else None
        handler.owner_secret = None
        handler.owner_id = "local-owner"
        handler.runtime_home = (
            Path(runtime_home).expanduser().resolve()
            if runtime_home is not None else Path(database.path).resolve().parent)
        handler.source_repo = (
            Path(source_repo).expanduser().resolve()
            if source_repo is not None else None)
        handler.discovery_cycle_enabled = discovery_cycle_enabled
        handler.static_root = Path(static_root).resolve() if static_root else None
        self._handler = handler
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self.httpd.server_address[0], int(self.httpd.server_address[1])

    def configure_owner(self, secret: bytes, *, owner_id: str = "local-owner") -> None:
        """Enable authenticated controls explicitly; never inferred from env."""
        if not secret:
            raise ValueError("owner secret cannot be empty")
        self._handler.owner_secret = bytes(secret)
        self._handler.owner_id = owner_id
        self._handler._invalidate_snapshot_cache()

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="jarvis-command-center", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None
