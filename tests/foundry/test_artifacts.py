"""WP1 atomic evidence artifact hashing and storage tests."""

from __future__ import annotations

import pytest

from jarvis_foundry.artifacts import (
    UnsafeArtifactName,
    sha256_bytes,
    sha256_file,
    store_artifact,
    validate_artifact_name,
    verify_artifact,
)


def test_store_artifact_writes_bytes_hash_sidecar_and_record(tmp_path):
    data = b"evidence artifact bytes\n" * 100
    record = store_artifact(tmp_path, "bundle-1", "test-report.txt", data)

    assert record.path == tmp_path / "bundle-1" / "test-report.txt"
    assert record.path.read_bytes() == data
    assert record.size_bytes == len(data)
    assert record.sha256 == sha256_bytes(data)
    assert verify_artifact(record.path, record.sha256) is True

    sidecar = tmp_path / "bundle-1" / "test-report.txt.sha256"
    assert sidecar.read_text(encoding="utf-8") == f"{record.sha256}  test-report.txt\n"


def test_store_artifact_overwrites_atomically(tmp_path):
    record = store_artifact(tmp_path, "bundle-1", "report.json", b"old")
    replaced = store_artifact(tmp_path, "bundle-1", "report.json", b"new content")

    assert replaced.path.read_bytes() == b"new content"
    assert replaced.sha256 == sha256_bytes(b"new content")
    # sidecar tracks the new bytes
    sidecar = tmp_path / "bundle-1" / "report.json.sha256"
    assert sidecar.read_text(encoding="utf-8").startswith(replaced.sha256)
    assert record.path == replaced.path


def test_verify_artifact_rejects_tampered_content(tmp_path):
    record = store_artifact(tmp_path, "bundle-1", "evidence.bin", b"original")
    record.path.write_bytes(b"tampered")
    assert verify_artifact(record.path, record.sha256) is False


def test_sha256_file_matches_sha256_bytes(tmp_path):
    data = b"x" * 4096
    path = tmp_path / "blob.bin"
    path.write_bytes(data)
    assert sha256_file(path) == sha256_bytes(data)


def test_unsafe_artifact_names_are_rejected(tmp_path):
    for bad in (
        "",
        "a/b.txt",
        "a\\b.txt",
        "..",
        "..\\evil.txt",
        "../evil.txt",
        "C:evil.txt",
        "c:\\windows\\system32\\x",
        ".hidden",
        "name with spaces.txt",
    ):
        with pytest.raises(UnsafeArtifactName):
            store_artifact(tmp_path, "bundle-1", bad, b"data")


def test_unsafe_bundle_ids_are_rejected(tmp_path):
    for bad in (
        "",
        "bundle/../escape",
        "bundle\\..\\escape",
        "..",
        ".",
        "C:evil",
        "c:\\windows\\x",
        "with space",
        "a/b",
    ):
        with pytest.raises(UnsafeArtifactName):
            store_artifact(tmp_path, bad, "ok.txt", b"data")
    # nothing may be written outside the base dir
    assert list(tmp_path.iterdir()) == []


def test_bundle_id_is_scoped_to_single_directory(tmp_path):
    record = store_artifact(tmp_path, "bundle-1", "ok.txt", b"data")
    assert record.path.parent == (tmp_path / "bundle-1").resolve()
    assert record.bundle_id == "bundle-1"


def test_artifact_stays_below_base_dir(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "var" / "evidence"
    record = store_artifact(base, "bundle-1", "report.txt", b"data")
    assert record.path.is_relative_to(base)
    assert record.path.parent == base / "bundle-1"
    assert not (outside / "report.txt").exists()
