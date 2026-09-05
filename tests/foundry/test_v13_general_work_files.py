from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.connectors.general_work_files import (
    GeneralWorkFileConnector,
    InjectedGeneralWorkFileFailure,
)


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def test_inspect_and_search_are_bounded_and_ignore_internal_backups(
    tmp_path: Path,
) -> None:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "alpha.txt").write_text(
        "owner signal and supporting text", encoding="utf-8"
    )
    (tmp_path / "inputs" / "beta.log").write_text(
        "unrelated", encoding="utf-8"
    )
    internal = tmp_path / ".jarvis-general-work" / "backups"
    internal.mkdir(parents=True)
    (internal / "secret.txt").write_text("owner signal", encoding="utf-8")
    connector = GeneralWorkFileConnector(tmp_path)

    inspected = connector.inspect_file("inputs/alpha.txt", preview_bytes=12)
    searched = connector.search(
        start=".",
        name_pattern="*.txt",
        content_query="owner signal",
    )

    assert inspected["status"] == "COMPLETED"
    assert inspected["result"]["preview"] == "owner signal"
    assert searched["status"] == "COMPLETED"
    assert searched["readback"]["scanned_files"] == 2
    assert [
        match["identity"]["ref"] for match in searched["result"]["matches"]
    ] == ["inputs/alpha.txt"]


def test_copy_is_hash_guarded_and_refuses_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("captured", encoding="utf-8")
    connector = GeneralWorkFileConnector(tmp_path)
    expected = _sha256(b"captured")

    receipt = connector.copy_file(
        "input.txt",
        "delivery/copied.txt",
        expected_source_hash=expected,
        idempotency_key="copy-1",
    )
    overwrite = connector.copy_file(
        "input.txt",
        "delivery/copied.txt",
        expected_source_hash=expected,
        idempotency_key="copy-2",
    )

    assert receipt["status"] == "COMPLETED"
    assert receipt["target_identity"]["sha256"] == expected
    assert receipt["readback"]["source"]["sha256"] == expected
    assert overwrite["status"] == "FAILED"
    assert overwrite["error"]["classification"] == "target_exists"


