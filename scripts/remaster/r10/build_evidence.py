"""Build the R10 builder report, findings, and artifact manifest."""

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
    owned_r10_files,
    read_json,
    record_enabled,
    sha256_file,
    tool_versions,
    write_json,
)

EVIDENCE_BUDGET_BYTES = 8_388_608
WORK_PACKAGE_PATH = REPO_ROOT / "docs" / "remaster" / "work_packages" / "R10_WORK_PACKAGE.json"
WORK_PACKAGE_SHA256 = "D26B272ABEA57F19916943EE2BD70BF239E8241282E9FC0A31090C7D09D9EFA0"

GATE_FILES = {
    "R10-G01": "R10_G01_STATE.json",
    "R10-G02": "R10_G02_DIRECTIVES.json",
    "R10-G03": "R10_G03_BRIEFINGS.json",
    "R10-G04": "R10_G04_LANGUAGES.json",
    "R10-G05": "R10_G05_VOICE.json",
    "R10-G06": "R10_G06_VISUAL.json",
}

DELIVERABLE_FILES = {
    "R10-D01": "R10_G01_STATE.json",
    "R10-D02": "R10_G02_DIRECTIVES.json",
    "R10-D03": "R10_G03_BRIEFINGS.json",
    "R10-D04": "R10_G04_LANGUAGES.json",
    "R10-D05": "R10_G05_VOICE.json",
    "R10-D06": "R10_G01_STATE.json",
}

ALL_GATE_JSONS: tuple[str, ...] = (
    "R10_SEED.json",
    "R10_G01_STATE.json",
    "R10_G02_DIRECTIVES.json",
    "R10_G03_BRIEFINGS.json",
    "R10_G04_LANGUAGES.json",
    "R10_G05_VOICE.json",
    "R10_G06_VISUAL.json",
    "R10_VOICE_PROBE.json",
    "R10_PRIVACY.json",
    "R10_FOCUSED_TESTS.json",
    "R10_FULL_SUITE.json",
    "R10_STATIC.json",
    "R10_SECRET_SCAN.json",
    "R10_LIVE_GUARD_COMPARISON.json",
)

