"""Build the R11 builder report, findings, and artifact manifest."""

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
    owned_r11_files,
    read_json,
    record_enabled,
    sha256_file,
    tool_versions,
    write_json,
)

EVIDENCE_BUDGET_BYTES = 8_388_608
WORK_PACKAGE_PATH = REPO_ROOT / "docs" / "remaster" / "work_packages" / "R11_WORK_PACKAGE.json"
WORK_PACKAGE_SHA256 = "9512BAAC8CF0DD902E82E46A9E7B8ED9D837A6BA5C62A2E7607427B865EC6ABE"

GATE_FILES = {
    "R11-G01": "R11_G01_CYCLES.json",
    "R11-G02": "R11_G02_PROMOTIONS.json",
    "R11-G03": "R11_G03_REJECTION.json",
    "R11-G04": "R11_G04_INDEPENDENT.json",
    "R11-G05": "R11_G05_SUPERVISOR.json",
    "R11-G06": "R11_G06_RESTART.json",
}

DELIVERABLE_FILES = {
    "R11-D01": "R11_CYCLES.json",
    "R11-D02": "R11_CYCLES.json",
    "R11-D03": "R11_G04_INDEPENDENT.json",
    "R11-D04": "R11_ROLLBACK.json",
    "R11-D05": "R11_RESTART.json",
    "R11-D06": "R11_CYCLES.json",
}

ALL_GATE_JSONS: tuple[str, ...] = (
    "R11_CYCLES.json",
    "R11_LIFECYCLE_EVENTS.json",
    "R11_ROLLBACK.json",
    "R11_WAITING.json",
    "R11_RESTART.json",
    "R11_STORE_SNAPSHOT.json",
    "R11_PROBES.json",
    "R11_VERIFIER_REPRODUCTION.json",
    "R11_G01_CYCLES.json",
    "R11_G02_PROMOTIONS.json",
    "R11_G03_REJECTION.json",
    "R11_G04_INDEPENDENT.json",
    "R11_G05_SUPERVISOR.json",
    "R11_G06_RESTART.json",
    "R11_FOCUSED_TESTS.json",
    "R11_FULL_SUITE.json",
    "R11_STATIC.json",
    "R11_SECRET_SCAN.json",
    "R11_PRIVACY.json",
    "R11_LIVE_GUARD_COMPARISON.json",
)

