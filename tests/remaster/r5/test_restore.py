from __future__ import annotations

import json
from pathlib import Path

from conftest import make_legacy_db, sample_rows

from jarvis_foundry.kernel.compat.restore import (
    ComponentSpec,
    create_restore_point,
    previous_release_readable,
    restore_point,
)


def _components(tmp_path: Path) -> list[ComponentSpec]:
    db = make_legacy_db(tmp_path / "component.db", sample_rows())
    config = tmp_path / "foundry-profile.yaml"
    config.write_text("profile: test\n", encoding="utf-8")
    task = tmp_path / "task.xml"
    task.write_text("<Task/>", encoding="utf-16")
    secrets = tmp_path / "secrets-metadata.json"
    secrets.write_text(
        json.dumps({"values_copied": False, "key_names": ["TEST_KEY"]}),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"schema": "test"}), encoding="utf-8")
    return [
        ComponentSpec(name="database", kind="database", source=db),
        ComponentSpec(name="config", kind="config", source=config),
        ComponentSpec(name="task", kind="task", source=task),
        ComponentSpec(name="secrets_metadata", kind="secrets_metadata", source=secrets),
        ComponentSpec(name="evidence", kind="evidence", source=evidence),
    ]


def test_restore_point_roundtrip(tmp_path: Path) -> None:
    point_dir = tmp_path / "point"
    target_dir = tmp_path / "restored"
    manifest = create_restore_point(_components(tmp_path), point_dir)
    assert manifest["schema"] == "jarvis-r5-restore-point-v1"
    report = restore_point(point_dir, target_dir)
    assert report["restore_verified"]
    assert report["all_hash_matches"]
    assert report["all_validations_ok"]


def test_previous_release_readable_after_restore(tmp_path: Path) -> None:
    point_dir = tmp_path / "point"
    target_dir = tmp_path / "restored"
    create_restore_point(_components(tmp_path), point_dir)
    restore_point(point_dir, target_dir)
    db_path = target_dir / "component.db"
    check = previous_release_readable(db_path)
    assert check["readable"]
    assert check["event_count"] == 4
    assert check["max_schema_migration"] == 32


def test_restore_detects_hash_mismatch(tmp_path: Path) -> None:
    point_dir = tmp_path / "point"
    create_restore_point(_components(tmp_path), point_dir)
    tampered = point_dir / "component.db"
    tampered.write_bytes(tampered.read_bytes() + b"tamper")
    report = restore_point(point_dir, tmp_path / "restored")
    assert not report["all_hash_matches"]
    assert not report["restore_verified"]