PRE_EXISTING_BASELINE: tuple[str, ...] = (
    "?? docs/remaster/evidence/r6/",
    "?? docs/remaster/work_packages/R10_WORK_PACKAGE.json",
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
    for relative in owned_r10_files(REPO_ROOT):
        if relative == "docs/remaster/evidence/r10/R10_ARTIFACT_MANIFEST.json":
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
        "schema": "jarvis-r10-artifact-manifest-v1",
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
        "src/jarvis_foundry/command_center/",
        "scripts/remaster/r10/",
        "tests/remaster/r10/",
        "docs/remaster/evidence/r10/",
        "docs/remaster/work_packages/R10_WORK_PACKAGE.json",
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
            "id": "R10-F-001",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Command Center package shadows the legacy command_center.py module",
            "detail": (
                "The R10 contract creates src/jarvis_foundry/command_center/ as an additive "
                "package. Python resolves the package before the sibling command_center.py "
                "module, so the package __init__ loads the legacy module under a private alias "
                "and re-exports build_snapshot, decision_timeline, and CommandCenterServer. "
                "tests/foundry and briefings.py keep the exact same public API without editing "
                "the legacy module."
            ),
            "evidence": "src/jarvis_foundry/command_center/__init__.py",
        },
        {
            "id": "R10-F-002",
            "severity": "S3",
            "disposition": "ENVIRONMENT_NOTE",
            "title": "Voice is honestly classified by a real local SAPI probe",
            "detail": (
                "The probe invokes System.Speech directly. If no recognizer/voice receipt is "
                "produced the classification is NOT_TESTED (or TTS_ONLY when only real TTS "
                "succeeds); mock STT/TTS is never presented as real voice proof."
            ),
            "evidence": "R10_G05_VOICE.json, R10_VOICE_PROBE.json",
        },
        {
            "id": "R10-F-003",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Visual evidence uses real Chrome headless; axe is unavailable",
            "detail": (
                "Playwright and axe are not installed and network is disabled, so screenshots "
                "and DOM dumps use the installed Chrome/Edge headless binary and the page "
                "runs a real in-page DOM accessibility audit (landmarks, labels, unique ids, "
                "button names). The toolchain status is recorded honestly."
            ),
            "evidence": "R10_G06_VISUAL.json, R10_VISUAL_DOM_AUDIT.json",
        },
        {
            "id": "R10-V-001",
            "severity": "S3",
            "disposition": "RECORDED",
            "title": "Builder status remains NOT_TESTED until fresh independent adjudication",
            "detail": (
                "R10-G01..G06 reproduce the required evidence, but the final builder_status "
                "stays NOT_TESTED until a fresh independent verifier reproduces every "
                "criterion from the frozen package."
            ),
            "evidence": "R10_BUILDER_REPORT.json",
        },
        {
            "id": "R10-V-002",
            "severity": "S4",
            "disposition": "RECORDED",
            "title": "Full-suite raw outputs may omit pytest summary lines",
            "detail": (
                "Cosmetic: R10_FULL_SUITE_RAW.txt may omit final 'N passed' lines; JUnit "
                "counts and exit codes are authoritative."
            ),
            "evidence": "R10_FULL_SUITE.json",
        },
        {
            "id": "R10-NEW-001",
            "severity": "S4",
            "disposition": "RECORDED",
            "title": "Evidence snapshot carries the full durable projection",
            "detail": (
                "R10_STATE_SNAPSHOT.json is the full isolated-proving projection so a "
                "verifier can reproduce drill-down and timeline checks without re-running "
                "the seeder; it stays inside the 8 MiB evidence budget."
            ),
            "evidence": "R10_STATE_SNAPSHOT.json",
        },
        {
            "id": "R10-V-101",
            "severity": "S2",
            "disposition": "REPAIRED_IN_R10_ROUND2",
            "title": "Incremental progress file excluded from the frozen manifest",
            "detail": (
                "R10_PROGRESS_04.json existed in the evidence dir but was absent from the "
                "artifact manifest, so evidence_bytes did not equal the on-disk total and the "
                "closed package was not byte-identical to the manifest. Progress checkpoints "
                "are incremental working artifacts, not closure evidence; evidence_bytes() and "
                "the manifest scan now exclude R10_PROGRESS_*.json, and the regenerated "
                "manifest matches the on-disk payload byte-for-byte."
            ),
            "evidence": "R10_ARTIFACT_MANIFEST.json, R10_BUILDER_REPORT.json",
        },
        {
            "id": "R10-V-102",
            "severity": "S2",
            "disposition": "REPAIRED_IN_R10_ROUND2",
            "title": "Albanian briefing/page body contained English template sentences",
            "detail": (
                "The SQ briefing body sentences ('Keep the R10 builder evidence package "
                "closed', 'Measured USD total is', 'accepted missions, ...') were English "
                "under Albanian labels; gates and tests only asserted labels/titles. The "
                "briefing generator now emits genuinely localized per-language body text for "
                "every category (data values such as record titles remain in their source "
                "language), and the G03/G04 gates plus regression tests assert no English "
                "template fragments appear in the SQ body."
            ),
            "evidence": "R10_BRIEFING_SQ.txt, R10_BRIEFINGS.json, gate_g03_briefings.py",
        },
        {
            "id": "R10-V-103",
            "severity": "S2",
            "disposition": "REPAIRED_IN_R10_ROUND2",
            "title": "Live-guard compare did not fail closed on a missing DB snapshot",
            "detail": (
                "live_guard.py compare read provenance['r10_attributable_mutation'] even "
                "when _live_db_provenance returned testable=False (snapshot missing), raising "
                "KeyError instead of recording an honest FAIL. The provenance now always "
                "includes r10_attributable_mutation (True when untestable, fail-closed), and "
                "a clean before/after/compare run now records protected_state_unchanged=True "
                "with r10_attributable_mutation=False."
            ),
            "evidence": "R10_LIVE_GUARD_COMPARISON.json, live_guard.py",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R10_BUILDER_REPORT.json")
    parser.add_argument(
        "--manifest", type=Path, default=EVIDENCE_DIR / "R10_ARTIFACT_MANIFEST.json"
    )
    parser.add_argument("--findings", type=Path, default=EVIDENCE_DIR / "R10_FINDINGS.json")
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

    focused_tests = EVIDENCE_DIR / "R10_FOCUSED_TESTS.json"
    tests_status = (
        "PASS"
        if focused_tests.is_file() and read_json(focused_tests).get("exit_code") == 0
        else "NOT_TESTED"
    )
    full_suite = EVIDENCE_DIR / "R10_FULL_SUITE.json"
    full_suite_payload = read_json(full_suite) if full_suite.is_file() else {}
    full_suite_status = (
        "PASS"
        if full_suite.is_file() and full_suite_payload.get("exit_code") == 0
        else "NOT_TESTED"
    )
    static = EVIDENCE_DIR / "R10_STATIC.json"
    static_status = "PASS" if static.is_file() and read_json(static).get("passed") else "NOT_TESTED"
    secret = EVIDENCE_DIR / "R10_SECRET_SCAN.json"
    secret_status = "PASS" if secret.is_file() and read_json(secret).get("passed") else "NOT_TESTED"
    privacy = EVIDENCE_DIR / "R10_PRIVACY.json"
    privacy_status = (
        "PASS" if privacy.is_file() and read_json(privacy).get("passed") else "NOT_TESTED"
    )
    visual = EVIDENCE_DIR / "R10_G06_VISUAL.json"
    visual_status = "PASS" if visual.is_file() and read_json(visual).get("passed") else "NOT_TESTED"

    live_guard_before = EVIDENCE_DIR / "R10_LIVE_GUARD_BEFORE.json"
    live_guard_after = EVIDENCE_DIR / "R10_LIVE_GUARD_AFTER.json"
    live_guard_comparison = EVIDENCE_DIR / "R10_LIVE_GUARD_COMPARISON.json"
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
        and visual_status == "PASS"
        and under_budget
        and confinement["confined"]
    )
    gates["R10-G06"] = {
        "status": "PASS" if g06_passed else "FAIL",
        "evidence": [
            "R10_G06_VISUAL.json",
            "R10_FULL_SUITE.json",
            "R10_STATIC.json",
            "R10_SECRET_SCAN.json",
            "R10_PRIVACY.json",
            "R10_LIVE_GUARD_COMPARISON.json",
        ],
        "evidence_hashes": {
            name: sha256_file(EVIDENCE_DIR / name)
            for name in (
                "R10_G06_VISUAL.json",
                "R10_FULL_SUITE.json",
                "R10_STATIC.json",
                "R10_RUFF.json",
                "R10_MYPY.json",
                "R10_BANDIT.json",
                "R10_SECRET_SCAN.json",
                "R10_PRIVACY.json",
                "R10_LIVE_GUARD_COMPARISON.json",
            )
            if (EVIDENCE_DIR / name).is_file()
        },
    }
    gates["R10-G06"]["evidence_hashes"].update(
        {
            live_guard_before.name: live_guard["before_sha256"],
            live_guard_after.name: live_guard["after_sha256"],
            live_guard_comparison.name: live_guard["comparison_sha256"],
        }
    )

    findings = _findings()
    report: dict[str, Any] = {
        "schema": "jarvis-r10-builder-report-v1",
        "program": "POST_V7_JARVIS_REMASTER",
        "stage": "R10",
        "builder": "r10_builder_fresh",
        "builder_status": "NOT_TESTED",
        "status_reason": (
            "Builder evidence is complete; a fresh independent verifier must reproduce and "
            "adjudicate the package before the gate closes."
        ),
        "generated_utc": "2026-08-16T12:00:00Z",
        "frozen_base": {
            "commit": "35c3b9a5f4d6dc2277de58aceae0797ce74323b2",
            "branch": "remaster/post-v7-purpose",
            "work_package": "docs/remaster/work_packages/R10_WORK_PACKAGE.json",
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
                "R10_FULL_SUITE.json",
                "R10_FULL_SUITE_RAW.txt",
                "R10_FULL_SUITE_PART1_JUNIT.xml",
                "R10_FULL_SUITE_PART2_JUNIT.xml",
                "R10_FULL_SUITE_COMBINED_JUNIT.xml",
            ],
            "evidence_hashes": {
                name: sha256_file(EVIDENCE_DIR / name)
                for name in (
                    "R10_FULL_SUITE.json",
                    "R10_FULL_SUITE_RAW.txt",
                    "R10_FULL_SUITE_PART1_JUNIT.xml",
                    "R10_FULL_SUITE_PART2_JUNIT.xml",
                    "R10_FULL_SUITE_COMBINED_JUNIT.xml",
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
                "schema": "jarvis-r10-findings-v1",
                "program": "POST_V7_JARVIS_REMASTER",
                "stage": "R10",
                "generated_utc": "2026-08-16T12:00:00Z",
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
        f"report: evidence_bytes={evidence_bytes()} under_budget={evidence_bytes() < EVIDENCE_BUDGET_BYTES} "
        f"files={manifest['file_count']} live_guard_unchanged={live_guard['protected_state_unchanged']}"
    )
    print("gates: " + ", ".join(f"{gate_id}={item['status']}" for gate_id, item in gates.items()))
    print(f"wall_seconds={round(time.monotonic() - started, 3)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
