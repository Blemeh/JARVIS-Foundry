"""Independent verifier: recomputes truth from disk and never trusts worker prose."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.orchestration.dispatcher import run_tests
from jarvis_foundry.orchestration.git_ops import untracked_and_changed_files
from jarvis_foundry.orchestration.paths import TEMP_BASE, resolve_within, sha256_file
from jarvis_foundry.orchestration.schemas import WorkerResult


@dataclass(frozen=True)
class VerifierIssue:
    field: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "detail": self.detail}


@dataclass(frozen=True)
class VerifierVerdict:
    verdict: str
    issues: tuple[VerifierIssue, ...]
    verifier_id: str
    verified_at: str
    test_evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "issues": [issue.to_dict() for issue in self.issues],
            "verifier_id": self.verifier_id,
            "verified_at": self.verified_at,
            "test_evidence": self.test_evidence,
        }


class Verifier:
    def __init__(
        self,
        *,
        python: Path,
        verifier_id: str,
        verified_at: str,
        proving_root: Path | None = None,
    ) -> None:
        self._python = python
        self._verifier_id = verifier_id
        self._verified_at = verified_at
        self._proving_root = proving_root or TEMP_BASE

    def verify(
        self,
        clone: Path,
        result: WorkerResult,
        required_tests: list[str] | tuple[str, ...],
    ) -> VerifierVerdict:
        issues: list[VerifierIssue] = []
        issues.extend(self._verify_claimed_hashes(clone, result))
        test_evidence = self._verify_tests(clone, required_tests)
        for test_name, evidence in test_evidence.items():
            if evidence["exit_code"] != 0:
                issues.append(VerifierIssue("tests", f"required test failed: {test_name}"))
        issues.extend(self._check_completeness(clone, result))
        if result.status not in {"PASS", "FAIL", "BLOCKED", "NOT_TESTED", "WAITING"}:
            issues.append(VerifierIssue("status", "invalid worker status"))
        verdict = "PASS" if not issues else "FAIL"
        return VerifierVerdict(
            verdict=verdict,
            issues=tuple(issues),
            verifier_id=self._verifier_id,
            verified_at=self._verified_at,
            test_evidence=test_evidence,
        )

    def _verify_claimed_hashes(self, clone: Path, result: WorkerResult) -> list[VerifierIssue]:
        clone_root = resolve_within(self._proving_root, clone)
        issues: list[VerifierIssue] = []
        for change in result.files_changed:
            if change.action == "deleted":
                continue
            path = resolve_within(clone_root, clone_root / change.path)
            if not path.is_file():
                issues.append(
                    VerifierIssue("files_changed", f"claimed file missing: {change.path}")
                )
                continue
            actual_sha = sha256_file(path)
            actual_size = path.stat().st_size
            if actual_sha != change.sha256 or actual_size != change.size:
                issues.append(
                    VerifierIssue(
                        "files_changed",
                        f"content hash mismatch for {change.path}",
                    )
                )
        return issues

    def _verify_tests(
        self, clone: Path, required_tests: list[str] | tuple[str, ...]
    ) -> dict[str, Any]:
        clone_root = resolve_within(self._proving_root, clone)
        evidence: dict[str, Any] = {}
        for index, command in enumerate(required_tests):
            test_name = f"required-test-{index + 1}"
            parts = command.split(" ")
            argv = ["-m", *parts] if parts and parts[0] == "pytest" else parts
            result = run_tests(
                command=argv,
                cwd=clone_root,
                python=self._python,
                source_root=clone_root / "src",
                proving_root=self._proving_root,
            )
            evidence[test_name] = {
                "command": result["command"],
                "exit_code": result["exit_code"],
                "wall_seconds": result["wall_seconds"],
                "output_tail": result["output"][-2000:],
            }
        return evidence

    def _check_completeness(self, clone: Path, result: WorkerResult) -> list[VerifierIssue]:
        actual = untracked_and_changed_files(clone, proving_root=self._proving_root)
        actual = {path: entry for path, entry in actual.items() if path not in set(result.evidence)}
        claims = {change.path: change for change in result.files_changed}
        unclaimed = [path for path in actual if path not in claims]
        missing = [
            path
            for path, change in claims.items()
            if change.action != "deleted" and path not in actual
        ]
        issues = [
            VerifierIssue("completeness", f"unclaimed change: {path}") for path in sorted(unclaimed)
        ]
        issues += [
            VerifierIssue("completeness", f"claimed file missing: {path}")
            for path in sorted(missing)
        ]
        return issues
