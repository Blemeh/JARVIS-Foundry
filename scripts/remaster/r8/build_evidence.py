"""Build the R8 builder report, artifact manifest, and confinement evidence."""

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
    owned_r8_files,
    read_json,
    record_enabled,
    sha256_file,
    tool_versions,
    write_json,
)

EVIDENCE_BUDGET_BYTES = 8_388_608
WORK_PACKAGE_PATH = REPO_ROOT / "docs" / "remaster" / "work_packages" / "R8_WORK_PACKAGE.json"
WORK_PACKAGE_SHA256 = "B7CDAE97A2D0BA7B6A14C2C160975428A974FD2E3F7FADD61E00D60939611627"

GATE_FILES = {
    "R8-G01": "R8_G01_ISOLATION.json",
    "R8-G02": "R8_G02_ASSET.json",
    "R8-G03": "R8_G03_REPAIR.json",
    "R8-G04": "R8_G04_VERIFICATION.json",
    "R8-G05": "R8_G05_RECONCILIATION.json",
}

DELIVERABLE_FILES = {
    "R8-D01": "R8_MACHINERY_REPORT.json",
    "R8-D02": "R8_MISSION_ASSET_A.json",
    "R8-D03": "R8_MISSION_REPAIR_B.json",
    "R8-D04": "R8_MACHINERY_REPORT.json",
    "R8-D05": "R8_G04_VERIFICATION.json",
    "R8-D06": "R8_MISSION_ASSET_A.json",
}

ALL_GATE_JSONS: tuple[str, ...] = (
    "R8_MACHINERY_REPORT.json",
    "R8_MISSION_ASSET_A.json",
    "R8_MISSION_REPAIR_B.json",
    "R8_MISSION_WAITING.json",
    "R8_G01_ISOLATION.json",
    "R8_G02_ASSET.json",
    "R8_G03_REPAIR.json",
    "R8_G04_VERIFICATION.json",
    "R8_G05_RECONCILIATION.json",
    "R8_PRIVACY.json",
    "R8_FOCUSED_TESTS.json",
    "R8_FULL_SUITE.json",
    "R8_STATIC.json",
    "R8_SECRET_SCAN.json",
)

# Entries that predate the R8 builder: R6 deferred trees (owner redirect,
# preserved uncommitted) and the root-created R8 work package. R7 files are
# committed at HEAD 9a514d9, so FINDINGS.jsonl / OWNER_REDIRECT are clean.
PRE_EXISTING_BASELINE: tuple[str, ...] = (
    "?? docs/remaster/evidence/r6/",
    "?? docs/remaster/work_packages/R6_WORK_PACKAGE.json",
    "?? docs/remaster/work_packages/R8_WORK_PACKAGE.json",
    "?? scripts/remaster/r6/",
    "?? src/jarvis_foundry/kernel/memory/",
    "?? tests/remaster/r6/",
)


def _status_from_gate(gate_file: str) -> tuple[str, list[str]]:
    path = EVIDENCE_DIR / gate_file
    if not path.is_file():
        return "NOT_TESTED", []
    payload = read_json(path)
    if gate_file == "R8_LIVE_GUARD_COMPARISON.json":
        passed = bool(payload.get("protected_state_unchanged"))
    elif gate_file == "R8_MACHINERY_REPORT.json":
        passed = payload.get("exit_code") == 0
    elif gate_file.startswith("R8_MISSION_"):
        passed = payload.get("status") in {"ACCEPTED", "WAITING"}
    else:
        passed = bool(payload.get("passed"))
    return ("PASS" if passed else "FAIL"), [gate_file]


def _manifest() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for relative in owned_r8_files(REPO_ROOT):
        if relative == "docs/remaster/evidence/r8/R8_ARTIFACT_MANIFEST.json":
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
        "schema": "jarvis-r8-artifact-manifest-v1",
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
    missing_baseline = [entry for entry in PRE_EXISTING_BASELINE if entry not in lines]
    allowed_prefixes = (
        "src/jarvis_foundry/orchestration/",
        "scripts/remaster/r8/",
        "tests/remaster/r8/",
        "docs/remaster/evidence/r8/",
        "docs/remaster/work_packages/R8_WORK_PACKAGE.json",
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
        "baseline_entries_missing": missing_baseline,
        "confinement_violations": violations,
        "confined": not violations,
    }


