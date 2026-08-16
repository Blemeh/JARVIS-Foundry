"""Fresh independent verifier subprocess for R11 candidates.

The verifier receives a verification request (criteria, expected artifact
hashes, baseline repository, measurement commands) and nothing else.  It
rebuilds the candidate from a fresh baseline clone, re-measures baseline and
candidate, and writes its own verdict.  It never imports builder or engine
context, so a candidate cannot certify itself.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess  # nosec B404 - deterministic non-shell process isolation
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from jarvis_foundry.evolution_live.store import sha256_file, tree_hash


def _run(command: list[str], cwd: Path) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(  # nosec B603 - command list, no shell, bounded timeout
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "wall_seconds": round(time.monotonic() - started, 3),
        "output": completed.stdout + completed.stderr,
    }


def _parse_pytest(output: str) -> tuple[int, int]:
    passed = 0
    failed = 0
    for line in output.splitlines():
        match_passed = re.search(r"(\d+) passed", line)
        match_failed = re.search(r"(\d+) failed", line)
        if match_passed:
            passed += int(match_passed.group(1))
        if match_failed:
            failed += int(match_failed.group(1))
    return passed, failed


def _parse_probe(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed: Any = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    return {"checks": {}, "passed": 0, "total": 0}


def measure(root: Path, commands: list[list[str]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    pytest_passed = 0
    pytest_failed = 0
    behavior: dict[str, Any] = {}
    for command in commands:
        result = _run(command, root)
        results.append(
            {
                "command": command,
                "exit_code": result["exit_code"],
                "wall_seconds": result["wall_seconds"],
            }
        )
        if "pytest" in command[1:]:
            passed, failed = _parse_pytest(result["output"])
            pytest_passed += passed
            pytest_failed += failed
        if command and command[-1] == "probe.py":
            behavior = _parse_probe(result["output"])
    return {
        "pytest_passed": pytest_passed,
        "pytest_failed": pytest_failed,
        "behavior_passed": int(behavior.get("passed", 0)),
        "behavior_total": int(behavior.get("total", 0)),
        "commands_exit_zero": all(item["exit_code"] == 0 for item in results),
        "command_results": results,
        "probe": behavior,
    }


def verify(request: dict[str, Any], python: str) -> dict[str, Any]:
    baseline_repo = Path(str(request["baseline_repo"]))
    artifacts_dir = Path(str(request["artifacts_dir"]))
    expected_hashes = request["expected_artifact_hashes"]
    commands = request["measure_commands"]
    criteria = request["criteria"]
    verifier_id = f"r11-fresh-verifier-{uuid.uuid4().hex[:10]}"
    fresh_root = Path(tempfile.mkdtemp(prefix="r11-verifier-"))
    try:
        candidate_clone = fresh_root / "candidate"
        subprocess.run(  # nosec B603 B607 - git path fixed by local temp context
            ["git", "clone", "--local", str(baseline_repo), str(candidate_clone)],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        baseline_metrics = measure(candidate_clone, commands)
        artifact_hashes: dict[str, str] = {}
        for relative in expected_hashes:
            source = artifacts_dir / relative
            target = candidate_clone / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            actual = sha256_file(target)
            artifact_hashes[relative] = actual
        hashes_match = artifact_hashes == expected_hashes
        subprocess.run(  # nosec B603 B607 - git path fixed by local temp context
            [
                "git",
                "-c",
                "user.name=independent-verifier",
                "-c",
                "user.email=verifier@r11.invalid",
                "add",
                "-A",
            ],
            cwd=str(candidate_clone),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        subprocess.run(  # nosec B603 B607 - git path fixed by local temp context
            [
                "git",
                "-c",
                "user.name=independent-verifier",
                "-c",
                "user.email=verifier@r11.invalid",
                "commit",
                "-m",
                f"verifier applies {request['candidate_id']}",
            ],
            cwd=str(candidate_clone),
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        candidate_metrics = measure(candidate_clone, commands)
        checks: dict[str, bool] = {
            "artifacts_hash_match": hashes_match,
            "candidate_pytest_improves": (
                candidate_metrics["pytest_passed"] > baseline_metrics["pytest_passed"]
            ),
            "behavior_no_regression": (
                candidate_metrics["behavior_passed"] >= baseline_metrics["behavior_passed"]
            ),
        }
        for criterion in criteria:
            if criterion not in checks:
                checks[criterion] = False
        verdict = "PASS" if all(checks.values()) else "FAIL"
        return {
            "schema": "jarvis-r11-independent-verification-v1",
            "verifier_id": verifier_id,
            "request_id": request["request_id"],
            "cycle_id": request["cycle_id"],
            "candidate_id": request["candidate_id"],
            "verdict": verdict,
            "checks": checks,
            "baseline_metrics": baseline_metrics,
            "candidate_metrics": candidate_metrics,
            "artifact_hashes": artifact_hashes,
            "fresh_clone_hash": tree_hash(candidate_clone),
            "fresh_context": "fresh temp clone; no builder/candidate context passed",
            "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    finally:
        shutil.rmtree(fresh_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    payload = verify(request, args.python)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"verifier {payload['verifier_id']}: verdict={payload['verdict']} "
        f"checks={sum(payload['checks'].values())}/{len(payload['checks'])}"
    )
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
