from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.workforce.contracts import ConvergenceDecision
from jarvis_foundry.workforce.convergence import (
    ConvergenceAnalysisError,
    PackageConvergenceEvidence,
    analyze_convergence,
    reconstruct_convergence_package_deltas,
    record_convergence_decision,
    validate_convergence_integration_union,
    validate_convergence_package_delta,
    validate_convergence_package_deltas,
)
from jarvis_foundry.workforce.state import rebuild_workforce_state


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "jarvis@example.invalid")
    _git(repository, "config", "user.name", "JARVIS Test")
    for name in (
        "conflict.txt",
        "partial.txt",
        "represented.txt",
        "superseded.txt",
    ):
        (repository / name).write_text("base\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "base")
    return repository, _git(repository, "rev-parse", "HEAD")


def _commit(
    repository: Path,
    *,
    branch: str,
    parent: str,
    changes: dict[str, str],
) -> str:
    _git(repository, "checkout", "-qB", branch, parent)
    for relative, content in changes.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-qm", branch)
    return _git(repository, "rev-parse", "HEAD")


def _evidence(
    package_id: str,
    base_sha: str,
    result_sha: str,
    *,
    status: str = "ACCEPTED",
) -> PackageConvergenceEvidence:
    return PackageConvergenceEvidence(
        package_id=package_id,
        base_sha=base_sha,
        result_sha=result_sha,
        status=status,
        evidence=(f"verifier:{package_id}",),
    )


def _repository_snapshot(repository: Path) -> tuple[str, str, str]:
    return (
        _git(repository, "rev-parse", "HEAD"),
        _git(
            repository,
            "for-each-ref",
            "--format=%(refname)%00%(objectname)",
        ),
        _git(repository, "status", "--porcelain=v1", "--untracked-files=all"),
    )


def test_all_classifications_are_semantic_and_repository_stays_immutable(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    superseded_sha = _commit(
        repository,
        branch="candidate/superseded",
        parent=base_sha,
        changes={"superseded.txt": "earlier accepted behavior\n"},
    )
    already_sha = _commit(
        repository,
        branch="candidate/already",
        parent=base_sha,
        changes={"represented.txt": "represented\n"},
    )
    target_sha = _commit(
        repository,
        branch="integration/target",
        parent=superseded_sha,
        changes={
            "conflict.txt": "target\n",
            "partial.txt": "shared\n",
            "superseded.txt": "represented\n",
            "represented.txt": "represented\n",
        },
    )
    unique_sha = _commit(
        repository,
        branch="candidate/unique",
        parent=base_sha,
        changes={"unique.txt": "unique\n"},
    )
    partial_sha = _commit(
        repository,
        branch="candidate/partial",
        parent=base_sha,
        changes={
            "partial.txt": "shared\n",
            "partial-only.txt": "unique\n",
        },
    )
    conflict_sha = _commit(
        repository,
        branch="candidate/conflict",
        parent=base_sha,
        changes={"conflict.txt": "candidate\n"},
    )
    _git(repository, "checkout", "-q", "integration/target")
    before = _repository_snapshot(repository)

    decision = analyze_convergence(
        repository,
        decision_id="decision-all-classifications",
        integration_target="candidate/v13-integration",
        target_sha=target_sha,
        packages=(
            _evidence("pkg-already", base_sha, already_sha),
            _evidence("pkg-unique", base_sha, unique_sha),
            _evidence("pkg-superseded", base_sha, superseded_sha),
            _evidence("pkg-partial", base_sha, partial_sha),
            _evidence("pkg-conflict", base_sha, conflict_sha),
            _evidence(
                "pkg-rejected",
                base_sha,
                target_sha,
                status="REJECTED",
            ),
            _evidence(
                "pkg-evidence",
                base_sha,
                unique_sha,
                status="EVIDENCE_ONLY",
            ),
            _evidence(
                "pkg-incomplete",
                base_sha,
                superseded_sha,
                status="INCOMPLETE",
            ),
        ),
    )

    assert decision.classifications == {
        "pkg-already": "ALREADY_REPRESENTED",
        "pkg-conflict": "CONFLICTING",
        "pkg-evidence": "EVIDENCE_ONLY",
        "pkg-incomplete": "INCOMPLETE",
        "pkg-partial": "PARTIALLY_ABSORBED",
        "pkg-rejected": "FAILED_REJECTED",
        "pkg-superseded": "FULLY_SUPERSEDED",
        "pkg-unique": "UNIQUE_ACCEPTED_DELTA",
    }
    assert decision.accepted_packages == (
        "pkg-already",
        "pkg-conflict",
        "pkg-partial",
        "pkg-superseded",
        "pkg-unique",
    )
    assert any(
        conflict == "pkg-conflict:conflict.txt:integration-target"
        for conflict in decision.conflicts
    )
    assert "pkg-unique [unique.txt]" in decision.recommendation
    assert "pkg-partial [partial-only.txt]" in decision.recommendation
    assert "Resolve conflicts before integration" in decision.recommendation
    assert "Exclude rejected packages: pkg-rejected" in decision.recommendation
    assert "performs no Git mutation" in decision.recommendation
    assert _repository_snapshot(repository) == before


def test_peer_subset_is_superseded_without_dropping_shared_delta(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    wide_sha = _commit(
        repository,
        branch="candidate/wide",
        parent=base_sha,
        changes={
            "partial.txt": "shared\n",
            "wide-only.txt": "unique\n",
        },
    )
    subset_sha = _commit(
        repository,
        branch="candidate/subset",
        parent=base_sha,
        changes={"partial.txt": "shared\n"},
    )

    decision = analyze_convergence(
        repository,
        decision_id="decision-peer-overlap",
        integration_target="candidate/v13-integration",
        target_sha=base_sha,
        packages=(
            _evidence("pkg-wide", base_sha, wide_sha),
            _evidence("pkg-subset", base_sha, subset_sha),
        ),
    )

    assert decision.classifications == {
        "pkg-subset": "FULLY_SUPERSEDED",
        "pkg-wide": "UNIQUE_ACCEPTED_DELTA",
    }
    assert "pkg-wide [partial.txt, wide-only.txt]" in decision.recommendation


def test_disjoint_same_file_changes_are_unique_not_conflicting(
    tmp_path: Path,
) -> None:
    repository, initial_sha = _repository(tmp_path)
    base_sha = _commit(
        repository,
        branch="prepared/base",
        parent=initial_sha,
        changes={"shared.txt": "one\ntwo\nthree\nfour\n"},
    )
    target_sha = _commit(
        repository,
        branch="integration/target",
        parent=base_sha,
        changes={"shared.txt": "ONE\ntwo\nthree\nfour\n"},
    )
    candidate_sha = _commit(
        repository,
        branch="candidate/disjoint",
        parent=base_sha,
        changes={"shared.txt": "one\ntwo\nthree\nFOUR\n"},
    )

    decision = analyze_convergence(
        repository,
        decision_id="decision-disjoint-same-file",
        integration_target="candidate/v13-integration",
        target_sha=target_sha,
        packages=(
            _evidence("pkg-disjoint", base_sha, candidate_sha),
        ),
    )

    assert decision.classifications == {
        "pkg-disjoint": "UNIQUE_ACCEPTED_DELTA",
    }
    assert decision.conflicts == ()
    assert "pkg-disjoint [shared.txt]" in decision.recommendation


def test_same_file_target_hunk_produces_partial_absorption(
    tmp_path: Path,
) -> None:
    repository, initial_sha = _repository(tmp_path)
    base_sha = _commit(
        repository,
        branch="prepared/base",
        parent=initial_sha,
        changes={"shared.txt": "one\ntwo\nthree\nfour\n"},
    )
    target_sha = _commit(
        repository,
        branch="integration/target",
        parent=base_sha,
        changes={"shared.txt": "ONE\ntwo\nthree\nfour\n"},
    )
    candidate_sha = _commit(
        repository,
        branch="candidate/partial-same-file",
        parent=base_sha,
        changes={"shared.txt": "ONE\ntwo\nthree\nFOUR\n"},
    )

    decision = analyze_convergence(
        repository,
        decision_id="decision-partial-same-file",
        integration_target="candidate/v13-integration",
        target_sha=target_sha,
        packages=(
            _evidence("pkg-partial", base_sha, candidate_sha),
        ),
    )

    assert decision.classifications == {
        "pkg-partial": "PARTIALLY_ABSORBED",
    }
    assert decision.conflicts == ()
    assert "pkg-partial [shared.txt]" in decision.recommendation


def test_adjacent_target_edit_does_not_hide_represented_line_change(
    tmp_path: Path,
) -> None:
    repository, initial_sha = _repository(tmp_path)
    base_sha = _commit(
        repository,
        branch="prepared/base",
        parent=initial_sha,
        changes={"shared.txt": "one\ntwo\nthree\n"},
    )
    target_sha = _commit(
        repository,
        branch="integration/target",
        parent=base_sha,
        changes={"shared.txt": "ONE\nTWO\nthree\n"},
    )
    candidate_sha = _commit(
        repository,
        branch="candidate/represented-adjacent",
        parent=base_sha,
        changes={"shared.txt": "ONE\ntwo\nthree\n"},
    )

    decision = analyze_convergence(
        repository,
        decision_id="decision-represented-adjacent",
        integration_target="candidate/v13-integration",
        target_sha=target_sha,
        packages=(
            _evidence("pkg-represented", base_sha, candidate_sha),
        ),
    )

    assert decision.classifications == {
        "pkg-represented": "ALREADY_REPRESENTED",
    }
    assert decision.conflicts == ()


def test_peer_disjoint_same_file_changes_can_both_converge(
    tmp_path: Path,
) -> None:
    repository, initial_sha = _repository(tmp_path)
    base_sha = _commit(
        repository,
        branch="prepared/base",
        parent=initial_sha,
        changes={"shared.txt": "one\ntwo\nthree\nfour\n"},
    )
    first_sha = _commit(
        repository,
        branch="candidate/first-disjoint",
        parent=base_sha,
        changes={"shared.txt": "ONE\ntwo\nthree\nfour\n"},
    )
    second_sha = _commit(
        repository,
        branch="candidate/second-disjoint",
        parent=base_sha,
        changes={"shared.txt": "one\ntwo\nthree\nFOUR\n"},
    )

    decision = analyze_convergence(
        repository,
        decision_id="decision-peer-disjoint-same-file",
        integration_target="candidate/v13-integration",
        target_sha=base_sha,
        packages=(
            _evidence("pkg-first", base_sha, first_sha),
            _evidence("pkg-second", base_sha, second_sha),
        ),
    )

    assert decision.classifications == {
        "pkg-first": "UNIQUE_ACCEPTED_DELTA",
        "pkg-second": "UNIQUE_ACCEPTED_DELTA",
    }
    assert decision.conflicts == ()
    assert "pkg-first [shared.txt]" in decision.recommendation
    assert "pkg-second [shared.txt]" in decision.recommendation


def test_peer_path_conflict_marks_both_candidates(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    first_sha = _commit(
        repository,
        branch="candidate/first",
        parent=base_sha,
        changes={"conflict.txt": "first\n"},
    )
    second_sha = _commit(
        repository,
        branch="candidate/second",
        parent=base_sha,
        changes={"conflict.txt": "second\n"},
    )

    decision = analyze_convergence(
        repository,
        decision_id="decision-peer-conflict",
        integration_target="candidate/v13-integration",
        target_sha=base_sha,
        packages=(
            _evidence("pkg-first", base_sha, first_sha),
            _evidence("pkg-second", base_sha, second_sha),
        ),
    )

    assert decision.classifications == {
        "pkg-first": "CONFLICTING",
        "pkg-second": "CONFLICTING",
    }
    assert decision.accepted_packages == ("pkg-first", "pkg-second")
    assert decision.conflicts == (
        "pkg-first:conflict.txt:pkg-second",
    )


def test_conflicting_superset_cannot_hide_safe_unique_delta(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    target_sha = _commit(
        repository,
        branch="integration/target",
        parent=base_sha,
        changes={"conflict.txt": "target\n"},
    )
    safe_sha = _commit(
        repository,
        branch="candidate/safe",
        parent=base_sha,
        changes={"safe.txt": "safe accepted delta\n"},
    )
    unsafe_sha = _commit(
        repository,
        branch="candidate/unsafe",
        parent=base_sha,
        changes={
            "conflict.txt": "unsafe\n",
            "safe.txt": "safe accepted delta\n",
        },
    )

    decision = analyze_convergence(
        repository,
        decision_id="decision-safe-beside-conflict",
        integration_target="candidate/v13-integration",
        target_sha=target_sha,
        packages=(
            _evidence("pkg-safe", base_sha, safe_sha),
            _evidence("pkg-unsafe", base_sha, unsafe_sha),
        ),
    )

    assert decision.classifications == {
        "pkg-safe": "UNIQUE_ACCEPTED_DELTA",
        "pkg-unsafe": "CONFLICTING",
    }
    assert "pkg-safe [safe.txt]" in decision.recommendation
    assert any(
        conflict == "pkg-unsafe:conflict.txt:integration-target"
        for conflict in decision.conflicts
    )


def test_exact_sha_and_repository_identity_are_required(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    with pytest.raises(ConvergenceAnalysisError, match="40-character"):
        _evidence("pkg-symbolic", "HEAD", base_sha, status="INCOMPLETE")
    with pytest.raises(ConvergenceAnalysisError, match="result_sha"):
        PackageConvergenceEvidence(
            package_id="pkg-missing-result",
            base_sha=base_sha,
            result_sha=None,
            status="INCOMPLETE",
            evidence=("worker:partial",),
        )

    missing_sha = "f" * 40
    unresolved = _evidence(
        "pkg-unresolved",
        base_sha,
        missing_sha,
    )
    with pytest.raises(
        ConvergenceAnalysisError,
        match="result_sha for pkg-unresolved is not a commit",
    ):
        analyze_convergence(
            repository,
            decision_id="decision-unresolved",
            integration_target="candidate/v13-integration",
            target_sha=base_sha,
            packages=(unresolved,),
        )
    with pytest.raises(ConvergenceAnalysisError, match="40-character"):
        analyze_convergence(
            repository,
            decision_id="decision-symbolic-target",
            integration_target="candidate/v13-integration",
            target_sha="main",
            packages=(
                _evidence(
                    "pkg-incomplete",
                    base_sha,
                    base_sha,
                    status="INCOMPLETE",
                ),
            ),
        )


def test_convergence_git_reads_ignore_replacement_objects(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    candidate_sha = _commit(
        repository,
        branch="candidate/replacement-object",
        parent=base_sha,
        changes={"replacement-object.txt": "original candidate\n"},
    )
    original_tree = _git(
        repository,
        "--no-replace-objects",
        "rev-parse",
        f"{candidate_sha}^{{tree}}",
    )
    base_tree = _git(
        repository,
        "--no-replace-objects",
        "rev-parse",
        f"{base_sha}^{{tree}}",
    )
    replacement_sha = _git(
        repository,
        "commit-tree",
        base_tree,
        "-p",
        base_sha,
        "-m",
        "forged replacement object",
    )
    _git(repository, "checkout", "-q", "--detach", base_sha)
    _git(repository, "replace", candidate_sha, replacement_sha)
    assert _git(
        repository,
        "rev-parse",
        f"{candidate_sha}^{{tree}}",
    ) == base_tree
    before = _repository_snapshot(repository)
    packages = (
        _evidence("pkg-replacement-object", base_sha, candidate_sha),
    )

    decision = analyze_convergence(
        repository,
        decision_id="decision-replacement-object",
        integration_target="candidate/v13-integration",
        target_sha=base_sha,
        packages=packages,
    )
    reconstructed = reconstruct_convergence_package_deltas(
        repository,
        target_sha=base_sha,
        packages=packages,
    )

    assert decision.classifications == {
        "pkg-replacement-object": "UNIQUE_ACCEPTED_DELTA",
    }
    assert reconstructed[0]["result_tree"] == original_tree
    assert reconstructed[0]["changed_paths"] == ["replacement-object.txt"]
    assert _repository_snapshot(repository) == before


@pytest.mark.parametrize("status", ("FAILED", "REJECTED"))
def test_failed_and_rejected_evidence_is_never_recommended(
    tmp_path: Path,
    status: str,
) -> None:
    repository, base_sha = _repository(tmp_path)
    rejected_sha = _commit(
        repository,
        branch=f"candidate/{status.lower()}",
        parent=base_sha,
        changes={"rejected.txt": "must not integrate\n"},
    )

    decision = analyze_convergence(
        repository,
        decision_id=f"decision-{status.lower()}",
        integration_target="candidate/v13-integration",
        target_sha=base_sha,
        packages=(
            _evidence(
                f"pkg-{status.lower()}",
                base_sha,
                rejected_sha,
                status=status,
            ),
        ),
    )

    assert decision.classifications == {
        f"pkg-{status.lower()}": "FAILED_REJECTED",
    }
    assert decision.accepted_packages == ()
    assert "No unique accepted delta is ready." in decision.recommendation


def test_durable_package_delta_shape_preserves_delimiter_paths(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    relative = "proof;delimiter,artifact.txt"
    result_sha = _commit(
        repository,
        branch="candidate/delimiter-safe",
        parent=base_sha,
        changes={relative: "delimiter-safe\n"},
    )
    decision = analyze_convergence(
        repository,
        decision_id="decision-delimiter-safe",
        integration_target="candidate/v13-integration",
        target_sha=base_sha,
        packages=(
            _evidence("pkg-delimiter-safe", base_sha, result_sha),
        ),
    )
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()

    record_convergence_decision(database, decision)

    event = next(
        event
        for event in database.events(
            event_types=("workforce.convergence_recorded",),
            readonly=True,
        )
    )
    assert event.payload["package_deltas_schema"] == (
        "jarvis.workforce.convergence-package-deltas.v1"
    )
    assert event.payload["package_deltas"] == [{
        "package_id": "pkg-delimiter-safe",
        "status": "ACCEPTED",
        "classification": "UNIQUE_ACCEPTED_DELTA",
        "target_sha": base_sha,
        "target_tree": _git(
            repository,
            "rev-parse",
            f"{base_sha}^{{tree}}",
        ),
        "package_universe_digest": (
            event.payload["package_deltas"][0]["package_universe_digest"]
        ),
        "base_sha": base_sha,
        "result_sha": result_sha,
        "base_tree": _git(
            repository,
            "rev-parse",
            f"{base_sha}^{{tree}}",
        ),
        "result_tree": _git(
            repository,
            "rev-parse",
            f"{result_sha}^{{tree}}",
        ),
        "patch_id": event.payload["package_deltas"][0]["patch_id"],
        "change_count": event.payload["package_deltas"][0]["change_count"],
        "changed_paths": [relative],
    }]
    assert event.payload["package_deltas"][0]["patch_id"]
    assert event.payload["package_deltas"][0]["change_count"] > 0
    assert event.payload["package_deltas"][0][
        "package_universe_digest"
    ].startswith("sha256:")


@pytest.mark.parametrize(
    ("status", "classification"),
    (
        ("ACCEPTED", "FAILED_REJECTED"),
        ("FAILED", "UNIQUE_ACCEPTED_DELTA"),
        ("REJECTED", "EVIDENCE_ONLY"),
        ("EVIDENCE_ONLY", "INCOMPLETE"),
        ("INCOMPLETE", "CONFLICTING"),
    ),
)
def test_typed_package_delta_rejects_impossible_status_classification_pair(
    tmp_path: Path,
    status: str,
    classification: str,
) -> None:
    repository, base_sha = _repository(tmp_path)
    result_sha = _commit(
        repository,
        branch="candidate/status-classification",
        parent=base_sha,
        changes={"status-classification.txt": "candidate\n"},
    )
    decision = analyze_convergence(
        repository,
        decision_id="decision-status-classification",
        integration_target="candidate/v13-integration",
        target_sha=base_sha,
        packages=(
            _evidence("pkg-status-classification", base_sha, result_sha),
        ),
    )
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    record_convergence_decision(database, decision)
    event = next(
        event
        for event in database.events(
            event_types=("workforce.convergence_recorded",),
            readonly=True,
        )
    )
    payload = dict(event.payload["package_deltas"][0])
    payload.update({
        "status": status,
        "classification": classification,
    })

    with pytest.raises(
        ConvergenceAnalysisError,
        match="classification does not match package status",
    ):
        validate_convergence_package_delta(
            repository,
            payload,
            target_sha=base_sha,
            packages=(
                _evidence(
                    "pkg-status-classification",
                    base_sha,
                    result_sha,
                ),
            ),
        )


def test_semantic_delta_validation_rejects_forged_classification(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    result_sha = _commit(
        repository,
        branch="candidate/forged-classification",
        parent=base_sha,
        changes={"forged-classification.txt": "unique accepted delta\n"},
    )
    packages = (
        _evidence("pkg-forged-classification", base_sha, result_sha),
    )
    payload = dict(reconstruct_convergence_package_deltas(
        repository,
        target_sha=base_sha,
        packages=packages,
    )[0])
    payload["classification"] = "CONFLICTING"

    with pytest.raises(
        ConvergenceAnalysisError,
        match="does not match semantic reconstruction",
    ):
        validate_convergence_package_delta(
            repository,
            payload,
            target_sha=base_sha,
            packages=packages,
        )


def test_semantic_delta_validation_rejects_omitted_accepted_package(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    first_sha = _commit(
        repository,
        branch="candidate/first-universe",
        parent=base_sha,
        changes={"first-universe.txt": "first\n"},
    )
    second_sha = _commit(
        repository,
        branch="candidate/second-universe",
        parent=base_sha,
        changes={"second-universe.txt": "second\n"},
    )
    packages = (
        _evidence("pkg-first-universe", base_sha, first_sha),
        _evidence("pkg-second-universe", base_sha, second_sha),
    )
    payloads = reconstruct_convergence_package_deltas(
        repository,
        target_sha=base_sha,
        packages=packages,
    )

    with pytest.raises(
        ConvergenceAnalysisError,
        match="exact package universe",
    ):
        validate_convergence_package_deltas(
            repository,
            payloads[:1],
            target_sha=base_sha,
            packages=packages,
        )


def test_semantic_delta_validation_rejects_wrong_target_sha(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    result_sha = _commit(
        repository,
        branch="candidate/target-bound",
        parent=base_sha,
        changes={"target-bound.txt": "candidate\n"},
    )
    wrong_target_sha = _commit(
        repository,
        branch="integration/wrong-target",
        parent=base_sha,
        changes={"unrelated-target.txt": "unrelated\n"},
    )
    packages = (_evidence("pkg-target-bound", base_sha, result_sha),)
    payloads = reconstruct_convergence_package_deltas(
        repository,
        target_sha=base_sha,
        packages=packages,
    )

    with pytest.raises(
        ConvergenceAnalysisError,
        match="does not match semantic reconstruction",
    ):
        validate_convergence_package_deltas(
            repository,
            payloads,
            target_sha=wrong_target_sha,
            packages=packages,
        )


def test_semantic_reconstruction_preserves_disjoint_same_file_edits(
    tmp_path: Path,
) -> None:
    repository, initial_sha = _repository(tmp_path)
    base_sha = _commit(
        repository,
        branch="prepared/semantic-disjoint-base",
        parent=initial_sha,
        changes={"semantic-shared.txt": "one\ntwo\nthree\nfour\n"},
    )
    first_sha = _commit(
        repository,
        branch="candidate/semantic-first-disjoint",
        parent=base_sha,
        changes={"semantic-shared.txt": "ONE\ntwo\nthree\nfour\n"},
    )
    second_sha = _commit(
        repository,
        branch="candidate/semantic-second-disjoint",
        parent=base_sha,
        changes={"semantic-shared.txt": "one\ntwo\nthree\nFOUR\n"},
    )
    packages = (
        _evidence("pkg-semantic-first", base_sha, first_sha),
        _evidence("pkg-semantic-second", base_sha, second_sha),
    )

    reconstructed = reconstruct_convergence_package_deltas(
        repository,
        target_sha=base_sha,
        packages=packages,
    )
    validated = validate_convergence_package_deltas(
        repository,
        reconstructed,
        target_sha=base_sha,
        packages=packages,
    )

    assert {
        record["package_id"]: record["classification"]
        for record in validated
    } == {
        "pkg-semantic-first": "UNIQUE_ACCEPTED_DELTA",
        "pkg-semantic-second": "UNIQUE_ACCEPTED_DELTA",
    }
    assert all(
        record["changed_paths"] == ["semantic-shared.txt"]
        for record in validated
    )


def test_integration_union_rejects_reordered_inserted_lines(
    tmp_path: Path,
) -> None:
    repository, initial_sha = _repository(tmp_path)
    base_sha = _commit(
        repository,
        branch="prepared/reordered-insertion-base",
        parent=initial_sha,
        changes={"ordered-insertions.txt": "anchor\n"},
    )
    candidate_sha = _commit(
        repository,
        branch="candidate/ordered-insertions",
        parent=base_sha,
        changes={
            "ordered-insertions.txt": "first\nsecond\nanchor\n",
        },
    )
    reordered_sha = _commit(
        repository,
        branch="forged/reordered-insertions",
        parent=base_sha,
        changes={
            "ordered-insertions.txt": "second\nfirst\nanchor\n",
        },
    )
    packages = (
        _evidence("pkg-ordered-insertions", base_sha, candidate_sha),
    )

    valid = validate_convergence_integration_union(
        repository,
        target_sha=base_sha,
        integration_sha=candidate_sha,
        packages=packages,
    )
    assert valid["change_count"] == 2

    with pytest.raises(
        ConvergenceAnalysisError,
        match="exact ordered semantic union",
    ):
        validate_convergence_integration_union(
            repository,
            target_sha=base_sha,
            integration_sha=reordered_sha,
            packages=packages,
        )


def test_integration_union_rejects_missing_duplicate_inserted_line(
    tmp_path: Path,
) -> None:
    repository, initial_sha = _repository(tmp_path)
    base_sha = _commit(
        repository,
        branch="prepared/duplicate-insertion-base",
        parent=initial_sha,
        changes={"duplicate-insertions.txt": "anchor\n"},
    )
    candidate_sha = _commit(
        repository,
        branch="candidate/duplicate-insertions",
        parent=base_sha,
        changes={
            "duplicate-insertions.txt": "same\nsame\nanchor\n",
        },
    )
    missing_duplicate_sha = _commit(
        repository,
        branch="forged/missing-duplicate-insertion",
        parent=base_sha,
        changes={"duplicate-insertions.txt": "same\nanchor\n"},
    )
    packages = (
        _evidence("pkg-duplicate-insertions", base_sha, candidate_sha),
    )

    valid = validate_convergence_integration_union(
        repository,
        target_sha=base_sha,
        integration_sha=candidate_sha,
        packages=packages,
    )
    assert valid["change_count"] == 2

    with pytest.raises(
        ConvergenceAnalysisError,
        match="exact ordered semantic union",
    ):
        validate_convergence_integration_union(
            repository,
            target_sha=base_sha,
            integration_sha=missing_duplicate_sha,
            packages=packages,
        )


def test_durable_recording_is_idempotent_and_rejects_changed_content(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    decision = ConvergenceDecision(
        decision_id="decision-durable",
        integration_target="candidate/v13-integration",
        classifications={
            "pkg-accepted": "UNIQUE_ACCEPTED_DELTA",
            "pkg-rejected": "FAILED_REJECTED",
        },
        accepted_packages=("pkg-accepted",),
        conflicts=(),
        evidence=("git:exact-sha", "verifier:pass"),
        recommendation="Integrate pkg-accepted. Exclude pkg-rejected.",
        created_at="2026-09-04T00:00:00Z",
    )

    first = record_convergence_decision(database, decision)
    replayed = record_convergence_decision(
        database,
        replace(decision, created_at="2026-09-04T00:01:00Z"),
    )

    assert replayed == first
    assert database.event_count(
        event_type="workforce.convergence_recorded"
    ) == 1
    reopened = FoundryDatabase(database.path)
    assert rebuild_workforce_state(
        reopened
    ).convergence_decisions[decision.decision_id] == first

    with pytest.raises(
        ConvergenceAnalysisError,
        match="different convergence content",
    ):
        record_convergence_decision(
            reopened,
            replace(decision, recommendation="Choose another package."),
        )
    assert database.event_count(
        event_type="workforce.convergence_recorded"
    ) == 1


def test_stop_blocks_new_convergence_recording_but_not_analysis(
    tmp_path: Path,
) -> None:
    repository, base_sha = _repository(tmp_path)
    candidate_sha = _commit(
        repository,
        branch="candidate/stop-proof",
        parent=base_sha,
        changes={"stop-proof.txt": "bounded delta\n"},
    )
    decision = analyze_convergence(
        repository,
        decision_id="decision-stop-proof",
        integration_target="candidate/v13-integration",
        target_sha=base_sha,
        packages=(
            _evidence("pkg-stop-proof", base_sha, candidate_sha),
        ),
    )
    database = FoundryDatabase(tmp_path / "state" / "foundry.db")
    database.initialize()
    stop_path = tmp_path / "STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="workforce stopped"):
        record_convergence_decision(
            database,
            decision,
            stop_path=stop_path,
        )

    assert database.event_count(
        event_type="workforce.convergence_recorded"
    ) == 0

    stop_path.unlink()
    recorded = record_convergence_decision(
        database,
        decision,
        stop_path=stop_path,
    )
    stop_path.write_text("owner stop\n", encoding="utf-8")
    replayed = record_convergence_decision(
        database,
        replace(decision, created_at="2026-09-04T00:01:00Z"),
        stop_path=stop_path,
    )

    assert replayed == recorded
    assert database.event_count(
        event_type="workforce.convergence_recorded"
    ) == 1
