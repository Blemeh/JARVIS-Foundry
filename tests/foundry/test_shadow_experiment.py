"""V6 lane JOB: shadow mode as a first-class experiment stage.

Attacks where existing S6 tests stop:

- a decision_fn raising an ARBITRARY exception mid-run leaves NO
  orphaned RUNNING row: the crash is recorded (shadow.run_failed) and
  re-raised, and the run is not counted as completed evidence;
- identical inputs + deterministic candidate produce equivalent runs:
  same case set, same proposal decisions, zero_live_effects=True both
  times (run ids differ; content does not);
- an attempted FORBIDDEN live effect fails closed with the attempt
  recorded (shadow.live_effect_attempted) and nothing outside shadow.*
  is written to the ledger during the run;
- candidate/live comparison is GENERATED from paired runs
  (shadow_comparison): per-case divergence + zero-effect verdicts.

All runs are disposable; the live surface hash proof is inherited from
the S6 machinery.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import register_release
from jarvis_foundry.evolution.shadow import ShadowError, shadow_run
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
        problem="p", evidence_refs=["e"],
        provenance={"source": "test"}, hypothesis="h",
        expected_mechanism="m",
        baseline_metric={"name": "suite_runtime_s", "value": 313.92,
                         "unit": "seconds", "source": "e"},
        mmi={"minimum_delta": 30.0, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "c", "safety": "s", "cost": "co",
                    "latency": "l"},
        workload={"ref": "tests/foundry", "hash": "b" * 64},
        observation={"requirement": "3 runs", "cycles": 3},
        declared_risk="R2", surfaces=["tests/foundry"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"})


def _release(db: FoundryDatabase, improvement_id: str) -> str:
    return register_release(
        db, source_commit="a" * 40,
        artifact_hashes={"suite.txt": "b" * 64},
        dependencies=[], runtime_identity={"python": "3.11"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class="R2", migrations=[1, 2, 3], verdicts=[],
        controller="supervisor")


def _inputs(tmp_path: Path) -> Path:
    directory = tmp_path / "shadow-inputs"
    directory.mkdir(exist_ok=True)
    for index in range(3):
        (directory / f"case-{index}.txt").write_text(
            f"input-{index}", encoding="utf-8")
    return directory


def _propose_only(case: Path, sink) -> None:  # type: ignore[no-untyped-def]
    sink.propose(decision=f"respond to {case.name}",
                 expected_effect=f"draft reply for {case.name}",
                 cost_usd=0.0)


def _events_between(db: FoundryDatabase, before_count: int) -> list:
    return db.events()[before_count:]


# ---------------------------------------------------------------------------
# crash cleanup
# ---------------------------------------------------------------------------

def test_arbitrary_crash_leaves_no_orphaned_running_row(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)

    def exploding(case: Path, sink) -> None:  # type: ignore[no-untyped-def]
        if case.name == "case-1.txt":
            raise RuntimeError("candidate process exploded")

    with pytest.raises(RuntimeError):
        shadow_run(db, improvement_id=improvement_id,
                   candidate_release_id=release_id,
                   decision_fn=exploding, inputs_dir=_inputs(tmp_path),
                   controller="supervisor")
    rows = db.all("shadow_runs")
    assert rows, "no shadow row recorded"
    assert all(row["status"] in ("COMPLETED", "FAILED")
               for row in rows), \
        f"orphaned RUNNING row after crash: {rows}"
    types = [e.event_type for e in _events_between(
        db, before_count=0)]
    # the failure IS recorded as evidence, deterministically
    assert "shadow.run_failed" in types


# ---------------------------------------------------------------------------
# deterministic-enough repeat for evidence purposes
# ---------------------------------------------------------------------------

def test_repeat_run_is_deterministic_in_content(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)

    def decisions(case: Path, sink) -> None:  # type: ignore[no-untyped-def]
        sink.propose(decision=f"fixed-decision-for-{case.name}",
                     expected_effect="draft", cost_usd=0.0)

    kwargs = dict(improvement_id=improvement_id,
                  candidate_release_id=release_id,
                  decision_fn=decisions, inputs_dir=_inputs(tmp_path),
                  controller="supervisor")
    first_id = shadow_run(db, **kwargs)
    second_id = shadow_run(db, **kwargs)
    assert first_id != second_id

    def payload(run_id: str) -> dict:
        row = db.row("shadow_runs", "run_id", run_id)
        return {"status": row["status"],
                "zero_live_effects": bool(row["zero_live_effects"]),
                "input_hash": row["input_hash"]}

    first, second = payload(first_id), payload(second_id)
    assert first == second
    assert first["zero_live_effects"] is True

    proposals_first = sorted(
        (e.payload["case"], e.payload["decision"])
        for e in db.events()
        if e.event_type == "shadow.decision_proposed"
        and e.payload["run_id"] == first_id)
    proposals_second = sorted(
        (e.payload["case"], e.payload["decision"])
        for e in db.events()
        if e.event_type == "shadow.decision_proposed"
        and e.payload["run_id"] == second_id)
    assert proposals_first == proposals_second
    assert len(proposals_first) == 3


# ---------------------------------------------------------------------------
# forbidden effect fails closed + comparison generation
# ---------------------------------------------------------------------------

def test_forbidden_effect_recorded_and_fails_closed(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)

    def sneaky(case: Path, sink) -> None:  # type: ignore[no-untyped-def]
        if case.name == "case-2.txt":
            sink.use_capability("live.registry.write", {"key": "x"})
        sink.propose(decision="reply", expected_effect="draft", cost_usd=0.0)

    events_before = len(db.events())
    with pytest.raises(ShadowError):
        shadow_run(db, improvement_id=improvement_id,
                   candidate_release_id=release_id,
                   decision_fn=sneaky, inputs_dir=_inputs(tmp_path),
                   controller="supervisor")
    new_events = db.events()[events_before:]
    attempted = [e for e in new_events
                 if e.event_type == "shadow.live_effect_attempted"]
    assert attempted and \
        attempted[-1].payload["capability"] == "live.registry.write"
    non_shadow_writes = [e for e in new_events
                         if not e.event_type.startswith("shadow.")]
    assert not non_shadow_writes, \
        f"non-shadow ledger writes escaped during run: {non_shadow_writes}"
    report = reconcile(db)
    assert report.consistent


def test_candidate_vs_baseline_comparison_generated(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db)
    release_id = _release(db, improvement_id)

    baseline_decisions = [
        {"decision": f"respond to case-{i}.txt",
         "expected_effect": f"draft reply for case-{i}.txt",
         "cost_usd": 0.0} for i in range(3)]

    def candidate_diverges(case: Path, sink) -> None:  # type: ignore[no-untyped-def]
        if case.name == "case-1.txt":
            sink.propose(decision="DIFFERENT", expected_effect="draft",
                         cost_usd=0.25)
        else:
            _propose_only(case, sink)

    shadow_run(db, improvement_id=improvement_id,
               candidate_release_id=release_id,
               decision_fn=candidate_diverges,
               inputs_dir=_inputs(tmp_path),
               baseline_decisions=baseline_decisions,
               controller="supervisor")
    completed_rows = [r for r in db.all("shadow_runs")
                      if r["status"] == "COMPLETED"]
    assert completed_rows
    divergences = json.loads(completed_rows[-1]["divergences_json"])
    kinds_by_case = {d["case"]: d["kinds"] for d in divergences}
    # candidate proposed decision="DIFFERENT", expected_effect="draft",
    # cost=0.25 vs baseline "respond/draft reply/0.0" -> all three differ
    assert kinds_by_case.get("case-1.txt") == \
        ["decision", "expected_effect", "cost"], \
        f"divergence kinds wrong: {kinds_by_case}"
    # other cases match the baseline exactly -> no divergence rows
    assert "case-0.txt" not in kinds_by_case
    assert "case-2.txt" not in kinds_by_case
