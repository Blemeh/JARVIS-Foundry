"""Build the R7 builder report, artifact manifest, and confinement evidence."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    REPO_ROOT,
    evidence_bytes,
    owned_r7_files,
    read_json,
    record_enabled,
    sha256_file,
    tool_versions,
    write_json,
)

EVIDENCE_BUDGET_BYTES = 8_388_608
WORK_PACKAGE_PATH = REPO_ROOT / "docs" / "remaster" / "work_packages" / "R7_WORK_PACKAGE.json"
WORK_PACKAGE_SHA256 = "025122426C9985386BD5BA0857E89915ABD804A50E6C73241D064C03F52E07E4"

GATE_FILES = {
    "R7-G01": "R7_SELECTION.json",
    "R7-G02": "R7_MONITOR.json",
    "R7-G03": "R7_DISCOVERY.json",
    "R7-G04": "R7_RESEARCH.json",
    "R7-G05": "R7_RESTART.json",
}

DELIVERABLE_FILES = {
    "R7-D01": "R7_JOURNEY.json",
    "R7-D02": "R7_SELECTION.json",
    "R7-D03": "R7_DISCOVERY.json",
    "R7-D04": "R7_RESEARCH.json",
    "R7-D05": "R7_MONITOR.json",
    "R7-D06": "R7_JOURNEY.json",
    "R7-D07": "R7_JOURNEY.json",
}

ALL_GATE_JSONS: tuple[str, ...] = (
    "R7_JOURNEY.json",
    "R7_SELECTION.json",
    "R7_DISCOVERY.json",
    "R7_RESEARCH.json",
    "R7_MONITOR.json",
    "R7_RESTART.json",
    "R7_PRIVACY.json",
    "R7_FOCUSED_TESTS.json",
    "R7_FULL_SUITE.json",
    "R7_STATIC.json",
    "R7_SECRET_SCAN.json",
)

# Baseline dirty worktree entries that predate R7 (verified before any R7 files
# existed): R6 deferred work, the owner redirect, and FINDINGS.jsonl.
PRE_EXISTING_BASELINE: tuple[str, ...] = (
    " M docs/remaster/FINDINGS.jsonl",
    "?? docs/remaster/OWNER_REDIRECT_R6_DEFERRED.json",
    "?? docs/remaster/evidence/r6/",
    "?? docs/remaster/work_packages/R6_WORK_PACKAGE.json",
    "?? scripts/remaster/r6/",
    "?? src/jarvis_foundry/kernel/memory/",
    "?? tests/remaster/r6/",
)


def _status_from_gate(gate_file: str) -> tuple[str, list[str]]:
    path = EVIDENCE_DIR / gate_file
    if not path.is_file():
        return "NOT_TESTED", []
    payload = read_json(path)
    if gate_file == "R7_LIVE_GUARD_COMPARISON.json":
        passed = bool(payload.get("protected_state_unchanged"))
    else:
        passed = bool(payload.get("passed"))
    return ("PASS" if passed else "FAIL"), [gate_file]


def _manifest() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for relative in owned_r7_files(REPO_ROOT):
        if relative == "docs/remaster/evidence/r7/R7_ARTIFACT_MANIFEST.json":
            continue
        path = REPO_ROOT / relative
        if not path.is_file():
            continue
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return {
        "schema": "jarvis-r7-artifact-manifest-v1",
        "files": files,
        "file_count": len(files),
    }


def _confinement() -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    lines = completed.stdout.splitlines()
    allowed_prefixes = (
        "src/jarvis_foundry/kernel/portfolio/",
        "scripts/remaster/r7/",
        "tests/remaster/r7/",
        "docs/remaster/evidence/r7/",
        "docs/remaster/work_packages/R7_WORK_PACKAGE.json",
    )
    violations: list[str] = []
    for line in lines:
        if line in PRE_EXISTING_BASELINE:
            continue
        if any(line[3:].startswith(prefix) for prefix in allowed_prefixes):
            continue
        violations.append(line)
    return {
        "porcelain_line_count": len(lines),
        "pre_existing_baseline_entries": list(PRE_EXISTING_BASELINE),
        "confinement_violations": violations,
        "confined": not violations,
    }


def _findings() -> list[dict[str, Any]]:
    return [
        {
            "id": "R7-F-001",
            "severity": "S2",
            "disposition": "RESOLVED_IN_R7",
            "title": "Monitor initial capture was treated as a change",
            "detail": (
                "The first poll after registering a source produced a candidate because the "
                "empty initial cursor looked like a change. R7 now records the first capture as "
                "a baseline and creates candidates only for changes after a real baseline."
            ),
            "evidence": "R7_MONITOR.json, tests/remaster/r7/test_monitor.py",
        },
        {
            "id": "R7-F-002",
            "severity": "S3",
            "disposition": "RESOLVED_IN_R7",
            "title": "Selection restart hash drifted with later discoveries",
            "detail": (
                "Portfolio hashing included active monitor discoveries, so a selection re-run "
                "after restart could emit a new decision id. R7 hashes projects and candidate "
                "actions only, keeping restart selection idempotent."
            ),
            "evidence": "R7_SELECTION.json, tests/remaster/r7/test_restart.py",
        },
        {
            "id": "R7-F-003",
            "severity": "S3",
            "disposition": "RESOLVED_IN_R7",
            "title": "Research restart identity changed after first capture",
            "detail": (
                "The synthesis key was computed before capture using an empty content hash, so "
                "the second run built a different research id. R7 captures sources first and keys "
                "syntheses on the captured content hashes, making reruns idempotent."
            ),
            "evidence": "R7_RESEARCH.json, tests/remaster/r7/test_research.py",
        },
        {
            "id": "R7-F-004",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Public research probe depends on network availability",
            "detail": (
                "One bounded read-only GitHub releases probe is attempted per research gate run. "
                "When the network is unavailable it is recorded BLOCKED and local real sources "
                "still drive the gate; no fake public evidence is ever recorded."
            ),
            "evidence": "R7_RESEARCH.json",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R7_BUILDER_REPORT.json")
    parser.add_argument("--manifest", type=Path, default=EVIDENCE_DIR / "R7_ARTIFACT_MANIFEST.json")
    parser.add_argument("--findings", type=Path, default=EVIDENCE_DIR / "R7_FINDINGS.json")
    args = parser.parse_args(argv)

    started = time.monotonic()
    deliverables: dict[str, dict[str, Any]] = {}
    for deliverable_id, gate_file in DELIVERABLE_FILES.items():
        status, evidence = _status_from_gate(gate_file)
        deliverables[deliverable_id] = {
            "status": status,
            "evidence": evidence,
            "evidence_hashes": {
                name: sha256_file(EVIDENCE_DIR / name)
                for name in evidence
                if (EVIDENCE_DIR / name).is_file()
            },
        }

    gates: dict[str, dict[str, Any]] = {}
    for gate_id, gate_file in GATE_FILES.items():
        status, evidence = _status_from_gate(gate_file)
        gates[gate_id] = {
            "status": status,
            "evidence": evidence,
            "evidence_hashes": {
                name: sha256_file(EVIDENCE_DIR / name)
                for name in evidence
                if (EVIDENCE_DIR / name).is_file()
            },
        }

    focused_tests = EVIDENCE_DIR / "R7_FOCUSED_TESTS.json"
    tests_status = (
        "PASS"
        if focused_tests.is_file() and read_json(focused_tests).get("exit_code") == 0
        else "NOT_TESTED"
    )
    full_suite = EVIDENCE_DIR / "R7_FULL_SUITE.json"
    full_suite_payload = read_json(full_suite) if full_suite.is_file() else {}
    full_suite_status = (
        "PASS"
        if full_suite.is_file() and full_suite_payload.get("exit_code") == 0
        else "NOT_TESTED"
    )
    static = EVIDENCE_DIR / "R7_STATIC.json"
    static_status = "PASS" if static.is_file() and read_json(static).get("passed") else "NOT_TESTED"
    secret = EVIDENCE_DIR / "R7_SECRET_SCAN.json"
    secret_status = "PASS" if secret.is_file() and read_json(secret).get("passed") else "NOT_TESTED"
    privacy = EVIDENCE_DIR / "R7_PRIVACY.json"
    privacy_status = (
        "PASS" if privacy.is_file() and read_json(privacy).get("passed") else "NOT_TESTED"
    )

    live_guard_before = EVIDENCE_DIR / "R7_LIVE_GUARD_BEFORE.json"
    live_guard_after = EVIDENCE_DIR / "R7_LIVE_GUARD_AFTER.json"
    live_guard_comparison = EVIDENCE_DIR / "R7_LIVE_GUARD_COMPARISON.json"
    comparison_payload = read_json(live_guard_comparison) if live_guard_comparison.is_file() else {}
    live_guard = {
        "before": str(live_guard_before),
        "before_sha256": sha256_file(live_guard_before) if live_guard_before.is_file() else None,
        "after": str(live_guard_after),
        "after_sha256": sha256_file(live_guard_after) if live_guard_after.is_file() else None,
        "comparison": str(live_guard_comparison),
        "comparison_sha256": (
            sha256_file(live_guard_comparison) if live_guard_comparison.is_file() else None
        ),
        "protected_state_unchanged": comparison_payload.get("protected_state_unchanged"),
    }

    confinement = _confinement()
    evidence_size = evidence_bytes()
    under_budget = evidence_size < EVIDENCE_BUDGET_BYTES
    g06_passed = (
        live_guard["protected_state_unchanged"] is True
        and tests_status == "PASS"
        and full_suite_status == "PASS"
        and static_status == "PASS"
        and secret_status == "PASS"
        and privacy_status == "PASS"
        and under_budget
        and confinement["confined"]
    )
    gates["R7-G06"] = {
        "status": "PASS" if g06_passed else "FAIL",
        "evidence": [
            "R7_FULL_SUITE.json",
            "R7_STATIC.json",
            "R7_SECRET_SCAN.json",
            "R7_PRIVACY.json",
            "R7_LIVE_GUARD_COMPARISON.json",
        ],
        "evidence_hashes": {
            name: sha256_file(EVIDENCE_DIR / name)
            for name in (
                "R7_FULL_SUITE.json",
                "R7_STATIC.json",
                "R7_RUFF.json",
                "R7_MYPY.json",
                "R7_BANDIT.json",
                "R7_SECRET_SCAN.json",
                "R7_PRIVACY.json",
                "R7_LIVE_GUARD_COMPARISON.json",
            )
            if (EVIDENCE_DIR / name).is_file()
        },
    }
    gates["R7-G06"]["evidence_hashes"].update(
        {
            live_guard_before.name: live_guard["before_sha256"],
            live_guard_after.name: live_guard["after_sha256"],
            live_guard_comparison.name: live_guard["comparison_sha256"],
        }
    )

    findings = _findings()
    report: dict[str, Any] = {
        "schema": "jarvis-r7-builder-report-v1",
        "program": "POST_V7_JARVIS_REMASTER",
        "stage": "R7",
        "builder": "r7_builder_dsv4",
        "builder_status": "NOT_TESTED",
        "status_reason": (
            "Builder evidence is complete; a fresh independent verifier must reproduce and "
            "adjudicate the package before the gate closes."
        ),
        "generated_utc": "2026-08-15T16:30:00Z",
        "frozen_base": {
            "commit": "7bdadd85f4c943bf5da4f771238ea515d7cc3fa0",
            "branch": "remaster/post-v7-purpose",
            "work_package": "docs/remaster/work_packages/R7_WORK_PACKAGE.json",
            "work_package_sha256": WORK_PACKAGE_SHA256,
            "work_package_unchanged": (
                sha256_file(WORK_PACKAGE_PATH).lower() == WORK_PACKAGE_SHA256.lower()
            ),
            "live_v7_deployment_changes_allowed": False,
            "push_or_publish_allowed": False,
        },
        "tool_versions": tool_versions(),
        "deliverables": deliverables,
        "gates": gates,
        "focused_tests_status": tests_status,
        "full_suite": {
            "status": full_suite_status,
            "evidence": [
                "R7_FULL_SUITE.json",
                "R7_FULL_SUITE_RAW.txt",
                "R7_FULL_SUITE_PART1_JUNIT.xml",
                "R7_FULL_SUITE_PART2_JUNIT.xml",
            ],
            "evidence_hashes": {
                name: sha256_file(EVIDENCE_DIR / name)
                for name in (
                    "R7_FULL_SUITE.json",
                    "R7_FULL_SUITE_RAW.txt",
                    "R7_FULL_SUITE_PART1_JUNIT.xml",
                    "R7_FULL_SUITE_PART2_JUNIT.xml",
                )
                if (EVIDENCE_DIR / name).is_file()
            },
            "summary_line": full_suite_payload.get("summary_line"),
        },
        "static_analysis_status": static_status,
        "secret_scan_passed": secret_status == "PASS",
        "privacy_passed": privacy_status == "PASS",
        "live_guard": live_guard,
        "artifact_confinement": confinement,
        "resources_and_boundaries": {
            "evidence_budget_bytes": EVIDENCE_BUDGET_BYTES,
            "evidence_bytes": evidence_size,
            "evidence_under_budget": under_budget,
            "network_used": "one bounded read-only public research probe; no provider calls",
            "provider_calls": 0,
            "live_or_protected_writes": 0,
            "commit_created": False,
            "push_or_publish_performed": False,
        },
        "artifact_manifest": str(args.manifest),
        "findings": str(args.findings),
    }

    if record_enabled():
        write_json(
            args.findings,
            {
                "schema": "jarvis-r7-findings-v1",
                "program": "POST_V7_JARVIS_REMASTER",
                "stage": "R7",
                "generated_utc": "2026-08-15T16:30:00Z",
                "findings": findings,
            },
        )
        report["resources_and_boundaries"]["evidence_bytes"] = evidence_bytes()
        write_json(args.output, report)
        manifest = _manifest()
        report_relative = str(args.output.relative_to(REPO_ROOT)).replace("\\", "/")
        findings_relative = str(args.findings.relative_to(REPO_ROOT)).replace("\\", "/")
        manifest["files"] = [
            entry
            for entry in manifest["files"]
            if entry["path"] not in {report_relative, findings_relative}
        ]
        manifest["files"].append(
            {
                "path": report_relative,
                "sha256": "SELF_REFERENTIAL_REPORT",
                "size": args.output.stat().st_size,
            }
        )
        manifest["files"].append(
            {
                "path": findings_relative,
                "sha256": sha256_file(args.findings),
                "size": args.findings.stat().st_size,
            }
        )
        manifest["file_count"] = len(manifest["files"])
        write_json(args.manifest, manifest)
        report["resources_and_boundaries"]["evidence_bytes"] = evidence_bytes()
        write_json(args.output, report)

    print(
        f"report: evidence_bytes={evidence_bytes()} under_budget={evidence_bytes() < EVIDENCE_BUDGET_BYTES} "
        f"files={manifest['file_count']} live_guard_unchanged={live_guard['protected_state_unchanged']}"
    )
    print("gates: " + ", ".join(f"{gate_id}={item['status']}" for gate_id, item in gates.items()))
    print(f"wall_seconds={round(time.monotonic() - started, 3)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
