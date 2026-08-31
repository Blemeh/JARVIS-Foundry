"""Safe, disposable benchmark for the V9 stabilization read paths.

The fixture is created under the requested evidence directory and is never
resolved through the live JARVIS home.  It approximates the production ledger
scale with material entities/directives plus heartbeat-heavy history, then
measures the same HTTP surfaces with SQLite trace counters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from jarvis_foundry.command_center import CommandCenterServer
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, canonical_json, payload_hash
from jarvis_foundry.executive.autonomy import build_plan, create_objective


LIVE_SHA = "5167e2cbaa5df3c1ad00b660138a5a8b6b2bd1bd"
LIVE_RELEASE_ID = "rel-5167e2cbaa5d-bca7362e"
DEFAULT_EVENT_COUNT = 136_346
EXECUTIVE_FIXTURE_OBJECTIVES = 4
HEARTBEAT_EVENT_TYPES = (
    "supervisor.heartbeat",
    "worker_loop.cycle_started",
    "worker_loop.waiting",
    "agent_run.heartbeat",
)
ENDPOINTS = (
    ("/api/health", "health", False),
    ("/api/status", "status", False),
    ("/api/executive", "executive", False),
    ("/api/executive/objectives", "executive-objectives", False),
    ("/api/executive/briefing", "executive-briefing", False),
    ("/api/timeline", "timeline", False),
    ("/api/command-center", "command-center-cold", True),
    ("/api/command-center", "command-center-warm", False),
)


def _fixture_install(output_dir: Path) -> Path:
    install = output_dir / "fixture-install"
    release = install / "releases" / LIVE_SHA
    release.mkdir(parents=True, exist_ok=True)
    (install / "CURRENT").write_text(LIVE_SHA + "\n", encoding="utf-8")
    (release / "RELEASE.json").write_text(
        json.dumps({
            "source_sha": LIVE_SHA,
            "release_id": LIVE_RELEASE_ID,
            "schema": 38,
        }, sort_keys=True),
        encoding="utf-8",
    )
    return install


def _material_fixture_events() -> list[Event]:
    events: list[Event] = [
        Event.create(
            "supervisor.started",
            {
                "instance_id": "stabilization-fixture",
                "trust_state": "LAB",
                "release_id": LIVE_RELEASE_ID,
                "pid": 0,
            },
            source_type="supervisor",
            source_id="stabilization-fixture",
            idempotency_key="fixture:supervisor.started",
        ),
        Event.create(
            "emergency_stop.reconciled",
            {"instance_id": "stabilization-fixture", "reason": "fixture trust boundary"},
            source_type="supervisor",
            source_id="stabilization-fixture",
            idempotency_key="fixture:emergency_stop.reconciled",
        ),
    ]
    for index in range(121):
        mission_id = f"fixture-mission-{index:03d}"
        events.append(Event.create(
            "mission.created",
            {
                "mission_id": mission_id,
                "objective": f"fixture objective {index}",
                "resource_id": f"fixture-resource-{index:03d}",
                "project_id": f"fixture-project-{index % 12:02d}",
                "state": "PLANNED",
                "acceptance_criteria": ["bounded benchmark fixture"],
                "next_action": "wait",
            },
            source_type="supervisor",
            source_id="stabilization-fixture",
            idempotency_key=f"fixture:mission.created:{mission_id}",
        ))
    for index in range(121):
        resource_id = f"fixture-resource-{index:03d}"
        events.append(Event.create(
            "project.registered",
            {
                "resource_id": resource_id,
                "kind": "filesystem",
                "root_path": f"fixture://resource/{index}",
                "resolved_root": f"fixture://resource/{index}",
                "baseline": {"fixture": True},
                "capabilities": {"read": "READY"},
                "readiness_state": "READY",
            },
            source_type="supervisor",
            source_id="stabilization-fixture",
            idempotency_key=f"fixture:project.registered:{resource_id}",
        ))
    for index in range(99):
        system_id = f"fixture-system-{index:03d}"
        events.append(Event.create(
            "system.registered",
            {
                "system_id": system_id,
                "kind": "fixture",
                "name": system_id,
                "capabilities": {"read": "READY"},
            },
            source_type="supervisor",
            source_id="stabilization-fixture",
            idempotency_key=f"fixture:system.registered:{system_id}",
        ))
    # These records intentionally use unknown targets. They exercise the
    # owner-facing target resolver at the same directive scale as the trace
    # without granting synthetic owner authority.
    for index in range(121):
        directive_id = f"fixture-directive-{index:03d}"
        events.append(Event.create(
            "owner_directive.recorded",
            {
                "directive_id": directive_id,
                "command_type": "CONSTRAINT",
                "target": f"fixture-unknown-target-{index:03d}",
                "exact_text": "benchmark-only malformed owner record",
            },
            source_type="fixture",
            source_id="stabilization-fixture",
            idempotency_key=f"fixture:owner_directive.recorded:{directive_id}",
        ))
    return events


def create_fixture(database_path: Path, event_count: int) -> FoundryDatabase:
    if database_path.exists():
        raise FileExistsError(f"refusing to overwrite existing fixture: {database_path}")
    database = FoundryDatabase(database_path)
    database.initialize()
    material = _material_fixture_events()
    # Admit the executive fixture before the explicit SUSPENDED trust
    # boundary. The real write APIs correctly refuse new authority while a
    # STOP/SUSPENDED state is active; setup ordering must respect that gate.
    database.append(material[0])
    database.append_events(material[2:])
    for index in range(EXECUTIVE_FIXTURE_OBJECTIVES):
        objective_id = f"fixture-objective-{index:02d}"
        objective = create_objective(
            database,
            f"fixture executive objective {index}",
            objective_id=objective_id,
            priority=float(EXECUTIVE_FIXTURE_OBJECTIVES - index),
            success_criteria=("bounded benchmark fixture",),
        )
        build_plan(
            database,
            objective.objective_id,
            [{"step_id": "observe", "description": "Read fixture state"}],
        )
    # Keep the durable Supervisor projection healthy while retaining the
    # production trust boundary. The remaining heartbeat history is inserted
    # directly below so it cannot alter projection state during fixture setup.
    database.append(material[1])
    database.append(Event.create(
        "supervisor.heartbeat",
        {"instance_id": "stabilization-fixture", "trust_state": "SUSPENDED"},
        source_type="supervisor",
        source_id="stabilization-fixture",
        idempotency_key="fixture:supervisor.heartbeat:projection",
    ))
    current = database.event_count()
    if current > event_count:
        raise ValueError(f"event_count {event_count} is below material fixture size {current}")

    rows: list[tuple[Any, ...]] = []
    for index in range(event_count - current):
        event_type = HEARTBEAT_EVENT_TYPES[index % len(HEARTBEAT_EVENT_TYPES)]
        payload = {"fixture": True, "heartbeat_index": index}
        raw_payload = canonical_json(payload)
        event_id = str(uuid.uuid4())
        timestamp = (
            datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
            + timedelta(seconds=index)
        ).isoformat().replace("+00:00", "Z")
        rows.append((
            event_id,
            event_type,
            timestamp,
            timestamp,
            "fixture",
            "stabilization-fixture",
            event_id,
            None,
            f"fixture:heartbeat:{index}",
            1,
            raw_payload,
            payload_hash(payload),
        ))
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            """INSERT INTO events(
                event_id, event_type, occurred_at, recorded_at, source_type,
                source_id, correlation_id, causation_id, idempotency_key,
                schema_version, payload_json, payload_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    return database


def _new_probe() -> dict[str, Any]:
    return {
        "sql_statements": 0,
        "readonly_connections": 0,
        "writable_connections": 0,
        "begin_immediate": 0,
        "commits": 0,
        "full_ledger_reads": 0,
        "mission_id_lookups": 0,
        "resource_id_lookups": 0,
        "system_id_lookups": 0,
        "target_batch_resolutions": 0,
        "events_calls": 0,
        "event_rows_materialized": 0,
        "event_result_ids": set(),
    }


def _trace(probe: dict[str, Any], statement: str) -> None:
    compact = " ".join(statement.upper().split())
    if not compact:
        return
    probe["sql_statements"] += 1
    if "BEGIN IMMEDIATE" in compact:
        probe["begin_immediate"] += 1
    if compact == "COMMIT" or compact.startswith("COMMIT "):
        probe["commits"] += 1
    if "SELECT * FROM EVENTS ORDER BY SEQUENCE ASC" in compact and "LIMIT" not in compact:
        probe["full_ledger_reads"] += 1
    if "FROM MISSIONS WHERE MISSION_ID =" in compact:
        probe["mission_id_lookups"] += 1
    if "FROM RESOURCES WHERE RESOURCE_ID =" in compact:
        probe["resource_id_lookups"] += 1
    if "FROM SYSTEMS WHERE SYSTEM_ID =" in compact:
        probe["system_id_lookups"] += 1
    if (
        "SELECT MISSION_ID AS TARGET FROM MISSIONS WHERE MISSION_ID IN" in compact
        and "UNION SELECT RESOURCE_ID AS TARGET" in compact
    ):
        probe["target_batch_resolutions"] += 1


def _attach_probes(database: FoundryDatabase) -> dict[str, Any]:
    state: dict[str, Any] = {"current": None}
    real_connect = database.connect
    real_events = database.events

    def connect(*, readonly: bool = False):
        connection = real_connect(readonly=readonly)
        probe = state.get("current")
        if probe is not None:
            probe["readonly_connections" if readonly else "writable_connections"] += 1
            connection.set_trace_callback(lambda statement: _trace(probe, statement))
        return connection

    def events(*args: Any, **kwargs: Any):
        result = real_events(*args, **kwargs)
        probe = state.get("current")
        if probe is not None:
            probe["events_calls"] += 1
            result_id = id(result)
            if result_id not in probe["event_result_ids"]:
                probe["event_result_ids"].add(result_id)
                probe["event_rows_materialized"] += len(result)
        return result

    database.connect = connect  # type: ignore[method-assign]
    database.events = events  # type: ignore[method-assign]
    state["set_current"] = lambda probe: state.__setitem__("current", probe)
    return state


def _fixture_event_count(database_path: Path) -> int:
    with sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])


