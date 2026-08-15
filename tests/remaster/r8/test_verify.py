from __future__ import annotations

from pathlib import Path

from r8_helpers import FIXED_UTC, commit_all, make_worker_result, write_files

from jarvis_foundry.orchestration.paths import sha256_file
from jarvis_foundry.orchestration.schemas import FileChange
from jarvis_foundry.orchestration.verify import Verifier


def _verifier(proving_root: Path) -> Verifier:
    return Verifier(
        python=Path("python"),
        verifier_id="unit-verifier",
        verified_at=FIXED_UTC,
        proving_root=proving_root,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    write_files(repo, {"a.txt": "a"})
    commit_all(repo)
    (repo / "a.txt").write_text("a2", encoding="utf-8")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    return repo


def test_verifier_recomputes_disk_hashes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    claimed_sha = sha256_file(repo / "a.txt")
    result = make_worker_result(
        files_changed=(
            FileChange(
                path="a.txt",
                action="modified",
                sha256="0" * 64,
                size=(repo / "a.txt").stat().st_size,
            ),
        )
    )
    verdict = _verifier(tmp_path).verify(repo, result, [])
    assert claimed_sha != "0" * 64
    assert verdict.verdict == "FAIL"
    assert any(issue.field == "files_changed" for issue in verdict.issues)


def test_verifier_rejects_unclaimed_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = make_worker_result(
        files_changed=(
            FileChange(
                path="a.txt",
                action="modified",
                sha256=sha256_file(repo / "a.txt"),
                size=(repo / "a.txt").stat().st_size,
            ),
        )
    )
    verdict = _verifier(tmp_path).verify(repo, result, [])
    assert any(issue.field == "completeness" for issue in verdict.issues), str(verdict.to_dict())
    assert any("b.txt" in issue.detail for issue in verdict.issues)


def test_verifier_runs_required_tests_fresh(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    a_change = FileChange(
        path="a.txt",
        action="modified",
        sha256=sha256_file(repo / "a.txt"),
        size=(repo / "a.txt").stat().st_size,
    )
    b_change = FileChange(
        path="b.txt",
        action="added",
        sha256=sha256_file(repo / "b.txt"),
        size=(repo / "b.txt").stat().st_size,
    )
    verifier = Verifier(
        python=Path("python"),
        verifier_id="unit-verifier",
        verified_at=FIXED_UTC,
        proving_root=tmp_path,
    )
    result = make_worker_result(files_changed=(a_change, b_change))
    passing = verifier.verify(repo, result, ["-c print('fresh-ok')"])
    assert passing.verdict == "PASS"
    failing = verifier.verify(repo, result, ["-c import sys; sys.exit(3)"])
    assert failing.verdict == "FAIL"
    assert any(issue.field == "tests" for issue in failing.issues)
