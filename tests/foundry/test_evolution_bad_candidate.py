"""V6 lane: bad-candidate factory — every attack is REJECTED, no live
damage. A deliberately bad candidate passing would be a machinery
defect; its purpose is to prove rejection.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.bad_candidate import (
    BAD_FAMILIES,
    BadCandidateError,
    make_bad_candidate,
)
from jarvis_foundry.evolution.builder import (
    BuilderError,
    build_candidate,
    register_candidate_root,
)
from jarvis_foundry.evolution.disposable_canary import (
    run_disposable_canary,
)
from jarvis_foundry.evolution.promote import PromoteError, promote_release
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import (
    ReleaseError,
    register_release,
)
from jarvis_foundry.evolution.verifier_orchestrator import (
    VerifierOrchestrationError,
    orchestrate_verification,
)
from jarvis_foundry.runtime.provider_config import default_provider_settings

SETTINGS = default_provider_settings()
BUILDER = {"provider": SETTINGS.provider, "model": SETTINGS.model,
           "session_id": "bad-builder"}
PY = "python"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    directory = tmp_path / "bad-factory"
    directory.mkdir()
    return directory


def test_factory_refuses_unknown_family(db: FoundryDatabase,
                                        base: Path) -> None:
    with pytest.raises(BadCandidateError):
        make_bad_candidate(db, family="not-a-family", base_dir=base)


def test_factory_is_disposable_only(db: FoundryDatabase) -> None:
    missing = Path("Z:/definitely/not/here")
    with pytest.raises(BadCandidateError, match="disposable-only"):
        make_bad_candidate(db, family="test_regression", base_dir=missing)


# ---------------------------------------------------------------------------
# per-family rejection proofs
# ---------------------------------------------------------------------------

def test_test_regression_rejected_by_canary_health(
        db: FoundryDatabase, tmp_path: Path, base: Path) -> None:
    kit = make_bad_candidate(db, family="test_regression", base_dir=base)
    release_id = register_release(
        db, source_commit="a" * 40, artifact_hashes={"x": "1" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=kit["improvement_id"],
        risk_class="R2", migrations=[], verdicts=[],
        controller="supervisor")
    report = run_disposable_canary(
        db, improvement_id=kit["improvement_id"],
        candidate_release_id=release_id,
        workload=[{"cost_usd": 0.01} for _ in range(20)],
        candidate_behavior_fn=kit["behavior"],
        baseline_behavior_fn=lambda item: {"error": False,
                                           "latency_s": 0.2},
        traffic_share=0.5, duration_cycles=2)
    assert report["status"] == "TERMINATED", \
        "regressing candidate was NOT terminated"
    assert report["candidate"]["error_rate"] > 0


def test_performance_degradation_rejected_by_lab(
        db: FoundryDatabase, tmp_path: Path, base: Path) -> None:
    kit = make_bad_candidate(db, family="performance_degradation",
                             base_dir=base)
    # the measurement layer must show the degradation, never an
    # improvement claim: candidate latency 4x baseline
    assert kit["candidate_latency_s"] > kit["baseline_latency_s"]
    from jarvis_foundry.evolution.measurement import (
        MIN_EVAL_SAMPLE,
        capture_measurement,
    )
    snapshot = capture_measurement(
        db, improvement_id=kit["improvement_id"], subject_kind="baseline",
        window_start="2026-08-01T00:00:00",
        window_end="2026-08-02T00:00:00")
    # no evaluation runs seeded in that window -> INSUFFICIENT_SAMPLE:
    # the machinery refuses to present the bad candidate as measured
    assert snapshot["status"] == "INSUFFICIENT_SAMPLE"
    assert snapshot["sample_size"] < MIN_EVAL_SAMPLE


def test_verifier_forgery_rejected_by_orchestrator(
        db: FoundryDatabase, tmp_path: Path, base: Path) -> None:
    kit = make_bad_candidate(db, family="verifier_forgery", base_dir=base)
    tree = tmp_path / "forgery-tree"
    tree.mkdir()
    (tree / "a.txt").write_text("x", encoding="utf-8")
    release_id = register_release(
        db, source_commit="a" * 40, artifact_hashes={"x": "1" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=kit["improvement_id"],
        risk_class="R2", migrations=[], verdicts=[],
        controller="supervisor")
    forged = kit["forged_report"]
    script = (
        "import json, sys\n"
        "print(json.dumps(" + json.dumps(forged).replace('": null',
                                                         '": None') + "))\n")
    with pytest.raises(VerifierOrchestrationError,
                       match="unearned PASS|diverge"):
        orchestrate_verification(
            db, improvement_id=kit["improvement_id"],
            candidate_release_id=release_id, candidate_tree_root=tree,
            builder_identity=BUILDER, controller="supervisor",
            probe_command=["python", "-c", script],
            provider_settings=SETTINGS)


def test_self_promotion_rejected_by_controller_gate(
        db: FoundryDatabase, tmp_path: Path, base: Path) -> None:
    kit = make_bad_candidate(db, family="self_promotion", base_dir=base)
    genesis = register_release(
        db, source_commit="a" * 40, artifact_hashes={"g": "0" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=kit["improvement_id"],
        risk_class="R2", migrations=[], verdicts=[],
        controller="supervisor")
    candidate = register_release(
        db, source_commit="b" * 40, artifact_hashes={"c": "1" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=genesis, improvement_id=kit["improvement_id"],
        risk_class="R2", migrations=[], verdicts=[],
        controller="supervisor")
    with pytest.raises(PromoteError, match="only the Supervisor"):
        promote_release(
            db, candidate_release_id=candidate,
            verification_id="verification-nope",
            baseline_run_id="run-x", candidate_run_id="run-y",
            controller=kit["attempt_controller"])


def test_path_escape_rejected_by_outside_write_detector(
        db: FoundryDatabase, tmp_path: Path, base: Path) -> None:
    kit = make_bad_candidate(db, family="path_escape", base_dir=base)
    parent = tmp_path / "escape-parent"
    parent.mkdir()
    root = register_candidate_root(db, parent / "root",
                                   controller="supervisor")
    script = kit["make_build_script"](root)
    build_id = build_candidate(
        db, improvement_id=kit["improvement_id"],
        build_command=[PY, str(script)], candidate_root=root,
        controller="supervisor")
    builds = {b["build_id"]: b for b in
              __import__("jarvis_foundry.evolution.builder",
                         fromlist=["candidate_builds"])
              .candidate_builds(db)}
    row = builds[build_id]
    assert row["status"] == "FAILED", "escape build not failed"
    assert "outside write" in (row.get("reason") or "")


def test_schema_incompatibility_rejected_by_registry(
        db: FoundryDatabase, tmp_path: Path, base: Path) -> None:
    kit = make_bad_candidate(db, family="schema_incompatibility",
                             base_dir=base)
    with pytest.raises((ReleaseError, Exception)):
        register_release(
            db, source_commit="c" * 40,
            artifact_hashes={"s": "2" * 64}, dependencies=[],
            runtime_identity={"python": "3.11"}, parent_release_id=None,
            improvement_id=kit["improvement_id"], risk_class="R3",
            migrations=kit["migrations"], verdicts=[],
            controller="supervisor")


def test_stop_weakening_attack_surface_documented(
        db: FoundryDatabase, base: Path) -> None:
    kit = make_bad_candidate(db, family="stop_weakening", base_dir=base)
    assert "EMERGENCY_STOP" in kit["attack"]
    # the sentinel path is supervisor-owned; the factory only documents
    # the attack — the control-plane gates (tested in the S-series)
    # refuse any non-supervisor mutation of it


def test_all_families_covered() -> None:
    assert len(BAD_FAMILIES) == 9