def test_copy_fails_closed_when_source_changed(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("changed", encoding="utf-8")
    connector = GeneralWorkFileConnector(tmp_path)

    receipt = connector.copy_file(
        "input.txt",
        "delivery/copied.txt",
        expected_source_hash=_sha256(b"captured"),
        idempotency_key="copy-stale",
    )

    assert receipt["status"] == "FAILED"
    assert receipt["error"]["classification"] == "source_hash_mismatch"
    assert not (tmp_path / "delivery" / "copied.txt").exists()


def test_move_and_rename_remove_the_source_after_readback(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    first.write_text("move me", encoding="utf-8")
    connector = GeneralWorkFileConnector(tmp_path)
    expected = _sha256(b"move me")

    moved = connector.move_file(
        "first.txt",
        "staging/second.txt",
        expected_source_hash=expected,
        idempotency_key="move-1",
    )
    renamed = connector.rename_file(
        "staging/second.txt",
        "delivery/final.txt",
        expected_source_hash=expected,
        idempotency_key="rename-1",
    )

    assert moved["status"] == "COMPLETED"
    assert moved["readback"]["source"]["exists"] is False
    assert renamed["status"] == "COMPLETED"
    assert renamed["readback"]["source"]["exists"] is False
    assert (tmp_path / "delivery" / "final.txt").read_text(
        encoding="utf-8"
    ) == "move me"


@pytest.mark.parametrize(
    ("operation", "method_name"),
    (
        ("file.copy", "copy_file"),
        ("file.move", "move_file"),
        ("file.rename", "rename_file"),
    ),
)
def test_transfer_fault_after_publish_reconciles_only_matching_key(
    tmp_path: Path,
    operation: str,
    method_name: str,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("landed once", encoding="utf-8")
    target_ref = "delivery/landed.txt"
    expected = _sha256(b"landed once")

    def fail(stage: str) -> None:
        if stage == f"{operation}.after_publish":
            raise InjectedGeneralWorkFileFailure("simulated caller restart")

    first_connector = GeneralWorkFileConnector(tmp_path, fault_injector=fail)
    first = getattr(first_connector, method_name)(
        "source.txt",
        target_ref,
        expected_source_hash=expected,
        idempotency_key="transfer-replay",
    )

    assert first["status"] == "UNKNOWN"
    assert first["readback"]["target"]["sha256"] == expected

    replay = getattr(GeneralWorkFileConnector(tmp_path), method_name)(
        "source.txt",
        target_ref,
        expected_source_hash=expected,
        idempotency_key="transfer-replay",
    )
    unrelated = getattr(GeneralWorkFileConnector(tmp_path), method_name)(
        "source.txt",
        target_ref,
        expected_source_hash=expected,
        idempotency_key="different-transfer",
    )

    assert replay["status"] == "COMPLETED"
    assert replay["replayed"] is True
    assert replay["reconciled"] is True
    assert replay["readback"]["target"]["sha256"] == expected
    assert unrelated["status"] == "FAILED"
    assert unrelated["error"]["classification"] == "target_exists"


def test_safe_edit_preserves_original_and_reconciles_replay(
    tmp_path: Path,
) -> None:
    target = tmp_path / "brief.txt"
    target.write_text("original", encoding="utf-8")
    connector = GeneralWorkFileConnector(tmp_path)
    expected = _sha256(b"original")

    first = connector.safe_text_edit(
        "brief.txt",
        "reconstructed",
        expected_source_hash=expected,
        idempotency_key="edit-1",
    )
    replay = connector.safe_text_edit(
        "brief.txt",
        "reconstructed",
        expected_source_hash=expected,
        idempotency_key="edit-1",
    )

    backup = tmp_path / first["result"]["backup_ref"]
    assert first["status"] == "COMPLETED"
    assert target.read_text(encoding="utf-8") == "reconstructed"
    assert backup.read_text(encoding="utf-8") == "original"
    assert replay["status"] == "COMPLETED"
    assert replay["replayed"] is True
    assert replay["reconciled"] is True


def test_safe_edit_fault_after_publish_is_unknown_with_reconcilable_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "brief.txt"
    target.write_text("original", encoding="utf-8")

    def fail(stage: str) -> None:
        if stage == "file.safe_edit.after_publish":
            raise InjectedGeneralWorkFileFailure("simulated restart")

    connector = GeneralWorkFileConnector(tmp_path, fault_injector=fail)
    expected = _sha256(b"original")
    receipt = connector.safe_text_edit(
        "brief.txt",
        "reconstructed",
        expected_source_hash=expected,
        idempotency_key="edit-fault",
    )

    assert receipt["status"] == "UNKNOWN"
    assert receipt["error"]["classification"] == "partial_failure"
    assert receipt["readback"]["target"]["sha256"] == _sha256(b"reconstructed")
    assert receipt["readback"]["backup"]["sha256"] == expected


def test_paths_cannot_escape_or_enter_connector_backup_state(
    tmp_path: Path,
) -> None:
    connector = GeneralWorkFileConnector(tmp_path)

    traversal = connector.inspect_file("../outside.txt")
    internal = connector.inspect_file(".jarvis-general-work")

    assert traversal["status"] == "FAILED"
    assert traversal["error"]["classification"] == "path_outside_root"
    assert internal["status"] == "FAILED"
    assert internal["error"]["classification"] == "reserved_path"


@pytest.mark.parametrize(
    "reference",
    (
        "ordinary.txt:hidden-stream",
        "dir:name/file.txt",
        "C:relative.txt",
    ),
)
def test_paths_reject_drive_and_stream_components(
    tmp_path: Path,
    reference: str,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("bounded", encoding="utf-8")
    connector = GeneralWorkFileConnector(tmp_path)

    inspected = connector.inspect_file(reference)
    copied = connector.copy_file(
        "source.txt",
        reference,
        expected_source_hash=_sha256(b"bounded"),
        idempotency_key=f"stream-refused:{reference}",
    )

    assert inspected["status"] == "FAILED"
    assert inspected["error"]["classification"] == "invalid_input"
    assert copied["status"] == "FAILED"
    assert copied["error"]["classification"] == "invalid_input"
    assert not (tmp_path / reference.split(":", 1)[0]).exists()
