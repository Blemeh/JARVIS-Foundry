"""Master Goal 2 — Independent Verifier Hardening: adversarial coverage.

Every REQUIRED PROOF row is attacked here:

- self-certification (builder run identity == verifier run identity);
- wrong target / wrong step / stale builder-run binding;
- stale hash, tampered binding, replayed terminal event;
- missing evidence keys and missing fabrication review;
- false success (claim contradicted by observed evidence);
- verifier crash -> honest BLOCKED, resumable with a fresh binding;
- repair invalidates the old verdict (fresh binding + fresh verifier);
- duplicate completion events are idempotent;
- STOP blocks dispatch and acceptance; authenticated veto precedence;
- crash/resume and expired-lease idempotency of the acceptance decision;
- same-provider verification records reduced independence while
  enforcing fresh-session isolation.

The builder's report is never proof: the gate re-reads the durable ledger
and re-hashes the live tree itself.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from test_mission_loop import mission_fixture
from test_worker_orchestrator import (
    MutatingScriptedAdapter,
    bounded_contract,
    fake_verifier,
    openhands_ready_router,
)
from worker_test_helpers import make_controller, make_repository

from jarvis_foundry.control.broker import emergency_stop_reason, matching_veto_reason
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.verification import (
    record_verification_outcome,
)
from jarvis_foundry.mission.verifier_binding import (
    BINDING_SCHEMA,
    VERDICTS,
    AcceptanceGate,
    build_terminal_evidence,
    capture_binding,
    fresh_verifier_identity,
)
from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import (
    WORKER_HERMES,
    WORKER_OPENHANDS,
)
from jarvis_foundry.workers.orchestrator import PASSED
from jarvis_foundry.workers.router import READY


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def started_payload(database: FoundryDatabase, verdict_id: str) -> dict:
    for event in database.events():
        if event.event_type == "verification.started" and \
                str(event.payload.get("verdict_id") or "") == verdict_id:
            return dict(event.payload)
    raise AssertionError(f"no verification.started event for {verdict_id}")


def all_verification_events(database: FoundryDatabase) -> list[tuple[str, dict]]:
    out = []
    for event in database.events():
        if event.event_type.startswith("verification."):
            out.append((event.event_type, dict(event.payload)))
    return out


def gate_from(database: FoundryDatabase, tmp_path: Path) -> AcceptanceGate:
    return AcceptanceGate(database, stop_path=tmp_path / "STOP")


# ---------------------------------------------------------------------------
# Unit surface: binding capture + verdict vocabulary
# ---------------------------------------------------------------------------


def test_capture_binding_freezes_full_tuple(tmp_path):
    repository = make_repository(tmp_path)
    target = repository / "out"
    target.mkdir()
    (target / "a.txt").write_text("one\n", encoding="utf-8")
    database = FoundryDatabase(tmp_path / "db.sqlite")
    database.initialize()
    builder_run = "run-builder"
    verdict_id, verifier_run_id = fresh_verifier_identity(builder_run)
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="m1", step_id="s1",
        target=target, builder_run_id=builder_run,
        verifier_run_id=verifier_run_id,
        context_manifest_hash="cmh-123",
        independence={"session_isolation": "fresh-session"},
        criteria=["artifact exists"],
    )
    assert capture.binding_hash
    artifact_file = target / "a.txt"
    assert capture.artifact_hashes == {
        "a.txt": sha256_bytes(artifact_file.read_bytes())}
    assert capture.context_manifest_hash == "cmh-123"
    payload = started_payload(database, verdict_id)
    assert payload["binding"]["binding_hash"] == capture.binding_hash
    assert payload["binding"]["schema"] == BINDING_SCHEMA
    # distinct identities: verifier never derives from builder
    assert verifier_run_id != builder_run


def test_verdict_vocabulary_is_exact():
    assert VERDICTS == ("PASS", "FAIL", "BLOCKED", "NOT_TESTED")
    with pytest.raises(ValueError):
        record_verification_outcome(
            FoundryDatabase(":memory:"), verdict_id="v", outcome="MAYBE",
            findings=[], evidence={})


def test_gate_fails_closed_on_internal_error(tmp_path):
    database, _repository, _policy, _router = mission_fixture(tmp_path)
    broken_target = tmp_path / "does-not-exist-at-all" / "deeper"
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1",
        target=broken_target, builder_run_id="run-x")
    assert decision.decision == "REFUSE"
    assert "no verification verdict" in decision.reason


# ---------------------------------------------------------------------------
# Self-certification attacks
# ---------------------------------------------------------------------------


def test_builder_cannot_certify_own_work(tmp_path):
    """A PASS recorded under a shared builder/verifier identity must be
    refused — even when every artifact hash matches."""
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "artifact.md").write_text("deliverable\n", encoding="utf-8")
    shared_run = "run-shared"
    verdict_id, _ = fresh_verifier_identity(shared_run)
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id=shared_run,
        verifier_run_id=shared_run,  # SELF-CERTIFICATION
        independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=shared_run))
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id=shared_run)
    assert not decision.accepted
    assert "self" in decision.reason.lower() or "certif" in decision.reason.lower()


def test_hybrid_controller_never_reuses_builder_run_for_verifier(tmp_path):
    controller, database, repo = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    outcome = controller.run_step(bounded_contract(repo))
    assert outcome.outcome == PASSED
    started = [p for t, p in all_verification_events(database)
               if any(k == "binding" for k in p)]
    assert started, "expected binding-carrying verification.started"
    binding = started[-1]["binding"]
    assert binding["verifier_run_id"] != binding["builder_run_id"]


# ---------------------------------------------------------------------------
# Wrong target / wrong step / wrong run
# ---------------------------------------------------------------------------


def test_wrong_step_refused(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-OTHER",  # WRONG STEP
        target=target, builder_run_id="run-a")
    assert not decision.accepted
    # The refusal must be identity-scoped: no verdict exists for THAT step,
    # so nothing about step-1's PASS may leak onto it.
    assert "no verification verdict" in decision.reason
    # ...and the correct step still authorizes.
    ok = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert ok.accepted


def test_wrong_builder_run_stale_verdict_refused(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-old")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-old",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-new")  # superseded run tries to inherit the verdict
    assert not decision.accepted
    assert "stale/replayed verdict" in decision.reason


# ---------------------------------------------------------------------------
# Stale hash / tampered binding / replay
# ---------------------------------------------------------------------------


def test_post_verdict_mutation_refused(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("before verdict\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    # MUTATE AFTER THE VERDICT
    (target / "f.txt").write_text("mutated after verdict\n", encoding="utf-8")
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert not decision.accepted
    assert "post-verdict mutation" in decision.reason


def test_added_file_after_verdict_refused(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("stable\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    (target / "smuggled.txt").write_text("planted later\n", encoding="utf-8")
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert not decision.accepted
    assert "post-verdict mutation" in decision.reason


def test_tampered_started_event_breaks_binding_integrity(tmp_path):
    """Rewriting the STARTED binding in place must break the recomputed
    digest — fabricated bindings cannot authorize."""
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    # Tamper directly at the ledger row level (fabrication attempt).
    # canonical_json is compact: no space after the colon.
    with database.connect() as connection:
        connection.execute(
            "UPDATE events SET payload_json = replace(payload_json, "
            "'\"diff_hash\":\"', '\"diff_hash\":\"zz') "
            "WHERE event_type = 'verification.started'")
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert not decision.accepted
    assert "binding" in decision.reason.lower()


def test_missing_evidence_keys_fail_closed(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    # Terminal outcome WITHOUT the required evidence set (legacy shape).
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence={"verifier_run_id": verifier_run_id})
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert not decision.accepted
    assert "missing keys" in decision.reason


def test_missing_fabrication_review_marker_refused(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    stripped = {
        k: v for k, v in build_terminal_evidence(
            capture=capture, verifier_session_id=verifier_run_id).items()
        if k != "fabrication_review"
    }
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=stripped)
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert not decision.accepted
    assert "fabrication review" in decision.reason


def test_replayed_terminal_before_start_refused(tmp_path):
    """A grafted terminal event whose sequence precedes its STARTED event is
    a replay — refused regardless of payload correctness. Simulated by
    rewriting the ledger sequence numbers (the projection's ordering key)."""
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    # Fabricate the replay: push the STARTED event ABOVE its terminal in
    # ledger-sequence terms (the projection's ordering key).
    with database.connect() as connection:
        started_row = connection.execute(
            "SELECT sequence FROM events WHERE event_type = "
            "'verification.started' AND payload_json LIKE ?",
            (f"%{verdict_id}%",)).fetchone()
        started_seq = started_row[0]
    with database.connect() as connection:
        max_seq = connection.execute(
            "SELECT MAX(sequence) FROM events").fetchone()[0]
        connection.execute(
            "UPDATE events SET sequence = ? WHERE sequence = ?",
            (max_seq + 3, started_seq))
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert not decision.accepted
    assert "replay" in decision.reason.lower()


def test_duplicate_completion_is_idempotent(tmp_path):
    """Re-recording an identical PASS outcome (crashed-caller retry) keeps
    one authorizing verdict and stays acceptable — no double effect."""
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    evidence = build_terminal_evidence(capture=capture,
                                       verifier_session_id=verifier_run_id)
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[], evidence=evidence)
    before = len(all_verification_events(database))
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[], evidence=evidence)
    after = len(all_verification_events(database))
    assert after == before  # idempotency key absorbed the redelivery
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert decision.accepted


