"""Evidence-vs-claim reconciliation (H5, H16, §7, B3 regression)."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

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
