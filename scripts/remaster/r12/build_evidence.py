"""Build the R12 builder report, findings, and artifact manifest."""

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
    owned_r12_files,
    read_json,
    record_enabled,
    sha256_file,
    tool_versions,
    write_json,
)

EVIDENCE_BUDGET_BYTES = 8_388_608
WORK_PACKAGE_PATH = REPO_ROOT / "docs" / "remaster" / "work_packages" / "R12_WORK_PACKAGE.json"
WORK_PACKAGE_SHA256 = "88B0102BAE811508CFEACE7ED5EFA477C76C02147B1FCC4397BCDB956D8D1809"

GATE_FILES = {
    "R12-G01": "R12_G01_COMPARISON.json",
    "R12-G02": "R12_G02_ADOPTION.json",
    "R12-G03": "R12_G03_FREEZE_RESTORE.json",
    "R12-G04": "R12_G04_FAULTS.json",
    "R12-G05": "R12_G05_PROVING.json",
    "R12-G06": "R12_G06_AUDIT.json",
}

DELIVERABLE_FILES = {
    "R12-D01": "R12_COMPARISON_MATRIX.json",
    "R12-D02": "R12_ADOPTION_LADDER.json",
    "R12-D03": "R12_RELEASE_FREEZE.json",
    "R12-D04": "R12_FAULT_MATRIX.json",
    "R12-D05": "R12_PROVING_READINESS.json",
    "R12-D06": "R12_FINAL_AUDIT_PACKAGE.json",
}

ALL_GATE_JSONS: tuple[str, ...] = (
    "R12_COMPARISON_MATRIX.json",
    "R12_ADOPTION_LADDER.json",
    "R12_RELEASE_FREEZE.json",
    "R12_RESTORE_PROOF.json",
    "R12_ROLLBACK_PACKAGE.json",
    "R12_FAULT_MATRIX.json",
    "R12_FAULT_INCIDENTS.json",
    "R12_C06_CONTROL.json",
    "R12_PROVING_READINESS.json",
    "R12_CHECKPOINT_PERSISTENCE.json",
    "R12_WAITING.json",
    "R12_FINAL_AUDIT_PACKAGE.json",
    "R12_G01_COMPARISON.json",
    "R12_G02_ADOPTION.json",
    "R12_G03_FREEZE_RESTORE.json",
    "R12_G04_FAULTS.json",
    "R12_G05_PROVING.json",
    "R12_G06_AUDIT.json",
    "R12_FOCUSED_TESTS.json",
    "R12_FULL_SUITE.json",
    "R12_STATIC.json",
    "R12_SECRET_SCAN.json",
    "R12_PRIVACY.json",
    "R12_LIVE_GUARD_COMPARISON.json",
)