def _correctness(endpoint: str, payload: Any) -> dict[str, Any]:
    if endpoint == "/api/timeline":
        rows = payload if isinstance(payload, list) else []
        sequences = [int(row.get("sequence", 0)) for row in rows if isinstance(row, dict)]
        return {
            "kind": "timeline",
            "count": len(rows),
            "ordered": sequences == sorted(sequences),
            "first_sequence": sequences[0] if sequences else None,
            "last_sequence": sequences[-1] if sequences else None,
        }
    if not isinstance(payload, dict):
        return {"kind": "invalid", "status": "UNAVAILABLE"}
    if endpoint == "/api/health":
        return {
            "kind": "health",
            "status": payload.get("status"),
            "trust_state": payload.get("trust_state"),
            "schema": payload.get("schema"),
            "event_count": payload.get("event_count"),
            "release_current": payload.get("installed_release_sha"),
        }
    if endpoint == "/api/status":
        return {
            "kind": "status",
            "status": payload.get("status"),
            "trust_state": payload.get("trust_state"),
            "schema": payload.get("schema"),
            "event_count": payload.get("event_count"),
        }
    if endpoint.startswith("/api/executive"):
        objectives = payload.get("objectives")
        return {
            "kind": "executive",
            "status": payload.get("status", "AVAILABLE"),
            "objective_count": len(objectives) if isinstance(objectives, dict) else None,
            "priority_count": len(payload.get("priorities", [])) if isinstance(payload.get("priorities"), list) else None,
        }
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    source = payload.get("source_of_truth") if isinstance(payload.get("source_of_truth"), dict) else {}
    release = payload.get("release_identity") if isinstance(payload.get("release_identity"), dict) else {}
    executive = payload.get("executive") if isinstance(payload.get("executive"), dict) else {}
    autonomy = executive.get("autonomy") if isinstance(executive.get("autonomy"), dict) else {}
    return {
        "kind": "snapshot",
        "trust_state": health.get("trust_state"),
        "schema": health.get("schema"),
        "ledger_head": source.get("ledger_head_sequence"),
        "release_current": release.get("current"),
        "executive_objective_count": len(autonomy.get("objectives", {})) if isinstance(autonomy.get("objectives"), dict) else None,
        "world_intelligence_present": "world_intelligence" in payload,
    }


