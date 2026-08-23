"""V6 lane JOB: candidate-builder hardening — adversarial attacks.

Attacks the S3 builder where its existing 18 tests do not:

- corrupt/unparseable build marker (crashed executor mid-write) must
  be reaped, not crash the next build;
- pre-existing pollution inside the root BEFORE the first build must
  NOT be attributed to the candidate (artifact manifest starts clean);
- a build that MODIFIES a sibling file in the parent directory (not
  just creating new entries) is an outside write and must fail;
- duplicate builds of the same improvement are independent attempts;
  each records its own lifecycle and the ledger stays consistent.

All attacks run against disposable roots; no live surface is touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.builder import (
    MARKER_NAME,
    BuilderError,
    build_candidate,
    candidate_builds,
    register_candidate_root,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.reconcile import reconcile

PY = sys.executable


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db,
        problem="suite runtime exceeds budget",
        evidence_refs=["e"],
        provenance={"source": "test"},
        hypothesis="h",
        expected_mechanism="m",
        baseline_metric={"name": "suite_runtime_s", "value": 313.92,
                         "unit": "seconds", "source": "e"},
        mmi={"minimum_delta": 30.0, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry", "hash": "b" * 64},
        observation={"requirement": "3 runs", "cycles": 3},
        declared_risk="R2",
        surfaces=["tests/foundry", "pyproject.toml"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


_OK_SCRIPT = (
    "import pathlib\n"
    "pathlib.Path('out.txt').write_text('built ok')\n"
)


# ---------------------------------------------------------------------------
# attacks
# ---------------------------------------------------------------------------

def test_corrupt_marker_is_reaped_not_fatal(db: FoundryDatabase,
                                            tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    root = register_candidate_root(db, tmp_path / "root-corrupt",
                                   controller="supervisor")
    # simulate a crashed executor that died mid-marker-write
    root.joinpath(MARKER_NAME).write_text("{not json at all",
                                          encoding="utf-8")
    build_id = build_candidate(
        db, improvement_id=improvement_id,
        build_command=[PY, "-c", _OK_SCRIPT], candidate_root=root,
        controller="supervisor")
    builds = {b["build_id"]: b for b in candidate_builds(db)}
    assert builds[build_id]["status"] == "COMPLETED"
    assert not root.joinpath(MARKER_NAME).exists()


def test_preexisting_pollution_not_attributed_to_candidate(
        db: FoundryDatabase, tmp_path: Path) -> None:
    _improvement(db)
    polluted = tmp_path / "root-polluted"
    polluted.mkdir()
    polluted.joinpath("stale-from-previous-life.txt").write_text(
        "old bytes", encoding="utf-8")
    # a polluted root is REFUSED at registration: candidate provenance
    # must start clean, so stale files can never enter the release
    # manifest attributed to the candidate
    with pytest.raises(BuilderError, match="not empty"):
        register_candidate_root(db, polluted, controller="supervisor")


def test_sibling_modification_detected_as_outside_write(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    parent = tmp_path / "parent-mod"
    parent.mkdir()
    root = register_candidate_root(db, parent / "root",
                                   controller="supervisor")
    sibling = parent / "sibling.txt"
    sibling.write_text("original", encoding="utf-8")
    mutator = (
        "import pathlib\n"
        "pathlib.Path('../sibling.txt').write_text('tampered')\n"
    )
    build_id = build_candidate(
        db, improvement_id=improvement_id,
        build_command=[PY, "-c", mutator], candidate_root=root,
        controller="supervisor")
    builds = {b["build_id"]: b for b in candidate_builds(db)}
    row = builds[build_id]
    assert row["status"] == "FAILED"
    reason = row.get("reason") or ""
    assert "outside write" in reason or "sibling" in reason, \
        f"sibling modification not detected: {reason!r}"
    assert sibling.read_text(encoding="utf-8") == "tampered"  # damage done,
    # but the build FAILED and the artifact manifest was never recorded


def test_duplicate_build_attempts_are_independent(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    root = register_candidate_root(db, tmp_path / "root-dup",
                                   controller="supervisor")
    first = build_candidate(db, improvement_id=improvement_id,
                            build_command=[PY, "-c", _OK_SCRIPT],
                            candidate_root=root, controller="supervisor")
    second = build_candidate(db, improvement_id=improvement_id,
                             build_command=[PY, "-c", _OK_SCRIPT],
                             candidate_root=root, controller="supervisor")
    assert first != second
    builds = candidate_builds(db)
    assert len([b for b in builds
                if b["improvement_id"] == improvement_id]) == 2
    report = reconcile(db)
    assert report.consistent
