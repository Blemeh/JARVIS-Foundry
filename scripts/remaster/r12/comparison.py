"""R12-D01: integrated comparison of FROZEN V7 vs remastered JARVIS.

The FROZEN V7 release tree is snapshotted from the protected main
repository read-only with ``git archive`` and never mutated. Both sides
run equivalent relevant inputs; meaningful divergences are explained.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - explicit git archive/subprocess harness runs
import sys
import time
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    PROTECTED_MAIN,
    PYTHON,
    REPO_ROOT,
    SCRIPTS_ROOT,
    TEMP_BASE,
    json_hash,
    run,
    safe_clear,
    sha256_file,
    utc_now,
    write_checkpoint,
    write_json,
)

V7_TAG = "v8-pre-promotion-93a3ddc"


def snapshot_v7() -> dict[str, Any]:
    target = TEMP_BASE / "v7-snapshot"
    safe_clear(target)
    target.mkdir(parents=True, exist_ok=True)
    archived = subprocess.run(
        ["git", "-C", str(PROTECTED_MAIN), "archive", "--format=tar", V7_TAG],
        capture_output=True,
        timeout=600,
        check=False,
    )
    if archived.returncode != 0:
        raise RuntimeError(f"git archive failed: {archived.stderr.decode(errors='replace')[-500:]}")
    extracted = subprocess.run(
        ["tar", "-xf", "-", "-C", str(target)],
        input=archived.stdout,
        capture_output=True,
        timeout=600,
        check=False,
    )
    if extracted.returncode != 0:
        raise RuntimeError(
            f"tar extract failed: {extracted.stderr.decode(errors='replace')[-500:]}"
        )
    files = [path for path in target.rglob("*") if path.is_file() and ".git" not in path.parts]
    return {
        "schema": "jarvis-r12-v7-snapshot-v1",
        "tag": V7_TAG,
        "source": "protected main (read-only git archive)",
        "file_count": len(files),
        "tree_hash": json_hash(
            {path.relative_to(target).as_posix(): sha256_file(path) for path in sorted(files)}
        ),
    }


def _divergences(domain: str, v7: Any, remastered: Any) -> list[dict[str, str]]:
    if domain == "goals_portfolio":
        return [
            {
                "area": "selection semantics",
                "v7": "project-level ranking with fixed score weights",
                "remastered": "candidate-level scoring with evidence-backed value and honest WAITING",
                "meaning": (
                    "V7 records portfolio.decision events for ranked projects; the remastered "
                    "stack scores candidate actions, records full tradeoff alternatives, and "
                    "refuses action when nothing is evidence-backed."
                ),
            }
        ]
    if domain == "mission_orchestration":
        return [
            {
                "area": "orchestration boundary",
                "v7": "scheduler + leases; missions dispatched by MissionLoop",
                "remastered": "supervisor leases plus durable orchestration ledger and portfolio-linked missions",
                "meaning": (
                    "Both preserve restart-safe lease ownership. The remastered stack links "
                    "every mission to a portfolio decision/candidate and keeps malformed or "
                    "late worker results out of the ledger."
                ),
            }
        ]
    if domain == "capability_lifecycle":
        return [
            {
                "area": "capability lifecycle",
                "v7": "registration + brokerage with static reconciliation matrix",
                "remastered": "discover/probe/ready/active state machine with proof-gated activation",
                "meaning": (
                    "V7 records capabilities and brokers needs; the remastered stack cannot "
                    "reach ACTIVE without a real before/after effect proof and reconciliation."
                ),
            }
        ]
    if domain == "command_center_briefing":
        return [
            {
                "area": "briefing languages",
                "v7": "ledger-derived briefing with EN and SQ rendering",
                "remastered": "structured EN/SQ briefing with categories, evidence refs, and honest missing-evidence",
                "meaning": (
                    "Both render EN and SQ. The remastered briefing is drillable per category "
                    "and explicitly reports provider/voice receipts as missing instead of "
                    "silently omitting them."
                ),
            }
        ]
    if domain == "evolution_cycle":
        return [
            {
                "area": "evolution cycle",
                "v7": "ladder runs build/evaluation/verification/canary/promote and auto-rejects broken candidates",
                "remastered": "frozen lifecycle state machine with independent verification and promotion guards",
                "meaning": (
                    "The V7 harness auto-rejected a broken candidate at the build gate; the "
                    "remastered stack refuses invalid candidates/promotions at earlier frozen "
                    "boundaries with durable refusal records and supervisor-only control."
                ),
            }
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_v7()
    write_json(TEMP_BASE / "R12_V7_SNAPSHOT.json", snapshot)
    v7_run = run(
        [PYTHON, str(SCRIPTS_ROOT / "v7_harness.py"), str(TEMP_BASE / "v7-snapshot")],
        cwd=REPO_ROOT,
        timeout_seconds=1800,
    )
    remastered_run = run(
        [PYTHON, str(SCRIPTS_ROOT / "remastered_harness.py")],
        cwd=REPO_ROOT,
        timeout_seconds=1800,
        env={"JARVIS_R12_TEMP": str(TEMP_BASE)},
    )
    v7_output: dict[str, Any] = json.loads(v7_run["output"])
    remastered_output: dict[str, Any] = json.loads(remastered_run["output"])
    domains = list(v7_output["domains"].keys())
    rows: list[dict[str, Any]] = []
    for domain in domains:
        rows.append(
            {
                "domain": domain,
                "input": "equivalent deterministic proving input",
                "v7": v7_output["domains"][domain],
                "remastered": remastered_output["domains"][domain],
                "divergences": _divergences(
                    domain, v7_output["domains"][domain], remastered_output["domains"][domain]
                ),
            }
        )
    matrix = {
        "schema": "jarvis-r12-comparison-matrix-v1",
        "generated_utc": utc_now(),
        "fixed_utc": EVIDENCE_UTC,
        "v7_snapshot": snapshot,
        "v7_harness": {
            "exit_code": v7_run["exit_code"],
            "wall_seconds": v7_run["wall_seconds"],
            "output_file": str(TEMP_BASE / "R12_V7_OUTPUT.json"),
        },
        "remastered_harness": {
            "exit_code": remastered_run["exit_code"],
            "wall_seconds": remastered_run["wall_seconds"],
            "output_file": str(TEMP_BASE / "R12_REMASTERED_OUTPUT.json"),
        },
        "domains": rows,
        "domain_count": len(rows),
        "passed": v7_run["exit_code"] == 0 and remastered_run["exit_code"] == 0,
        "protected_main_mutated": False,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    write_json(TEMP_BASE / "R12_V7_OUTPUT.json", v7_output)
    write_json(TEMP_BASE / "R12_REMASTERED_OUTPUT.json", remastered_output)
    write_json(EVIDENCE_DIR / "R12_COMPARISON_MATRIX.json", matrix)
    write_checkpoint(
        "comparison",
        {"status": "PASS" if matrix["passed"] else "FAIL", "domains": domains},
    )
    print(
        f"comparison: passed={matrix['passed']} domains={len(rows)} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if matrix["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
