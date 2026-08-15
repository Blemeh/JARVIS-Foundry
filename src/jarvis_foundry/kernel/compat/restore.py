"""Recovery-unit capture and full-restoration verification on copies."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as ET

from jarvis_foundry.kernel.compat.readers import LegacyEventsReader

RESTORE_POINT_SCHEMA = "jarvis-r5-restore-point-v1"


@dataclass(frozen=True)
class ComponentSpec:
    name: str
    kind: str
    source: Path
    required: bool = True


@dataclass(frozen=True)
class ComponentEntry:
    name: str
    kind: str
    file_name: str
    sha256: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_name": self.file_name,
            "sha256": self.sha256,
            "size": self.size,
        }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_restore_point(
    specs: Iterable[ComponentSpec],
    point_dir: str | Path,
) -> dict[str, Any]:
    """Copy every component into a self-contained restore point."""
    point_dir = Path(point_dir)
    point_dir.mkdir(parents=True, exist_ok=True)
    entries: list[ComponentEntry] = []
    for spec in specs:
        if not spec.source.is_file():
            if spec.required:
                raise FileNotFoundError(f"required component missing: {spec.source}")
            continue
        destination = point_dir / spec.source.name
        shutil.copy2(spec.source, destination)
        entries.append(
            ComponentEntry(
                name=spec.name,
                kind=spec.kind,
                file_name=spec.source.name,
                sha256=_file_sha256(destination),
                size=destination.stat().st_size,
            )
        )
    entries.sort(key=lambda item: item.name)
    manifest = {
        "schema": RESTORE_POINT_SCHEMA,
        "components": [entry.to_dict() for entry in entries],
        "aggregate_sha256": _aggregate_hash(entries),
    }
    manifest_path = point_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _aggregate_hash(entries: Iterable[ComponentEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.name):
        digest.update(entry.name.encode("utf-8"))
        digest.update(entry.sha256.encode("utf-8"))
        digest.update(str(entry.size).encode("utf-8"))
    return digest.hexdigest()


def restore_point(
    point_dir: str | Path,
    target_dir: str | Path,
) -> dict[str, Any]:
    """Restore every component and verify hashes and readability rules."""
    point_dir = Path(point_dir)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = point_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != RESTORE_POINT_SCHEMA:
        raise ValueError(f"unsupported restore point schema: {manifest.get('schema')}")
    restored: list[dict[str, Any]] = []
    for component in manifest["components"]:
        source = point_dir / component["file_name"]
        destination = target_dir / component["file_name"]
        shutil.copy2(source, destination)
        sha256 = _file_sha256(destination)
        restored.append(
            {
                "name": component["name"],
                "kind": component["kind"],
                "file_name": component["file_name"],
                "hash_matches": sha256 == component["sha256"],
                "sha256": sha256,
                "expected_sha256": component["sha256"],
                "size_matches": destination.stat().st_size == component["size"],
                "validation": _validate_component(
                    component["kind"], destination, component["name"]
                ),
            }
        )
    all_match = all(item["hash_matches"] and item["size_matches"] for item in restored)
    validations_ok = all(item["validation"]["ok"] for item in restored)
    return {
        "schema": "jarvis-r5-restore-report-v1",
        "aggregate_expected": manifest["aggregate_sha256"],
        "aggregate_actual": _aggregate_hash(
            ComponentEntry(
                name=item["name"],
                kind=item["kind"],
                file_name=item["file_name"],
                sha256=item["sha256"],
                size=Path(target_dir / item["file_name"]).stat().st_size,
            )
            for item in restored
        ),
        "restored_components": restored,
        "all_hash_matches": all_match,
        "all_validations_ok": validations_ok,
        "restore_verified": all_match and validations_ok,
    }


def _validate_component(kind: str, path: Path, name: str) -> dict[str, Any]:
    try:
        if kind in {"database", "wal_safe_database"}:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            finally:
                connection.close()
            ok = integrity == "ok"
            return {
                "ok": ok,
                "detail": f"integrity={integrity}, events={events}",
            }
        if kind == "bundle":
            header = path.read_bytes()[:4]
            ok = header.startswith((b"v2", b"#"))
            return {"ok": ok, "detail": f"git bundle magic present: {header[:4]!r}"}
        if kind == "config":
            text = path.read_text(encoding="utf-8")
            ok = ":" in text or text.strip() == ""
            return {"ok": ok, "detail": "config file readable"}
        if kind == "task":
            raw = path.read_bytes()
            decoded = raw.decode("utf-16")
            ET.fromstring(decoded)
            return {"ok": True, "detail": "UTF-16 XML parses"}
        if kind == "secrets_metadata":
            payload = json.loads(path.read_text(encoding="utf-8"))
            ok = "values_copied" in payload or "key_names" in payload
            return {"ok": ok, "detail": "metadata-only secret record"}
        if kind == "evidence":
            json.loads(path.read_text(encoding="utf-8"))
            return {"ok": True, "detail": "evidence JSON parses"}
        if kind == "freeze":
            return {
                "ok": path.read_text(encoding="utf-8").strip() != "",
                "detail": "freeze readable",
            }
        return {"ok": path.is_file(), "detail": "file restored"}
    except (
        ValueError,
        TypeError,
        sqlite3.Error,
        OSError,
        ET.ParseError,
        UnicodeDecodeError,
    ):
        return {"ok": False, "detail": "validation failed"}


def previous_release_readable(database_path: str | Path) -> dict[str, Any]:
    """Verify the previous (legacy V7) release can still read the restored DB."""
    database_path = Path(database_path)
    reader = LegacyEventsReader(database_path, migration_version=32)
    rows = reader.read_rows()
    connection = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        max_version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
    finally:
        connection.close()
    readable = bool(rows) and reader.to_ledger_event(rows[0]) is not None
    return {
        "readable": readable,
        "event_count": len(rows),
        "max_schema_migration": int(max_version),
    }
