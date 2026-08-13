"""V6-S8: atomic promotion and automatic rollback.

Promotion requires the full gate chain — candidate slot, trusted
independent PASS verdict, measured improvement (comparison with
improvement_met=True), and a successful canary for R2/R3 — and applies
the slot transition atomically (intent + slot batch + promoted in ONE
transaction, crash-recoverable). Rollback is freeze-first, uses the
trigger vocabulary, restores the exact previous release, and records
the reason. A candidate can never promote or roll back itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.canary import (
    canary_tick,
    start_canary,
)
from jarvis_foundry.evolution.lab import (
    register_workload,
    run_evaluation,
)
from jarvis_foundry.evolution.promote import (
    TRIGGERS,
    PromoteError,
    freeze_active,
    is_frozen,
    promote_release,
    promotion_status,
    recover_promotions,
    rollback_release,
    unfreeze_active,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.evolution.registry import (
    register_release,
    set_slot,
    slot_pointers,
)
from jarvis_foundry.evolution.verify import (
    record_verification_request,
    record_verification_verdict,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "supervisor"
CANDIDATE_CONTROLLER = "candidate-builder-1"
DIRECTIVE_IDENTITY = {"provider": "opencode_go",
                      "model": "deepseek-v4-flash",
                      "session_id": "session-builder-s8"}
OTHER_IDENTITY = {"provider": "opencode_go",
                  "model": "deepseek-v4-flash",
                  "session_id": "session-verifier-s8"}
THRESHOLDS = {"error_rate_max": 0.05, "latency_max_s": 2.0}


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase, risk: str = "R1") -> str:
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
        mmi={"minimum_delta": 0.01, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "3 full-suite runs", "cycles": 3},
        declared_risk=risk,
        surfaces=["tests/foundry", "pyproject.toml"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _release(db: FoundryDatabase, improvement_id: str,
             risk: str = "R1", source_commit: str = "a" * 40) -> str:
    return register_release(
        db, source_commit=source_commit,
        artifact_hashes={"suite.txt": "b" * 64},
        dependencies=[], runtime_identity={"python": "3.13.5"},
        parent_release_id=None, improvement_id=improvement_id,
        risk_class=risk, migrations=[1, 2, 3], verdicts=[],
        controller="v6-s8-test")


def _verification(db: FoundryDatabase, improvement_id: str) -> str:
    verification_id = record_verification_request(
        db, improvement_id=improvement_id,
        subject_ref="release-candidate-0001",
        criteria=["criterion-1: suite green", "criterion-2: latency"],
        artifacts={"suite.txt": "b" * 64},
        builder_identity=DIRECTIVE_IDENTITY, controller=CONTROLLER)
    record_verification_verdict(
        db, verification_id=verification_id, verdict="PASS",
        criteria_results=[
            {"criterion": "criterion-1: suite green", "status": "PASS",
             "evidence_hash": "c" * 64},
            {"criterion": "criterion-2: latency", "status": "PASS",
             "evidence_hash": "d" * 64},
        ],
        findings=[], verifier_identity=OTHER_IDENTITY,
        evidence_refs=["var/evidence/v6-s8/probe.txt"])
    return verification_id


def _runs(db: FoundryDatabase, tmp_path: Path, improvement_id: str,
          slow: bool = True) -> tuple[str, str]:
    """Baseline + candidate runs on the same workload. The candidate is
    FASTER (improvement met for the MMI below)."""
    directory = tmp_path / "workload"
    directory.mkdir(exist_ok=True)
    for index in range(3):
        (directory / f"case-{index}.txt").write_text(
            f"input-{index}", encoding="utf-8")
    workload_id = register_workload(db, name="w1", input_dir=directory,
                                    config={}, seeds=[], controller=CONTROLLER)
    baseline_cmd = [sys.executable, "-c",
                    "import pathlib,sys,time\n"
                    "pathlib.Path(sys.argv[1]).read_text()\n"
                    "time.sleep(0.05)\n"]
    candidate_cmd = [sys.executable, "-c",
                     "import pathlib,sys\n"
                     "pathlib.Path(sys.argv[1]).read_text()\n"]
    baseline_id = run_evaluation(db, workload_id=workload_id,
                                 subject_kind="baseline",
                                 subject_identity="base-v1",
                                 command=baseline_cmd,
                                 controller=CONTROLLER)
    candidate_id = run_evaluation(db, workload_id=workload_id,
                                  subject_kind="candidate",
                                  subject_identity="cand-v1",
                                  command=candidate_cmd,
                                  controller=CONTROLLER)
    return baseline_id, candidate_id


def _canary(db: FoundryDatabase, improvement_id: str,
            release_id: str) -> str:
    canary_id = start_canary(
        db, improvement_id=improvement_id,
        candidate_release_id=release_id, traffic_share=0.05,
        duration_cycles=2, max_cost_usd=1.0,
        health_thresholds=THRESHOLDS, controller=CONTROLLER)
    canary_tick(db, canary_id=canary_id, cycle=1, traffic_served=0.05,
                errors=0, latency_s=0.5, cost_usd=0.01,
                controller=CONTROLLER)
    canary_tick(db, canary_id=canary_id, cycle=2, traffic_served=0.05,
                errors=0, latency_s=0.5, cost_usd=0.01,
                controller=CONTROLLER)
    return canary_id


def _promotable(db: FoundryDatabase, tmp_path: Path,
                risk: str = "R1") -> dict[str, str]:
    """Full gate chain: genesis live release, candidate release in the
    candidate slot, trusted PASS verdict, measured improvement, and
    (for R2+) a successful canary. NOTE: the surfaces classifier forces
    tests/ + pyproject.toml to R1, so declared R0 freezes at R1; the
    manifest risk must equal the FROZEN risk."""
    frozen_risk = "R2" if risk == "R2" else "R1"
    improvement_id = _improvement(db, risk=risk)
    genesis = _release(db, improvement_id, frozen_risk,
                       source_commit="a" * 40)
    set_slot(db, "live", genesis, reason="genesis",
             controller=CONTROLLER)
    candidate = _release(db, improvement_id, frozen_risk,
                         source_commit="c" * 40)
    set_slot(db, "candidate", candidate, reason="candidate",
             controller=CONTROLLER)
    verification_id = _verification(db, improvement_id)
    baseline_id, candidate_run_id = _runs(db, tmp_path, improvement_id)
    return {"improvement_id": improvement_id, "genesis": genesis,
            "candidate": candidate, "verification_id": verification_id,
            "baseline_run_id": baseline_id,
            "candidate_run_id": candidate_run_id}


# ---------------------------------------------------------------------------
# promotion gates
# ---------------------------------------------------------------------------

def test_promotion_gates_refuse_missing_pieces(db: FoundryDatabase,
                                               tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R2")
    with pytest.raises(PromoteError):  # no canary for R2
        promote_release(
            db, candidate_release_id=setup["candidate"],
            verification_id=setup["verification_id"],
            baseline_run_id=setup["baseline_run_id"],
            candidate_run_id=setup["candidate_run_id"],
            controller=CONTROLLER)


def test_promotion_requires_candidate_slot(db: FoundryDatabase,
                                           tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path)
    # move the candidate out of the candidate slot (clear + quarantine)
    set_slot(db, "candidate", None, reason="move",
             controller=CONTROLLER)
    set_slot(db, "quarantine", setup["candidate"],
             reason="move", controller=CONTROLLER)
    with pytest.raises(PromoteError):
        promote_release(
            db, candidate_release_id=setup["candidate"],
            verification_id=setup["verification_id"],
            baseline_run_id=setup["baseline_run_id"],
            candidate_run_id=setup["candidate_run_id"],
            controller=CONTROLLER)


def test_promotion_requires_trusted_verdict(db: FoundryDatabase,
                                            tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    # record a FAIL verdict for a fresh verification
    fail_verification = record_verification_request(
        db, improvement_id=setup["improvement_id"],
        subject_ref="release-candidate-0001",
        criteria=["criterion-1: suite green"],
        artifacts={"suite.txt": "b" * 64},
        builder_identity=DIRECTIVE_IDENTITY, controller=CONTROLLER)
    record_verification_verdict(
        db, verification_id=fail_verification, verdict="FAIL",
        criteria_results=[
            {"criterion": "criterion-1: suite green", "status": "FAIL",
             "evidence_hash": "e" * 64}],
        findings=[{"severity": "HIGH", "finding": "suite red"}],
        verifier_identity=OTHER_IDENTITY,
        evidence_refs=["var/evidence/v6-s8/fail.txt"])
    with pytest.raises(PromoteError):
        promote_release(
            db, candidate_release_id=setup["candidate"],
            verification_id=fail_verification,
            baseline_run_id=setup["baseline_run_id"],
            candidate_run_id=setup["candidate_run_id"],
            controller=CONTROLLER)


def test_promotion_r0_without_canary_succeeds(db: FoundryDatabase,
                                              tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    promotion_id = promote_release(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run_id"],
        candidate_run_id=setup["candidate_run_id"],
        controller=CONTROLLER)
    assert promotion_status(db, promotion_id) == "PROMOTED"
    pointers = slot_pointers(db)
    assert pointers["live"] == setup["candidate"]
    assert pointers["previous"] == setup["genesis"]
    assert pointers["candidate"] is None


def test_promotion_r2_requires_successful_canary(db: FoundryDatabase,
                                                 tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R2")
    failing_canary = start_canary(
        db, improvement_id=setup["improvement_id"],
        candidate_release_id=setup["candidate"], traffic_share=0.05,
        duration_cycles=5, max_cost_usd=1.0,
        health_thresholds=THRESHOLDS, controller=CONTROLLER)
    status = canary_tick(db, canary_id=failing_canary, cycle=1,
                         traffic_served=0.05, errors=9, latency_s=0.5,
                         cost_usd=0.01, controller=CONTROLLER)
    assert status == "TERMINATED"
    with pytest.raises(PromoteError):
        promote_release(
            db, candidate_release_id=setup["candidate"],
            verification_id=setup["verification_id"],
            canary_id=failing_canary,
            baseline_run_id=setup["baseline_run_id"],
            candidate_run_id=setup["candidate_run_id"],
            controller=CONTROLLER)


def test_promotion_r2_with_successful_canary_succeeds(
        db: FoundryDatabase, tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R2")
    canary_id = _canary(db, setup["improvement_id"], setup["candidate"])
    promotion_id = promote_release(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"], canary_id=canary_id,
        baseline_run_id=setup["baseline_run_id"],
        candidate_run_id=setup["candidate_run_id"],
        controller=CONTROLLER)
    assert promotion_status(db, promotion_id) == "PROMOTED"
    assert slot_pointers(db)["live"] == setup["candidate"]


def test_promotion_refused_when_frozen_or_stopped_or_vetoed(
        db: FoundryDatabase, tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    freeze_active(db, reason="investigation", controller=CONTROLLER)
    with pytest.raises(PromoteError):
        promote_release(
            db, candidate_release_id=setup["candidate"],
            verification_id=setup["verification_id"],
            baseline_run_id=setup["baseline_run_id"],
            candidate_run_id=setup["candidate_run_id"],
            controller=CONTROLLER)
    unfreeze_active(db, controller=CONTROLLER)
    with pytest.raises(PromoteError):  # vetoed
        promote_release(
            db, candidate_release_id=setup["candidate"],
            verification_id=setup["verification_id"],
            baseline_run_id=setup["baseline_run_id"],
            candidate_run_id=setup["candidate_run_id"],
            controller=CONTROLLER, veto_check=lambda: True)
    sentinel = Path(db.path).parent / "EMERGENCY_STOP"
    sentinel.write_text("stop", encoding="utf-8")
    try:
        with pytest.raises(PromoteError):  # stop active
            promote_release(
                db, candidate_release_id=setup["candidate"],
                verification_id=setup["verification_id"],
                baseline_run_id=setup["baseline_run_id"],
                candidate_run_id=setup["candidate_run_id"],
                controller=CONTROLLER)
    finally:
        sentinel.unlink()


def test_candidate_cannot_promote(db: FoundryDatabase,
                                  tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    with pytest.raises(PromoteError):
        promote_release(
            db, candidate_release_id=setup["candidate"],
            verification_id=setup["verification_id"],
            baseline_run_id=setup["baseline_run_id"],
            candidate_run_id=setup["candidate_run_id"],
            controller=CANDIDATE_CONTROLLER)
    assert slot_pointers(db)["live"] == setup["genesis"]


# ---------------------------------------------------------------------------
# atomicity and recovery
# ---------------------------------------------------------------------------

def test_promotion_atomic_single_batch(db: FoundryDatabase,
                                       tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    events_before = len(db.events())
    promote_release(db, candidate_release_id=setup["candidate"],
                    verification_id=setup["verification_id"],
                    baseline_run_id=setup["baseline_run_id"],
                    candidate_run_id=setup["candidate_run_id"],
                    controller=CONTROLLER)
    # intent (1) + previous (1) + live (1) + candidate-clear (1) +
    # promoted (1) + comparison (1) = 6 new events
    assert len(db.events()) == events_before + 6
    assert promotion_status(db, _promotion_of(db, setup["candidate"])) \
        == "PROMOTED"


def _promotion_of(db: FoundryDatabase, release_id: str) -> str:
    for event in db.events():
        if event.event_type == "release.promoted" and \
                event.payload["release_id"] == release_id:
            return event.payload["promotion_id"]
    raise AssertionError(f"no promoted event for {release_id}")


def test_crash_recovery_reapplies_intent(db: FoundryDatabase,
                                         tmp_path: Path) -> None:
    """A crash between intent and completion is recovered: the same
    promotion is re-driven to PROMOTED idempotently."""
    setup = _promotable(db, tmp_path, risk="R0")
    # simulate the crash: only the intent event was appended
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
    assert slot_pointers(db)["previous"] == setup["genesis"]


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

def test_rollback_restores_previous_and_quarantines(
        db: FoundryDatabase, tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    promotion_id = promote_release(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run_id"],
        candidate_run_id=setup["candidate_run_id"],
        controller=CONTROLLER)
    rollback_release(db, trigger="outcome_regression",
                     reason="error rate regressed after promotion",
                     controller=CONTROLLER)
    pointers = slot_pointers(db)
    assert pointers["live"] == setup["genesis"]
    assert pointers["quarantine"] == setup["candidate"]
    assert pointers["previous"] is None
    assert promotion_status(db, promotion_id) == "ROLLED_BACK"
    # the promotion ledger kept its history
    assert is_frozen(db) is True  # freeze precedes rollback


def test_rollback_requires_previous(db: FoundryDatabase,
                                    tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    with pytest.raises(PromoteError):  # nothing to roll back to
        rollback_release(db, trigger="manual", reason="test",
                         controller=CONTROLLER)
    assert slot_pointers(db)["live"] == setup["genesis"]


def test_rollback_trigger_vocabulary(db: FoundryDatabase,
                                     tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    promote_release(db, candidate_release_id=setup["candidate"],
                    verification_id=setup["verification_id"],
                    baseline_run_id=setup["baseline_run_id"],
                    candidate_run_id=setup["candidate_run_id"],
                    controller=CONTROLLER)
    with pytest.raises(PromoteError):
        rollback_release(db, trigger="made-up-trigger", reason="x",
                         controller=CONTROLLER)
    assert slot_pointers(db)["live"] == setup["candidate"]
    # every vocabulary trigger is accepted
    for trigger in TRIGGERS:
        rollback_release(db, trigger=trigger, reason=f"test {trigger}",
                         controller=CONTROLLER)
        assert slot_pointers(db)["live"] == setup["genesis"]
        # rollback freezes (freeze precedes rollback) and moves the
        # failed release to quarantine; unfreeze + return it to the
        # candidate slot for the next promotion round
        unfreeze_active(db, controller=CONTROLLER)
        set_slot(db, "candidate", setup["candidate"],
                 reason="candidate", controller=CONTROLLER,
                 transition_id=f"re-candidate-{trigger}")
        promote_release(db, candidate_release_id=setup["candidate"],
                        verification_id=setup["verification_id"],
                        baseline_run_id=setup["baseline_run_id"],
                        candidate_run_id=setup["candidate_run_id"],
                        controller=CONTROLLER)


def test_rollback_reconciles(db: FoundryDatabase, tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    promote_release(db, candidate_release_id=setup["candidate"],
                    verification_id=setup["verification_id"],
                    baseline_run_id=setup["baseline_run_id"],
                    candidate_run_id=setup["candidate_run_id"],
                    controller=CONTROLLER)
    rollback_release(db, trigger="verifier_contradiction",
                     reason="second verifier contradicted the PASS",
                     controller=CONTROLLER)
    report = reconcile(db)
    assert report.consistent is True


def test_candidate_cannot_rollback(db: FoundryDatabase,
                                   tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    promote_release(db, candidate_release_id=setup["candidate"],
                    verification_id=setup["verification_id"],
                    baseline_run_id=setup["baseline_run_id"],
                    candidate_run_id=setup["candidate_run_id"],
                    controller=CONTROLLER)
    with pytest.raises(PromoteError):
        rollback_release(db, trigger="manual", reason="self-rollback",
                         controller=CANDIDATE_CONTROLLER)
    assert slot_pointers(db)["live"] == setup["candidate"]


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase, tmp_path: Path) -> None:
    setup = _promotable(db, tmp_path, risk="R0")
    promotion_id = promote_release(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run_id"],
        candidate_run_id=setup["candidate_run_id"],
        controller=CONTROLLER)
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("promotions")
    db.rebuild_projections()
    assert db.count("promotions") == before
    row = db.row("promotions", "promotion_id", promotion_id)
    assert row is not None and row["status"] == "PROMOTED"
    # release_control rows are created lazily by the first
    # frozen/unfrozen event (no DDL seed — reconcile stays clean)
    assert db.count("release_control") == 0
    freeze_active(db, reason="test", controller=CONTROLLER)
    assert db.count("release_control") == 1
    assert is_frozen(db) is True
    unfreeze_active(db, controller=CONTROLLER)
    assert is_frozen(db) is False
    assert reconcile(db).consistent is True
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 28
    assert SCHEMA_VERSION >= 28


# -----------------------------------...[truncated]


# ---------------------------------------------------------------------------
# round-1 repairs (deleg_bbce3e0a findings: recovery dedupe/state/freeze,
# already-live guard)
# ---------------------------------------------------------------------------

def test_promote_refused_when_already_live(db: FoundryDatabase,
                                           tmp_path: Path) -> None:
    """S8 r1 (MEDIUM): an already-live release cannot be promoted
    again even if the candidate slot is re-set."""
    setup = _promotable(db, tmp_path, risk="R0")
    promote_release(db, candidate_release_id=setup["candidate"],
                    verification_id=setup["verification_id"],
                    baseline_run_id=setup["baseline_run_id"],
                    candidate_run_id=setup["candidate_run_id"],
                    controller=CONTROLLER)
    # re-set the candidate slot to the now-live release
    set_slot(db, "candidate", setup["candidate"],
             reason="candidate", controller=CONTROLLER,
             transition_id="re-set-after-live")
    with pytest.raises(PromoteError):
        promote_release(db, candidate_release_id=setup["candidate"],
                        verification_id=setup["verification_id"],
                        baseline_run_id=setup["baseline_run_id"],
                        candidate_run_id=setup["candidate_run_id"],
                        controller=CONTROLLER)
    assert slot_pointers(db)["live"] == setup["candidate"]


def test_recovery_skips_when_frozen_or_moved(db: FoundryDatabase,
                                             tmp_path: Path) -> None:
    """S8 r1 (MEDIUM x2): recovery re-checks the world — frozen
    releases and releases moved out of the candidate slot post-crash
    are left INTENT, not promoted."""
    setup = _promotable(db, tmp_path, risk="R0")
    from jarvis_foundry.events import Event
    promotion_id = "promotion-" + "b" * 12
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
    # frozen world: recovery skips
    freeze_active(db, reason="investigation", controller=CONTROLLER)
    assert recover_promotions(db, controller=CONTROLLER) == []
    assert promotion_status(db, promotion_id) == "INTENT"
    unfreeze_active(db, controller=CONTROLLER)
    # moved world: the release left the candidate slot post-crash
    set_slot(db, "candidate", None, reason="move",
             controller=CONTROLLER)
    set_slot(db, "quarantine", setup["candidate"],
             reason="move", controller=CONTROLLER)
    assert recover_promotions(db, controller=CONTROLLER) == []
    assert promotion_status(db, promotion_id) == "INTENT"
    # restore the slot: recovery now completes it
    set_slot(db, "candidate", setup["candidate"],
             reason="candidate", controller=CONTROLLER,
             transition_id="back-to-candidate")
    recovered = recover_promotions(db, controller=CONTROLLER)
    assert promotion_id in recovered
    assert promotion_status(db, promotion_id) == "PROMOTED"
    assert slot_pointers(db)["live"] == setup["candidate"]
    assert slot_pointers(db)["previous"] == setup["genesis"]


def test_recovery_after_slot_batch_applied_is_clean(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """S8 r1 (MEDIUM): a crash AFTER the slot batch but BEFORE
    promoted — recovery completes with dedupe, not EventConflict, and
    the previous pointer is NOT corrupted (planned.previous governs)."""
    setup = _promotable(db, tmp_path, risk="R0")
    promotion_id = promote_release(
        db, candidate_release_id=setup["candidate"],
        verification_id=setup["verification_id"],
        baseline_run_id=setup["baseline_run_id"],
        candidate_run_id=setup["candidate_run_id"],
        controller=CONTROLLER)
    # simulate the crash: roll back the promoted event only — the slot
    # batch stays applied. Rebuild the world: recovery sees an INTENT
    # whose release is already live.
    from jarvis_foundry.events import Event
    events_before = len(db.events())
    row = db.row("promotions", "promotion_id", promotion_id)
    # remove the promoted row (crash before the handler ran) and the
    # promoted event is already gone; instead simulate: append the
    # intent WITHOUT the promoted event by re-driving recovery on a
    # FRESH intent whose slot batch is already applied:
    events = [e for e in db.events()
              if e.event_type == "release.promoted"
              and e.payload["promotion_id"] == promotion_id]
    assert len(events) == 1
    # delete the promoted event + row to simulate the crash window
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM events WHERE idempotency_key = ?",
            (f"release.promoted:{promotion_id}",))
        connection.execute(
            "UPDATE promotions SET status = 'INTENT', finished_at = NULL "
            "WHERE promotion_id = ?", (promotion_id,))
    assert promotion_status(db, promotion_id) == "INTENT"
    assert slot_pointers(db)["live"] == setup["candidate"]
    recovered = recover_promotions(db, controller=CONTROLLER)
    assert promotion_id in recovered
    assert promotion_status(db, promotion_id) == "PROMOTED"
    # pointers intact — previous is the ORIGINAL genesis, not the
    # already-live candidate
    assert slot_pointers(db)["live"] == setup["candidate"]
    assert slot_pointers(db)["previous"] == setup["genesis"]
    # dedupe: the live/candidate slot events were NOT re-created
    promoted_count = sum(1 for e in db.events()
                         if e.event_type == "release.promoted"
                         and e.payload["promotion_id"] == promotion_id)
    assert promoted_count == 1
    assert len(db.events()) <= events_before + 2  # +1 previous echo + promoted
