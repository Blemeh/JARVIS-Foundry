from __future__ import annotations

from pathlib import Path

from conftest import make_legacy_db, sample_rows

from jarvis_foundry.kernel.compat.migrations import MIGRATION_STEPS, MigrationRehearsal


def _rehearsal(tmp_path: Path, name: str) -> tuple[Path, MigrationRehearsal]:
    source = make_legacy_db(tmp_path / f"{name}-source.db", sample_rows())
    workdir = tmp_path / name
    return source, MigrationRehearsal(source, workdir)


def test_fresh_runs_are_idempotent(tmp_path: Path) -> None:
    _, first = _rehearsal(tmp_path, "run-a")
    result_a = first.run()
    assert result_a.exit_code == 0
    assert result_a.last_step == "COMPLETE"

    source_b, second = _rehearsal(tmp_path, "run-b")
    assert source_b.read_bytes() == first.source.read_bytes()
    result_b = second.run()
    assert result_b.artifact_hashes == result_a.artifact_hashes


def test_interruption_at_every_step_resumes_identically(tmp_path: Path) -> None:
    _, baseline_run = _rehearsal(tmp_path, "baseline")
    baseline = baseline_run.run()
    assert baseline.last_step == "COMPLETE"

    for step in MIGRATION_STEPS:
        _, interrupted = _rehearsal(tmp_path, f"interrupt-{step.lower()}")
        result = interrupted.run(interrupt_after=step)
        assert result.interrupted
        assert result.exit_code == 42
        assert result.last_step == step
        resumed = interrupted.resume()
        assert resumed.exit_code == 0
        assert resumed.last_step == "COMPLETE"
        assert resumed.artifact_hashes == baseline.artifact_hashes


def test_rollback_restores_previous_release(tmp_path: Path) -> None:
    source, rehearsal = _rehearsal(tmp_path, "rollback")
    original = source.read_bytes()
    rehearsal.run()
    report = rehearsal.rollback()
    assert report["restored_byte_identical"]
    assert source.read_bytes() == original
    assert report["previous_release_readable"]


def test_source_copy_never_modified_by_migration(tmp_path: Path) -> None:
    source, rehearsal = _rehearsal(tmp_path, "no-mutation")
    before = source.read_bytes()
    rehearsal.run()
    after = source.read_bytes()
    assert before == after
    assert not list(source.parent.glob(f"{source.name}-wal"))
    assert not list(source.parent.glob(f"{source.name}-shm"))