def _json_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def measure(database: FoundryDatabase, output_dir: Path, source_repo: Path, timeout: float) -> dict[str, Any]:
    probe_state = _attach_probes(database)
    server = CommandCenterServer(
        database,
        stop_path=output_dir / "EMERGENCY_STOP",
        runtime_home=output_dir,
        source_repo=source_repo,
    )
    server.configure_owner(b"stabilization-benchmark-owner")
    initial_event_count = _fixture_event_count(database.path)
    server.start()
    results: list[dict[str, Any]] = []
    try:
        base_url = f"http://{server.address[0]}:{server.address[1]}"
        for endpoint, label, cold in ENDPOINTS:
            if cold:
                server._handler._invalidate_snapshot_cache()
            probe = _new_probe()
            probe_state["set_current"](probe)
            before_count = _fixture_event_count(database.path)
            started = time.perf_counter()
            status = 0
            payload: Any = None
            error: str | None = None
            try:
                with urlopen(base_url + endpoint, timeout=timeout) as response:
                    status = int(response.status)
                    raw = response.read()
                body = raw.decode("utf-8")
                payload = json.loads(body)
                response_size = len(raw)
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                response_size = 0
                error = f"{type(exc).__name__}: {exc}"
            elapsed = time.perf_counter() - started
            after_count = _fixture_event_count(database.path)
            probe_state["set_current"](None)
            result = {
                "endpoint": endpoint,
                "label": label,
                "status": status,
                "elapsed_seconds": round(elapsed, 6),
                "response_bytes": response_size,
                "before_event_count": before_count,
                "after_event_count": after_count,
                "event_count_delta": after_count - before_count,
                "correctness": _correctness(endpoint, payload),
                "correctness_hash": _json_hash(_correctness(endpoint, payload)),
                "error": error,
                "sql": {
                    key: value for key, value in probe.items()
                    if key != "event_result_ids"
                },
            }
            results.append(result)
    finally:
        probe_state["set_current"](None)
        server.close()
    return {
        "benchmark": "v9-stabilization",
        "source_sha": LIVE_SHA,
        "release_id": LIVE_RELEASE_ID,
        "schema": 38,
        "fixture": {
            "database": str(database.path),
            "initial_event_count": initial_event_count,
            "event_count": _fixture_event_count(database.path),
            "material_event_count": len(_material_fixture_events())
            + EXECUTIVE_FIXTURE_OBJECTIVES * 3 + 1,
            "heartbeat_event_types": list(HEARTBEAT_EVENT_TYPES),
        },
        "requests": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--event-count", type=int, default=DEFAULT_EVENT_COUNT)
    parser.add_argument("--label", default="after")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    database_path = output_dir / "fixture.db"
    install_root = _fixture_install(output_dir)
    database = create_fixture(database_path, max(1, int(args.event_count)))
    prior_install = os.environ.get("JARVIS_INSTALL_ROOT")
    os.environ["JARVIS_INSTALL_ROOT"] = str(install_root)
    try:
        report = measure(
            database,
            output_dir,
            Path(__file__).resolve().parents[1],
            max(1.0, float(args.timeout)),
        )
    finally:
        if prior_install is None:
            os.environ.pop("JARVIS_INSTALL_ROOT", None)
        else:
            os.environ["JARVIS_INSTALL_ROOT"] = prior_install
    result_path = output_dir / f"{args.label}.json"
    result_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": str(result_path),
        "fixture": report["fixture"],
        "requests": report["requests"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
