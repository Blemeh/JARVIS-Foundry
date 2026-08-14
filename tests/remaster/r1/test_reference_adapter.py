from __future__ import annotations

import tracemalloc
from pathlib import Path

from acceptance import JOURNEY_IDS, formatted_json, run_external_adapter


EVALUATION_TIME = "2030-01-01T00:00:00Z"


def test_reference_adapter_passes_all_ten_only_as_harness_self_test(
    purpose_root: Path,
    reference_command: list[str],
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "reference-run"
    tracemalloc.start()
    try:
        result = run_external_adapter(reference_command, purpose_root, EVALUATION_TIME, run_root=run_root)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result["status"] == "PASS"
    assert result["proof_scope"] == "HARNESS_SELF_TEST"
    assert result["qualification"] == "HARNESS_SELF_TEST_ONLY"
    assert result["product_claim_status"] == "NOT_TESTED"
    assert result["findings"] == []
    assert [item["journey_id"] for item in result["journeys"]] == list(JOURNEY_IDS)
    assert all(item["status"] == "PASS" for item in result["journeys"])
    assert all(item["criterion_evidence"] for item in result["journeys"])
    assert sorted(path.name for path in run_root.iterdir()) == list(JOURNEY_IDS)
    for journey_id in JOURNEY_IDS:
        journey_root = run_root / journey_id
        assert (journey_root / "target-state.json").is_file()
        evidence_files = sorted(journey_root.glob("evidence-*.json"))
        assert len(evidence_files) >= 5
        assert all(path.stat().st_size > 0 for path in evidence_files)
    assert not list(tmp_path.rglob("*.db"))
    assert not list(tmp_path.rglob("__pycache__"))
    assert peak < 256 * 1024 * 1024


def test_identical_reference_runs_produce_byte_identical_verdicts(
    purpose_root: Path,
    reference_command: list[str],
    tmp_path: Path,
) -> None:
    first = run_external_adapter(reference_command, purpose_root, EVALUATION_TIME, run_root=tmp_path / "first")
    second = run_external_adapter(reference_command, purpose_root, EVALUATION_TIME, run_root=tmp_path / "second")
    assert formatted_json(first).encode("utf-8") == formatted_json(second).encode("utf-8")


def test_automatic_temporary_run_is_removed(
    purpose_root: Path,
    reference_command: list[str],
    tmp_path: Path,
) -> None:
    parent = tmp_path / "temporary-parent"
    result = run_external_adapter(
        reference_command,
        purpose_root,
        EVALUATION_TIME,
        run_root=None,
        temporary_parent=parent,
    )
    assert result["status"] == "PASS"
    assert result["run_confinement"] == {"temporary": True, "cleanup": "completed"}
    assert list(parent.iterdir()) == []