def test_non_pass_verdicts_never_authorize(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    gate = gate_from(database, tmp_path)
    for outcome in ("FAIL", "BLOCKED", "NOT_TESTED"):
        verdict_id, verifier_run_id = fresh_verifier_identity(f"run-{outcome}")
        capture = capture_binding(
            database, verdict_id=verdict_id, mission_id="mission-1",
            step_id="step-1", target=target, builder_run_id=f"run-{outcome}",
            verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
        record_verification_outcome(
            database, verdict_id=verdict_id, outcome=outcome, findings=["f"],
            evidence=build_terminal_evidence(capture=capture,
                                             verifier_session_id=verifier_run_id))
        decision = gate.evaluate(
            mission_id="mission-1", step_id="step-1", target=target,
            builder_run_id=f"run-{outcome}")
        assert not decision.accepted
        assert f"{outcome} is not PASS" in decision.reason


# ---------------------------------------------------------------------------
# False-success claims through the real hybrid engine
# ---------------------------------------------------------------------------


def test_false_success_claim_cannot_advance(tmp_path):
    """Worker claims feature.py changed but writes nothing: reconciliation
    disagrees, the verifier confirms the defect, nothing advances."""
    controller, database, repo = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=(),  # claims change, delivers NOTHING
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    hermes_repair = MutatingScriptedAdapter(
        WORKER_HERMES, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry(
        {WORKER_OPENHANDS: openhands, WORKER_HERMES: hermes_repair})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    outcome = controller.run_step(bounded_contract(repo))
    # The claim was false; whatever the engine did about it (repair, fail or
    # block), the FALSE-SUCCESS run itself must never have passed: the
    # returned verdict belongs to a repair run, never the lying builder.
    if outcome.outcome == PASSED:
        assert outcome.run_id != "run-1", (
            "the original false-success run must not be the accepted one")
        assert outcome.repair_run_id  # acceptance came only after repair
    types = [t for t, _ in all_verification_events(database)]
    assert "verification.passed" in types or outcome.outcome != PASSED
    # The first verification round must NOT be a PASS for the lying run.
    started_rows = [
        p for t, p in all_verification_events(database)
        if t == "verification.started" and p.get("binding")]
    assert started_rows
    first_binding = started_rows[0]
    assert first_binding["builder_run_id"] == "run-1"
    assert any(
        t == "verification.failed"
        for t, _ in all_verification_events(database))


def test_mission_loop_false_success_commit_blocked(tmp_path):
    """A verifier that PASSes while the artifact predicate fails must end in
    FAIL/BLOCKED — never commit, never ACCEPTING, never mission.completed."""
    from jarvis_foundry.mission.loop import MissionLoop
    from jarvis_foundry.roles.adapter import FakeHermesAdapter
    from jarvis_foundry.roles.results import COMPLETED, RoleResult

    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()  # EMPTY: expected artifact will be missing

    class LyingVerifier(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            if manifest.role == "verifier":
                return RoleResult(
                    run_id=manifest.run_id, role=manifest.role,
                    status=COMPLETED, provider=manifest.provider,
                    model=manifest.model, verdict="PASS",
                    evidence=("i swear it is there",))
            return super().dispatch(manifest, heartbeat=heartbeat)

    loop = MissionLoop(database, router, LyingVerifier(), policy=policy)
    result = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=target,
        instructions="produce the report",
        expected_effects=("report exists",),
        rollback={"kind": "git_reset_soft"}, risk_class="C1",
        verification_criteria=["report exists"],
        expected_artifacts=("step-out/report.md",),
    )
    assert result.outcome != "PASSED"
    with database.connect() as connection:
        completed = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='mission.completed'"
        ).fetchone()[0]
        commits = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type LIKE '%commit%'"
        ).fetchone()[0]
    assert completed == 0
    assert commits == 0


# ---------------------------------------------------------------------------
# Verifier crash / resume / repair-reverify
# ---------------------------------------------------------------------------


def test_verifier_crash_blocks_then_resumes_with_fresh_binding(tmp_path):
    """A crashed verifier records an honest BLOCKED (never PASS); a resumed
    pass on the same tree supersedes it with a NEW verdict id, NEW binding,
    and a fresh verifier identity."""
    calls = {"n": 0}

    def crashing_then_ok_verifier(contract, *, envelope=None, report=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("verifier process killed mid-review")
        return fake_verifier(contract, envelope=envelope, report=report)

    controller_a, db_a, repo = make_controller(
        tmp_path, router=openhands_ready_router(),
        verifier_factory=crashing_then_ok_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller_a.registry = WorkerRegistry({
        WORKER_OPENHANDS: openhands,
        WORKER_HERMES: MutatingScriptedAdapter(
            WORKER_HERMES, write_files=("feature.py",),
            outcome=ScriptedOutcome(files_changed=("feature.py",))),
    })
    controller_a.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                   note="containment proven",
                                   evidence=("test-proof",))
    first = controller_a.run_step(bounded_contract(repo))
    blocked = [p for t, p in all_verification_events(db_a)
               if t == "verification.blocked"
               and p.get("blocking_dependency") == "verifier_crashed"]
    assert blocked, "crashed verifier must leave a durable verifier_crashed BLOCKED"
    crashed_verdict_id = blocked[0]["verdict_id"]
    # The crashed verifier NEVER produced a PASS for its own verdict...
    passed_for_crash = [
        p for t, p in all_verification_events(db_a)
        if t == "verification.passed" and p.get("verdict_id") == crashed_verdict_id]
    assert not passed_for_crash
    # ...and if the step still passed, it did so ONLY through the distinct
    # repair path (a different builder run + its own fresh verdict).
    if first.outcome == PASSED:
        assert first.run_id != "run-1"
        assert first.repair_run_id
        assert first.verdict_id != crashed_verdict_id

    # Resumed pass: fresh controller instance, FRESH repo+DB (a real resume
    # happens on a new mission attempt; here we prove the new verdict is a
    # distinct binding with fresh identities).
    resume_root = tmp_path / "resume"
    resume_root.mkdir()
    controller_b, db_b, _repo_b = make_controller(
        resume_root, router=openhands_ready_router(),
        verifier_factory=fake_verifier)
    controller_b.registry = WorkerRegistry({WORKER_OPENHANDS: MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))})
    controller_b.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                   note="containment proven",
                                   evidence=("test-proof",))
    second = controller_b.run_step(bounded_contract(repo))
    assert second.outcome == PASSED
    assert second.verdict_id != crashed_verdict_id
    started_rows = [
        p for t, p in all_verification_events(db_b)
        if t == "verification.started" and p.get("binding")]
    latest_binding = started_rows[-1]["binding"]
    assert latest_binding["verifier_run_id"] != latest_binding["builder_run_id"]


