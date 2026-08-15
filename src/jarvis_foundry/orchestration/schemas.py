"""Typed worker result contract and fail-closed validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jarvis_foundry.orchestration.paths import ensure_relative_safe

ALLOWED_STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_TESTED", "WAITING")
REQUIRED_KEYS = (
    "status",
    "claims",
    "files_changed",
    "commands_run",
    "tests",
    "evidence",
    "artifacts",
    "external_effects",
    "blockers",
    "uncertainty",
    "recommended_next_action",
)


class WorkerResultError(ValueError):
    """Raised when a worker result does not satisfy the contract."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass(frozen=True)
class FileChange:
    path: str
    action: str
    sha256: str
    size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "action": self.action,
            "sha256": self.sha256,
            "size": self.size,
        }


@dataclass(frozen=True)
class TestRun:
    name: str
    passed: bool
    exit_code: int
    summary: str
    evidence_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "summary": self.summary,
            "evidence_path": self.evidence_path,
        }


@dataclass(frozen=True)
class WorkerResult:
    status: str
    claims: Mapping[str, Any]
    files_changed: tuple[FileChange, ...]
    commands_run: tuple[str, ...]
    tests: tuple[TestRun, ...]
    evidence: tuple[str, ...]
    artifacts: tuple[str, ...]
    external_effects: Mapping[str, Any]
    blockers: tuple[str, ...]
    uncertainty: str
    recommended_next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "claims": dict(self.claims),
            "files_changed": [change.to_dict() for change in self.files_changed],
            "commands_run": list(self.commands_run),
            "tests": [test.to_dict() for test in self.tests],
            "evidence": list(self.evidence),
            "artifacts": list(self.artifacts),
            "external_effects": dict(self.external_effects),
            "blockers": list(self.blockers),
            "uncertainty": self.uncertainty,
            "recommended_next_action": self.recommended_next_action,
        }


def _expect_mapping(raw: Mapping[str, Any], key: str, reasons: list[str]) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        reasons.append(f"{key} must be an object")
        return {}
    return value


def _expect_string_list(raw: Mapping[str, Any], key: str, reasons: list[str]) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        reasons.append(f"{key} must be a list of strings")
        return ()
    return tuple(value)


def _expect_evidence_list(raw: Mapping[str, Any], key: str, reasons: list[str]) -> tuple[str, ...]:
    """Evidence/artifact paths must be safe relative paths, fail-closed."""
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        reasons.append(f"{key} must be a list of strings")
        return ()
    validated: list[str] = []
    for item in value:
        try:
            validated.append(ensure_relative_safe(item))
        except ValueError as exc:
            reasons.append(f"{key} contains an unsafe path: {exc}")
            continue
    return tuple(validated)


def _parse_file_changes(raw: Mapping[str, Any], reasons: list[str]) -> tuple[FileChange, ...]:
    value = raw.get("files_changed")
    if not isinstance(value, list):
        reasons.append("files_changed must be a list")
        return ()
    changes: list[FileChange] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            reasons.append(f"files_changed[{index}] must be an object")
            continue
        try:
            path = ensure_relative_safe(str(item["path"]))
        except (KeyError, ValueError) as exc:
            reasons.append(f"files_changed[{index}].path invalid: {exc}")
            continue
        if path in seen:
            reasons.append(f"files_changed[{index}].path duplicated: {path}")
            continue
        seen.add(path)
        action = str(item.get("action", ""))
        digest = str(item.get("sha256", ""))
        try:
            size = int(item.get("size", -1))
        except (TypeError, ValueError):
            reasons.append(f"files_changed[{index}].size must be an integer")
            continue
        if action not in {"added", "modified", "deleted"}:
            reasons.append(f"files_changed[{index}].action invalid: {action}")
        if len(digest) != 64:
            reasons.append(f"files_changed[{index}].sha256 must be 64 hex chars")
        if size < 0:
            reasons.append(f"files_changed[{index}].size must be non-negative")
        changes.append(FileChange(path=path, action=action, sha256=digest, size=size))
    return tuple(changes)


def _parse_tests(raw: Mapping[str, Any], reasons: list[str]) -> tuple[TestRun, ...]:
    value = raw.get("tests")
    if not isinstance(value, list):
        reasons.append("tests must be a list")
        return ()
    tests: list[TestRun] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            reasons.append(f"tests[{index}] must be an object")
            continue
        try:
            evidence_path = ensure_relative_safe(str(item["evidence_path"]))
        except (KeyError, ValueError) as exc:
            reasons.append(f"tests[{index}].evidence_path invalid: {exc}")
            continue
        passed = bool(item.get("passed", False))
        try:
            exit_code = int(item.get("exit_code", 1))
        except (TypeError, ValueError):
            reasons.append(f"tests[{index}].exit_code must be an integer")
            continue
        tests.append(
            TestRun(
                name=str(item.get("name", "")),
                passed=passed,
                exit_code=exit_code,
                summary=str(item.get("summary", "")),
                evidence_path=evidence_path,
            )
        )
    return tuple(tests)


def validate_worker_result(raw: Mapping[str, Any]) -> WorkerResult:
    """Validate a machine-readable worker result; malformed input is rejected."""
    reasons: list[str] = []
    missing = [key for key in REQUIRED_KEYS if key not in raw]
    if missing:
        reasons.append(f"missing required keys: {sorted(missing)}")
    status = str(raw.get("status", ""))
    if status not in ALLOWED_STATUSES:
        reasons.append(f"status must be one of {ALLOWED_STATUSES}")
    claims = _expect_mapping(raw, "claims", reasons)
    files_changed = _parse_file_changes(raw, reasons)
    commands_run = _expect_string_list(raw, "commands_run", reasons)
    tests = _parse_tests(raw, reasons)
    evidence = _expect_evidence_list(raw, "evidence", reasons)
    artifacts = _expect_evidence_list(raw, "artifacts", reasons)
    external_effects = _expect_mapping(raw, "external_effects", reasons)
    blockers = _expect_string_list(raw, "blockers", reasons)
    uncertainty = str(raw.get("uncertainty", ""))
    recommended = str(raw.get("recommended_next_action", ""))
    if reasons:
        raise WorkerResultError(reasons)
    return WorkerResult(
        status=status,
        claims=claims,
        files_changed=files_changed,
        commands_run=commands_run,
        tests=tests,
        evidence=evidence,
        artifacts=artifacts,
        external_effects=external_effects,
        blockers=blockers,
        uncertainty=uncertainty,
        recommended_next_action=recommended,
    )
