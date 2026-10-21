"""Release retention: protection rules, fail-closed planning, safe apply."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jarvis_foundry.deployment.retention import (
    ReleaseEntry,
    apply_release_retention,
    plan_release_retention,
)


def sha(n: int) -> str:
    return f"{n:040x}"


def make_install(tmp_path: Path, count: int = 10, *, current: int = 9, previous: int = 8) -> Path:
    root = tmp_path / "JARVIS"
    (root / "releases").mkdir(parents=True)
    for n in range(count):
        release = root / "releases" / sha(n)
        (release / ".venv").mkdir(parents=True)
        (release / ".venv" / "marker.bin").write_bytes(b"x" * 100)
        (release / "RELEASE.json").write_text(json.dumps({
            "git_commit": sha(n),
            "created_at": f"2026-10-{n + 1:02d}T00:00:00Z",
        }), encoding="utf-8")
    (root / "CURRENT").write_text(sha(current) + "\n", encoding="utf-8")
    (root / "PREVIOUS").write_text(sha(previous) + "\n", encoding="utf-8")
    return root


def test_current_previous_and_newest_are_never_candidates(tmp_path) -> None:
    root = make_install(tmp_path)
    plan = plan_release_retention(root, keep_recent=2)
    assert plan.refusal is None
    candidate_shas = {entry.sha for entry in plan.candidates}
    assert sha(9) not in candidate_shas and sha(8) not in candidate_shas
    assert "CURRENT" in plan.protected[sha(9)]
    assert any("PREVIOUS" in reason for reason in plan.protected[sha(8)])
    # keep_recent=2 -> the two newest (9, 8) are already CURRENT/PREVIOUS; 0..7 are candidates
    assert candidate_shas == {sha(n) for n in range(8)}


def test_keep_recent_protects_extra_newest_releases(tmp_path) -> None:
    root = make_install(tmp_path)
    plan = plan_release_retention(root, keep_recent=4)
    assert {entry.sha for entry in plan.candidates} == {sha(n) for n in range(6)}
    assert any("most recent" in reason for reason in plan.protected[sha(7)])


def test_release_json_generation_rollback_and_database_references_are_protected(tmp_path) -> None:
    root = make_install(tmp_path)
    (root / "releases" / sha(9) / "RELEASE.json").write_text(json.dumps({
        "git_commit": sha(9), "created_at": "2026-10-10T00:00:00Z",
        "previous_runtime": {"git_commit": sha(1), "source_sha": sha(1)},
    }), encoding="utf-8")
    (root / "GENERATION.json").write_text(json.dumps({"previous_runtime": {"git_commit": sha(2)}}), encoding="utf-8")
    (root / "ROLLBACK_RUNTIME.json").write_text(json.dumps({"frozen": {"source_sha": sha(3)}}), encoding="utf-8")
    plan = plan_release_retention(root, keep_recent=0, database_active=[sha(4)], extra_protected=[sha(5)])
    assert {entry.sha for entry in plan.candidates} == {sha(0), sha(6), sha(7)}
    reasons = {key: " ".join(value) for key, value in plan.protected.items()}
    assert "previous_runtime" in reasons[sha(1)]
    assert "GENERATION.json" in reasons[sha(2)]
    assert "ROLLBACK_RUNTIME.json" in reasons[sha(3)]
    assert "database" in reasons[sha(4)]
    assert "explicitly" in reasons[sha(5)]


def test_planning_fails_closed_without_a_readable_current_pointer(tmp_path) -> None:
    root = make_install(tmp_path)
    (root / "CURRENT").write_text("not-a-sha", encoding="utf-8")
    plan = plan_release_retention(root)
    assert plan.refusal and "CURRENT" in plan.refusal
    assert plan.candidates == []
    with pytest.raises(ValueError, match="refused"):
        apply_release_retention(plan)


def test_non_release_folders_and_files_are_ignored(tmp_path) -> None:
    root = make_install(tmp_path, count=3, current=2, previous=1)
    (root / "releases" / "not-a-release").mkdir()
    (root / "releases" / "notes.txt").write_text("hi", encoding="utf-8")
    plan = plan_release_retention(root, keep_recent=0)
    assert {entry.sha for entry in plan.candidates} == {sha(0)}


def test_apply_deletes_only_candidates_and_writes_an_audit_record(tmp_path) -> None:
    root = make_install(tmp_path)
    plan = plan_release_retention(root, keep_recent=2)
    audit = apply_release_retention(plan, now=datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc))
    assert {p.name for p in (root / "releases").iterdir()} == {sha(8), sha(9)}
    assert sorted(audit["deleted"]) == sorted(sha(n) for n in range(8))
    assert audit["skipped"] == []
    assert (root / "CURRENT").read_text(encoding="utf-8").strip() == sha(9)
    record = json.loads((root / "logs" / "release-retention-20261021T120000Z.json").read_text(encoding="utf-8"))
    assert record["kind"] == "release_retention" and record["deleted"] == audit["deleted"]


def test_apply_is_refused_during_owner_stop(tmp_path) -> None:
    root = make_install(tmp_path)
    plan = plan_release_retention(root, keep_recent=2)
    with pytest.raises(RuntimeError, match="STOP"):
        apply_release_retention(plan, stop_active=True)
    assert len(list((root / "releases").iterdir())) == 10


def test_apply_revalidates_each_target_immediately_before_deleting(tmp_path) -> None:
    root = make_install(tmp_path)
    plan = plan_release_retention(root, keep_recent=2)
    # State changes between plan and apply: sha(0) becomes protected (e.g. promoted).
    plan.protected.setdefault(sha(0), []).append("promoted after planning")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    plan.candidates.append(ReleaseEntry(sha(0xAB), str(outside), "x"))
    audit = apply_release_retention(plan)
    assert sha(0) not in audit["deleted"]
    assert any(item["sha"] == sha(0) and item["reason"] == "protected" for item in audit["skipped"])
    assert outside.exists()
    assert any(item["sha"] == sha(0xAB) for item in audit["skipped"])


def test_measure_reports_candidate_bytes(tmp_path) -> None:
    root = make_install(tmp_path, count=4, current=3, previous=2)
    plan = plan_release_retention(root, keep_recent=0, measure=True)
    assert {entry.sha for entry in plan.candidates} == {sha(0), sha(1)}
    assert plan.candidate_bytes >= 2 * 100
