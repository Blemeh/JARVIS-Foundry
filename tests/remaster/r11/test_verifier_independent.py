"""The fresh independent verifier subprocess has no candidate context."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from r11_helpers import EVIDENCE_UTC, REPO_ROOT, spec_by_id
from run_cycles import prepare_baseline_repo

from jarvis_foundry.evolution_live.store import sha256_file
from jarvis_foundry.evolution_live.verifier import verify


def _request(root: Path) -> tuple[dict[str, object], Path]:
    baseline = prepare_baseline_repo(root)
    spec = spec_by_id("c1")
    candidate = root / "candidate"
    subprocess.run(
        ["git", "clone", "--local", str(baseline), str(candidate)],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    patch = Path(str(spec["patch_path"]))
    target = candidate / str(spec["patch_target"])
    target.write_bytes(patch.read_bytes())
    artifacts = root / "artifacts"
    artifact_target = artifacts / str(spec["patch_target"])
    artifact_target.parent.mkdir(parents=True)
    artifact_target.write_bytes(patch.read_bytes())
    request = {
        "request_id": "req-verifier-test",
        "cycle_id": "cycle-verifier-test",
        "candidate_id": "c1",
        "baseline_repo": str(baseline),
        "artifacts_dir": str(artifacts),
        "expected_artifact_hashes": {str(spec["patch_target"]): sha256_file(patch)},
        "criteria": [
            "artifacts_hash_match",
            "candidate_pytest_improves",
            "behavior_no_regression",
        ],
        "measure_commands": [
            [sys.executable, "-m", "pytest", "tests", "-q"],
            [sys.executable, "probe.py"],
        ],
        "verifier_utc": EVIDENCE_UTC,
    }
    return request, baseline


def test_fresh_verifier_subprocess_verifies_candidate(tmp_path) -> None:
    request, _baseline = _request(tmp_path)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    output_path = tmp_path / "verdict.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "jarvis_foundry.evolution_live.verifier",
            "--request",
            str(request_path),
            "--output",
            str(output_path),
            "--python",
            sys.executable,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    verdict = json.loads(output_path.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert verdict["verdict"] == "PASS"
    assert verdict["verifier_id"].startswith("r11-fresh-verifier-")
    assert verdict["candidate_id"] == "c1"


def test_verifier_function_checks_fresh_hashes(tmp_path) -> None:
    request, _baseline = _request(tmp_path)
    payload = verify(request, sys.executable)
    assert payload["verdict"] == "PASS"
    assert payload["checks"]["artifacts_hash_match"] is True
    assert payload["baseline_metrics"]["pytest_passed"] == 2
    assert payload["candidate_metrics"]["pytest_passed"] == 6
