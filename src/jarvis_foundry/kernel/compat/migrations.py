"""Rehearsed, interruption-safe migration planning on WAL-safe copies."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_bytes
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.compat.corrections import CorrectionLedger
from jarvis_foundry.kernel.compat.cutover import rehearse_dual_run
from jarvis_foundry.kernel.compat.errors import CompatReadError
from jarvis_foundry.kernel.compat.projections import build_projection_registry
from jarvis_foundry.kernel.compat.readers import LegacyEventsReader
from jarvis_foundry.kernel.compat.registry import compat_kind_registry
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.projections import ProjectionDispatcher, snapshot_bytes

MIGRATION_STEPS: tuple[str, ...] = (
    "PREPARE",
    "READ",
    "CONVERT",
    "LEDGER",
    "PROJECT",
    "CORRECT",
    "CUTOVER",
    "COMPLETE",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def copy_database_read_only(source: Path, destination: Path) -> None:
    """Create a consistent WAL-safe copy without opening the source for writes."""
    source_uri = source.as_posix()
    source_connection = sqlite3.connect(f"file:{source_uri}?mode=ro", uri=True, timeout=30)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA journal_mode = DELETE")
        result = destination_connection.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"copy failed integrity check: {result}")
    finally:
        destination_connection.close()
        source_connection.close()


@dataclass(frozen=True)
class MigrationRunResult:
    workdir: Path
    last_step: str
    interrupted: bool
    exit_code: int
    artifact_hashes: Mapping[str, str]
    wall_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "workdir": str(self.workdir),
            "last_step": self.last_step,
            "interrupted": self.interrupted,
            "exit_code": self.exit_code,
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
            "wall_seconds": round(self.wall_seconds, 3),
        }


class MigrationRehearsal:
    """Runs the additive migration on a WAL-safe copy with step markers.

    Every step is idempotent: rebuilt artifacts must match a fresh run exactly.
    Interruption simulates a crash immediately after a completed boundary, and
    resume continues from the recorded marker without touching legacy rows.
    """

    def __init__(
        self,
        source: str | Path,
        workdir: str | Path,
        *,
        migration_version: int = 32,
        recorded_at: str = "2026-08-15T00:00:00Z",
    ) -> None:
        self.source = Path(source)
        self.workdir = Path(workdir)
        self.migration_version = migration_version
        self.recorded_at = recorded_at
        self.artifacts = self.workdir / "artifacts"
        self.snapshot_dir = self.workdir / "snapshot"
        self.migrated_dir = self.workdir / "migrated"
        self.state_path = self.workdir / "state.json"

    @property
    def source_sha256(self) -> str:
        return file_sha256(self.source)

    def _state(self) -> dict[str, Any]:
        if self.state_path.is_file():
            payload = _read_json(self.state_path)
            if isinstance(payload, dict):
                return payload
            raise RuntimeError("migration state must be a JSON object")
        return {
            "schema": "jarvis-r5-migration-state-v1",
            "last_step": None,
            "source_sha256": self.source_sha256,
            "artifact_hashes": {},
            "completed": False,
        }

    def _record(self, step: str, hashes: Mapping[str, str]) -> None:
        state = self._state()
        state["last_step"] = step
        state["artifact_hashes"] = dict(sorted(hashes.items()))
        state["completed"] = step == "COMPLETE"
        state["finished_at_utc"] = "2026-08-15T00:00:00Z"
        _write_json(self.state_path, state)

    def _run_step(self, step: str) -> dict[str, str]:
        if step == "PREPARE":
            return self._prepare()
        if step == "READ":
            return self._read_events()
        if step == "CONVERT":
            return self._convert_events()
        if step == "LEDGER":
            return self._build_ledger()
        if step == "PROJECT":
            return self._project()
        if step == "CORRECT":
            return self._correct()
        if step == "CUTOVER":
            return self._cutover()
        if step == "COMPLETE":
            return self._complete()
        raise ValueError(f"unknown migration step: {step}")

    def _prepare(self) -> dict[str, str]:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.migrated_dir.mkdir(parents=True, exist_ok=True)
        snapshot = self.snapshot_dir / "source-pre.db"
        if not snapshot.is_file():
            shutil.copy2(self.source, snapshot)
        return {"source_snapshot_sha256": file_sha256(snapshot)}

    def _read_events(self) -> dict[str, str]:
        reader = LegacyEventsReader(self.source, migration_version=self.migration_version)
        rows = reader.read_rows()
        output = self.artifacts / "legacy_rows.jsonl"
        digest = hashlib.sha256()
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                line = json.dumps(row.to_dict(), sort_keys=True, separators=(",", ":"))
                handle.write(line + "\n")
                digest.update(line.encode("utf-8"))
                digest.update(b"\n")
        return {
            "legacy_rows_sha256": digest.hexdigest(),
            "legacy_rows_count": str(len(rows)),
        }

    def _convert_events(self) -> dict[str, str]:
        reader = LegacyEventsReader(self.source, migration_version=self.migration_version)
        rows = reader.read_rows()
        events = [reader.to_ledger_event(row) for row in rows]
        output = self.artifacts / "converted_events.jsonl"
        digest = hashlib.sha256()
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            for index, event in enumerate(events):
                payload = {
                    "index": index,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at,
                    "recorded_at": event.recorded_at,
                    "source_type": event.source_type,
                    "source_id": event.source_id,
                    "correlation_id": event.correlation_id,
                    "causation_id": event.causation_id,
                    "idempotency_key": event.idempotency_key,
                    "schema_version": event.schema_version,
                    "kind_version": event.kind_version,
                    "payload": event.payload,
                    "payload_hash": event.payload_hash,
                }
                line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                handle.write(line + "\n")
                digest.update(line.encode("utf-8"))
                digest.update(b"\n")
        return {
            "converted_events_sha256": digest.hexdigest(),
            "converted_events_count": str(len(events)),
        }

    def _build_ledger(self) -> dict[str, str]:
        reader = LegacyEventsReader(self.source, migration_version=self.migration_version)
        events = [reader.to_ledger_event(row) for row in reader.read_rows()]
        observed_types = sorted({event.event_type for event in events})
        ledger_path = self.migrated_dir / "r4-ledger.db"
        if ledger_path.is_file():
            ledger_path.unlink()
        registry = compat_kind_registry(observed_types)
        ledger = Ledger(ledger_path, registry=registry, clock=FixedClock("1970-01-01T00:00:00Z"))
        with ledger.transaction() as connection:
            for event in events:
                ledger.append_with_connection(connection, event)
        chain = ledger.verify_chain()
        digest = hashlib.sha256()
        for event in ledger.replay():
            payload = {
                "sequence": event.sequence,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "idempotency_key": event.idempotency_key,
                "payload_hash": event.payload_hash,
                "previous_hash": event.previous_hash,
                "header_hash": event.header_hash,
            }
            digest.update(canonical_bytes(payload))
        summary = {
            "event_count": chain.sequence_count,
            "chain_valid": chain.valid,
            "head_hash": chain.head_hash,
            "ledger_content_sha256": digest.hexdigest(),
            "ledger_db_sha256": file_sha256(ledger_path),
        }
        _write_json(self.artifacts / "ledger_summary.json", summary)
        return {key: str(value) for key, value in summary.items()}

    def _project(self) -> dict[str, str]:
        reader = LegacyEventsReader(self.source, migration_version=self.migration_version)
        rows = reader.read_rows()
        observed_types = sorted({row.event_type for row in rows})
        ledger_path = self.migrated_dir / "r4-ledger.db"
        registry = compat_kind_registry(observed_types)
        ledger = Ledger(ledger_path, registry=registry, clock=FixedClock("1970-01-01T00:00:00Z"))
        snapshot = ProjectionDispatcher(build_projection_registry(observed_types)).replay(
            ledger.replay()
        )
        snapshot_sha256 = hashlib.sha256(snapshot_bytes(snapshot)).hexdigest()
        summary = {
            "snapshot_sha256": snapshot_sha256,
            "projection_versions": dict(sorted(snapshot.versions.items())),
            "event_count": int(snapshot.states["legacy_history"]["event_count"]),
        }
        _write_json(self.artifacts / "projection_summary.json", summary)
        return {
            "projection_snapshot_sha256": snapshot_sha256,
            "projection_event_count": str(summary["event_count"]),
        }

    def _correct(self) -> dict[str, str]:
        ledger_path = self.migrated_dir / "r4-ledger.db"
        reader = LegacyEventsReader(self.source, migration_version=self.migration_version)
        observed_types = sorted({row.event_type for row in reader.read_rows()})
        registry = compat_kind_registry(observed_types)
        ledger = Ledger(ledger_path, registry=registry, clock=FixedClock("1970-01-01T00:00:00Z"))
        ledger_file = CorrectionLedger(self.workdir / "corrections.json")
        corrections = ledger_file.load()
        results = ledger_file.apply_to_ledger(ledger, corrections, recorded_at=self.recorded_at)
        summary = {
            "corrections_applied": len(results),
            "correction_ids": sorted(item.event.payload["correction_id"] for item in results),
            "ledger_event_count_after": ledger.next_sequence() - 1,
        }
        _write_json(self.artifacts / "correction_summary.json", summary)
        return {key: str(value) for key, value in summary.items()}

    def _cutover(self) -> dict[str, str]:
        cutover_dir = self.workdir / "cutover"
        cutover_dir.mkdir(parents=True, exist_ok=True)
        legacy_copy = cutover_dir / "legacy-copy.db"
        r4_copy = cutover_dir / "r4-ledger.db"
        if legacy_copy.is_file():
            legacy_copy.unlink()
        if r4_copy.is_file():
            r4_copy.unlink()
        copy_database_read_only(self.source, legacy_copy)
        report = rehearse_dual_run(
            legacy_copy,
            r4_copy,
            ledger_workdir=cutover_dir / "work",
            recorded_at=self.recorded_at,
        )
        summary = report.to_dict()
        summary.pop("idempotency_signature", None)
        _write_json(self.artifacts / "cutover_summary.json", summary)
        return {key: str(value) for key, value in summary.items()}

    def _complete(self) -> dict[str, str]:
        state = self._state()
        return {"run_fingerprint": _run_fingerprint(state.get("artifact_hashes", {}))}

    def run(self, *, interrupt_after: str | None = None) -> MigrationRunResult:
        started = time.monotonic()
        state = self._state()
        last = state.get("last_step")
        start_index = MIGRATION_STEPS.index(last) + 1 if last in MIGRATION_STEPS else 0
        hashes: dict[str, str] = dict(state.get("artifact_hashes", {}))
        interrupted = False
        for step in MIGRATION_STEPS[start_index:]:
            step_hashes = self._run_step(step)
            hashes.update(step_hashes)
            self._record(step, hashes)
            if step == interrupt_after:
                interrupted = True
                break
        return MigrationRunResult(
            workdir=self.workdir,
            last_step=self._state()["last_step"],
            interrupted=interrupted,
            exit_code=42 if interrupted else 0,
            artifact_hashes=hashes,
            wall_seconds=time.monotonic() - started,
        )

    def resume(self) -> MigrationRunResult:
        return self.run(interrupt_after=None)

    def rollback(self) -> dict[str, Any]:
        snapshot = self.snapshot_dir / "source-pre.db"
        if not snapshot.is_file():
            raise FileNotFoundError("rollback snapshot missing; run PREPARE first")
        before = self.source_sha256
        shutil.copy2(snapshot, self.source)
        after = self.source_sha256
        rollback_dir = self.workdir / "rollback"
        if rollback_dir.exists():
            shutil.rmtree(rollback_dir)
        shutil.copytree(self.migrated_dir, rollback_dir)
        report = {
            "source_before_sha256": before,
            "source_after_sha256": after,
            "restored_byte_identical": before == after,
            "migrated_archived_to": str(rollback_dir),
            "previous_release_readable": self._previous_release_readable(),
        }
        _write_json(self.workdir / "rollback_report.json", report)
        return report

    def _previous_release_readable(self) -> bool:
        try:
            reader = LegacyEventsReader(self.source, migration_version=self.migration_version)
            rows = reader.read_rows()
            return bool(rows) and reader.to_ledger_event(rows[0]) is not None
        except (CompatReadError, sqlite3.Error, OSError):
            return False


def _run_fingerprint(artifact_hashes: Mapping[str, str]) -> str:
    return hashlib.sha256(canonical_bytes(dict(sorted(artifact_hashes.items())))).hexdigest()
