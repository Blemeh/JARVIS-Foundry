from __future__ import annotations

import ast
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from acceptance import (
    ALLOWED_STATUSES,
    ValidationError,
    build_request,
    evaluate_response,
    load_purpose_bundle,
    run_external_adapter,
    validate_request,
)
from mutant_adapter import apply_mutant
from reference_adapter import build_response


EVALUATION_TIME = "2030-01-01T00:00:00Z"


def test_malformed_json_from_zero_exit_subprocess_fails_closed(
    purpose_root: Path,
    mutant_command,
    tmp_path: Path,
) -> None:
    result = run_external_adapter(
        mutant_command("malformed_json"),
        purpose_root,
        EVALUATION_TIME,
        run_root=tmp_path / "malformed_json",
    )
    assert result["status"] == "FAIL"
    assert "MALFORMED_JSON" in {finding["code"] for finding in result["findings"]}
    assert all(journey["criterion_evidence"] for journey in result["journeys"])


def test_unknown_tampered_and_path_escape_responses_fail_closed(
    purpose_root: Path,
    tmp_path: Path,
) -> None:
    expected = {
        "unknown_field": "UNKNOWN_FIELD",
        "unknown_journey": "UNKNOWN_JOURNEY_ID",
        "invalid_status": "INVALID_STATUS",
        "evidence_tamper": "EVIDENCE_HASH_MISMATCH",
        "path_escape": "ARTIFACT_PATH_ESCAPE",
    }
    bundle = load_purpose_bundle(purpose_root)
    process = {"exit_code": 0, "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stderr_present": False, "timed_out": False}
    run_root = tmp_path / "protocol-mutations"
    request = build_request(bundle, run_root, EVALUATION_TIME)
    baseline_response = build_response(request, compact_artifacts=True)
    for mutant, expected_code in expected.items():
        response = apply_mutant(copy.deepcopy(baseline_response), mutant)
        result = evaluate_response(request, response, bundle["manifest"], run_root, process)
        assert result["status"] == "FAIL"
        assert expected_code in {finding["code"] for finding in result["findings"]}
        assert all(journey["criterion_evidence"] for journey in result["journeys"])


def test_all_blind_nonpass_status_words_fail_strict_validation(
    purpose_root: Path,
    tmp_path: Path,
) -> None:
    bundle = load_purpose_bundle(purpose_root)
    process = {"exit_code": 0, "stderr_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "stderr_present": False, "timed_out": False}
    run_root = tmp_path / "status-vocabulary"
    request = build_request(bundle, run_root, EVALUATION_TIME)
    baseline_response = build_response(request, compact_artifacts=True)
    for status in ("FAIL", "BLOCKED", "NOT_TESTED", "WAITING"):
        response = apply_mutant(copy.deepcopy(baseline_response), f"status_{status.lower()}")
        result = evaluate_response(request, response, bundle["manifest"], run_root, process)
        assert result["status"] == "FAIL"
        assert result["journeys"][0]["status"] == "FAIL"
        assert "STATUS_DETAIL_REQUIRED" in {finding["code"] for finding in result["findings"]}


def test_status_vocabulary_is_exactly_five_words() -> None:
    assert ALLOWED_STATUSES == ("PASS", "FAIL", "BLOCKED", "NOT_TESTED", "WAITING")


def test_unknown_request_field_is_rejected(purpose_root: Path, tmp_path: Path) -> None:
    bundle = load_purpose_bundle(purpose_root)
    request = build_request(bundle, tmp_path / "request", EVALUATION_TIME)
    request["surprise"] = True
    with pytest.raises(ValidationError, match="UNKNOWN_FIELD"):
        validate_request(request)


def test_unknown_contract_field_is_rejected_before_adapter_execution(
    purpose_root: Path,
    reference_command: list[str],
    tmp_path: Path,
) -> None:
    copied = tmp_path / "purpose"
    shutil.copytree(purpose_root, copied)
    contract_path = copied / "journeys" / "J01.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["implementation_hint"] = "forbidden coupling"
    contract_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")
    with pytest.raises(ValidationError, match="UNKNOWN_FIELD"):
        run_external_adapter(reference_command, copied, EVALUATION_TIME, run_root=tmp_path / "never-run")


def test_missing_adapter_endpoint_is_a_structured_rejection(
    purpose_root: Path,
    tmp_path: Path,
) -> None:
    command = [str(tmp_path / "no-such-adapter.exe")]
    result = run_external_adapter(command, purpose_root, EVALUATION_TIME, run_root=tmp_path / "missing")
    assert result["status"] == "FAIL"
    assert result["findings"][0]["code"] == "ADAPTER_UNAVAILABLE"
    assert len(result["journeys"]) == 10
    assert all(journey["criterion_evidence"] for journey in result["journeys"])


def test_evaluator_and_adapters_have_no_product_module_imports(script_root: Path) -> None:
    scripts = sorted(script_root.glob("*.py"))
    assert scripts
    for path in scripts:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("jarvis_foundry") for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("jarvis_foundry"), path


def test_cli_runs_an_arbitrary_external_adapter_command(
    repo_root: Path,
    purpose_root: Path,
    script_root: Path,
    python_executable: Path,
    reference_command: list[str],
    tmp_path: Path,
) -> None:
    output = tmp_path / "cli-result.json"
    run_root = tmp_path / "cli-run"
    command = [
        str(python_executable),
        "-B",
        str(script_root / "run_acceptance.py"),
        "--purpose-root",
        str(purpose_root),
        "--evaluation-time",
        EVALUATION_TIME,
        "--run-root",
        str(run_root),
        "--output",
        str(output),
        "--",
        *reference_command,
    ]
    completed = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, check=False, timeout=15)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert result["proof_scope"] == "HARNESS_SELF_TEST"
    assert result["product_claim_status"] == "NOT_TESTED"
