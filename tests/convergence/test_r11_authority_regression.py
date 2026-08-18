"""R11 authority regression: effectful evolution is Supervisor-pinned.

Covers the canonical low-level evolution APIs directly (not only the
control-plane wrapper): freeze/unfreeze, slot/registry transitions,
discovery, canary, cycle, promote/rollback, restore, acceptance, shadow
and verification requests ALL refuse any controller identity other than
the exact Supervisor identity, and a candidate-created verification
request cannot become a trusted self-certification path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution import acceptance
from jarvis_foundry.evolution.builder import (
    BuilderError,
    build_candidate,
    candidate_builds,
    register_candidate_root,
)
from jarvis_foundry.evolution.canary import (
    CanaryError,
    canary_tick,
    start_canary,
    terminate_canary,
)
from jarvis_foundry.evolution.cycle import CycleError, run_cycle
from jarvis_foundry.evolution.discover import (
    DiscoveryError,
    candidate_improvements,
    record_discovered_improvements,
)
from jarvis_foundry.evolution.promote import (
    PromoteError,
    freeze_active,
    promote_release,
    rollback_release,
    unfreeze_active,
)
from jarvis_foundry.evolution.portfolio import (
    PortfolioError,
    register_project,
    select_projects,
)
from jarvis_foundry.evolution.lab import (
    LabError,
    register_workload,
    run_evaluation,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import (
    ReleaseError,
    register_release,
    set_slot,
)
from jarvis_foundry.evolution.restore import (
    RestoreError,
    create_restore_point,
    rehearse_restore,
    restore_from_point,
)
from jarvis_foundry.evolution.scheduler import (
    SchedulerError,
    disable_scheduler,
    enable_scheduler,
    register_schedule_item,
    tick_scheduler,
)
from jarvis_foundry.capabilities.registry import (
    CapabilityError,
    broker_mission_need,
    register_capability,
)
from jarvis_foundry.gates.minigates import GateError, gate_operation
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
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER

CONTROLLER = "supervisor"
CANDIDATE = "candidate-builder-1"
DIRECTIVE_IDENTITY = {"provider": "opencode_go",
                      "model": "deepseek-v4-flash",
                      "session_id": "session-builder-authority"}
OTHER_IDENTITY = {"provider": "opencode_go",
                  "model": "deepseek-v4-flash",
                  "session_id": "session-verifier-authority"}


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
        problem="evolution authority regression observation",
        evidence_refs=["var/evidence/r11-authority/observation.txt"],
        provenance={"source": "r11 authority regression",
                    "observed_at": "2026-08-18T00:00:00Z"},
        hypothesis="pinned supervisor authority removes spoofing",
        expected_mechanism="exact identity gate at every effectful entry",
        baseline_metric={"name": "authority_refusals", "value": 0,
                         "unit": "refusals",
                         "source": "r11-authority-observation.txt"},
        mmi={"minimum_delta": 1.0, "direction": "decrease",
             "unit": "refusals"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/convergence",
                  "hash": "a" * 64},
        observation={"requirement": "1 regression sweep", "cycles": 1},
        declared_risk="R1", surfaces=["tests/convergence"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _release(db: FoundryDatabase, improvement_id: str) -> str:
    return register_release(
        db, source_commit="a" * 40,
        artifact_hashes={"suite.txt": "b" * 64},
        dependencies=[], runtime_identity={"python": "3.13.5"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class="R1", migrations=[], verdicts=[],
        controller=CONTROLLER)


def _inputs(tmp_path: Path) -> Path:
    directory = tmp_path / "shadow-inputs"
    directory.mkdir(exist_ok=True)
    (directory / "case.txt").write_text("input", encoding="utf-8")
    return directory


def _propose(case: Path, sink: ShadowSink) -> None:
    sink.propose(decision="proposal", expected_effect="none",
                 cost_usd=0.0)


def test_refuses_non_supervisor_identities(db: FoundryDatabase,
                                           tmp_path: Path) -> None:
    """Every effectful low-level entry refuses a non-Supervisor
    controller BEFORE any state mutation."""
    improvement_id = _improvement(db)
    events_before = len(db.events())
    with pytest.raises(ReleaseError, match="exact Supervisor"):
        register_release(
            db, source_commit="a" * 40,
            artifact_hashes={"suite.txt": "b" * 64},
            dependencies=[], runtime_identity={"python": "3.13.5"},
            parent_release_id=None, improvement_id=improvement_id,
            risk_class="R1", migrations=[], verdicts=[],
            controller=CANDIDATE)
    assert len(db.events()) == events_before

    release_id = _release(db, improvement_id)
    events_before = len(db.events())

    with pytest.raises(PromoteError):
        freeze_active(db, reason="probe", controller=CANDIDATE)
    with pytest.raises(PromoteError):
        unfreeze_active(db, controller=CANDIDATE)
    with pytest.raises(ReleaseError):
        set_slot(db, "candidate", release_id, reason="probe",
                 controller=CANDIDATE)
    with pytest.raises(DiscoveryError):
        candidate_improvements(db, sources={}, controller=CANDIDATE)
    with pytest.raises(DiscoveryError):
        record_discovered_improvements(db, candidates=[], sources={},
                                       controller=CANDIDATE)
    with pytest.raises(CanaryError):
        start_canary(db, improvement_id=improvement_id,
                     candidate_release_id=release_id,
                     traffic_share=0.05, duration_cycles=2,
                     max_cost_usd=0.0,
                     health_thresholds={"error_rate_max": 0.05,
                                        "latency_max_s": 2.0},
                     controller=CANDIDATE)
    with pytest.raises(CanaryError):
        canary_tick(db, canary_id="canary-x", cycle=1,
                    traffic_served=0.0, errors=0, latency_s=0.0,
                    cost_usd=0.0, controller=CANDIDATE)
    with pytest.raises(CanaryError):
        terminate_canary(db, canary_id="canary-x", reason="probe",
                         controller=CANDIDATE)
    with pytest.raises(RestoreError):
        create_restore_point(db, label="probe", controller=CANDIDATE)
    with pytest.raises(RestoreError):
        restore_from_point(db, point_id="point-x", controller=CANDIDATE)
    with pytest.raises(RestoreError):
        rehearse_restore(db, point_id="point-x", controller=CANDIDATE)
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.record_acceptance(db, report={"passed": True},
                                     controller=CANDIDATE)
    with pytest.raises(ShadowError):
        shadow_run(db, improvement_id=improvement_id,
                   candidate_release_id=release_id,
                   decision_fn=_propose, inputs_dir=_inputs(tmp_path),
                   controller=CANDIDATE)
    with pytest.raises(CycleError):
        run_cycle(db, improvement_id=improvement_id,
                  source_commit="c" * 40, workload_dir=tmp_path,
                  baseline_command=["true"], candidate_command=["true"],
                  build_command=["true"], controller=CANDIDATE)

    assert len(db.events()) == events_before


def test_promote_and_rollback_refuse_candidate(
        db: FoundryDatabase) -> None:
    improvement_id = _improvement(db)
    genesis = _release(db, improvement_id)
    set_slot(db, "live", genesis, reason="genesis",
             controller=CONTROLLER)
    candidate = _release(db, improvement_id)
    set_slot(db, "candidate", candidate, reason="candidate",
             controller=CONTROLLER)
    with pytest.raises(PromoteError):
        promote_release(db, candidate_release_id=candidate,
                        verification_id="verification-x",
                        baseline_run_id="baseline-x",
                        candidate_run_id="candidate-x",
                        controller=CANDIDATE)
    with pytest.raises(PromoteError):
        rollback_release(db, trigger="manual", reason="probe",
                         controller=CANDIDATE)


def test_verification_request_supervisor_only(db: FoundryDatabase) -> None:
    """A candidate cannot create a verification request against its own
    build, so a candidate session can never open a trusted-self path."""
    improvement_id = _improvement(db)
    with pytest.raises(VerificationError):
        record_verification_request(
            db, improvement_id=improvement_id,
            subject_ref="release-candidate-x",
            criteria=["criterion: welcome"],
            artifacts={"suite.txt": "b" * 64},
            builder_identity=DIRECTIVE_IDENTITY, controller=CANDIDATE)
    # the genuine Supervisor-created request still exists...
    verification_id = record_verification_request(
        db, improvement_id=improvement_id,
        subject_ref="release-candidate-x",
        criteria=["criterion: welcome"],
        artifacts={"suite.txt": "b" * 64},
        builder_identity=DIRECTIVE_IDENTITY, controller=CONTROLLER)
    # ...but a same-session verdict is refused: no self-certification
    with pytest.raises(VerificationError):
        record_verification_verdict(
            db, verification_id=verification_id, verdict="PASS",
            criteria_results=[
                {"criterion": "criterion: welcome", "status": "PASS",
                 "evidence_hash": "c" * 64}],
            findings=[], verifier_identity=DIRECTIVE_IDENTITY,
            evidence_refs=["var/evidence/r11-authority/self.txt"])
    assert is_trusted_verdict(db, verification_id,
                              DIRECTIVE_IDENTITY) is False
    # independent completion is still valid
    record_verification_verdict(
        db, verification_id=verification_id, verdict="PASS",
        criteria_results=[
            {"criterion": "criterion: welcome", "status": "PASS",
             "evidence_hash": "c" * 64}],
        findings=[], verifier_identity=OTHER_IDENTITY,
        evidence_refs=["var/evidence/r11-authority/independent.txt"])
    assert is_trusted_verdict(db, verification_id,
                              DIRECTIVE_IDENTITY) is True
    assert SUPERVISOR_CONTROLLER == CONTROLLER == "supervisor"


def test_direct_builder_and_lab_mutators_refuse_forged_controller(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """Workers/candidates cannot bypass the direct builder or lab APIs."""
    improvement_id = _improvement(db)

    root = tmp_path / "candidate-root"
    before = len(db.events())
    with pytest.raises(BuilderError, match="exact Supervisor"):
        register_candidate_root(db, root, controller=CANDIDATE)
    assert len(db.events()) == before
    assert not root.exists()

    register_candidate_root(db, root, controller=CONTROLLER)
    before = len(db.events())
    with pytest.raises(BuilderError, match="exact Supervisor"):
        build_candidate(
            db, improvement_id=improvement_id,
            build_command=[sys.executable, "-c", "pass"],
            candidate_root=root, controller=CANDIDATE)
    assert len(db.events()) == before
    assert not (root / ".candidate-build.json").exists()
    assert candidate_builds(db) == []

    build_id = build_candidate(
        db, improvement_id=improvement_id,
        build_command=[sys.executable, "-c",
                       "from pathlib import Path; "
                       "Path('authority-artifact.txt').write_text('built', "
                       "encoding='utf-8')"],
        candidate_root=root, controller=CONTROLLER)
    assert db.row("candidate_builds", "build_id", build_id) is not None

    workload = tmp_path / "workload"
    workload.mkdir()
    (workload / "case.txt").write_text("case", encoding="utf-8")
    before = len(db.events())
    with pytest.raises(LabError, match="exact Supervisor"):
        register_workload(db, name="forged", input_dir=workload,
                          controller=CANDIDATE)
    assert len(db.events()) == before
    assert not [event for event in db.events()
                if event.event_type == "evaluation.workload_registered"]

    workload_id = register_workload(db, name="valid", input_dir=workload,
                                    controller=CONTROLLER)
    scratch = Path(db.path).parent / "lab-scratch"
    scratch_before = set(scratch.iterdir()) if scratch.exists() else set()
    before = len(db.events())
    with pytest.raises(LabError, match="exact Supervisor"):
        run_evaluation(
            db, workload_id=workload_id, subject_kind="baseline",
            subject_identity="forged-worker", command=[sys.executable,
            "-c", "pass"], controller=CANDIDATE)
    scratch_after = set(scratch.iterdir()) if scratch.exists() else set()
    assert len(db.events()) == before
    assert scratch_after == scratch_before


def test_portfolio_scheduler_capability_and_minigate_refuse_forged_controller(
        db: FoundryDatabase) -> None:
    """Every canonical V7 control family pins its direct entry gate.

    These calls intentionally stop at the authority check, proving that a
    forged worker identity cannot reach ledger mutation in portfolio,
    scheduler, capability brokerage, or family mini-gates.
    """
    before = len(db.events())
    source = {"source_type": "r11-authority", "source_id": "forged"}

    with pytest.raises(PortfolioError, match="Supervisor"):
        register_project(db, controller=CANDIDATE, project_id="p-forged",
                         source_type=source["source_type"],
                         source_id=source["source_id"])
    with pytest.raises(PortfolioError, match="Supervisor"):
        select_projects(db, controller=CANDIDATE, available_slots=1,
                        **source)

    with pytest.raises(SchedulerError, match="Supervisor"):
        enable_scheduler(db, controller=CANDIDATE, **source)
    with pytest.raises(SchedulerError, match="Supervisor"):
        disable_scheduler(db, controller=CANDIDATE, **source)
    with pytest.raises(SchedulerError, match="Supervisor"):
        register_schedule_item(db, item_id="i-forged", controller=CANDIDATE,
                               **source)
    with pytest.raises(SchedulerError, match="Supervisor"):
        tick_scheduler(db, controller=CANDIDATE, **source)

    with pytest.raises(CapabilityError, match="Supervisor"):
        register_capability(
            db, capability_id="cap-forged", name="forged", family="local",
            surfaces=["file"], effect_class="file-write", risk="R1",
            verification_hint="read-back", controller=CANDIDATE, **source)
    with pytest.raises(CapabilityError, match="Supervisor"):
        broker_mission_need(db, need_id="need-forged", surfaces=["file"],
                            controller=CANDIDATE, **source)

    with pytest.raises(GateError, match="Supervisor"):
        gate_operation(db, family="local-computer", operation="read_file",
                       controller=CANDIDATE, params={}, **source)

    assert len(db.events()) == before
