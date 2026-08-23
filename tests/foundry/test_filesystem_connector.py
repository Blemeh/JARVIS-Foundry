"""V5-5 real filesystem connector vertical slice."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis_foundry.connectors.filesystem import (
    FilesystemConnector,
    FilesystemConnectorError,
)


def test_write_read_retry_restart_reconcile_and_rollback(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    connector = FilesystemConnector(root)

    before = connector.snapshot("notes.txt")
    written = connector.request(
        "file.write", "notes.txt", "hello", idempotency_key="write-1"
    )
    assert written.outcome == "COMPLETED"
    assert written.after_state["content_hash"] == connector.snapshot("notes.txt").content_hash
    mtime = (root / "notes.txt").stat().st_mtime_ns

    retry = connector.request(
        "file.write", "notes.txt", "hello", idempotency_key="write-1"
    )
    assert retry.outcome == "COMPLETED"
    assert retry.replayed is True
    assert (root / "notes.txt").stat().st_mtime_ns == mtime

    restarted = FilesystemConnector(root)
    replay_after_restart = restarted.request(
        "file.write", "notes.txt", "hello", idempotency_key="write-1"
    )
    assert replay_after_restart.replayed is True
    assert restarted.reconcile("notes.txt", expected_hash=written.after_state["content_hash"]).reconciled

    restored = restarted.rollback("notes.txt", before)
    assert restored.outcome == "COMPLETED"
    assert not (root / "notes.txt").exists()


def test_conflicting_retry_and_drift_are_not_silently_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    connector = FilesystemConnector(root)
    first = connector.request("file.write", "state.txt", "one", idempotency_key="same")
    conflict = connector.request("file.write", "state.txt", "two", idempotency_key="same")
    assert first.outcome == "COMPLETED"
    assert conflict.outcome == "FAILED"
    assert conflict.error_code == "idempotency_conflict"

    (root / "state.txt").write_text("tampered", encoding="utf-8")
    drift = FilesystemConnector(root).reconcile(
        "state.txt", expected_hash=first.after_state["content_hash"]
    )
    assert drift.outcome == "UNKNOWN"
    assert drift.reconciled is False
    assert drift.error_code == "state_drift"


def test_path_escape_symlink_and_reserved_ledger_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("owner", encoding="utf-8")
    connector = FilesystemConnector(root)

    with pytest.raises(FilesystemConnectorError, match="escapes"):
        connector.request("file.write", "../outside/secret.txt", "pwned", idempotency_key="escape")
    try:
        (root / "link.txt").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(FilesystemConnectorError, match="escapes"):
        connector.request("file.write", "link.txt", "pwned", idempotency_key="link")
    with pytest.raises(FilesystemConnectorError, match="reserved"):
        connector.request("file.write", ".jarvis-fs-idempotency.json", "pwned", idempotency_key="ledger")
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "owner"


def test_read_only_file_is_refused_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "readonly.txt"
    target.write_text("before", encoding="utf-8")
    target.chmod(0o444)
    connector = FilesystemConnector(root)
    try:
        result = connector.request("file.write", "readonly.txt", "after", idempotency_key="readonly")
        assert result.outcome == "FAILED"
        assert result.error_code == "read_only"
    finally:
        target.chmod(0o644)
    assert target.read_text(encoding="utf-8") == "before"