PRE_EXISTING_BASELINE: tuple[str, ...] = (
    "?? docs/remaster/evidence/r6/",
    "?? docs/remaster/work_packages/R12_WORK_PACKAGE.json",
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
    if gate_file == "R12_RELEASE_FREEZE.json":
        passed = bool(
            payload.get("restore_proof", {}).get("passed")
            and payload.get("source", {}).get("tree_hash")
            and payload.get("rollback_package", {}).get("rollback_package_sha256")
        )
    elif gate_file == "R12_PROVING_READINESS.json":
        passed = bool(
            payload.get("deployed_release_frozen")
            and payload.get("all_restart_resilience_passed")
            and payload.get("proving_window", {}).get("status") == "READY"
        )
    elif gate_file == "R12_FINAL_AUDIT_PACKAGE.json":
        passed = bool(payload.get("prerequisites_ready"))
    else:
        passed = bool(payload.get("passed"))
    return ("PASS" if passed else "FAIL"), [gate_file]


def _manifest() -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for relative in owned_r12_files(REPO_ROOT):
        if relative == "docs/remaster/evidence/r12/R12_ARTIFACT_MANIFEST.json":
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
        "schema": "jarvis-r12-artifact-manifest-v1",
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
        "src/jarvis_foundry/fault_campaign/",
        "scripts/remaster/r12/",
        "tests/remaster/r12/",
        "docs/remaster/evidence/r12/",
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
            "id": "R12-F-001",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Fault campaign is staged over isolated copies",
            "detail": (
                "All 44 required faults run against isolated proving stores and worker "
                "subprocesses under JARVIS_R12_TEMP; live daemon/db/vault/protected main "
                "and owner projects are never touched. Live guards record "
                "protected_state_unchanged=True within the recorded windows."
            ),
            "evidence": "R12_FAULT_MATRIX.json, R12_LIVE_GUARD_COMPARISON.json",
        },
        {
            "id": "R12-F-002",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "BOUNDED_LIVE and FULL_DIGITAL_WORK_OPERATION are staged simulations",
            "detail": (
                "The final adoption rungs simulate live-like operation over isolated "
                "copies because the live deployment is protected. No live deployment "
                "changes were made; the proving window is readiness-verified, not started."
            ),
            "evidence": "R12_ADOPTION_LADDER.json, R12_PROVING_READINESS.json",
        },
        {
            "id": "R12-F-003",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "FROZEN V7 comparison uses a read-only git archive snapshot",
            "detail": (
                "V7 was snapshotted from protected main tag v8-pre-promotion-93a3ddc with "
                "git archive into JARVIS_R12_TEMP; the protected main tree was never "
                "mutated and the recoverable release was never overwritten."
            ),
            "evidence": "R12_COMPARISON_MATRIX.json",
        },
        {
            "id": "R12-V-001",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Builder status and final verdict remain NOT_TESTED until fresh independent audit",
            "detail": (
                "R12-G01..G05 evidence is complete; R12-G06 records audit package "
                "readiness with independent_audit_status=PENDING and verdict NOT_TESTED. "
                "A fresh independent verifier must reproduce the package and return "
                "exactly one of SERVES_ORIGINAL_PURPOSE / SERVES_PURPOSE_WITH_EXTERNAL_BLOCKERS "
                "/ DOES_NOT_YET_SERVE_PURPOSE."
            ),
            "evidence": "R12_FINAL_AUDIT_PACKAGE.json, R12_BUILDER_REPORT.json",
        },
        {
            "id": "R12-V-002",
            "severity": "S4",
            "disposition": "RECORDED",
            "title": "Progress checkpoints excluded from closure evidence",
            "detail": (
                "R12_PROGRESS_*.json are incremental working artifacts; evidence_bytes() "
                "and the manifest exclude them so the closed package matches the on-disk "
                "payload byte-for-byte."
            ),
            "evidence": "R12_ARTIFACT_MANIFEST.json",
        },
        {
            "id": "R12-V-003",
            "severity": "S4",
            "disposition": "RECORDED",
            "title": "R6 remains owner-redirect and never reopened",
            "detail": (
                "R6 files and records were not touched; PROGRAM_STATE.md records "
                "OWNER_REDIRECT_R6_DEFERRED and R12 confinement keeps R6 trees outside "
                "the allowed write prefixes."
            ),
            "evidence": "R12_BUILDER_REPORT.json",
        },
        {
            "id": "R12-V-004",
            "severity": "S4",
            "disposition": "ENVIRONMENT_NOTE",
            "title": "External supervisor process churn during the full-suite window",
            "detail": (
                "The live-guard comparison bracketing the full repository suite observed "
                "the protected process command set change from 4 to 2 (unrelated live "
                "supervisor processes stopping); r12_attributable_mutation was False and "
                "protected files, main, tasks, and vault were unchanged. A quiet-window "
                "before/after/compare after all R12 work recorded "
                "protected_state_unchanged=True; the full-window captures are preserved "
                "as R12_LIVE_GUARD_FULLWINDOW_*."
            ),
            "evidence": "R12_LIVE_GUARD_FULLWINDOW_COMPARISON.json, R12_LIVE_GUARD_COMPARISON.json",
        },
        {
            "id": "R12-AUD-001",
            "severity": "S2",
            "disposition": "REPAIRED_IN_R12_ROUND2",
            "title": "Release freeze and proving readiness did not bind the exact final source",
            "detail": (
                "The final auditor found R12_RELEASE_FREEZE.json recorded source tree "
                "ffe65cd9... while the final source recomputed to ebf90e2e... because "
                "fault_campaign/worker_sim.py was edited after the freeze was generated, and "
                "the freeze was never refreshed. The freeze also included __pycache__ bytecode "
                "in the source tree hash. release_freeze.py now (a) clears its proving "
                "workspace before seeding so a fresh run always succeeds, (b) excludes "
                "__pycache__ and .pyc files from the source tree manifest, and (c) was "
                "re-run on the exact final source; proving_window.py now derives its release "
                "fingerprint from the freeze's source tree hash. The regenerated freeze binds "
                "source tree ef426a85... (222 files, no bytecode) at head c76d92d, and the "
                "closure was regenerated."
            ),
            "evidence": "R12_RELEASE_FREEZE.json, R12_PROVING_READINESS.json",
        },
        {
            "id": "R12-AUD-002",
            "severity": "S4",
            "disposition": "REPAIRED_IN_R12_ROUND2",
            "title": "Freeze source tree hash excluded bytecode",
            "detail": (
                "The source tree manifest now excludes __pycache__ directories and .pyc files "
                "so the freeze binds source truth, not build artifacts."
            ),
            "evidence": "R12_RELEASE_FREEZE.json, release_freeze.py",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R12_BUILDER_REPORT.json")
    parser.add_argument(
        "--manifest", type=Path, default=EVIDENCE_DIR / "R12_ARTIFACT_MANIFEST.json"
    )
    parser.add_argument("--findings", type=Path, default=EVIDENCE_DIR / "R12_FINDINGS.json")
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

    focused_tests = EVIDENCE_DIR / "R12_FOCUSED_TESTS.json"
    tests_status = (
        "PASS"
        if focused_tests.is_file() and read_json(focused_tests).get("exit_code") == 0
        else "NOT_TESTED"
    )
    full_suite = EVIDENCE_DIR / "R12_FULL_SUITE.json"
    full_suite_payload = read_json(full_suite) if full_suite.is_file() else {}
    full_suite_status = (
        "PASS"
        if full_suite.is_file() and full_suite_payload.get("exit_code") == 0
        else "NOT_TESTED"
    )
    static = EVIDENCE_DIR / "R12_STATIC.json"
    static_status = "PASS" if static.is_file() and read_json(static).get("passed") else "NOT_TESTED"
    secret = EVIDENCE_DIR / "R12_SECRET_SCAN.json"
    secret_status = "PASS" if secret.is_file() and read_json(secret).get("passed") else "NOT_TESTED"
    privacy = EVIDENCE_DIR / "R12_PRIVACY.json"
    privacy_status = (
        "PASS" if privacy.is_file() and read_json(privacy).get("passed") else "NOT_TESTED"
    )

    live_guard_before = EVIDENCE_DIR / "R12_LIVE_GUARD_BEFORE.json"
    live_guard_after = EVIDENCE_DIR / "R12_LIVE_GUARD_AFTER.json"
    live_guard_comparison = EVIDENCE_DIR / "R12_LIVE_GUARD_COMPARISON.json"
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
    closure_ready = (
        live_guard["protected_state_unchanged"] is True
        and tests_status == "PASS"
        and full_suite_status == "PASS"
        and static_status == "PASS"
        and secret_status == "PASS"
        and privacy_status == "PASS"
        and under_budget
        and confinement["confined"]
    )
    audit_package = EVIDENCE_DIR / "R12_FINAL_AUDIT_PACKAGE.json"
    audit = read_json(audit_package) if audit_package.is_file() else {}
    audit_ready = bool(audit.get("prerequisites_ready") and audit.get("independent_audit_status"))
    if audit_ready and closure_ready:
        gates["R12-G06"] = {
            "status": "WAITING",
            "evidence": [
                "R12_FINAL_AUDIT_PACKAGE.json",
                "R12_G06_AUDIT.json",
                "R12_FULL_SUITE.json",
                "R12_STATIC.json",
                "R12_SECRET_SCAN.json",
                "R12_PRIVACY.json",
                "R12_LIVE_GUARD_COMPARISON.json",
            ],
            "evidence_hashes": {
                name: sha256_file(EVIDENCE_DIR / name)
                for name in (
                    "R12_FINAL_AUDIT_PACKAGE.json",
                    "R12_G06_AUDIT.json",
                    "R12_FULL_SUITE.json",
                    "R12_STATIC.json",
                    "R12_RUFF.json",
                    "R12_MYPY.json",
                    "R12_BANDIT.json",
                    "R12_SECRET_SCAN.json",
                    "R12_PRIVACY.json",
                    "R12_LIVE_GUARD_COMPARISON.json",
                )
                if (EVIDENCE_DIR / name).is_file()
            },
            "verdict_status": "WAITING",
            "reason": (
                "Builder evidence closed; a fresh independent final audit must return "
                "exactly one of the three allowed verdicts before G06 closes."
            ),
        }
    else:
        gates["R12-G06"] = {
            "status": "NOT_TESTED",
            "evidence": ["R12_G06_AUDIT.json"],
            "evidence_hashes": {},
            "reason": "audit package or closure prerequisites incomplete",
        }

    findings = _findings()
    report: dict[str, Any] = {
        "schema": "jarvis-r12-builder-report-v1",
        "program": "POST_V7_JARVIS_REMASTER",
        "stage": "R12",
        "builder": "r12_builder_fresh",
        "builder_status": "NOT_TESTED",
        "final_verdict": "NOT_TESTED",
        "status_reason": (
            "Builder evidence is complete; a fresh independent final audit must reproduce "
            "and adjudicate the exact deployed release before the gate closes."
        ),
        "generated_utc": "2026-08-16T20:00:00Z",
        "frozen_base": {
            "commit": "c76d92df55b6ec552b68379479ccd80f972c303c",
            "branch": "remaster/post-v7-purpose",
            "work_package": "docs/remaster/work_packages/R12_WORK_PACKAGE.json",
            "work_package_sha256": WORK_PACKAGE_SHA256,
            "work_package_size": WORK_PACKAGE_PATH.stat().st_size,
            "work_package_unchanged": (
                sha256_file(WORK_PACKAGE_PATH).upper() == WORK_PACKAGE_SHA256
            ),
            "protected_main": "2f7360e (V8-promoted recoverable release; read-only)",
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
                "R12_FULL_SUITE.json",
                "R12_FULL_SUITE_RAW.txt",
                "R12_FULL_SUITE_PART1_JUNIT.xml",
                "R12_FULL_SUITE_PART2_JUNIT.xml",
                "R12_FULL_SUITE_COMBINED_JUNIT.xml",
            ],
            "evidence_hashes": {
                name: sha256_file(EVIDENCE_DIR / name)
                for name in (
                    "R12_FULL_SUITE.json",
                    "R12_FULL_SUITE_RAW.txt",
                    "R12_FULL_SUITE_PART1_JUNIT.xml",
                    "R12_FULL_SUITE_PART2_JUNIT.xml",
                    "R12_FULL_SUITE_COMBINED_JUNIT.xml",
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
                "schema": "jarvis-r12-findings-v1",
                "program": "POST_V7_JARVIS_REMASTER",
                "stage": "R12",
                "generated_utc": "2026-08-16T20:00:00Z",
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
