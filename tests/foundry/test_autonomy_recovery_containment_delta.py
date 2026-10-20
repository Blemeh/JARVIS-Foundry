"""Scope containment must judge the run's own delta, not the whole tree.

Live 2026-10-04, release 99661a8, mission-4646c7023b05 (three attempts):
every attempt ran the investigate step correctly, created the declared
artifact, and still finalized FAILED/``evidence_mismatch`` with an EMPTY
mismatch list.  That signature has exactly one producer:
``EvidenceReconciler.reconcile`` computes ``agree = not mismatches and not
containment and not unrecorded``, and an empty mismatch list there means
only the CONTAINMENT list falsified the run.

The containment loop judged every path in the WHOLE observed worktree diff
instead of the delta the run introduced.  ``docs/evidence/
windows-ci-fix-20260930.md`` -- untracked output of the previous day's
mission, sitting under this contract's forbidden ``docs/`` prefix -- then
condemned every later run on ``resource-jarvis-canonical`` no matter what
the worker did.  A wholly correct mission burned its entire repair budget.

The repair attributes containment to the run: a capture-listed path is
exempt ONLY when its recorded bytes are proven byte-identical now.
Anything created, modified, or unprovable still condemns.

Measured against `99661a8` (base): the four attribution tests fail there
-- ``preexisting_forbidden_output``, ``unprovable_baseline_bytes``,
``absent_before_state``, ``baseline_path_identity`` -- because the
whole-tree scan condemns every run.  The other five are guard tests: they
PASS on the base too, and stay to prove the repair does not widen the
exemption (created/modified forbidden files, renames, traversal).
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from jarvis_foundry.workers.contracts import WorkerStepContract
from jarvis_foundry.workers.envelope import COMPLETED, ResultEnvelope
from jarvis_foundry.workers.evidence import EvidenceReconciler

from worker_test_helpers import make_repository


def _contract(repository: Path, *forbidden: str, **overrides) -> WorkerStepContract:
    payload = {
        "run_id": "run-1", "mission_id": "mission-1", "step_id": "discovery-plan",
        "attempt": 1, "role": "builder", "objective": "o", "instructions": "i",
        "working_root": repository,
        "forbidden_paths": tuple(
            (repository / entry) for entry in (forbidden or ("docs",))
        ),
        "expected_artifacts": (".jarvis/discovery/investigation-abc.md",),
    }
    payload.update(overrides)
    return WorkerStepContract(**payload)


def _envelope(repository: Path, contract, *files: str) -> ResultEnvelope:
    return ResultEnvelope.from_contract(
        contract, framework="hermes", worker_status=COMPLETED,
        claimed_outcome="Hermes session completed",
        files_changed=tuple(files))


def _live_shape(repository: Path) -> None:
    """The exact pre-existing untracked set on the registered resource."""
    (repository / ".github" / "workflows").mkdir(parents=True)
    (repository / ".github" / "workflows" / "windows-ci.yml").write_text(
        "name: ci\n", encoding="utf-8")
    (repository / "docs" / "evidence").mkdir(parents=True)
    (repository / "docs" / "evidence" / "windows-ci-fix-20260930.md").write_text(
        "prior mission output\n", encoding="utf-8")
    (repository / ".jarvis" / "owner-goals" / "0123456789").mkdir(parents=True)
    (repository / ".jarvis" / "owner-goals" / "0123456789" / "plan.md").write_text(
        "receipt\n", encoding="utf-8")


def _correct_work(repository: Path) -> str:
    artifact = ".jarvis/discovery/investigation-abc.md"
    (repository / ".jarvis" / "discovery").mkdir(parents=True)
    (repository / ".jarvis" / "discovery" / "investigation-abc.md").write_text(
        "## Evidence\nreal\n## Findings\nreal\n## Uncertainties\nnone\n",
        encoding="utf-8")
    return artifact


def test_preexisting_forbidden_output_does_not_condemn_correct_work(
        tmp_path: Path) -> None:
    """The live defect: prior-mission output under docs/ failed every run."""
    repository = make_repository(tmp_path)
    _live_shape(repository)
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    envelope = _envelope(repository, contract, artifact)

    report = reconciler.reconcile(
        contract, envelope, before=before,
        delta_baseline=before.changed_files)

    assert report.containment_violations == (), (
        "a pre-existing untracked file the worker never touched must not "
        f"condemn the run: {report.containment_violations}")
    assert report.mismatches == ()
    assert report.agree is True, (
        f"correct work must reconcile: mismatches={report.mismatches}, "
        f"containment={report.containment_violations}, "
        f"unrecorded={report.unrecorded_mutations}")


def test_clean_worktree_still_reconciles(tmp_path: Path) -> None:
    """No baseline at all: behavior is the plain delta reconciliation."""
    repository = make_repository(tmp_path)
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    envelope = _envelope(repository, contract, artifact)
    report = reconciler.reconcile(contract, envelope, before=before)
    assert report.agree is True


def test_worker_created_forbidden_file_still_condemns(tmp_path: Path) -> None:
    """The guard stays: a file THIS run creates under docs/ is a violation."""
    repository = make_repository(tmp_path)
    _live_shape(repository)
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    (repository / "docs" / "evidence" / "new-leak.md").write_text(
        "created by this run\n", encoding="utf-8")
    envelope = _envelope(repository, contract, artifact)

    report = reconciler.reconcile(
        contract, envelope, before=before,
        delta_baseline=before.changed_files)

    assert report.agree is False
    assert any("new-leak.md" in entry
               for entry in report.containment_violations), (
        f"run-created forbidden file must condemn: {report.containment_violations}")


def test_worker_modified_preexisting_forbidden_file_still_condemns(
        tmp_path: Path) -> None:
    """Mutating a pre-existing forbidden file is attributable and condemns."""
    repository = make_repository(tmp_path)
    _live_shape(repository)
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    prior = repository / "docs" / "evidence" / "windows-ci-fix-20260930.md"
    prior.write_text("mutated by this run\n", encoding="utf-8")
    envelope = _envelope(
        repository, contract, artifact,
        "docs/evidence/windows-ci-fix-20260930.md")

    report = reconciler.reconcile(
        contract, envelope, before=before,
        delta_baseline=before.changed_files)

    assert report.agree is False
    assert any("windows-ci-fix-20260930.md" in entry
               for entry in report.containment_violations), (
        f"modified forbidden file must condemn: {report.containment_violations}")


def test_unprovable_baseline_bytes_stay_attributed(tmp_path: Path) -> None:
    """Fail closed: without recorded capture bytes the path still condemns."""
    repository = make_repository(tmp_path)
    _live_shape(repository)
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    blind = replace(
        before, file_hashes={}, raw_file_hashes={}, raw_changed_files=())
    envelope = _envelope(repository, contract, artifact)

    report = reconciler.reconcile(
        contract, envelope, before=blind,
        delta_baseline=before.changed_files)

    assert report.agree is False
    assert any("windows-ci-fix-20260930.md" in entry
               for entry in report.containment_violations), (
        "a baseline path whose capture bytes cannot be proven must stay "
        f"attributable: {report.containment_violations}")


def test_absent_before_state_is_never_an_exemption(tmp_path: Path) -> None:
    """Replay/missing-evidence shape: an EMPTY capture exempts nothing.

    A caller that loses the before-state (crash replay, stale hand-off)
    must not gain a wider exemption than a caller that captured one.
    """
    repository = make_repository(tmp_path)
    _live_shape(repository)
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    empty = replace(before, changed_files=(), file_hashes={},
                    raw_changed_files=(), raw_file_hashes={})
    envelope = _envelope(repository, contract, artifact)

    report = reconciler.reconcile(contract, envelope, before=empty)

    assert report.agree is False
    assert any("windows-ci-fix-20260930.md" in entry
               for entry in report.containment_violations), (
        "an empty baseline must not exempt a forbidden path: "
        f"{report.containment_violations}")


def test_baseline_path_identity_must_match_exactly(tmp_path: Path) -> None:
    """A non-canonical spelling of the path grants no exemption.

    Capture and reconcile both read ``git status --porcelain`` through the
    same normalizer, so a baseline entry written any other way is not the
    same key -- and an unmatched key fails CLOSED, it never widens.
    """
    repository = make_repository(tmp_path)
    _live_shape(repository)
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    misspelled = "docs\\evidence\\windows-ci-fix-20260930.md"
    tampered = replace(
        before,
        raw_changed_files=(misspelled,),
        raw_file_hashes=dict(before.raw_file_hashes, **{
            misspelled: before.raw_file_hashes[
                "docs/evidence/windows-ci-fix-20260930.md"]}),
    )
    envelope = _envelope(repository, contract, artifact)

    report = reconciler.reconcile(
        contract, envelope, before=tampered, delta_baseline=())

    assert report.agree is False
    assert any("windows-ci-fix-20260930.md" in entry
               for entry in report.containment_violations), (
        "a baseline recorded under a different path identity must not "
        f"exempt the real path: {report.containment_violations}")


def test_rename_into_the_forbidden_tree_is_attributed(
        tmp_path: Path) -> None:
    """Moving an already-dirty file under docs/ is this run's violation."""
    repository = make_repository(tmp_path)
    _live_shape(repository)
    (repository / "notes").mkdir()
    (repository / "notes" / "scratch.md").write_text(
        "pre-existing draft\n", encoding="utf-8")
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    (repository / "docs" / "evidence" / "leak.md").write_bytes(
        (repository / "notes" / "scratch.md").read_bytes())
    (repository / "notes" / "scratch.md").unlink()
    envelope = _envelope(repository, contract, artifact)

    report = reconciler.reconcile(
        contract, envelope, before=before,
        delta_baseline=before.changed_files)

    assert report.agree is False
    assert any("leak.md" in entry
               for entry in report.containment_violations), (
        "a rename that lands inside a forbidden root must condemn: "
        f"{report.containment_violations}")


