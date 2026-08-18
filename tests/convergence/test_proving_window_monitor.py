"""Read-only proving-window monitor contract."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.convergence.monitor_proving_window import (
    load_manifest,
    read_checkpoint,
    write_checkpoint,
)


def _store(path: Path, *, heartbeats: list[str], status: str = "RUNNING",
           stop: bool = False) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE events(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT, event_type TEXT, occurred_at TEXT,
            recorded_at TEXT, source_type TEXT, source_id TEXT,
            correlation_id TEXT, causation_id TEXT, idempotency_key TEXT,
            schema_version INTEGER, payload_json TEXT, payload_hash TEXT
        );
        CREATE TABLE supervisor_state(
            singleton INTEGER PRIMARY KEY, instance_id TEXT, status TEXT,
            trust_state TEXT, last_heartbeat_at TEXT, stop_id TEXT
        );
        CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY);
        CREATE TABLE leases(lease_key TEXT PRIMARY KEY, holder_id TEXT,
                            expires_at TEXT);
        CREATE TABLE directives(
            directive_id TEXT PRIMARY KEY, command_type TEXT, target TEXT,
            active INTEGER, updated_at TEXT
        );
        """)
    for index, occurred in enumerate(heartbeats, start=1):
        connection.execute(
            "INSERT INTO events(event_type, occurred_at, source_id, payload_json) "
            "VALUES ('supervisor.heartbeat', ?, 'supervisor-1', '{}')",
            (occurred,))
    connection.execute("INSERT INTO schema_migrations VALUES (33)")
    connection.execute(
        "INSERT INTO supervisor_state VALUES (1, 'supervisor-1', ?, 'LAB', ?, NULL)",
        (status, heartbeats[-1] if heartbeats else None))
    if stop:
        connection.execute(
            "INSERT INTO directives VALUES ('stop-1', 'PAUSE', '*', 1, ?)",
            (heartbeats[-1] if heartbeats else "2026-08-18T00:00:00+00:00",))
    connection.commit()
    connection.close()


def _manifest(path: Path, *, start: str = "2026-08-18T00:00:00Z",
              duration: int = 604800) -> dict[str, object]:
    value = {
        "schema": "jarvis-proving-window-manifest-v1",
        "window_id": "test-window",
        "release_source_commit": "e781f2c",
        "release_pointer": "main",
        "started_at": start,
        "required_duration_seconds": duration,
        "max_heartbeat_gap_seconds": 300,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def test_monitor_waits_without_heartbeat(tmp_path: Path) -> None:
    database = tmp_path / "foundry.db"
    _store(database, heartbeats=[])
    manifest = _manifest(tmp_path / "manifest.json")
    checkpoint = read_checkpoint(
        database, manifest, now=datetime(2026, 8, 18, 0, 1, tzinfo=timezone.utc))
    assert checkpoint["status"] == "WAITING_HEARTBEAT"
    assert checkpoint["heartbeat"]["count"] == 0


def test_monitor_detects_gap_and_never_claims_external_effects(tmp_path: Path) -> None:
    database = tmp_path / "foundry.db"
    _store(database, heartbeats=[
        "2026-08-18T00:00:05Z", "2026-08-18T00:10:05Z"])
    manifest = _manifest(tmp_path / "manifest.json")
    checkpoint = read_checkpoint(
        database, manifest, now=datetime(2026, 8, 18, 0, 11, tzinfo=timezone.utc))
    assert checkpoint["status"] == "WAITING_HEARTBEAT"
    assert checkpoint["heartbeat"]["max_gap_seconds"] == 600.0
    assert checkpoint["evidence_limits"]["claims_external_effects"] is False


def test_monitor_reaches_ready_only_after_real_duration(tmp_path: Path) -> None:
    database = tmp_path / "foundry.db"
    _store(database, heartbeats=[
        "2026-08-18T00:00:05Z", "2026-08-18T00:00:10Z"])
    manifest = _manifest(tmp_path / "manifest.json", duration=10)
    before = read_checkpoint(
        database, manifest, now=datetime(2026, 8, 18, 0, 0, 9, tzinfo=timezone.utc))
    after = read_checkpoint(
        database, manifest, now=datetime(2026, 8, 18, 0, 0, 11, tzinfo=timezone.utc))
    assert before["status"] == "WAITING_ELAPSED"
    assert after["status"] == "READY_TO_CLOSE"


def test_monitor_preserves_owner_stop_as_waiting(tmp_path: Path) -> None:
    database = tmp_path / "foundry.db"
    _store(database, heartbeats=["2026-08-18T00:00:05Z"], stop=True)
    manifest = _manifest(tmp_path / "manifest.json", duration=1)
    checkpoint = read_checkpoint(
        database, manifest, now=datetime(2026, 8, 18, 0, 1, tzinfo=timezone.utc))
    assert checkpoint["status"] == "WAITING_OWNER_STOP"
    assert checkpoint["runtime"]["active_stop_directives"]


def test_checkpoint_write_is_atomic_and_reloadable(tmp_path: Path) -> None:
    database = tmp_path / "foundry.db"
    _store(database, heartbeats=["2026-08-18T00:00:05Z"])
    manifest_path = tmp_path / "manifest.json"
    manifest = _manifest(manifest_path)
    assert load_manifest(manifest_path)["window_id"] == "test-window"
    checkpoint = read_checkpoint(
        database, manifest, now=datetime(2026, 8, 18, 0, 1, tzinfo=timezone.utc))
    output = write_checkpoint(tmp_path / "checkpoint.json", checkpoint)
    restored = json.loads(output.read_text(encoding="utf-8"))
    assert restored["schema"] == "jarvis-proving-window-checkpoint-v1"
    assert restored["manifest_hash"] == checkpoint["manifest_hash"]
