from __future__ import annotations

from pathlib import Path

from conftest import make_legacy_db, sample_rows

from jarvis_foundry.kernel.compat.cutover import rehearse_dual_run


def test_dual_run_is_lossless_and_duplicate_free(tmp_path: Path) -> None:
    legacy = make_legacy_db(tmp_path / "legacy.db", sample_rows())
    report = rehearse_dual_run(
        legacy,
        tmp_path / "r4.db",
        synthetic_count=3,
        ledger_workdir=tmp_path / "cutover",
    )
    assert report.lossless
    assert report.duplicate_free
    assert report.reconciliation_clean
    assert report.chain_valid
    assert report.legacy_count == 7
    assert report.r4_count == 7
    assert report.matched_ids == 7
    assert report.legacy_only_ids == ()
    assert report.r4_only_ids == ()


def test_cutover_rehearsal_is_idempotent(tmp_path: Path) -> None:
    first_legacy = make_legacy_db(tmp_path / "first.db", sample_rows())
    first = rehearse_dual_run(
        first_legacy,
        tmp_path / "first-r4.db",
        synthetic_count=2,
        ledger_workdir=tmp_path / "first-cutover",
    )
    second_legacy = make_legacy_db(tmp_path / "second.db", sample_rows())
    second = rehearse_dual_run(
        second_legacy,
        tmp_path / "second-r4.db",
        synthetic_count=2,
        ledger_workdir=tmp_path / "second-cutover",
    )
    assert first.idempotency_signature == second.idempotency_signature
    assert first.to_dict()["legacy_count"] == second.to_dict()["legacy_count"]