def test_traversal_cannot_bypass_scope(tmp_path: Path) -> None:
    """A claimed or written path outside the working root is rejected."""
    repository = make_repository(tmp_path)
    _live_shape(repository)
    contract = _contract(repository, "docs")
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    artifact = _correct_work(repository)
    (tmp_path / "outside.md").write_text("escaped\n", encoding="utf-8")
    envelope = _envelope(repository, contract, artifact, "../outside.md")

    report = reconciler.reconcile(
        contract, envelope, before=before,
        delta_baseline=before.changed_files,
        persistence_scan_roots=(tmp_path,))

    assert report.agree is False
    assert any("claimed files_changed" in entry
               for entry in report.mismatches), (
        f"a traversal claim must not be accepted: {report.mismatches}")
    assert any("outside.md" in entry for entry in report.mismatches), (
        "state persisted outside the declared root must be reported: "
        f"{report.mismatches}")


def test_exemption_predicate_fails_closed(tmp_path: Path,
                                          monkeypatch) -> None:
    """``_unchanged_since_capture`` widens nothing: bytes OR no exemption.

    The symlink arm cannot be staged on Windows without privilege, so the
    predicate itself is exercised; the same guard is applied when the
    capture decides what to record (``capture_before`` skips links).
    """
    regular = tmp_path / "file.md"
    regular.write_text("same\n", encoding="utf-8")
    digest = hashlib.sha256(regular.read_bytes()).hexdigest()

    assert EvidenceReconciler._unchanged_since_capture(
        regular, digest) is True
    assert EvidenceReconciler._unchanged_since_capture(
        regular, None) is False, "missing capture bytes must not exempt"
    assert EvidenceReconciler._unchanged_since_capture(
        tmp_path, digest) is False, "a directory has no comparable bytes"
    assert EvidenceReconciler._unchanged_since_capture(
        tmp_path / "missing.md", digest) is False, "a deleted path condemns"

    regular.write_text("changed\n", encoding="utf-8")
    assert EvidenceReconciler._unchanged_since_capture(
        regular, digest) is False, "modified bytes must not exempt"

    swapped = tmp_path / "swapped.md"
    swapped.write_text("same\n", encoding="utf-8")
    swapped_digest = hashlib.sha256(swapped.read_bytes()).hexdigest()
    monkeypatch.setattr(Path, "is_symlink", lambda self: True)
    assert EvidenceReconciler._unchanged_since_capture(
        swapped, swapped_digest) is False, (
        "content equality through a link must never waive the "
        "resolved-path containment check")
