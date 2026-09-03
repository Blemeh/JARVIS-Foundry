"""Run the disposable V12 A5-style self-expansion convergence proof."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence" / "v12-self-expansion-convergence" / "proof.json",
    )
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    selected_mcp_output = output.with_name("selected-mcp-adoption.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["JARVIS_V12_PROOF_OUTPUT"] = str(output)
    env["JARVIS_V12_MCP_ADOPTION_PROOF_OUTPUT"] = str(selected_mcp_output)
    uv_executable = shutil.which("uv")
    if not uv_executable:
        raise RuntimeError("uv is required for the locked disposable proof environment")
    command = [
        uv_executable,
        "run",
        "--extra",
        "dev",
        "--extra",
        "web",
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/foundry/test_v12_self_expansion_convergence.py",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    if (
        result.returncode != 0
        or not output.is_file()
        or not selected_mcp_output.is_file()
    ):
        failure = {
            "contract_version": "jarvis.v12.self-expansion-proof.v1",
            "passed": False,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-4000:],
            "stderr_tail": result.stderr[-4000:],
            "live_mutated": False,
            "main_or_origin_mutated": False,
        }
        output.write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"passed": False, "proof": str(output)}, sort_keys=True))
        return 1
    proof = json.loads(output.read_text(encoding="utf-8"))
    selected_mcp = json.loads(selected_mcp_output.read_text(encoding="utf-8"))
    proof["selected_mcp_adoption"] = selected_mcp
    proof["runner"] = {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
    }
    output.write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "passed": bool(proof.get("passed")),
        "proof": str(output),
        "trial_count": proof.get("trial_count"),
        "duplicate_registrations": proof.get("duplicate_registrations"),
    }, sort_keys=True))
    restoration = selected_mcp.get("restoration_failure") or {}
    passed = (
        proof.get("passed") is True
        and selected_mcp.get("passed") is True
        and restoration.get("readiness") == "DEGRADED"
        and restoration.get("result_status") == "FAILED"
        and restoration.get("result_provider") == "fabric"
        and restoration.get("generic_dispatches") == 0
        and restoration.get("worker_route_event_delta") == 0
        and restoration.get("spine_result_count") == 1
        and restoration.get("spine_claimed") == "BLOCKED"
        and restoration.get("spine_verified") == "UNKNOWN"
        and restoration.get("spine_success") is False
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