def test_repair_invalidates_old_verdict_requires_fresh_verify(tmp_path):
    """After a FAIL, only a brand-new PASS (new verdict id + fresh binding +
    fresh verifier identity) may authorize the repaired tree."""
    from jarvis_foundry.mission.loop import MissionLoop
    from jarvis_foundry.roles.adapter import FakeHermesAdapter
    from jarvis_foundry.roles.results import COMPLETED, RoleResult

    database, repository, policy, router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()

    class RepairScript(FakeHermesAdapter):
        """Attempt 1: no artifact -> FAIL. Attempt 2: artifact -> PASS."""

        def __init__(self) -> None:
            super().__init__()
            self.builder_runs = 0

        def dispatch(self, manifest, *, heartbeat):
            role = manifest.role
            artifact = target / "report.md"
            if role in ("builder", "repair"):
                self.builder_runs += 1
                if self.builder_runs >= 2:
                    artifact.write_text("repaired deliverable\n", encoding="utf-8")
            if role == "verifier":
                if artifact.exists():
                    return RoleResult(
                        run_id=manifest.run_id, role=role, status=COMPLETED,
                        provider=manifest.provider, model=manifest.model,
                        verdict="PASS", evidence=("ok",))
                return RoleResult(
                    run_id=manifest.run_id, role=role, status=COMPLETED,
                    provider=manifest.provider, model=manifest.model,
                    verdict="FAIL", evidence=("missing artifact",))
            return RoleResult(
                run_id=manifest.run_id, role=role, status=COMPLETED,
                provider=manifest.provider, model=manifest.model,
                verdict=None, evidence=())  # builders carry no verdict

    loop = MissionLoop(database, router, RepairScript(), policy=policy)
    first = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=target,
        instructions="produce the report",
        expected_effects=("report exists",),
        rollback={"kind": "git_reset_soft"}, risk_class="C1",
        verification_criteria=["report exists"],
    )
    assert first.outcome == "FAILED"  # FAIL verdict consumed, repair armed
    old_verdict_id = first.verdict_id
    second = loop.run_step(
        mission_id="mission-1", step_id="step-1",
        capability="git_local_commit", target=target,
        instructions="repair the report",
        expected_effects=("report exists",),
        rollback={"kind": "git_reset_soft"}, risk_class="C1",
        verification_criteria=["report exists"],
    )
    assert second.outcome == "PASSED"
    assert second.verdict_id != old_verdict_id
    started_rows = [
        p for t, p in all_verification_events(database)
        if t == "verification.started" and p.get("binding")]
    assert len(started_rows) >= 2
    # Old verdict can no longer authorize the mutated tree...
    gate = AcceptanceGate(database, stop_path=tmp_path / "STOP")
    decision_old = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id=str(started_rows[0]["builder_run_id"]))
    assert not decision_old.accepted  # artifacts changed since that binding
    # ...while the fresh one does.
    decision_new = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id=str(started_rows[-1]["builder_run_id"]))
    assert decision_new.accepted


