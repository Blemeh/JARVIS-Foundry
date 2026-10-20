"""Evidence-vs-claim reconciliation (H5, H16, §7, B3 regression)."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import jarvis_foundry.workers.evidence as evidence_module
from jarvis_foundry.workers.contracts import WorkerStepContract
from jarvis_foundry.workers.envelope import COMPLETED, ResultEnvelope
from jarvis_foundry.workers.evidence import EvidenceReconciler


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    git("init", cwd=repository)
    git("config", "user.email", "jarvis-test@example.invalid", cwd=repository)
    git("config", "user.name", "JARVIS Test", cwd=repository)
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    git("add", "README.md", cwd=repository)
    git("commit", "-m", "baseline", cwd=repository)
    return repository


def contract(repository: Path, **signals) -> WorkerStepContract:
    return WorkerStepContract(
        run_id="run-1", mission_id="mission-1", step_id="step-1",
        attempt=1, role="builder", objective="o", instructions="i",
        working_root=repository, task_signals=signals,
        expected_artifacts=("feature.py",),
    )


def reconcile(repository: Path, envelope: ResultEnvelope, *, before=None,
              reconciler=None, **kwargs):
    reconciler = reconciler or EvidenceReconciler()
    step = contract(repository)
    before = before or reconciler.capture_before(step)
    return reconciler.reconcile(step, envelope, before=before, **kwargs)


def success_envelope(repository: Path, **overrides):
    return ResultEnvelope.from_contract(
        contract(repository), framework="openhands",
        claimed_outcome="implemented feature", **overrides)


def owner_goal_contract(repository: Path, *expected_artifacts: str):
    return WorkerStepContract(
        run_id="run-owner-goal", mission_id="mission-owner-goal",
        step_id="step-owner-goal", attempt=1, role="builder",
        objective="complete the owner goal", instructions="make the change",
        working_root=repository, expected_artifacts=expected_artifacts,
    )


def test_false_success_claim_rejected(tmp_path):
    """B3 regression: worker claims success but changed nothing."""
    repository = make_repo(tmp_path)
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    envelope = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is False
    assert any("files_changed disagree" in m for m in report.mismatches)


def test_unrecorded_mutation_detected(tmp_path):
    repository = make_repo(tmp_path)
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    envelope = success_envelope(repository, files_changed=())
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is False
    assert report.unrecorded_mutations == ("feature.py",)


def test_scope_escape_detected(tmp_path):
    repository = make_repo(tmp_path)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("escape\n", encoding="utf-8")
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    envelope = success_envelope(repository, files_changed=("feature.py",))
    # Allowed scope is repository only; observed change outside -> violation.
    report = reconciler.reconcile(
        step, envelope, before=before,
        persistence_scan_roots=(tmp_path,))
    assert report.agree is False


def test_forbidden_path_touch_detected(tmp_path):
    repository = make_repo(tmp_path)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    forbidden = tmp_path / "secrets"
    forbidden.mkdir()
    (forbidden / "key.txt").write_text("s\n", encoding="utf-8")
    step = WorkerStepContract(
        run_id="run-1", mission_id="m", step_id="s", attempt=1,
        role="builder", objective="o", instructions="i",
        working_root=repository, forbidden_paths=(forbidden,),
        expected_artifacts=("feature.py",))
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(step)
    envelope = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(
        step, envelope, before=before,
        persistence_scan_roots=(forbidden,))
    assert report.agree is False
    assert any("forbidden" in v for v in report.containment_violations)


def test_relative_forbidden_path_resolves_from_working_root(tmp_path):
    repository = make_repo(tmp_path)
    step = WorkerStepContract(
        run_id="run-relative-forbidden",
        mission_id="m",
        step_id="s",
        attempt=1,
        role="builder",
        objective="o",
        instructions="i",
        working_root=repository,
        allowed_paths=(repository,),
        forbidden_paths=(Path("secrets"),),
    )
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(step)
    secret = repository / "secrets" / "key.txt"
    secret.parent.mkdir()
    secret.write_text("do not touch\n", encoding="utf-8")
    envelope = ResultEnvelope.from_contract(
        step,
        framework="openhands",
        claimed_outcome="changed a forbidden file",
        files_changed=("secrets/key.txt",),
    )

    report = reconciler.reconcile(step, envelope, before=before)

    assert report.agree is False
    assert report.containment_violations == (
        f"secrets/key.txt touches forbidden path {secret.parent.resolve()}",
    )


def test_diff_hash_mismatch_detected(tmp_path):
    repository = make_repo(tmp_path)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    envelope = success_envelope(repository, files_changed=("feature.py",),
                                diff_hash="deadbeef")
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is False
    assert any("diff hash" in m for m in report.mismatches)


def test_head_advance_detected(tmp_path):
    repository = make_repo(tmp_path)
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "feature.py", cwd=repository)
    git("commit", "-m", "worker committed", cwd=repository)
    envelope = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is False
    assert any("HEAD advanced" in m for m in report.mismatches)


def test_missing_and_wrong_artifact_detected(tmp_path):
    repository = make_repo(tmp_path)
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    envelope = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(step, envelope, before=before)
    assert any("expected artifacts missing" in m for m in report.mismatches)

    (repository / "feature.py").write_text("wrong\n", encoding="utf-8")
    expected = {"feature.py": hashlib.sha256(b"right\n").hexdigest()}
    report2 = reconciler.reconcile(
        step, envelope, before=before,
        expected_artifact_hashes=expected)
    assert any("artifact hashes mismatch" in m for m in report2.mismatches)


def test_failed_test_forbids_pass(tmp_path):
    repository = make_repo(tmp_path)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    (repository / "test_feature.py").write_text(
        "def test_truth():\n    assert False\n", encoding="utf-8")
    reconciler = EvidenceReconciler(test_timeout_seconds=30)
    step = contract(repository)
    before = reconciler.capture_before(step)
    envelope = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(
        step, envelope, before=before,
        test_commands=("python -m pytest test_feature.py -q",))
    assert report.agree is False
    assert any("test command failed" in m for m in report.mismatches)


def test_required_tests_share_one_remaining_lifecycle_budget(
    tmp_path,
    monkeypatch,
):
    repository = make_repo(tmp_path)
    clock = [0.0]
    reconciler = EvidenceReconciler(
        test_timeout_seconds=10,
        monotonic=lambda: clock[0],
    )
    step = contract(repository)
    before = reconciler.capture_before(step)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    envelope = success_envelope(
        repository,
        files_changed=("feature.py",),
    )
    original_run = evidence_module._run
    observed_timeouts: list[float] = []

    def bounded_run(cmd, *, cwd, timeout=30):
        if cmd and cmd[0] == "git":
            return original_run(cmd, cwd=cwd, timeout=timeout)
        observed_timeouts.append(float(timeout))
        if len(observed_timeouts) == 1:
            clock[0] += 3.0
            return subprocess.CompletedProcess(cmd, 0, "first passed", "")
        clock[0] += float(timeout)
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(evidence_module, "_run", bounded_run)

    report = reconciler.reconcile(
        step,
        envelope,
        before=before,
        test_commands=("check-one", "check-two", "check-three"),
        test_deadline_monotonic=5.0,
    )

    assert observed_timeouts == [5.0, 2.0]
    assert [item["exit"] for item in report.test_results] == [0, 124, 124]
    assert "lifecycle_budget_exhausted" not in report.test_results[0]
    assert report.test_results[1]["lifecycle_budget_exhausted"] is True
    assert report.test_results[2]["lifecycle_budget_exhausted"] is True
    assert report.agree is False


def test_machine_cache_artifacts_do_not_condemn_work(tmp_path):
    """pytest __pycache__ noise is not an unrecorded mutation."""
    repository = make_repo(tmp_path)
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    (repository / "__pycache__").mkdir()
    (repository / "__pycache__" / "feature.cpython-313.pyc").write_text(
        "cached", encoding="utf-8")
    (repository / ".pytest_cache").mkdir()
    envelope = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is True
    assert report.unrecorded_mutations == ()
    assert "feature.py" in report.observed_changed_files


def test_verifier_delta_mode_ignores_preexisting_builder_work(tmp_path):
    """A verifier is only accountable for its own delta, not the builder's
    uncommitted changes already present at baseline."""
    repository = make_repo(tmp_path)
    reconciler = EvidenceReconciler()
    step = contract(repository)
    (repository / "feature.py").write_text("builder work\n", encoding="utf-8")
    before = reconciler.capture_before(step)
    # The verifier adds only a cache dir; the builder's feature.py was
    # already in the baseline diff.
    (repository / "__pycache__").mkdir()
    envelope = success_envelope(repository)
    report = reconciler.reconcile(
        step, envelope, before=before,
        delta_baseline=before.changed_files)
    assert report.agree is True


def test_delta_mode_discards_stale_prior_claims_but_keeps_new_changes(tmp_path):
    """A reused result file may repeat an unchanged prior path.

    The reconciler removes only that stale claim. If the same prior path is
    actually rewritten during this run, it remains part of the accountable
    delta and the worker must claim it.
    """
    repository = make_repo(tmp_path)
    (repository / "feature.py").write_text("builder work\n", encoding="utf-8")
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)

    unchanged = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(
        step, unchanged, before=before,
        delta_baseline=before.changed_files)
    assert report.agree is True
    assert report.unrecorded_mutations == ()

    (repository / "feature.py").write_text("current-step work\n", encoding="utf-8")
    changed = success_envelope(repository, files_changed=("feature.py",))
    report_changed = reconciler.reconcile(
        step, changed, before=before,
        delta_baseline=before.changed_files)
    assert report_changed.agree is True


def test_result_control_file_does_not_condemn_claim(tmp_path):
    """The .jarvis-result.json control file must not force a worker claim
    mismatch: the worker lists only its business files and the control file
    itself is excluded from the deliverable diff comparison."""
    repository = make_repo(tmp_path)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    (repository / ".jarvis-result.json").write_text(
        '{"files_changed": ["feature.py"]}', encoding="utf-8")
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    envelope = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is True
    assert ".jarvis-result.json" not in report.observed_changed_files


def test_expected_owner_goal_receipt_is_not_a_business_mutation(tmp_path):
    repository = make_repo(tmp_path)
    receipt = ".jarvis/owner-goals/0123456789/plan.md"
    receipt_path = repository / receipt
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text("bridge baseline\n", encoding="utf-8")
    reconciler = EvidenceReconciler()
    step = owner_goal_contract(repository, receipt, "feature.py")
    before = reconciler.capture_before(step)

    assert receipt not in before.changed_files
    assert receipt not in before.file_hashes

    receipt_path.write_text("required owner plan\n", encoding="utf-8")
    (repository / "feature.py").write_text("owner goal change\n", encoding="utf-8")
    expected_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    envelope = ResultEnvelope.from_contract(
        step, framework="openhands", claimed_outcome="implemented owner goal",
        files_changed=("feature.py",))
    report = reconciler.reconcile(
        step, envelope, before=before,
        delta_baseline=before.changed_files,
        expected_artifact_hashes={receipt: expected_hash})

    assert report.agree is True
    assert report.unrecorded_mutations == ()
    assert report.missing_artifacts == ()
    assert report.artifact_hash_mismatches == ()
    # Keep the system receipt visible in raw evidence while excluding it from
    # worker business-file accounting.
    assert receipt in report.observed_changed_files
    assert "feature.py" in report.observed_changed_files

    # Some adapters can echo declared artifacts in files_changed; that path is
    # normalized only because this exact receipt is declared by the contract.
    receipt_claim = ResultEnvelope.from_contract(
        step, framework="openhands", claimed_outcome="implemented owner goal",
        files_changed=("feature.py", receipt))
    receipt_claim_report = reconciler.reconcile(
        step, receipt_claim, before=before,
        delta_baseline=before.changed_files)
    assert receipt_claim_report.agree is True

    wrong_hash_report = reconciler.reconcile(
        step, envelope, before=before,
        delta_baseline=before.changed_files,
        expected_artifact_hashes={receipt: "0" * 64})
    assert wrong_hash_report.agree is False
    assert wrong_hash_report.artifact_hash_mismatches == (receipt,)


def test_missing_expected_owner_goal_receipt_still_fails(tmp_path):
    repository = make_repo(tmp_path)
    receipt = ".jarvis/owner-goals/0123456789/outcome.md"
    reconciler = EvidenceReconciler()
    step = owner_goal_contract(repository, receipt)
    before = reconciler.capture_before(step)
    envelope = ResultEnvelope.from_contract(
        step, framework="openhands", claimed_outcome="completed owner goal",
        files_changed=())

    report = reconciler.reconcile(step, envelope, before=before)

    assert report.agree is False
    assert report.missing_artifacts == (receipt,)
    assert any("expected artifacts missing" in item
               for item in report.mismatches)


def test_unexpected_owner_goal_path_remains_claim_accountable(tmp_path):
    repository = make_repo(tmp_path)
    receipt = ".jarvis/owner-goals/0123456789/plan.md"
    receipt_path = repository / receipt
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text("required plan\n", encoding="utf-8")
    reconciler = EvidenceReconciler()
    step = owner_goal_contract(repository, receipt)
    before = reconciler.capture_before(step)

    # A valid sibling receipt is still worker-accountable unless this exact
    # path was declared as expected for the current step.
    unexpected = ".jarvis/owner-goals/0123456789/outcome.md"
    unexpected_path = repository / unexpected
    unexpected_path.write_text("unclaimed extra\n", encoding="utf-8")
    envelope = ResultEnvelope.from_contract(
        step, framework="openhands", claimed_outcome="completed owner goal",
        files_changed=())

    report = reconciler.reconcile(
        step, envelope, before=before,
        delta_baseline=before.changed_files)

    assert report.agree is False
    assert report.unrecorded_mutations == (unexpected,)
    assert receipt in report.observed_changed_files
    assert unexpected in report.observed_changed_files
    assert any("files_changed disagree" in item
               for item in report.mismatches)


def test_verifier_delta_ignores_builder_result_control_file(tmp_path):
    """Verifier delta mode must not let the builder's .jarvis-result.json
    poison the delta: the control file is filtered from the before capture
    and from the delta baseline so a verifier claiming its own business
    files can PASS without control-file pollution."""
    repository = make_repo(tmp_path)
    # Builder finished: business file + control file present at baseline.
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    (repository / ".jarvis-result.json").write_text(
        '{"files_changed": ["feature.py"]}', encoding="utf-8")
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    assert ".jarvis-result.json" not in before.changed_files
    assert "feature.py" in before.changed_files
    # Fresh verifier adds only its own business file.
    (repository / "verifier_note.md").write_text("ok\n", encoding="utf-8")
    envelope = success_envelope(
        repository, files_changed=("verifier_note.md",))
    report = reconciler.reconcile(
        step, envelope, before=before,
        delta_baseline=before.changed_files)
    assert report.agree is True
    assert report.unrecorded_mutations == ()
    assert ".jarvis-result.json" not in report.observed_changed_files


def test_quoted_porcelain_control_file_is_still_excluded(tmp_path):
    """Windows git may quote .jarvis-result.json in porcelain output."""
    from jarvis_foundry.workers.evidence import _is_control_file, _porcelain_path

    assert _porcelain_path('?? ".jarvis-result.json"') == ".jarvis-result.json"
    assert _is_control_file(".jarvis-result.json")
    repository = make_repo(tmp_path)
    (repository / "feature.py").write_text("x = 1\n", encoding="utf-8")
    (repository / ".jarvis-result.json").write_text(
        '{"files_changed": ["feature.py"]}', encoding="utf-8")
    reconciler = EvidenceReconciler()
    step = contract(repository)
    before = reconciler.capture_before(step)
    envelope = success_envelope(repository, files_changed=("feature.py",))
    report = reconciler.reconcile(step, envelope, before=before)
    assert report.agree is True
    assert ".jarvis-result.json" not in report.observed_changed_files
