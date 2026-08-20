"""R11 forward-port gate: governed self-evolution via canonical main.

Adapted from the remaster R11 self-evolution contract onto canonical
main's evolution machinery (records, registry, builder, lab, verify,
shadow, canary, promote, restore, cycle, acceptance, discover) and the
Supervisor evolution control plane. The gate proves with REAL
subprocess workloads and the durable ledger that:

- a candidate can never self-certify, self-promote, roll itself back,
  start/tick/terminate its own canary, create/restore its own restore
  point, record its own acceptance, or drive discovery;
- a candidate can never lower its risk class or rewrite records
  (append-only, monotonic risk, Supervisor-pinned acceptance);
- STOP blocks every effectful path and no machinery path clears it;
- governed evaluate/shadow/canary/promote-or-reject/rollback semantics
  run through the real ladder, canary health comes from raw metrics,
  shadow runs are zero-effect, restores are verified/rehearsed, and
  crash recovery + stale-canary reaping are restart-safe;
- the operational acceptance minimums (3 cycles, 2 measured
  promotions, 1 R1, 1 R2-canary, 1 rejection, 1 rollback) are provable
  from the ledger and archived by the Supervisor only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution import acceptance
from jarvis_foundry.evolution.canary import (
    CanaryError,
    canary_completed_successfully,
    canary_status,
    canary_tick,
    reap_stale_canaries,
    start_canary,
    terminate_canary,
)
from jarvis_foundry.evolution.cycle import CycleError, run_cycle
from jarvis_foundry.evolution.discover import (
    DiscoveryError,
    candidate_improvements,
    record_discovered_improvements,
)
from jarvis_foundry.evolution.lab import (
    compare_evaluations,
    register_workload,
    run_evaluation,
)
from jarvis_foundry.evolution.promote import (
    PromoteError,
    freeze_active,
    promote_release,
    promotion_status,
    recover_promotions,
    rollback_release,
    slot_pointers,
    unfreeze_active,
)
from jarvis_foundry.evolution.records import (
    ImprovementError,
    raise_improvement_risk,
    record_improvement,
)
from jarvis_foundry.evolution.registry import register_release, set_slot
from jarvis_foundry.evolution.restore import (
    RestoreError,
    create_restore_point,
    data_loss_analysis,
    rehearse_restore,
    restore_from_point,
    verify_restore_point,
)
from jarvis_foundry.evolution.shadow import (
    ShadowError,
    ShadowSink,
    shadow_run,
)
from jarvis_foundry.evolution.verify import (
    VerificationError,
    is_trusted_verdict,
    record_verification_request,
    record_verification_verdict,
)
from jarvis_foundry.reconcile import reconcile
from jarvis_foundry.supervisor.estop import EmergencyStop
from jarvis_foundry.supervisor.evolution_control import (
    SUPERVISOR_CONTROLLER,
    EvolutionControl,
    EvolutionControlError,
)

from jarvis_foundry.runtime.provider_config import DEFAULT_MODEL, default_provider_settings

CONTROLLER = "supervisor"
CANDIDATE = "candidate-builder-1"
TEST_PROVIDER = default_provider_settings()
DIRECTIVE_IDENTITY = {"provider": "opencode_go",
                      "model": DEFAULT_MODEL,
                      "session_id": "session-builder-r11"}
OTHER_IDENTITY = {"provider": "opencode_go",
                  "model": DEFAULT_MODEL,
                  "session_id": "session-verifier-r11"}
THRESHOLDS = {"error_rate_max": 0.05, "latency_max_s": 2.0}

SLOW = [sys.executable, "-c",
        ("import pathlib,sys,time\n"
         "pathlib.Path(sys.argv[1]).read_text()\n"
         "time.sleep(0.3)\n")]
FAST = [sys.executable, "-c",
        ("import pathlib,sys\n"
         "pathlib.Path(sys.argv[1]).read_text()\n")]
BROKEN = [sys.executable, "-c",
          ("import pathlib,sys\n"
           "pathlib.Path(sys.argv[1]).read_text()\n"
           "raise SystemExit(1)\n")]
BUILD_OK = [sys.executable, "-c",
            ("import pathlib\n"
             "pathlib.Path('built.txt').write_text('built')\n")]


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase, risk: str = "R1") -> str:
    surfaces = ["tests/foundry"] if risk == "R1" else ["evaluation"]
    return record_improvement(
        db,
        problem="representative workload latency exceeds the threshold",
        evidence_refs=["var/evidence/r11/observation.txt"],
        provenance={"source": "r11 convergence gate observation",
                    "observed_at": "2026-08-18T00:00:00Z"},
        hypothesis="removing the serialized wait reduces latency",
        expected_mechanism="the measured workload carries a fixed "
                           "per-case wait",
        baseline_metric={"name": "latency_s", "value": 0.9,
                         "unit": "seconds",
                         "source": "r11-observation.txt"},
        mmi={"minimum_delta": 0.1, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "1 demonstration cycle", "cycles": 1},
        declared_risk=risk, surfaces=surfaces,
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _workload(tmp_path: Path) -> Path:
    directory = tmp_path / "workload"
    directory.mkdir(exist_ok=True)
    for index in range(3):
        (directory / f"case-{index}.txt").write_text(
            f"input-{index}", encoding="utf-8")
    return directory


def _cycle_args(db: FoundryDatabase, tmp_path: Path,
                risk: str = "R1",
                candidate_command=FAST,
                build_command=BUILD_OK,
                ) -> dict[str, object]:
    return {
        "improvement_id": _improvement(db, risk=risk),
        "source_commit": "c" * 40,
        "workload_dir": _workload(tmp_path),
        "baseline_command": SLOW,
        "candidate_command": candidate_command,
        "build_command": build_command,
    }


def _release(db: FoundryDatabase, improvement_id: str,
             source_commit: str = "a" * 40) -> str:
    return register_release(
        db, source_commit=source_commit,
        artifact_hashes={"suite.txt": "b" * 64},
        dependencies=[], runtime_identity={"python": "3.13.5"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class="R1", migrations=[], verdicts=[],
        controller=CONTROLLER)


def _verification(db: FoundryDatabase, improvement_id: str,
                  verifier_identity: dict[str, str] | None = None
                  ) -> str:
    verification_id = record_verification_request(
        db, improvement_id=improvement_id,
        subject_ref="release-candidate-r11",
        criteria=["criterion-1: suite green", "criterion-2: latency"],
        artifacts={"suite.txt": "b" * 64},
        builder_identity=DIRECTIVE_IDENTITY, controller=CONTROLLER,
        provider_settings=TEST_PROVIDER)
    record_verification_verdict(
        db, verification_id=verification_id, verdict="PASS",
        criteria_results=[
            {"criterion": "criterion-1: suite green", "status": "PASS",
             "evidence_hash": "c" * 64},
            {"criterion": "criterion-2: latency", "status": "PASS",
             "evidence_hash": "d" * 64},
        ],
        findings=[],
        verifier_identity=verifier_identity or OTHER_IDENTITY,
        evidence_refs=["var/evidence/r11/verdict.txt"],
        provider_settings=TEST_PROVIDER)
    return verification_id


def _runs(db: FoundryDatabase, tmp_path: Path) -> tuple[str, str]:
    directory = tmp_path / "eval-workload"
    directory.mkdir(exist_ok=True)
    for index in range(3):
        (directory / f"case-{index}.txt").write_text(
            f"input-{index}", encoding="utf-8")
    workload_id = register_workload(db, name="w-r11", input_dir=directory,
                                    config={}, seeds=[],
                                    controller=CONTROLLER)
    baseline_id = run_evaluation(db, workload_id=workload_id,
                                 subject_kind="baseline",
                                 subject_identity="base-r11",
                                 command=SLOW, controller=CONTROLLER)
    candidate_id = run_evaluation(db, workload_id=workload_id,
                                  subject_kind="candidate",
                                  subject_identity="cand-r11",
                                  command=FAST, controller=CONTROLLER)
    return baseline_id, candidate_id


def _promotable(db: FoundryDatabase, tmp_path: Path) -> dict[str, str]:
    improvement_id = _improvement(db, "R1")
    genesis = _release(db, improvement_id, source_commit="a" * 40)
    set_slot(db, "live", genesis, reason="genesis",
             controller=CONTROLLER)
    candidate = _release(db, improvement_id, source_commit="c" * 40)
    set_slot(db, "candidate", candidate, reason="candidate",
             controller=CONTROLLER)
    verification_id = _verification(db, improvement_id)
    baseline_id, candidate_run_id = _runs(db, tmp_path)
    return {"improvement_id": improvement_id, "genesis": genesis,
            "candidate": candidate, "verification_id": verification_id,
            "baseline_run_id": baseline_id,
            "candidate_run_id": candidate_run_id}


def _stop_sentinel(db: FoundryDatabase) -> Path:
    return Path(db.path).parent / "EMERGENCY_STOP"


def _events_of(db: FoundryDatabase, event_type: str) -> list[object]:
    return [event for event in db.events()
            if event.event_type == event_type]


# ---------------------------------------------------------------------------
# self-certification refusals
# ---------------------------------------------------------------------------

def test_candidate_cannot_self_certify(db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    verification_id = record_verification_request(
        db, improvement_id=improvement_id,
        subject_ref="release-candidate-r11",
        criteria=["criterion-1: suite green"],
        artifacts={"suite.txt": "b" * 64},
        builder_identity=DIRECTIVE_IDENTITY, controller=CONTROLLER,
        provider_settings=TEST_PROVIDER)
    # verifier session == builder session: self-certification refused
    with pytest.raises(VerificationError, match="self|same"):
        record_verification_verdict(
            db, verification_id=verification_id, verdict="PASS",
            criteria_results=[
                {"criterion": "criterion-1: suite green",
                 "status": "PASS", "evidence_hash": "c" * 64}],
            findings=[], verifier_identity=DIRECTIVE_IDENTITY,
            evidence_refs=["var/evidence/r11/self.txt"],
            provider_settings=TEST_PROVIDER)
    # a FAIL or not-yet-verdict is never a trusted promotion verdict
    assert is_trusted_verdict(db, verification_id, DIRECTIVE_IDENTITY) \
        is False
    # the independent verifier completes the SAME request
    record_verification_verdict(
        db, verification_id=verification_id, verdict="PASS",
        criteria_results=[
            {"criterion": "criterion-1: suite green", "status": "PASS",
             "evidence_hash": "c" * 64}],
        findings=[], verifier_identity=OTHER_IDENTITY,
        evidence_refs=["var/evidence/r11/independent.txt"],
        provider_settings=TEST_PROVIDER)
    assert is_trusted_verdict(db, verification_id, DIRECTIVE_IDENTITY) \
        is True
    # queried with the verifier's own session as builder: not trusted
    assert is_trusted_verdict(db, verification_id, OTHER_IDENTITY) \
        is False


def test_verdict_forgery_and_artifact_binding_refused(
        db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    with pytest.raises(VerificationError, match="forgery"):
        record_verification_verdict(
            db, verification_id="verification-fake", verdict="PASS",
            criteria_results=[
                {"criterion": "c", "status": "PASS",
                 "evidence_hash": "c" * 64}],
            findings=[], verifier_identity=OTHER_IDENTITY,
            evidence_refs=["var/evidence/r11/fake.txt"],
            provider_settings=TEST_PROVIDER)
    verification_id = record_verification_request(
        db, improvement_id=improvement_id,
        subject_ref="release-candidate-r11",
        criteria=["criterion-1: suite green"],
        artifacts={"suite.txt": "b" * 64},
        builder_identity=DIRECTIVE_IDENTITY, controller=CONTROLLER,
        provider_settings=TEST_PROVIDER)
    # verdict bound to different artifacts than the frozen request
    with pytest.raises(VerificationError, match="differ"):
        record_verification_verdict(
            db, verification_id=verification_id, verdict="PASS",
            criteria_results=[
                {"criterion": "criterion-1: suite green",
                 "status": "PASS", "evidence_hash": "c" * 64}],
            findings=[], verifier_identity=OTHER_IDENTITY,
            evidence_refs=["var/evidence/r11/alt.txt"],
            artifacts={"suite.txt": "e" * 64},
            provider_settings=TEST_PROVIDER)


# ---------------------------------------------------------------------------
# no self-promotion / no authority widening
# ---------------------------------------------------------------------------

def test_candidate_cannot_promote_or_rollback(db: FoundryDatabase,
                                              tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path)
    promote_kwargs = {
        "candidate_release_id": setup["candidate"],
        "verification_id": setup["verification_id"],
        "baseline_run_id": setup["baseline_run_id"],
        "candidate_run_id": setup["candidate_run_id"],
    }
    with pytest.raises(PromoteError, match="candidate"):
        promote_release(db, controller=CANDIDATE, **promote_kwargs)
    assert slot_pointers(db)["live"] == setup["genesis"]
    promote_release(db, controller=CONTROLLER, **promote_kwargs)
    assert slot_pointers(db)["live"] == setup["candidate"]
    with pytest.raises(PromoteError, match="candidate"):
        rollback_release(db, trigger="manual", reason="self-rollback",
                         controller=CANDIDATE)
    assert slot_pointers(db)["live"] == setup["candidate"]
    rollback_release(db, trigger="outcome_regression",
                     reason="measured regression", controller=CONTROLLER)
    assert slot_pointers(db)["live"] == setup["genesis"]
    assert slot_pointers(db)["quarantine"] == setup["candidate"]


def test_candidate_cannot_drive_cycle_canary_restore_acceptance_discovery(
        db: FoundryDatabase, tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path)
    with pytest.raises(CycleError, match="candidate"):
        run_cycle(db, controller=CANDIDATE, **args)  # type: ignore[arg-type]
    assert db.count("cycles") == 0
    with pytest.raises(CanaryError, match="Supervisor"):
        start_canary(db, improvement_id="improvement-x",
                     candidate_release_id="release-y",
                     traffic_share=0.05, duration_cycles=2,
                     max_cost_usd=0.0, health_thresholds=THRESHOLDS,
                     controller=CANDIDATE)
    with pytest.raises(CanaryError, match="Supervisor|candidate"):
        canary_tick(db, canary_id="canary-x", cycle=1,
                    traffic_served=0.05, errors=0, latency_s=0.5,
                    cost_usd=0.0, controller=CANDIDATE)
    with pytest.raises(CanaryError, match="Supervisor|candidate"):
        terminate_canary(db, canary_id="canary-x", reason="x",
                         controller=CANDIDATE)
    with pytest.raises(CanaryError, match="Supervisor|candidate"):
        reap_stale_canaries(db, current_cycle=10,
                            max_cycles_without_tick=2,
                            controller=CANDIDATE)
    with pytest.raises(RestoreError, match="candidate"):
        create_restore_point(db, label="x", controller=CANDIDATE)
    with pytest.raises(RestoreError, match="candidate"):
        restore_from_point(db, point_id="point-x", controller=CANDIDATE)
    with pytest.raises(RestoreError, match="Supervisor|candidate"):
        rehearse_restore(db, point_id="point-x", controller=CANDIDATE)
    with pytest.raises(acceptance.AcceptanceError, match="candidate"):
        acceptance.record_acceptance(db, report={"passed": True},
                                     controller=CANDIDATE)
    with pytest.raises(DiscoveryError, match="Supervisor|candidate"):
        record_discovered_improvements(db, candidates=[], sources={},
                                       controller=CANDIDATE)
    with pytest.raises(DiscoveryError, match="Supervisor|candidate"):
        candidate_improvements(db, sources={}, controller=CANDIDATE)
    assert _events_of(db, "acceptance.completed") == []
    assert _events_of(db, "discovery.observed") == []


def test_candidate_cannot_lower_risk_or_rewrite_records(
        db: FoundryDatabase) -> None:
    improvement_id = _improvement(db, "R1")
    events_before = len(db.events())
    # append-only: identical content dedupes to the same id
    duplicate = _improvement(db, "R1")
    assert duplicate == improvement_id
    assert len(db.events()) == events_before
    # changed content creates a NEW record; the old one is untouched
    changed = record_improvement(
        db, problem="a different measured problem",
        evidence_refs=["var/evidence/r11/observation.txt"],
        provenance={"source": "r11 convergence gate observation",
                    "observed_at": "2026-08-18T00:00:00Z"},
        hypothesis="a different hypothesis",
        expected_mechanism="a different mechanism",
        baseline_metric={"name": "latency_s", "value": 0.9,
                         "unit": "seconds",
                         "source": "r11-observation.txt"},
        mmi={"minimum_delta": 0.1, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "1 demonstration cycle", "cycles": 1},
        declared_risk="R1", surfaces=["tests/foundry"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )
    assert changed != improvement_id
    original = db.row("improvement_records", "improvement_id",
                      improvement_id)
    assert original is not None
    assert original["hypothesis"] == "removing the serialized wait " \
        "reduces latency"
    # monotonic risk: lowering or equal-class re-raise is refused
    with pytest.raises(ImprovementError, match="ascending"):
        raise_improvement_risk(db, improvement_id, "R0",
                               reason="self-downgrade")
    with pytest.raises(ImprovementError, match="ascending"):
        raise_improvement_risk(db, improvement_id, "R1",
                               reason="same-class re-raise")
    raise_improvement_risk(db, improvement_id, "R2",
                           reason="surface evidence found")
    assert db.row("improvement_records", "improvement_id",
                  improvement_id)["risk_class"] == "R2"


def test_evolution_control_pins_supervisor_identity(
        db: FoundryDatabase) -> None:
    control = EvolutionControl(db)
    assert control.mode() == "observe"
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.enable(controller=CANDIDATE)
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.record_improvement(controller=CANDIDATE,
                                   problem="x")
    control.enable(controller=SUPERVISOR_CONTROLLER)
    assert control.mode() == "active"
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.freeze(reason="probe", controller=CANDIDATE)
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.promote(controller=CANDIDATE)
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.rollback(trigger="manual", reason="probe",
                         controller=CANDIDATE)
    with pytest.raises(EvolutionControlError, match="Supervisor"):
        control.restore(point_id="point-x", controller=CANDIDATE)


# ---------------------------------------------------------------------------
# STOP blocks everything and cannot be disabled by a candidate
# ---------------------------------------------------------------------------

def test_stop_blocks_all_effectful_paths_and_persists(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db, "R1")
    release_id = _release(db, improvement_id)
    inputs = tmp_path / "shadow-inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "case.txt").write_text("input", encoding="utf-8")
    sentinel = _stop_sentinel(db)
    sentinel.write_text(
        '{"stop_id": "r11-gate", "reason": "gate probe"}',
        encoding="utf-8")
    try:
        args = _cycle_args(db, tmp_path)
        with pytest.raises(CycleError, match="stop"):
            run_cycle(db, controller=CONTROLLER, **args)  # type: ignore[arg-type]
        with pytest.raises(PromoteError, match="stop"):
            promote_release(db, candidate_release_id="release-x",
                            verification_id="verif-x",
                            baseline_run_id="b", candidate_run_id="c",
                            controller=CONTROLLER)
        with pytest.raises(CanaryError, match="stop"):
            start_canary(db, improvement_id=improvement_id,
                         candidate_release_id=release_id,
                         traffic_share=0.05, duration_cycles=2,
                         max_cost_usd=0.0,
                         health_thresholds=THRESHOLDS,
                         controller=CONTROLLER)
        with pytest.raises(RestoreError, match="stop"):
            create_restore_point(db, label="x", controller=CONTROLLER)
        with pytest.raises(ShadowError, match="stop"):
            shadow_run(db, improvement_id=improvement_id,
                       candidate_release_id=release_id,
                       decision_fn=lambda case, sink: sink.propose(
                           decision="file", expected_effect="none"),
                       inputs_dir=inputs, controller=CONTROLLER)
        control = EvolutionControl(db)
        control.enable(controller=SUPERVISOR_CONTROLLER)
        with pytest.raises(EvolutionControlError, match="STOP"):
            control.freeze(reason="probe",
                           controller=SUPERVISOR_CONTROLLER)
    finally:
        sentinel.unlink()
    assert db.count("cycles") == 0
    assert db.count("restore_points") == 0
    # the sentinel is still the only way STOP lifts: the machinery never
    # cleared it and every effectful path stayed blocked
    sentinel.write_text(
        '{"stop_id": "r11-gate-2", "reason": "persist probe"}',
        encoding="utf-8")
    try:
        args = _cycle_args(db, tmp_path)
        with pytest.raises(CycleError, match="stop"):
            run_cycle(db, controller=CONTROLLER, **args)  # type: ignore[arg-type]
        assert EmergencyStop(sentinel).active() is True
    finally:
        sentinel.unlink()


# ---------------------------------------------------------------------------
# governed evaluate / shadow / canary / promote-or-reject / rollback
# ---------------------------------------------------------------------------

def test_governed_cycle_measures_improvement_and_promotes(
        db: FoundryDatabase, tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1")
    improvement_id = str(args["improvement_id"])
    result = run_cycle(db, controller=CONTROLLER, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "promoted"
    assert result["promotion_id"].startswith("promotion-")
    assert slot_pointers(db)["live"] == result["candidate_release_id"]
    row = db.row("cycles", "cycle_id", result["cycle_id"])
    assert row is not None and row["outcome"] == "promoted"
    assert row["baseline_run_id"] and row["candidate_run_id"]
    comparison = compare_evaluations(
        db, baseline_run_id=result["baseline_run_id"],
        candidate_run_id=result["candidate_run_id"],
        improvement_id=improvement_id)
    assert comparison["improvement_met"] is True
    recorded = [event.payload for event in _events_of(
        db, "evaluation.comparison_recorded")]
    assert recorded and recorded[-1]["improvement_met"] is True
    assert reconcile(db).consistent is True


def test_r2_canary_cycle_promotes_and_health_is_raw_metric(
        db: FoundryDatabase, tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R2")
    improvement_id = str(args["improvement_id"])
    args["canary_ticks"] = [
        {"cycle": 1, "traffic_served": 0.05, "errors": 0,
         "latency_s": 0.5, "cost_usd": 0.01},
        {"cycle": 2, "traffic_served": 0.05, "errors": 0,
         "latency_s": 0.5, "cost_usd": 0.01},
    ]
    result = run_cycle(db, controller=CONTROLLER, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "promoted"
    assert result["canary_id"].startswith("canary-")
    assert slot_pointers(db)["live"] == result["candidate_release_id"]
    # a fresh canary: health is recomputed from RAW metrics, so a tick
    # that breaches thresholds terminates even if reported_healthy=True
    canary_id = start_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=result["candidate_release_id"],
        traffic_share=0.05, duration_cycles=5, max_cost_usd=1.0,
        health_thresholds=THRESHOLDS, controller=CONTROLLER)
    status = canary_tick(db, canary_id=canary_id, cycle=1,
                         traffic_served=0.05, errors=9, latency_s=0.5,
                         cost_usd=0.01, controller=CONTROLLER,
                         reported_healthy=True)
    assert status == "TERMINATED"
    assert canary_completed_successfully(db, canary_id) is False
    # out-of-order ticks are refused (monotonic guard)
    fresh = start_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=result["candidate_release_id"],
        traffic_share=0.05, duration_cycles=5, max_cost_usd=1.0,
        health_thresholds=THRESHOLDS, controller=CONTROLLER)
    canary_tick(db, canary_id=fresh, cycle=1, traffic_served=0.05,
                errors=0, latency_s=0.5, cost_usd=0.01,
                controller=CONTROLLER)
    with pytest.raises(CanaryError, match="monotonic"):
        canary_tick(db, canary_id=fresh, cycle=1, traffic_served=0.05,
                    errors=0, latency_s=0.5, cost_usd=0.01,
                    controller=CONTROLLER)


def test_broken_candidate_auto_rejected(db: FoundryDatabase,
                                        tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1",
                       candidate_command=BROKEN)
    result = run_cycle(db, controller=CONTROLLER, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "rejected"
    assert result["promotion_id"] is None
    assert slot_pointers(db)["live"] == result["genesis_release_id"]
    assert db.row("cycles", "cycle_id", result["cycle_id"])["outcome"] \
        == "rejected"


def test_governed_rollback_restores_previous_and_quarantines(
        db: FoundryDatabase, tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1")
    result = run_cycle(db, controller=CONTROLLER, **args)  # type: ignore[arg-type]
    assert result["outcome"] == "promoted"
    candidate = result["candidate_release_id"]
    rollback_release(db, trigger="outcome_regression",
                     reason="latency regressed post-promotion",
                     controller=CONTROLLER)
    pointers = slot_pointers(db)
    assert pointers["quarantine"] == candidate
    assert pointers["live"] != candidate
    assert promotion_status(db, result["promotion_id"]) == "ROLLED_BACK"
    assert reconcile(db).consistent is True


def test_shadow_zero_effect_live_attempt_and_stop_freeze(
        db: FoundryDatabase, tmp_path: Path) -> None:
    improvement_id = _improvement(db, "R1")
    release_id = _release(db, improvement_id)
    inputs = tmp_path / "shadow-inputs"
    inputs.mkdir(exist_ok=True)
    for index in range(3):
        (inputs / f"case-{index}.txt").write_text(
            f"input-{index}", encoding="utf-8")
    marker = tmp_path / "live-marker.txt"
    marker.write_bytes(b"live-state")

    def propose(case: Path, sink: ShadowSink) -> None:
        sink.propose(decision="file", expected_effect="proposal only",
                     cost_usd=0.0, kind="proposal")

    run_id = shadow_run(db, improvement_id=improvement_id,
                        candidate_release_id=release_id,
                        decision_fn=propose, inputs_dir=inputs,
                        controller=CONTROLLER, live_marker=marker)
    completed = [event.payload for event in _events_of(
        db, "shadow.run_completed")]
    assert completed[-1]["run_id"] == run_id
    assert completed[-1]["zero_live_effects"] is True
    assert completed[-1]["decisions_count"] == 3
    assert completed[-1]["live_attempts"] == 0
    assert marker.read_bytes() == b"live-state"

    def live_attempt(case: Path, sink: ShadowSink) -> None:
        sink.use_capability("files.write", {"path": "x"})

    with pytest.raises(ShadowError, match="live effect"):
        shadow_run(db, improvement_id=improvement_id,
                   candidate_release_id=release_id,
                   decision_fn=live_attempt, inputs_dir=inputs,
                   controller=CONTROLLER, live_marker=marker)
    attempts = _events_of(db, "shadow.live_effect_attempted")
    assert attempts
    failed = [event.payload for event in _events_of(db, "shadow.run_failed")]
    assert failed and failed[-1]["live_attempts"] == 1

    sentinel = _stop_sentinel(db)
    sentinel.write_text('{"stop_id": "shadow-stop", "reason": "probe"}',
                        encoding="utf-8")
    try:
        with pytest.raises(ShadowError, match="stop"):
            shadow_run(db, improvement_id=improvement_id,
                       candidate_release_id=release_id,
                       decision_fn=propose, inputs_dir=inputs,
                       controller=CONTROLLER, live_marker=marker)
        frozen = [event.payload for event in _events_of(
            db, "shadow.run_failed")]
        assert frozen[-1]["stop_observed"] is True
        assert frozen[-1]["decisions_count"] == 0
        proposals_before_stop = len(
            _events_of(db, "shadow.decision_proposed"))
        with pytest.raises(ShadowError, match="stop"):
            shadow_run(db, improvement_id=improvement_id,
                       candidate_release_id=release_id,
                       decision_fn=propose, inputs_dir=inputs,
                       controller=CONTROLLER, live_marker=marker)
        assert len(_events_of(db, "shadow.decision_proposed")) == \
            proposals_before_stop
    finally:
        sentinel.unlink()


def test_restore_point_verified_rehearsed_and_restored(
        db: FoundryDatabase) -> None:
    improvement_id = _improvement(db, "R1")
    point_id = create_restore_point(db, label="r11-gate",
                                    controller=CONTROLLER)
    assert verify_restore_point(db, point_id) is True
    point_events = int(db.row("restore_points", "point_id", point_id)[
        "event_count"])
    freeze_active(db, reason="drift", controller=CONTROLLER)
    unfreeze_active(db, controller=CONTROLLER)
    analysis = data_loss_analysis(db, point_id)
    assert analysis["lost_events"] == len(db.events()) - point_events
    rehearsal = rehearse_restore(db, point_id=point_id,
                                 controller=CONTROLLER)
    assert rehearsal["ok"] is True
    restored = restore_from_point(db, point_id=point_id,
                                  controller=CONTROLLER)
    assert restored == point_events
    assert reconcile(db).consistent is True
    assert db.row("improvement_records", "improvement_id",
                  improvement_id) is not None


def test_restart_safe_cadence_intent_recovery_and_stale_reap(
        db: FoundryDatabase, tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path)
    from jarvis_foundry.events import Event
    promotion_id = "promotion-" + "a" * 12
    db.append(Event.create(
        "release.promotion_intent",
        {"promotion_id": promotion_id,
         "release_id": setup["candidate"],
         "verification_id": setup["verification_id"],
         "canary_id": None,
         "baseline_run_id": setup["baseline_run_id"],
         "candidate_run_id": setup["candidate_run_id"],
         "risk_class": "R1",
         "planned": {"previous": setup["genesis"],
                     "live": setup["candidate"], "candidate": None},
         "controller": CONTROLLER},
        source_type="supervisor", source_id="v6",
        idempotency_key=f"release.promotion_intent:{promotion_id}"))
    assert promotion_status(db, promotion_id) == "INTENT"
    recovered = recover_promotions(db, controller=CONTROLLER)
    assert promotion_id in recovered
    assert promotion_status(db, promotion_id) == "PROMOTED"
    assert slot_pointers(db)["live"] == setup["candidate"]
    # a hung canary is reaped by the controller WITHOUT cooperation
    canary_id = start_canary(
        db, improvement_id=setup["improvement_id"],
        candidate_release_id=setup["candidate"],
        traffic_share=0.05, duration_cycles=5, max_cost_usd=1.0,
        health_thresholds=THRESHOLDS, controller=CONTROLLER)
    reaped = reap_stale_canaries(db, current_cycle=10,
                                 max_cycles_without_tick=2,
                                 controller=CONTROLLER)
    assert canary_id in reaped
    assert canary_status(db, canary_id) == "TERMINATED"


def test_evolution_control_observe_active_gate(db: FoundryDatabase,
                                               tmp_path: Path) -> None:
    args = _cycle_args(db, tmp_path, risk="R1")
    control = EvolutionControl(db)
    assert control.mode() == "observe"
    with pytest.raises(EvolutionControlError, match="observe"):
        control.run_cycle(controller=SUPERVISOR_CONTROLLER,
                          **args)  # type: ignore[arg-type]
    control.enable(controller=SUPERVISOR_CONTROLLER)
    result = control.run_cycle(controller=SUPERVISOR_CONTROLLER,
                               **args)  # type: ignore[arg-type]
    assert result["outcome"] == "promoted"
    control.disable(controller=SUPERVISOR_CONTROLLER)
    assert control.mode() == "observe"
    with pytest.raises(EvolutionControlError, match="observe"):
        control.run_cycle(controller=SUPERVISOR_CONTROLLER,
                          **args)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# operational acceptance minimums from the durable ledger
# ---------------------------------------------------------------------------

def test_operational_acceptance_minimums_pass_and_archive(
        db: FoundryDatabase, tmp_path: Path) -> None:
    r1_args = _cycle_args(db, tmp_path, risk="R1")
    r1 = run_cycle(db, controller=CONTROLLER, **r1_args)  # type: ignore[arg-type]
    assert r1["outcome"] == "promoted"
    r2_args = _cycle_args(db, tmp_path, risk="R2")
    r2_args["canary_ticks"] = [
        {"cycle": 1, "traffic_served": 0.05, "errors": 0,
         "latency_s": 0.5, "cost_usd": 0.01},
        {"cycle": 2, "traffic_served": 0.05, "errors": 0,
         "latency_s": 0.5, "cost_usd": 0.01},
    ]
    r2 = run_cycle(db, controller=CONTROLLER, **r2_args)  # type: ignore[arg-type]
    assert r2["outcome"] == "promoted"
    rollback_release(db, trigger="outcome_regression",
                     reason="post-promotion regression",
                     controller=CONTROLLER)
    broken = _cycle_args(db, tmp_path, risk="R1",
                         candidate_command=BROKEN)
    rejected = run_cycle(db, controller=CONTROLLER, **broken)  # type: ignore[arg-type]
    assert rejected["outcome"] == "rejected"
    report = acceptance.operational_acceptance(db)
    assert report["passed"] is True, report["reasons"]
    assert report["cycles_total"] == 3
    assert report["promotions"] == 2
    assert report["r1_promotions"] == 1
    assert report["r2_canary_promotions"] == 1
    assert report["rejections"] == 1
    assert report["rollbacks"] == 1
    assert report["measured_improvements"] == 2
    engineering = acceptance.engineering_acceptance(db)
    assert engineering["passed"] is True, engineering
    run_id = acceptance.record_acceptance(db, report=report,
                                          controller=CONTROLLER)
    assert run_id.startswith("acceptance-")
    archived = [event.payload for event in _events_of(
        db, "acceptance.completed")]
    assert archived and archived[-1]["run_id"] == run_id
    assert archived[-1]["passed"] is True