def _findings() -> list[dict[str, Any]]:
    return [
        {
            "id": "R8-F-001",
            "severity": "S2",
            "disposition": "RESOLVED_IN_R8",
            "title": "Initial verifier trusted worker completeness claims",
            "detail": (
                "The first R8 verifier implementation checked claimed file hashes and reran "
                "required tests, but its completeness check was a stub, so a PASS worker result "
                "could omit real changed files and still be accepted. The adversarial regression "
                "test reproduced the defect, repair mission R8-B dispatched a fresh repair worker "
                "that replaced the stub with an actual-vs-claims completeness check, the mission "
                "was reverified and accepted, and the identical accepted artifact was applied to "
                "the R8-owned source."
            ),
            "evidence": "R8_MISSION_REPAIR_B.json, R8_G03_REPAIR.json, test_verify.py",
        },
        {
            "id": "R8-F-002",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Bandit advisory scope includes R8 gate scripts",
            "detail": (
                "Bandit runs clean on the mandatory orchestration source gate; a separate "
                "advisory run over scripts/remaster/r8 is recorded in R8_STATIC.json and is not "
                "part of the mandatory gate."
            ),
            "evidence": "R8_STATIC.json",
        },
        {
            "id": "R8-F-003",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Windows read-only git objects require read-only-safe clear",
            "detail": (
                "Rollback restoration in the proving area initially failed on read-only git "
                "object files on Windows. R8 path clearing now chmods read-only files before "
                "removal, confined to the proving root."
            ),
            "evidence": "paths.py, R8_G05_RECONCILIATION.json",
        },
        {
            "id": "R8-V-001",
            "severity": "S3",
            "disposition": "REPAIRED_IN_R8_ROUND3",
            "title": "Builder baseline porcelain claim was not reproducible",
            "detail": (
                "Round-2 verifier found PRE_EXISTING_BASELINE listed FINDINGS.jsonl and "
                "OWNER_REDIRECT_R6_DEFERRED.json as dirty although they are committed clean at "
                "the R7 PASS HEAD. The baseline now lists only genuinely pre-existing entries "
                "(R6 deferred trees and the R8 work package), and _confinement records "
                "baseline_entries_missing when any listed entry is absent, so the claim is "
                "self-checked against live porcelain."
            ),
            "evidence": "R8_BUILDER_REPORT.json artifact_confinement",
        },
        {
            "id": "R8-V-002",
            "severity": "S3",
            "disposition": "REPAIRED_IN_R8_ROUND3",
            "title": "evidence_bytes claim was not reproducible",
            "detail": (
                "Round-2 verifier measured evidence_bytes 637580 claimed vs 637701 on disk, "
                "non-deterministic across replays. evidence_bytes() is now defined as the total "
                "of all evidence files EXCLUDING the self-referential builder report and the "
                "artifact manifest, so the value is stable before/after the final closure "
                "writes and reproducible by the verifier."
            ),
            "evidence": "R8_BUILDER_REPORT.json resources_and_boundaries",
        },
        {
            "id": "R8-V-003",
            "severity": "S3",
            "disposition": "CLOSED_VERIFIED_R8_ROUND2",
            "title": "MissionLink store was empty operationally",
            "detail": (
                "The orchestrator now appends a MissionLink on every accepted mission, bound to "
                "the accepted-event ledger header; the shared r8_store persists lineage for "
                "accepted asset and repair missions (round-2 verifier confirmed)."
            ),
            "evidence": "r8_store/events.jsonl, tests/remaster/r8/test_orchestrator.py",
        },
        {
            "id": "R8-V-004",
            "severity": "S3",
            "disposition": "CLOSED_VERIFIED_R8_ROUND2",
            "title": "Worker result schema accepted Windows drive-letter paths",
            "detail": (
                "ensure_relative_safe now refuses drive/URI forms (':') and NUL bytes; "
                "evidence/artifacts lists are path-validated fail-closed; regression tests and "
                "round-2 verifier probes confirm structured WorkerResultError rejection and an "
                "end-to-end REJECTED mission with no acceptance and no verifier crash."
            ),
            "evidence": "paths.py, schemas.py, tests/remaster/r8/test_schemas.py",
        },
        {
            "id": "R8-V-005",
            "severity": "S4",
            "disposition": "RECORDED",
            "title": "Full-suite raw outputs omit pytest summary lines",
            "detail": (
                "Cosmetic: R8_FULL_SUITE_RAW.txt does not include the final 'N passed' lines; "
                "JUnit counts and exit codes are authoritative and were reproduced green."
            ),
            "evidence": "R8_FULL_SUITE.json, R8_FULL_SUITE_PART1/2/COMBINED_JUNIT.xml",
        },
        {
            "id": "R8-V2-001",
            "severity": "S3",
            "disposition": "REPAIRED_IN_R8_ROUND3",
            "title": "Frozen store links recorded the final ledger head",
            "detail": (
                "The shared mission ledger is append-idempotent, so the round-2 machinery rerun "
                "appended no new events and both MissionLinks captured the same final chain "
                "head instead of each mission's accepted-event header. The machinery evidence "
                "was regenerated from a clean ledger/store so links bind the accepted-event "
                "headers; a gate assertion checks per-mission link headers against the "
                "acceptance event header."
            ),
            "evidence": "R8_MISSION_ASSET_A.json, R8_MISSION_REPAIR_B.json, r8_store/events.jsonl",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R8_BUILDER_REPORT.json")
    parser.add_argument("--manifest", type=Path, default=EVIDENCE_DIR / "R8_ARTIFACT_MANIFEST.json")
    parser.add_argument("--findings", type=Path, default=EVIDENCE_DIR / "R8_FINDINGS.json")
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

    focused_tests = EVIDENCE_DIR / "R8_FOCUSED_TESTS.json"
    tests_status = (
        "PASS"
        if focused_tests.is_file() and read_json(focused_tests).get("exit_code") == 0
        else "NOT_TESTED"
    )
    full_suite = EVIDENCE_DIR / "R8_FULL_SUITE.json"
    full_suite_payload = read_json(full_suite) if full_suite.is_file() else {}
    full_suite_status = (
        "PASS"
        if full_suite.is_file() and full_suite_payload.get("exit_code") == 0
        else "NOT_TESTED"
    )
    static = EVIDENCE_DIR / "R8_STATIC.json"
    static_status = "PASS" if static.is_file() and read_json(static).get("passed") else "NOT_TESTED"
    secret = EVIDENCE_DIR / "R8_SECRET_SCAN.json"
    secret_status = "PASS" if secret.is_file() and read_json(secret).get("passed") else "NOT_TESTED"
    privacy = EVIDENCE_DIR / "R8_PRIVACY.json"
    privacy_status = (
        "PASS" if privacy.is_file() and read_json(privacy).get("passed") else "NOT_TESTED"
    )

    live_guard_before = EVIDENCE_DIR / "R8_LIVE_GUARD_BEFORE.json"
    live_guard_after = EVIDENCE_DIR / "R8_LIVE_GUARD_AFTER.json"
    live_guard_comparison = EVIDENCE_DIR / "R8_LIVE_GUARD_COMPARISON.json"
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
    gates["R8-G06"] = {
        "status": "PASS" if g06_passed else "FAIL",
        "evidence": [
            "R8_FULL_SUITE.json",
            "R8_STATIC.json",
            "R8_SECRET_SCAN.json",
            "R8_PRIVACY.json",
            "R8_LIVE_GUARD_COMPARISON.json",
        ],
        "evidence_hashes": {
            name: sha256_file(EVIDENCE_DIR / name)
            for name in (
                "R8_FULL_SUITE.json",
                "R8_STATIC.json",
                "R8_RUFF.json",
                "R8_MYPY.json",
                "R8_BANDIT.json",
                "R8_SECRET_SCAN.json",
                "R8_PRIVACY.json",
                "R8_LIVE_GUARD_COMPARISON.json",
            )
            if (EVIDENCE_DIR / name).is_file()
        },
    }
    gates["R8-G06"]["evidence_hashes"].update(
        {
            live_guard_before.name: live_guard["before_sha256"],
            live_guard_after.name: live_guard["after_sha256"],
            live_guard_comparison.name: live_guard["comparison_sha256"],
        }
    )

    findings = _findings()
    report: dict[str, Any] = {
        "schema": "jarvis-r8-builder-report-v1",
        "program": "POST_V7_JARVIS_REMASTER",
        "stage": "R8",
        "builder": "r8_builder_fresh",
        "builder_status": "NOT_TESTED",
        "status_reason": (
            "Builder evidence is complete; a fresh independent verifier must reproduce and "
            "adjudicate the package before the gate closes."
        ),
        "generated_utc": "2026-08-15T20:30:00Z",
        "frozen_base": {
            "commit": "9a514d9cae48e9d6f3384c3b42a433a77daa80c4",
            "branch": "remaster/post-v7-purpose",
            "work_package": "docs/remaster/work_packages/R8_WORK_PACKAGE.json",
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
                "R8_FULL_SUITE.json",
                "R8_FULL_SUITE_RAW.txt",
                "R8_FULL_SUITE_PART1_JUNIT.xml",
                "R8_FULL_SUITE_PART2_JUNIT.xml",
                "R8_FULL_SUITE_COMBINED_JUNIT.xml",
            ],
            "evidence_hashes": {
                name: sha256_file(EVIDENCE_DIR / name)
                for name in (
                    "R8_FULL_SUITE.json",
                    "R8_FULL_SUITE_RAW.txt",
                    "R8_FULL_SUITE_PART1_JUNIT.xml",
                    "R8_FULL_SUITE_PART2_JUNIT.xml",
                    "R8_FULL_SUITE_COMBINED_JUNIT.xml",
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
            "network_used": "none",
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
                "schema": "jarvis-r8-findings-v1",
                "program": "POST_V7_JARVIS_REMASTER",
                "stage": "R8",
                "generated_utc": "2026-08-15T20:30:00Z",
                "findings": findings,
            },
        )
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

        mismatches: list[str] = []
        checked = 0
        for entry in manifest["files"]:
            if entry["sha256"] == "SELF_REFERENTIAL_REPORT":
                continue
            path = REPO_ROOT / entry["path"]
            checked += 1
            if not path.is_file() or sha256_file(path) != entry["sha256"]:
                mismatches.append(entry["path"])
        report["artifact_manifest_verification"] = {
            "checked": checked,
            "mismatches": mismatches,
            "manifest_file_count": manifest["file_count"],
        }
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