# ---------------------------------------------------------------------------
# STOP and veto precedence
# ---------------------------------------------------------------------------


def test_stop_blocks_acceptance_even_with_valid_pass(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    (tmp_path / "STOP").write_text("stop\n", encoding="utf-8")
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert not decision.accepted
    assert "STOP active at acceptance" in decision.reason


def test_stop_sentinel_matches_shared_emergency_stop_read(tmp_path):
    database, _repo, _policy, _router = mission_fixture(tmp_path)
    (tmp_path / "STOP").write_text("stop\n", encoding="utf-8")
    reason = emergency_stop_reason(database, tmp_path / "STOP")
    assert reason == "emergency stop marker is present"


def test_veto_precedence_over_pass(tmp_path):
    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    database.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "d-veto-1", "command_type": "STOP_MISSION",
         "target": "mission-1", "exact_text": "stop mission 1", "reason": "test"},
        source_type="owner", source_id="owner-console",
        idempotency_key="owner_directive.recorded:d-veto-1",
    ))
    veto = matching_veto_reason(database, {"GLOBAL", "mission-1"})
    assert veto, "veto fixture must be visible to the shared read"
    verdict_id, verifier_run_id = fresh_verifier_identity("run-a")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-a",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-a")
    assert not decision.accepted
    assert "veto active at acceptance" in decision.reason