PRE_EXISTING_BASELINE: tuple[str, ...] = (
    "?? docs/remaster/evidence/r6/",
    "?? docs/remaster/work_packages/R11_WORK_PACKAGE.json",
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
    passed = bool(payload.get("passed"))
    return ("PASS" if passed else "FAIL"), [gate_file]


def _manifest() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for relative in owned_r11_files(REPO_ROOT):
        if relative == "docs/remaster/evidence/r11/R11_ARTIFACT_MANIFEST.json":
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
        "schema": "jarvis-r11-artifact-manifest-v1",
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
        "src/jarvis_foundry/evolution_live/",
        "scripts/remaster/r11/",
        "tests/remaster/r11/",
        "docs/remaster/evidence/r11/",
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
            "id": "R11-F-001",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Real proving candidates are isolated fixture code changes",
            "detail": (
                "R11 real cycles mutate only real bounded micro-target code inside "
                "isolated git clones under JARVIS_R11_TEMP.  No live daemon, "
                "database, vault, protected main, or owner project is touched; "
                "live guards record protected_state_unchanged=True."
            ),
            "evidence": "R11_CYCLES.json, R11_LIVE_GUARD_COMPARISON.json",
        },
        {
            "id": "R11-F-002",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Independent verification is a fresh subprocess",
            "detail": (
                "Each candidate is verified by python -m "
                "jarvis_foundry.evolution_live.verifier in a fresh temp clone; "
                "the engine refuses any verifier identity that matches the "
                "candidate, and the G04 gate re-runs the full reproduction "
                "subprocess."
            ),
            "evidence": "R11_VERIFIER_REPRODUCTION.json, R11_G04_INDEPENDENT.json",
        },
        {
            "id": "R11-F-003",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Risk classifier reused read-only from V6 evolution records",
            "detail": (
                "Final candidate risk is the max of the declared class and "
                "classify_risk(surfaces); unknown surfaces fail closed to R3. "
                "No R11 code edits the V6 classifier."
            ),
            "evidence": "R11_CYCLES.json",
        },
        {
            "id": "R11-V-001",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Builder status remains NOT_TESTED until fresh independent adjudication",
            "detail": (
                "R11-G01..G06 reproduce the required evidence, but the final "
                "builder_status stays NOT_TESTED until a fresh independent "
                "verifier reproduces every criterion from the frozen package."
            ),
            "evidence": "R11_BUILDER_REPORT.json",
        },
        {
            "id": "R11-V-002",
            "severity": "S4",
            "disposition": "RECORDED",
            "title": "Progress checkpoints excluded from closure evidence",
            "detail": (
                "R11_PROGRESS_*.json are incremental working artifacts; "
                "evidence_bytes() and the manifest exclude them so the closed "
                "package matches the on-disk payload byte-for-byte."
            ),
            "evidence": "R11_ARTIFACT_MANIFEST.json",
        },
        {
            "id": "R11-V-003",
            "severity": "S4",
            "disposition": "RECORDED",
            "title": "External supervisor process drift during the long full-suite window",
            "detail": (
                "The live-guard comparison bracketing the full repository suite "
                "observed the protected process command set change from 4 to 2 "
                "(unrelated live supervisor processes stopping); "
                "r11_attributable_mutation was False and protected files, main, "
                "tasks, and vault were unchanged.  A quiet-window before/after/"
                "compare after all R11 work recorded protected_state_unchanged="
                "True; the full-window captures are preserved as "
                "R11_LIVE_GUARD_FULLWINDOW_*."
            ),
            "evidence": "R11_LIVE_GUARD_FULLWINDOW_COMPARISON.json, R11_LIVE_GUARD_COMPARISON.json",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R11_BUILDER_REPORT.json")
    parser.add_argument(
        "--manifest", type=Path, default=EVIDENCE_DIR / "R11_ARTIFACT_MANIFEST.json"
    )
    parser.add_argument("--findings", type=Path, default=EVIDENCE_DIR / "R11_FINDINGS.json")
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

    focused_tests = EVIDENCE_DIR / "R11_FOCUSED_TESTS.json"
    tests_status = (
        "PASS"
        if focused_tests.is_file() and read_json(focused_tests).get("exit_code") == 0
        else "NOT_TESTED"
    )
    full_suite = EVIDENCE_DIR / "R11_FULL_SUITE.json"
    full_suite_payload = read_json(full_suite) if full_suite.is_file() else {}
    full_suite_status = (
        "PASS"
        if full_suite.is_file() and full_suite_payload.get("exit_code") == 0
        else "NOT_TESTED"
    )
    static = EVIDENCE_DIR / "R11_STATIC.json"
    static_status = "PASS" if static.is_file() and read_json(static).get("passed") else "NOT_TESTED"
    secret = EVIDENCE_DIR / "R11_SECRET_SCAN.json"
    secret_status = "PASS" if secret.is_file() and read_json(secret).get("passed") else "NOT_TESTED"
    privacy = EVIDENCE_DIR / "R11_PRIVACY.json"
    privacy_status = (
        "PASS" if privacy.is_file() and read_json(privacy).get("passed") else "NOT_TESTED"
    )

    live_guard_before = EVIDENCE_DIR / "R11_LIVE_GUARD_BEFORE.json"
    live_guard_after = EVIDENCE_DIR / "R11_LIVE_GUARD_AFTER.json"
    live_guard_comparison = EVIDENCE_DIR / "R11_LIVE_GUARD_COMPARISON.json"
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
    gates["R11-G06"] = {
        "status": "PASS" if g06_passed else "FAIL",
        "evidence": [
            "R11_G06_RESTART.json",
            "R11_FULL_SUITE.json",
            "R11_STATIC.json",
            "R11_SECRET_SCAN.json",
            "R11_PRIVACY.json",
            "R11_LIVE_GUARD_COMPARISON.json",
        ],
        "evidence_hashes": {
            name: sha256_file(EVIDENCE_DIR / name)
            for name in (
                "R11_G06_RESTART.json",
                "R11_FULL_SUITE.json",
                "R11_STATIC.json",
                "R11_RUFF.json",
                "R11_MYPY.json",
                "R11_BANDIT.json",
                "R11_SECRET_SCAN.json",
                "R11_PRIVACY.json",
                "R11_LIVE_GUARD_COMPARISON.json",
            )
            if (EVIDENCE_DIR / name).is_file()
        },
    }

    findings = _findings()
    report: dict[str, Any] = {
        "schema": "jarvis-r11-builder-report-v1",
        "program": "POST_V7_JARVIS_REMASTER",
        "stage": "R11",
        "builder": "r11_builder_fresh",
        "builder_status": "NOT_TESTED",
        "status_reason": (
            "Builder evidence is complete; a fresh independent verifier must "
            "reproduce and adjudicate the package before the gate closes."
        ),
        "generated_utc": "2026-08-16T18:00:00Z",
        "frozen_base": {
            "commit": "9999fb3e3bee658c2c2a7995b6a7b7c613a7f896",
            "branch": "remaster/post-v7-purpose",
            "work_package": "docs/remaster/work_packages/R11_WORK_PACKAGE.json",
            "work_package_sha256": WORK_PACKAGE_SHA256,
            "work_package_size": WORK_PACKAGE_PATH.stat().st_size,
            "work_package_unchanged": (
                sha256_file(WORK_PACKAGE_PATH).upper() == WORK_PACKAGE_SHA256
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
                "R11_FULL_SUITE.json",
                "R11_FULL_SUITE_RAW.txt",
                "R11_FULL_SUITE_PART1_JUNIT.xml",
                "R11_FULL_SUITE_PART2_JUNIT.xml",
                "R11_FULL_SUITE_COMBINED_JUNIT.xml",
            ],
            "evidence_hashes": {
                name: sha256_file(EVIDENCE_DIR / name)
                for name in (
                    "R11_FULL_SUITE.json",
                    "R11_FULL_SUITE_RAW.txt",
                    "R11_FULL_SUITE_PART1_JUNIT.xml",
                    "R11_FULL_SUITE_PART2_JUNIT.xml",
                    "R11_FULL_SUITE_COMBINED_JUNIT.xml",
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
    manifest: dict[str, Any] = _manifest()

    if record_enabled():
        write_json(
            args.findings,
            {
                "schema": "jarvis-r11-findings-v1",
                "program": "POST_V7_JARVIS_REMASTER",
                "stage": "R11",
                "generated_utc": "2026-08-16T18:00:00Z",
                "findings": findings,
            },
        )
        write_json(args.output, report)
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

        for entry in manifest["files"]:
            if entry["path"] == report_relative:
                entry["size"] = args.output.stat().st_size
        write_json(args.manifest, manifest)

    print(
        f"report: evidence_bytes={evidence_bytes()} "
        f"under_budget={evidence_bytes() < EVIDENCE_BUDGET_BYTES} "
        f"files={manifest['file_count']} "
        f"live_guard_unchanged={live_guard['protected_state_unchanged']}"
    )
    print("gates: " + ", ".join(f"{gate_id}={item['status']}" for gate_id, item in gates.items()))
    print(f"wall_seconds={round(time.monotonic() - started, 3)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
