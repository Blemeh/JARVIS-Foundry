"""V6 lane: deterministic weakness/opportunity detection over durable
outcome projections.

Covers: empty-window honesty (zero observations, nothing fabricated),
window validation refusal, every detection rule firing on seeded
outcomes, minimum-sample suppression visibility, no-baseline confidence
capping, dedupe identity stability across identical re-runs, discovery
adapter compatibility with the S10 recording contract end-to-end, and
post-detection reconcile consistency.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.detector import (
    DetectionError,
    detect_weaknesses,
    discovery_sources,
    next_window,
)
from jarvis_foundry.evolution.discover import (
    candidate_improvements,
    record_discovered_improvements,
)
from jarvis_foundry.events import Event
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "supervisor"
WINDOW_END = "2026-08-23T12:00:00"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


# ---------------------------------------------------------------------------
# seeding helpers — real ledger events, backdated via Event.recorded_at
# (projections stamp rows from event.recorded_at)
# ---------------------------------------------------------------------------

def _backdated(db: FoundryDatabase, event_type: str, payload: dict,
               idem_key: str, at: str) -> None:
    event = Event.create(event_type, payload, source_type="test",
                         source_id="detector", idempotency_key=idem_key)
    db.append(replace(event, recorded_at=at))


def _seed_agent_run(db: FoundryDatabase, run_id: str, mission_id: str,
                    status: str, started_at: str, *, provider: str = "p1",
                    model: str = "m1") -> None:
    _backdated(db, "agent_run.started",
               {"run_id": run_id, "mission_id": mission_id,
                "role": "builder", "provider": provider, "model": model},
               f"rs:{run_id}", started_at)
    if status == "FAILED":
        _backdated(db, "agent_run.failed",
                   {"run_id": run_id, "failure_class": "test"},
                   f"rf:{run_id}", started_at)
    elif status == "COMPLETED":
        _backdated(db, "agent_run.completed",
                   {"run_id": run_id, "result": {}, "cost": {}},
                   f"rc:{run_id}", started_at)


def _seed_mission_blocked(db: FoundryDatabase, mission_id: str,
                          blocked_at: str) -> None:
    _backdated(db, "mission.created",
               {"mission_id": mission_id, "objective": "seed",
                "acceptance_criteria": ["c"]},
               f"mc:{mission_id}", blocked_at)
    _backdated(db, "mission.state_changed",
               {"mission_id": mission_id, "state": "ORIENTING"},
               f"ms1:{mission_id}", blocked_at)
    _backdated(db, "mission.state_changed",
               {"mission_id": mission_id, "state": "BLOCKED"},
               f"ms2:{mission_id}", blocked_at)


def _seed_evaluation_run(db: FoundryDatabase, run_id: str,
                         workload_id: str, subject_kind: str,
                         created_at: str, *, duration_s: float = 1.0,
                         error_rate: float = 0.0,
                         cost_usd: float = 0.0) -> None:
    _backdated(db, "evaluation.run_started",
               {"run_id": run_id, "workload_id": workload_id,
                "subject_kind": subject_kind, "subject_identity": "x",
                "command_hash": "h"},
               f"ers:{run_id}", created_at)
    _backdated(db, "evaluation.run_completed",
               {"run_id": run_id, "workload_id": workload_id,
                "subject_kind": subject_kind,
                "subject_identity": "x", "command_hash": "h",
                "input_hash": "ih",
                "exit_code": 0 if error_rate == 0 else 1,
                "duration_s": duration_s, "error_rate": error_rate,
                "cost_usd": cost_usd, "case_results": [],
                "output_hashes": {},
                "nondeterminism_handling": "not_seeded"},
               f"erc:{run_id}", created_at)


def _seed_verdict(db: FoundryDatabase, verification_id: str,
                  verdict: str, at: str) -> None:
    _backdated(db, "verification.requested",
               {"verification_id": verification_id,
                "improvement_id": "improvement-x", "subject_ref": "rel-1",
                "criteria": ["c"], "artifacts": {"a": "b" * 64},
                "builder_identity": {"session_id": "s1"}, "controller":
                    CONTROLLER},
               f"vr:{verification_id}", at)
    _backdated(db, "verification.verdict_recorded",
               {"verification_id": verification_id, "verdict": verdict,
                "criteria_results": [{"criterion": "c", "status": verdict}],
                 "findings": [], "verifier_identity": {"session_id": "s2"},
                "evidence_refs": [], "verdict_hash": "h",
                "same_provider_limitation": False},
               f"vd:{verification_id}", at)


# ---------------------------------------------------------------------------
# window contract
# ---------------------------------------------------------------------------

def test_window_validation_refuses_inverted() -> None:
    with pytest.raises(DetectionError):
        detect_weaknesses(None, window_start=WINDOW_END,
                          window_end="2026-08-23T00:00:00")  # type: ignore[arg-type]


def test_empty_window_fabricates_nothing(db: FoundryDatabase) -> None:
    result = detect_weaknesses(
        db, window_start="2026-08-20T00:00:00",
        window_end="2026-08-21T00:00:00")
    assert result["observations"] == []
    assert isinstance(result["suppressed"], list)


def test_next_window_deterministic() -> None:
    start, end = next_window(WINDOW_END, span_hours=24)
    assert start < end and end == WINDOW_END


# ---------------------------------------------------------------------------
# rule firing on seeded outcomes
# ---------------------------------------------------------------------------

def _in_window(offset_hours: int) -> str:
    base = datetime.fromisoformat("2026-08-22T12:00:00")
    return (base + timedelta(hours=offset_hours)).isoformat()


def test_repair_loop_detected(db: FoundryDatabase) -> None:
    # 3 failed runs, 2 of them followed by retries on the same missions.
    _seed_agent_run(db, "r1", "m1", "FAILED", _in_window(1))
    _seed_agent_run(db, "r2", "m1", "COMPLETED", _in_window(2))
    _seed_agent_run(db, "r3", "m2", "FAILED", _in_window(3))
    _seed_agent_run(db, "r4", "m2", "RUNNING", _in_window(4))
    _seed_agent_run(db, "r5", "m3", "FAILED", _in_window(5))
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    rules = {o["rule"]: o for o in result["observations"]}
    assert "agent_repair_rate" in rules
    obs = rules["agent_repair_rate"]
    assert obs["value"] == pytest.approx(round(2 / 3, 4))
    assert obs["window_start"] == _in_window(0)
    assert obs["evidence_refs"][0].startswith("agent_runs/")


def test_verifier_fail_rate_detected(db: FoundryDatabase) -> None:
    for i, verdict in enumerate(("FAIL", "FAIL", "PASS")):
        _seed_verdict(db, f"v-{i}", verdict, _in_window(i + 1))
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    rules = {o["rule"] for o in result["observations"]}
    assert "verifier_fail_rate" in rules


def test_mission_retry_detected_and_staleness_separate(
        db: FoundryDatabase) -> None:
    for i in range(3):
        _seed_agent_run(db, f"rr{i}", "m-retry", "FAILED",
                        _in_window(i + 1))
    # a BLOCKED mission untouched inside the window
    _seed_agent_run(db, "rb0", "m-stuck", "FAILED", _in_window(1))
    _seed_mission_blocked(db, "m-stuck", _in_window(2))
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    rules = {o["rule"] for o in result["observations"]}
    assert "mission_retry_count" in rules
    assert "mission_staleness" in rules


def test_provider_underperformance_detected(
        db: FoundryDatabase) -> None:
    for i in range(2):
        _seed_agent_run(db, f"pa{i}", f"mp{i}", "COMPLETED",
                        _in_window(i + 1), provider="good", model="gm")
    for i in range(4):
        _seed_agent_run(db, f"pb{i}", f"mb{i}",
                        "FAILED" if i % 2 == 0 else "COMPLETED",
                        _in_window(i + 3), provider="bad", model="bm")
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    provider_obs = [o for o in result["observations"]
                    if o["rule"] == "provider_failure_rate"]
    assert len(provider_obs) == 1
    assert "bad/bm" in provider_obs[0]["metric"]
    assert provider_obs[0]["baseline"] is not None


def test_candidate_latency_regression_detected(
        db: FoundryDatabase) -> None:
    wid = "workload-lat"
    _seed_evaluation_run(db, "eb1", wid, "baseline", _in_window(1),
                         duration_s=2.0)
    _seed_evaluation_run(db, "ec1", wid, "candidate", _in_window(2),
                         duration_s=4.0)
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    rules = {o["rule"] for o in result["observations"]}
    assert "candidate_latency_regression" in rules


def test_error_inflation_guardrail_breach_detected(
        db: FoundryDatabase) -> None:
    wid = "workload-err"
    _seed_evaluation_run(db, "ee-c1", wid, "candidate", _in_window(1),
                         error_rate=0.5)
    _seed_evaluation_run(db, "ee-c2", wid, "candidate", _in_window(2),
                         error_rate=0.25)
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    breach = [o for o in result["observations"]
              if o["rule"] == "evaluation_error_inflation"]
    assert len(breach) == 1
    assert breach[0]["observation_type"] == "guardrail_breaches"


def test_cost_inflation_detected_against_prior_window(
        db: FoundryDatabase) -> None:
    wid = "workload-cost"
    # prior window (before _in_window(0)): 0.5 total
    _seed_evaluation_run(db, "cp1", wid, "candidate", _in_window(-24),
                         cost_usd=0.5)
    # current window: 2.0 total -> ratio 4x >= 2.0 threshold
    _seed_evaluation_run(db, "cc1", wid, "candidate", _in_window(1),
                         cost_usd=1.0)
    _seed_evaluation_run(db, "cc2", wid, "candidate", _in_window(2),
                         cost_usd=1.0)
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    cost_obs = [o for o in result["observations"]
                if o["rule"] == "window_cost_inflation"]
    assert len(cost_obs) == 1
    assert cost_obs[0]["value"] == pytest.approx(4.0)


def test_cost_inflation_without_baseline_suppressed(
        db: FoundryDatabase) -> None:
    wid = "workload-cost-nobase"
    _seed_evaluation_run(db, "cn1", wid, "candidate", _in_window(1),
                         cost_usd=3.0)
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    assert all(o["rule"] != "window_cost_inflation"
               for o in result["observations"])
    assert any(s.get("reason") == "no prior-window cost baseline"
               for s in result["suppressed"])


# ---------------------------------------------------------------------------
# suppression + confidence + dedupe
# ---------------------------------------------------------------------------

def test_small_samples_are_suppressed_not_recorded(
        db: FoundryDatabase) -> None:
    _seed_agent_run(db, "solo", "m-solo", "FAILED", _in_window(1))
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    assert all(o["rule"] != "agent_repair_rate"
               for o in result["observations"])
    suppressed_rules = [s["rule"] for s in result["suppressed"]]
    assert "agent_repair_rate" in suppressed_rules


def test_no_baseline_caps_confidence(db: FoundryDatabase) -> None:
    for i, verdict in enumerate(("FAIL", "FAIL", "PASS")):
        _seed_verdict(db, f"vb-{i}", verdict, _in_window(i + 1))
    result = detect_weaknesses(
        db, window_start=_in_window(0), window_end=_in_window(24))
    fail_obs = [o for o in result["observations"]
                if o["rule"] == "verifier_fail_rate"]
    assert fail_obs and fail_obs[0]["baseline"] is None
    assert fail_obs[0]["confidence"] <= 0.6


def test_dedupe_identity_stable_across_reruns(
        db: FoundryDatabase) -> None:
    # 3 retries on one mission -> mission_retry_count fires (>=3 runs)
    for i in range(3):
        _seed_agent_run(db, f"d{i}", "dm-retry", "FAILED",
                        _in_window(i + 1))
    kwargs = dict(window_start=_in_window(0), window_end=_in_window(24))
    first = detect_weaknesses(db, **kwargs)
    second = detect_weaknesses(db, **kwargs)
    ids_first = [o["dedupe_identity"] for o in first["observations"]]
    ids_second = [o["dedupe_identity"] for o in second["observations"]]
    assert ids_first == ids_second and ids_first


# ---------------------------------------------------------------------------
# discovery adapter closes the producer gap END TO END
# ---------------------------------------------------------------------------

def test_adapter_feeds_discovery_recording(db: FoundryDatabase) -> None:
    # one mission with 3 runs -> mission_retry_count (task_failures);
    # a BLOCKED mission -> mission_staleness
    for i in range(3):
        _seed_agent_run(db, f"a{i}", "am-retry", "FAILED", _in_window(i + 1))
    _seed_mission_blocked(db, "am-stuck", _in_window(2))
    sources = discovery_sources(
        db, window_start=_in_window(0), window_end=_in_window(24))
    assert any(sources.values()), "adapter produced zero observations"
    candidates = candidate_improvements(db, sources=sources,
                                        controller=CONTROLLER)
    assert candidates, "discovery refused adapter observations"
    improvement_ids = record_discovered_improvements(
        db, candidates=candidates, sources=sources,
        controller=CONTROLLER)
    assert improvement_ids
    for improvement_id in improvement_ids:
        row = db.row("improvement_records", "improvement_id",
                     improvement_id)
        assert row is not None and row["state"] == "FROZEN"
    report = reconcile(db)
    assert report.consistent


def test_adapter_identical_state_identical_sources(
        db: FoundryDatabase) -> None:
    _seed_agent_run(db, "i1", "im1", "FAILED", _in_window(1))
    _seed_agent_run(db, "i2", "im1", "COMPLETED", _in_window(2))
    _seed_agent_run(db, "i3", "im2", "FAILED", _in_window(3))
    kwargs = dict(window_start=_in_window(0), window_end=_in_window(24))
    one = discovery_sources(db, **kwargs)
    two = discovery_sources(db, **kwargs)
    assert json.dumps(one, sort_keys=True) == json.dumps(two,
                                                         sort_keys=True)


# ---------------------------------------------------------------------------
# JOB V6-3: hypothesis records — lineage + duplicate collapse
# ---------------------------------------------------------------------------

def test_hypothesis_carries_detector_lineage(db: FoundryDatabase) -> None:
    for i in range(3):
        _seed_agent_run(db, "h{i}".format(i=i), "hm-retry", "FAILED",
                        _in_window(i + 1))
    sources = discovery_sources(
        db, window_start=_in_window(0), window_end=_in_window(24))
    candidates = candidate_improvements(db, sources=sources,
                                        controller=CONTROLLER)
    assert candidates
    candidate = candidates[0]
    provenance = candidate["provenance"]
    assert provenance["detector_rule"]
    assert provenance["dedupe_identity"].startswith("weakness-")
    # template contract keys are never overwritten by the detector merge
    assert provenance["source"].startswith("discovery:")


def test_duplicate_detection_run_collapses_to_one_record(
        db: FoundryDatabase) -> None:
    for i in range(3):
        _seed_agent_run(db, f"c{i}", "cm-retry", "FAILED", _in_window(i + 1))
    kwargs = dict(window_start=_in_window(0), window_end=_in_window(24))
    sources_one = discovery_sources(db, **kwargs)
    sources_two = discovery_sources(db, **kwargs)
    candidates_one = candidate_improvements(db, sources=sources_one,
                                            controller=CONTROLLER)
    candidates_two = candidate_improvements(db, sources=sources_two,
                                            controller=CONTROLLER)
    ids_one = record_discovered_improvements(
        db, candidates=candidates_one, sources=sources_one,
        controller=CONTROLLER)
    ids_two = record_discovered_improvements(
        db, candidates=candidates_two, sources=sources_two,
        controller=CONTROLLER)
    assert ids_one == ids_two, "identical evidence produced new ids"
    rows = db.all("improvement_records")
    matched = [r for r in rows if r["improvement_id"] == ids_one[0]]
    assert len(matched) == 1, "duplicate generation appended a second row"