# ---------------------------------------------------------------------------
# Same-provider limitation recording
# ---------------------------------------------------------------------------


def test_same_provider_limitation_recorded_with_fresh_session(tmp_path):
    controller, database, repo = make_controller(
        tmp_path, router=openhands_ready_router(), verifier_factory=fake_verifier)
    openhands = MutatingScriptedAdapter(
        WORKER_OPENHANDS, write_files=("feature.py",),
        outcome=ScriptedOutcome(files_changed=("feature.py",)))
    controller.registry = WorkerRegistry({WORKER_OPENHANDS: openhands})
    controller.router.mark_ready(WORKER_OPENHANDS, state=READY,
                                 note="containment proven",
                                 evidence=("test-proof",))
    outcome = controller.run_step(bounded_contract(repo))
    assert outcome.outcome == PASSED
    started = [p for t, p in all_verification_events(database)
               if t == "verification.started" and p.get("binding")]
    independence = started[-1]["independence"]
    assert independence["provider_diverse"] is False
    assert "same-provider" in independence["same_provider_limitation"].lower()
    assert independence["session_isolation"] == "fresh-session"


# ---------------------------------------------------------------------------
# Expired lease / interrupted verification idempotency
# ---------------------------------------------------------------------------


def test_expired_lease_leaves_no_authority_and_resumes(tmp_path):
    """A verifier run row left RUNNING by an expired lease grants no
    authority; the resumed pass still produces exactly one valid PASS."""
    from jarvis_foundry.supervisor.leases import LeaseManager

    database, repository, _policy, _router = mission_fixture(tmp_path)
    target = repository / "step-out"
    target.mkdir()
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    leases = LeaseManager(database)
    leases.acquire("role:verifier:mission-1:step-1", "orphan-verify", 1)
    # Simulate expiry: force-release then confirm the orphan run has no verdict.
    leases.release("role:verifier:mission-1:step-1", "orphan-verify")
    gate = gate_from(database, tmp_path)
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="orphan-verify")
    assert not decision.accepted  # no verdict at all -> refuse
    # Fresh pass completes normally afterwards.
    verdict_id, verifier_run_id = fresh_verifier_identity("run-late")
    capture = capture_binding(
        database, verdict_id=verdict_id, mission_id="mission-1",
        step_id="step-1", target=target, builder_run_id="run-late",
        verifier_run_id=verifier_run_id, independence={}, criteria=["c"])
    record_verification_outcome(
        database, verdict_id=verdict_id, outcome="PASS", findings=[],
        evidence=build_terminal_evidence(capture=capture,
                                         verifier_session_id=verifier_run_id))
    decision = gate.evaluate(
        mission_id="mission-1", step_id="step-1", target=target,
        builder_run_id="run-late")
    assert decision.accepted
