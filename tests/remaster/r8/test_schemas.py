from __future__ import annotations

import pytest

from jarvis_foundry.orchestration.schemas import (
    WorkerResultError,
    validate_worker_result,
)


def valid_payload() -> dict[str, object]:
    return {
        "status": "PASS",
        "claims": {"asset": "regression_suite"},
        "files_changed": [
            {
                "path": "tests/test_a.py",
                "action": "added",
                "sha256": "a" * 64,
                "size": 10,
            }
        ],
        "commands_run": ["pytest tests -q"],
        "tests": [
            {
                "name": "pytest tests -q",
                "passed": True,
                "exit_code": 0,
                "summary": "10 passed",
                "evidence_path": "test-results.xml",
            }
        ],
        "evidence": ["test-results.xml"],
        "artifacts": ["docs/health.md"],
        "external_effects": {"live": 0},
        "blockers": [],
        "uncertainty": "low",
        "recommended_next_action": "verify and accept",
    }


def test_valid_result_parses() -> None:
    result = validate_worker_result(valid_payload())
    assert result.status == "PASS"
    assert result.files_changed[0].path == "tests/test_a.py"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.pop("files_changed"),
        lambda payload: payload.__setitem__("status", "MAYBE"),
        lambda payload: payload.__setitem__("claims", "not-an-object"),
        lambda payload: payload.__setitem__("blockers", "not-a-list"),
    ],
)
def test_malformed_result_is_rejected(mutator) -> None:
    payload = valid_payload()
    mutator(payload)
    with pytest.raises(WorkerResultError):
        validate_worker_result(payload)


def test_unsafe_path_is_rejected() -> None:
    payload = valid_payload()
    payload["files_changed"] = [
        {
            "path": "../escape.txt",
            "action": "added",
            "sha256": "a" * 64,
            "size": 1,
        }
    ]
    with pytest.raises(WorkerResultError, match="unsafe relative path"):
        validate_worker_result(payload)


def test_windows_drive_path_is_rejected() -> None:
    payload = valid_payload()
    payload["files_changed"] = [
        {
            "path": "C:/evil/x.py",
            "action": "added",
            "sha256": "a" * 64,
            "size": 1,
        }
    ]
    with pytest.raises(WorkerResultError, match="drive/URI path refused"):
        validate_worker_result(payload)


def test_evidence_unsafe_path_is_rejected() -> None:
    payload = valid_payload()
    payload["evidence"] = ["C:/evil/evidence.xml"]
    with pytest.raises(WorkerResultError, match="evidence contains an unsafe path"):
        validate_worker_result(payload)


def test_artifact_unsafe_path_is_rejected() -> None:
    payload = valid_payload()
    payload["artifacts"] = ["C:/evil/artifact.bin"]
    with pytest.raises(WorkerResultError, match="artifacts contains an unsafe path"):
        validate_worker_result(payload)


def test_duplicate_path_is_rejected() -> None:
    payload = valid_payload()
    entry = payload["files_changed"][0]
    payload["files_changed"] = [entry, dict(entry)]
    with pytest.raises(WorkerResultError, match="duplicated"):
        validate_worker_result(payload)


def test_bad_hash_is_rejected() -> None:
    payload = valid_payload()
    payload["files_changed"] = [
        {
            "path": "tests/test_a.py",
            "action": "added",
            "sha256": "short",
            "size": 10,
        }
    ]
    with pytest.raises(WorkerResultError, match="sha256"):
        validate_worker_result(payload)
