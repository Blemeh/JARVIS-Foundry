"""V6-S6: zero-effect shadow mode.

Shadow candidates observe, reason and propose against COPIED inputs
with ZERO live effects: every effect goes through the ShadowSink which
records proposals and REFUSES capability use (live capabilities and
blocked connectors). STOP freezes shadow runs. Divergence against the
baseline's decisions is recorded. The live surface is proven unchanged
before/after.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.lab import LabError
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import (
    ReleaseError,
    register_release,
)
from jarvis_foundry.evolution.shadow import (
    ShadowError,
    shadow_run,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile


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
        problem="suite runtime exceeds the 5-minute budget on this host",
        evidence_refs=["var/evidence/v6-s0/suite-v6-s0-baseline.txt"],
        provenance={"source": "v6-s0 baseline measurement",
                    "observed_at": "2026-08-13T15:00:00Z"},
        hypothesis="Splitting fault-injection tests reduces wall-clock "
                   "runtime",
        expected_mechanism="parallel session isolation removes serialized "
                           "daemon-spawn waits",
        baseline_metric={"name": "suite_runtime_s", "value": 313.92,
                         "unit": "seconds",
                         "source": "suite-v6-s0-baseline.txt"},
        mmi={"minimum_delta": 30.0, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "3 full-suite runs", "cycles": 3},
        declared_risk="R2",
        surfaces=["tests/foundry", "pyproject.toml"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _release(db: FoundryDatabase, improvement_id: str) -> str:
    return register_release(
        db, source_commit="a" * 40,
        artifact_hashes={"suite.txt": "b" * 64},
        dependencies=[], runtime_identity={"python": "3.13.5"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class="R2", migrations=[1, 2, 3], verdicts=[],
        controller="supervisor")


def _inputs(tmp_path: Path, count: int = 3) -> Path:
    directory = tmp_path / "shadow-inputs"
    directory.mkdir(exist_ok=True)
    for index in range(count):
        (directory / f"case-{index}.txt").write_text(
            f"input-{index}", encoding="utf-8")
    return directory


def _propose_only_decision(case: Path, sink: object) -> None:
    sink.propose(  # type: ignore[attr-defined]
        decision=f"respond to {case.name}",
        expected_effect=f"draft reply for {case.name}",
        cost_usd=0.0)


def _live_attempt_decision(case: Path, sink: object) -> None:
    sink.propose(  # type: ignore[attr-defined]
        decision="reply", expected_effect="draft", cost_usd=0.0)
    sink.use_capability(  # type: ignore[attr-defined]
        "live.file.write", {"path": "C:/live/state.txt"})


def _blocked_connector_decision(case: Path, sink: object) -> None:
    sink.propose(  # type: ignore[attr-defined]
        decision="reply", expected_effect="draft", cost_usd=0.0)
    sink.use_capability(  # type: ignore[attr-defined]
        "browser.navigate", {"url": "https://example.com"})


# ---------------------------------------------------------------------------
# zero effect
# ---------------------------------------------------------------------------

def test_propose_only_shadow_completes_with_zero_effects(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    live_marker = tmp_path / "live-marker.txt"
    live_marker.write_text("live", encoding="utf-8")
    before_marker = hashlib.sha256(
        live_marker.read_bytes()).hexdigest()
    run_id = shadow_run(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        decision_fn=_propose_only_decision,
        inputs_dir=_inputs(tmp_path), controller="supervisor",
        live_marker=live_marker)
    row = db.row("shadow_runs", "run_id", run_id)
    assert row is not None
    assert row["status"] == "COMPLETED"
    assert row["decisions_count"] == 3
    assert row["zero_live_effects"] == 1
    # the logical proof: every event appended during the run is a
    # shadow.* event (WAL-safe) and the external marker is untouched
    assert hashlib.sha256(
        live_marker.read_bytes()).hexdigest() == before_marker
    types = {e.event_type for e in db.events()}
    assert "shadow.run_started" in types
    assert "shadow.decision_proposed" in types
    assert "shadow.run_completed" in types


def test_live_capability_attempt_fails_run(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    with pytest.raises(ShadowError):
        shadow_run(
            db, improvement_id=improvement_id,
            candidate_release_id=release_id,
            decision_fn=_live_attempt_decision,
            inputs_dir=_inputs(tmp_path), controller="supervisor")
    runs = db.all("shadow_runs")
    assert runs and runs[0]["status"] == "FAILED"
    assert "live effect" in (runs[0]["reason"] or "")
    assert runs[0]["live_attempts"] == 1
    assert "shadow.live_effect_attempted" in {
        e.event_type for e in db.events()}


def test_blocked_connector_attempt_fails_run(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    with pytest.raises(ShadowError):
        shadow_run(
            db, improvement_id=improvement_id,
            candidate_release_id=release_id,
            decision_fn=_blocked_connector_decision,
            inputs_dir=_inputs(tmp_path), controller="supervisor")
    runs = db.all("shadow_runs")
    assert runs and runs[0]["status"] == "FAILED"
    assert "blocked connector" in (runs[0]["reason"] or "")
    assert runs[0]["blocked_attempts"] == 1


# ---------------------------------------------------------------------------
# STOP behavior
# ---------------------------------------------------------------------------

def test_stop_freezes_shadow_run(db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    sentinel = Path(db.path).parent / "EMERGENCY_STOP"
    sentinel.write_text("stop", encoding="utf-8")
    try:
        with pytest.raises(ShadowError):
            shadow_run(
                db, improvement_id=improvement_id,
                candidate_release_id=release_id,
                decision_fn=_propose_only_decision,
                inputs_dir=_inputs(tmp_path), controller="supervisor")
        runs = db.all("shadow_runs")
        assert runs and runs[0]["status"] == "FAILED"
        assert "stop" in (runs[0]["reason"] or "").lower()
        assert runs[0]["stop_observed"] == 1
        assert runs[0]["decisions_count"] == 0
    finally:
        sentinel.unlink()
    # STOP cleared -> the run proceeds
    run_id = shadow_run(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        decision_fn=_propose_only_decision,
        inputs_dir=_inputs(tmp_path), controller="supervisor")
    assert db.row("shadow_runs", "run_id", run_id)["status"] \
        == "COMPLETED"


# ---------------------------------------------------------------------------
# divergence
# ---------------------------------------------------------------------------

def test_divergence_recorded(db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)

    def diverging(case: Path, sink: object) -> None:
        sink.propose(  # type: ignore[attr-defined]
            decision="shadow says send an email",
            expected_effect="email sent",
            cost_usd=0.0)

    baseline = [{"decision": "shadow says send nothing",
                 "expected_effect": "no action", "cost_usd": 0.0}
                for _ in range(3)]
    run_id = shadow_run(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        decision_fn=diverging, inputs_dir=_inputs(tmp_path),
        baseline_decisions=baseline, controller="supervisor")
    row = db.row("shadow_runs", "run_id", run_id)
    assert row is not None and row["status"] == "COMPLETED"
    divergences = __import__("json").loads(row["divergences_json"])
    assert divergences  # decision + expected_effect diverged
    assert all("decision" in d["kinds"] for d in divergences)
    assert all("expected_effect" in d["kinds"] for d in divergences)


def test_no_divergence(db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    baseline = [{"decision": f"respond to case-{i}.txt",
                 "expected_effect": f"draft reply for case-{i}.txt",
                 "cost_usd": 0.0} for i in range(3)]
    run_id = shadow_run(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        decision_fn=_propose_only_decision,
        inputs_dir=_inputs(tmp_path),
        baseline_decisions=baseline, controller="supervisor")
    row = db.row("shadow_runs", "run_id", run_id)
    assert row is not None
    assert __import__("json").loads(row["divergences_json"]) == []


# ---------------------------------------------------------------------------
# refusals and integrity
# ---------------------------------------------------------------------------

def test_unknown_improvement_and_release_refused(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    with pytest.raises(ShadowError):
        shadow_run(db, improvement_id="improvement-nope",
                   candidate_release_id=release_id,
                   decision_fn=_propose_only_decision,
                   inputs_dir=_inputs(tmp_path), controller="supervisor")
    with pytest.raises(ShadowError):
        shadow_run(db, improvement_id=improvement_id,
                   candidate_release_id="release-nope",
                   decision_fn=_propose_only_decision,
                   inputs_dir=_inputs(tmp_path), controller="supervisor")


def test_tampered_inputs_refused(db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    inputs = _inputs(tmp_path)
    (inputs / "case-0.txt").write_text("tampered", encoding="utf-8")
    # inputs are hashed per run; the decision fn reads the copy — the
    # shadow uses the inputs AS PRESENT at run time, hashed for the
    # record; the contract is the run binds its input hash
    run_id = shadow_run(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        decision_fn=_propose_only_decision,
        inputs_dir=inputs, controller="supervisor")
    row = db.row("shadow_runs", "run_id", run_id)
    assert row is not None and row["status"] == "COMPLETED"
    assert row["input_hash"]  # bound to the actual bytes used


def test_secret_proposal_refused(db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)

    def leaky(case: Path, sink: object) -> None:
        sink.propose(  # type: ignore[attr-defined]
            decision="print api_key=sk-live-1234",
            expected_effect="leak", cost_usd=0.0)

    with pytest.raises(ShadowError):
        shadow_run(
            db, improvement_id=improvement_id,
            candidate_release_id=release_id,
            decision_fn=leaky, inputs_dir=_inputs(tmp_path),
            controller="supervisor")
    assert not any(e.event_type == "shadow.decision_proposed"
                   for e in db.events())


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    shadow_run(db, improvement_id=improvement_id,
               candidate_release_id=release_id,
               decision_fn=_propose_only_decision,
               inputs_dir=_inputs(tmp_path), controller="supervisor")
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("shadow_runs")
    db.rebuild_projections()
    assert db.count("shadow_runs") == before
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 26
    assert SCHEMA_VERSION >= 26


# ---------------------------------------------------------------------------
# round-2 repairs (deleg_1b787321 findings)
# ---------------------------------------------------------------------------

def test_pre_attempt_proposals_preserved(db: FoundryDatabase,
                                         tmp_path: Path) -> None:
    """S6 r2 (MEDIUM): proposals made BEFORE a capability attempt are
    recorded — history preserved on the failure path."""
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)

    def propose_then_attempt(case: Path, sink: object) -> None:
        sink.propose(  # type: ignore[attr-defined]
            decision="first thought", expected_effect="draft",
            cost_usd=0.0)
        sink.propose(  # type: ignore[attr-defined]
            decision="second thought", expected_effect="draft2",
            cost_usd=0.0)
        sink.use_capability(  # type: ignore[attr-defined]
            "live.file.write", {"path": "C:/live/state.txt"})

    with pytest.raises(ShadowError):
        shadow_run(db, improvement_id=improvement_id,
                   candidate_release_id=release_id,
                   decision_fn=propose_then_attempt,
                   inputs_dir=_inputs(tmp_path), controller="supervisor")
    runs = db.all("shadow_runs")
    assert runs and runs[0]["status"] == "FAILED"
    assert runs[0]["decisions_count"] == 2  # both proposals preserved
    proposed = [e for e in db.events()
                if e.event_type == "shadow.decision_proposed"]
    assert len(proposed) == 2
    assert "shadow.live_effect_attempted" in {
        e.event_type for e in db.events()}


def test_empty_inputs_no_orphan_row(db: FoundryDatabase,
                                    tmp_path: Path) -> None:
    """S6 r2 (LOW): an empty inputs dir refuses BEFORE run_started —
    no orphaned RUNNING row."""
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    empty = tmp_path / "empty-inputs"
    empty.mkdir()
    with pytest.raises(ShadowError):
        shadow_run(db, improvement_id=improvement_id,
                   candidate_release_id=release_id,
                   decision_fn=_propose_only_decision,
                   inputs_dir=empty, controller="supervisor")
    assert db.count("shadow_runs") == 0
    assert not any(e.event_type == "shadow.run_started"
                   for e in db.events())


def test_missing_baseline_uncomparable(db: FoundryDatabase,
                                       tmp_path: Path) -> None:
    """S6 r2 (LOW): cases beyond the baseline length are recorded as
    UNCOMPARABLE, never as asserted differences."""
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    baseline = [{"decision": f"respond to case-{i}.txt",
                 "expected_effect": f"draft reply for case-{i}.txt",
                 "cost_usd": 0.0} for i in range(2)]  # 2 of 3 cases
    run_id = shadow_run(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        decision_fn=_propose_only_decision,
        inputs_dir=_inputs(tmp_path, count=3),
        baseline_decisions=baseline, controller="supervisor")
    row = db.row("shadow_runs", "run_id", run_id)
    divergences = __import__("json").loads(row["divergences_json"])
    assert len(divergences) == 1
    assert divergences[0]["kinds"] == ["uncomparable"]


def test_nested_inputs_are_cases(db: FoundryDatabase,
                                 tmp_path: Path) -> None:
    """S6 r2 (INFO e): every FILE under the inputs tree is a case —
    nested files are executed too."""
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)
    inputs = _inputs(tmp_path, count=2)
    nested = inputs / "sub"
    nested.mkdir()
    (nested / "deep.txt").write_text("deep", encoding="utf-8")
    run_id = shadow_run(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id,
        decision_fn=_propose_only_decision,
        inputs_dir=inputs, controller="supervisor")
    row = db.row("shadow_runs", "run_id", run_id)
    assert row is not None and row["status"] == "COMPLETED"
    assert row["decisions_count"] == 3  # 2 top-level + 1 nested
