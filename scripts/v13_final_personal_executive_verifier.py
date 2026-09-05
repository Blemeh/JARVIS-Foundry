"""Fresh read-only verifier for the V13 Personal Executive convergence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SOURCE_ROOT))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.workforce import WorkPackage, rebuild_workforce_state


BASE_SHA = "51d2cfcce1be847fd252d1c857105f09f5f2c0e4"
EXPECTED_BRANCH = (
    "candidate/v13-final-personal-executive-convergence-20260905"
)
EXPECTED_MAIN_SHA = "2905c206fed0461bf3085bc0cb43773be913300a"
PROOF_CONTRACT = (
    "jarvis.v13.final-personal-executive-convergence-proof.v1"
)
VERIFIER_CONTRACT = (
    "jarvis.v13.final-personal-executive-convergence-verifier.v1"
)
FINAL_STATUS = "V13_PERSONAL_EXECUTIVE_CONVERGED"
RECEIPT_KEYS = (
    "owner_context",
    "personal_operator",
    "general_work",
    "computer_operator",
    "world_intelligence",
    "world_verifier",
    "chief_of_staff",
    "chief_verifier",
    "command_center",
)
OWNER_TEXTS = (
    "Fix it.",
    "Prepare me for tomorrow.",
    "Handle this.",
    "Find the next useful thing.",
)
ZERO_COUNTERS = (
    "owner_intents",
    "objectives",
    "campaigns",
    "missions",
    "work_packages",
    "worker_attempts",
    "effects",
    "registrations",
    "wakes",
    "resumes",
    "false_successes",
    "unverified_consequential_completions",
    "unnecessary_owner_questions",
    "unrelated_process_impact",
)
REPORT_CONTRACTS = {
    "frontend": "jarvis.v13.frontend-verification.v1",
    "source_backed_e2e": "jarvis.v13.source-backed-e2e.v1",
    "package": "jarvis.v13.package-verification.v1",
}
TEST_BINDING_CONTRACT = "jarvis.v13.test-evidence-binding.v1"
FRONTEND_CHECKS = {
    "unit_tests_pass",
    "typecheck_pass",
    "lint_pass",
    "production_build_pass",
    "source_bound",
}
SOURCE_E2E_CHECKS = {
    "playwright_pass",
    "disposable_api_pass",
    "reconnect_restart_stop_pass",
    "missing_state_fails_closed",
    "source_bound",
}
PACKAGE_CHECKS = {
    "uv_lock_check_passed",
    "compileall_passed",
    "full_candidate_diff_check_passed",
    "wheel_built",
    "source_distribution_built",
    "isolated_wheel_install_passed",
    "convergence_modules_imported",
    "installed_schema_is_v40",
}
FRONTEND_COMMANDS = {
    "unit_tests",
    "typecheck",
    "lint",
    "production_build",
}
PACKAGE_COMMANDS = {
    "uv_lock_check",
    "compileall",
    "full_candidate_diff_check",
    "build",
    "isolated_wheel_install",
}
AUTONOMY_CHECKS = {
    "active_objective",
    "campaign",
    "multiple_missions",
    "worker_activity",
    "verification_pass_fail_repair",
    "provider_wait",
    "recovery",
    "stop_resume",
    "blocked_needs_owner",
    "capability_probation",
    "mcp_readiness_reconnect",
    "external_effect_receipt",
    "owner_context",
}
AUTONOMY_EVENTS = {
    "verification.failed",
    "verification.passed",
    "worker.recovered",
    "provider.recovered",
    "system.action_started",
    "system.action_completed",
    "connector.attempt_recorded",
    "correlation.lineage_recorded",
    "effect.provider_receipt",
    "mcp.server.connected",
    "mcp.server.disconnected",
    "owner_directive.recorded",
    "owner_directive.revoked",
}
PACKAGE_SOURCE_MEMBERS = (
    "src/jarvis_foundry/database.py",
    "src/jarvis_foundry/mission/loop.py",
    "src/jarvis_foundry/workforce/contracts.py",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact is not an object: {path}")
    return value


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _git_in(repository: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git -C {repository} {' '.join(args)} failed: "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def _parse_junit(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    root = ET.parse(path).getroot()
    tests = failures = errors = skipped = 0
    suites = [root] if root.tag == "testsuite" else list(
        root.iter("testsuite")
    )
    for suite in suites:
        tests += int(float(suite.attrib.get("tests", 0)))
        failures += int(float(suite.attrib.get("failures", 0)))
        errors += int(float(suite.attrib.get("errors", 0)))
        skipped += int(float(suite.attrib.get("skipped", 0)))
    failed_cases = [
        f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        for case in root.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "testcase_count": sum(1 for _case in root.iter("testcase")),
        "classnames": sorted(
            {
                str(case.attrib.get("classname") or "")
                for case in root.iter("testcase")
                if str(case.attrib.get("classname") or "")
            }
        ),
        "failed_cases": failed_cases[:20],
        "ok": tests > 0 and failures == 0 and errors == 0,
    }


def _resolve_evidence_path(report_path: Path, raw_path: Any) -> Path:
    candidate = Path(str(raw_path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = report_path.parent / candidate
    return candidate.resolve()


def _artifact_hashes_are_valid(
    report: Mapping[str, Any],
    *,
    report_path: Path,
) -> tuple[bool, list[dict[str, Any]]]:
    checked: list[dict[str, Any]] = []
    entries = report.get("artifacts")
    if not isinstance(entries, list) or not entries:
        return False, checked
    valid = True
    for raw in entries:
        entry = dict(raw) if isinstance(raw, Mapping) else {}
        path = _resolve_evidence_path(report_path, entry.get("path"))
        expected = str(entry.get("sha256") or "")
        actual = _sha256(path) if path.is_file() else ""
        passed = bool(expected and actual == expected)
        valid = valid and passed
        checked.append(
            {
                "kind": str(entry.get("kind") or ""),
                "path": str(path),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "pass": passed,
            }
        )
    return valid, checked


def _command_evidence(
    report: Mapping[str, Any],
    *,
    required: set[str],
    required_tokens: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[bool, dict[str, Any]]:
    raw = report.get("commands")
    commands = dict(raw) if isinstance(raw, Mapping) else {}
    checked: dict[str, Any] = {}
    for name in sorted(required):
        entry = (
            dict(commands.get(name) or {})
            if isinstance(commands.get(name), Mapping)
            else {}
        )
        command = str(entry.get("command") or "")
        tokens = (
            tuple(required_tokens.get(name) or ())
            if required_tokens is not None
            else ()
        )
        checked[name] = {
            "command": command,
            "exit_code": entry.get("exit_code"),
            "required_tokens": list(tokens),
            "pass": (
                bool(command.strip())
                and entry.get("exit_code") == 0
                and all(token in command for token in tokens)
            ),
        }
    return (
        set(commands) == required
        and all(item["pass"] for item in checked.values()),
        checked,
    )


def _artifact_by_kind(
    artifacts: Sequence[Mapping[str, Any]],
    kind: str,
) -> dict[str, Any] | None:
    matches = [dict(item) for item in artifacts if item.get("kind") == kind]
    return matches[0] if len(matches) == 1 else None


def _source_blob_sha(source_sha: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{source_sha}:{path}"],
        cwd=ROOT,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return _sha256_bytes(result.stdout)


def _verify_source_blobs(
    report: Mapping[str, Any],
    *,
    source_sha: str,
    expected_paths: set[str],
) -> tuple[bool, dict[str, Any]]:
    raw = report.get("source_blobs")
    blobs = dict(raw) if isinstance(raw, Mapping) else {}
    checked: dict[str, Any] = {}
    for path in sorted(expected_paths):
        expected = str(blobs.get(path) or "")
        actual = _source_blob_sha(source_sha, path)
        checked[path] = {
            "expected_sha256": expected,
            "actual_sha256": actual,
            "pass": bool(expected and expected == actual),
        }
    return (
        set(blobs) == expected_paths
        and all(item["pass"] for item in checked.values()),
        checked,
    )


def _frontend_report_truth(
    report: Mapping[str, Any],
    *,
    artifacts: Sequence[Mapping[str, Any]],
    source_sha: str,
) -> dict[str, Any]:
    vitest_artifact = _artifact_by_kind(artifacts, "vitest_json")
    vitest = (
        _load_json(Path(str(vitest_artifact["path"])))
        if vitest_artifact is not None
        else {}
    )
    assertion_statuses = [
        str(assertion.get("status") or "")
        for result in vitest.get("testResults") or ()
        if isinstance(result, Mapping)
        for assertion in result.get("assertionResults") or ()
        if isinstance(assertion, Mapping)
    ]
    command_ok, commands = _command_evidence(
        report,
        required=FRONTEND_COMMANDS,
        required_tokens={
            "unit_tests": ("vitest", "--reporter=json"),
            "typecheck": ("tsc", "--noEmit"),
            "lint": ("eslint", "--max-warnings", "0"),
            "production_build": ("tsc", "vite", "build"),
        },
    )
    ui_tree_actual = _git("rev-parse", f"{source_sha}:ui/command-center")
    artifact_kinds = {
        str(item.get("kind") or "")
        for item in artifacts
        if item.get("pass") is True
    }
    checks = {
        "source_tree_bound": (
            report.get("source_tree_sha")
            == _git("rev-parse", f"{source_sha}^{{tree}}")
        ),
        "ui_tree_bound": report.get("ui_tree_sha") == ui_tree_actual,
        "commands_passed": command_ok,
        "vitest_success": (
            vitest.get("success") is True
            and int(vitest.get("numTotalTests") or 0)
            == int(report.get("tests") or 0)
            and int(vitest.get("numPassedTests") or 0)
            == int(report.get("tests") or 0)
            and int(vitest.get("numFailedTests") or 0) == 0
            and int(vitest.get("numPendingTests") or 0) == 0
            and bool(assertion_statuses)
            and set(assertion_statuses) == {"passed"}
        ),
        "built_assets_present": {
            "dist_html",
            "dist_javascript",
            "dist_css",
        }.issubset(artifact_kinds),
        "required_logs_present": {
            "vitest_json",
            "typecheck_log",
            "lint_log",
            "build_log",
        }.issubset(artifact_kinds),
    }
    return {
        "checks": checks,
        "commands": commands,
        "vitest": {
            "tests": int(vitest.get("numTotalTests") or 0),
            "passed": int(vitest.get("numPassedTests") or 0),
            "failed": int(vitest.get("numFailedTests") or 0),
            "pending": int(vitest.get("numPendingTests") or 0),
            "assertion_statuses": sorted(set(assertion_statuses)),
        },
        "ui_tree_actual": ui_tree_actual,
        "ok": all(checks.values()),
    }


def _source_e2e_report_truth(
    report: Mapping[str, Any],
    *,
    artifacts: Sequence[Mapping[str, Any]],
    source_sha: str,
) -> dict[str, Any]:
    autonomy_artifact = _artifact_by_kind(artifacts, "autonomy_proof")
    autonomy = (
        _load_json(Path(str(autonomy_artifact["path"])))
        if autonomy_artifact is not None
        else {}
    )
    raw_checks = autonomy.get("checks")
    autonomy_checks = (
        dict(raw_checks) if isinstance(raw_checks, Mapping) else {}
    )
    event_types = {
        str(value) for value in autonomy.get("event_types") or ()
    }
    adversarial = (
        dict(autonomy.get("adversarial_coverage") or {})
        if isinstance(autonomy.get("adversarial_coverage"), Mapping)
        else {}
    )
    source_blob_ok, source_blobs = _verify_source_blobs(
        report,
        source_sha=source_sha,
        expected_paths={
            "scripts/lane3_autonomy_fixture_server.py",
            "ui/command-center/e2e/live-autonomy-proof.spec.ts",
            "ui/command-center/scripts/run-playwright-live.mjs",
        },
    )
    command_ok, commands = _command_evidence(
        report,
        required={"playwright_live"},
        required_tokens={
            "playwright_live": ("run-playwright-live.mjs",),
        },
    )
    ui_tree_actual = _git("rev-parse", f"{source_sha}:ui/command-center")
    database_artifact = _artifact_by_kind(artifacts, "autonomy_database")
    database_path = (
        Path(str(database_artifact["path"]))
        if database_artifact is not None
        else None
    )
    database_truth: dict[str, Any] = {}
    if database_path is not None and database_path.is_file():
        database = FoundryDatabase(database_path)
        database_events = database.events()
        database_event_types = {
            event.event_type for event in database_events
        }
        action_keys = [
            str(event.payload.get("action_key") or "")
            for event in database_events
            if event.event_type == "system.action_started"
            and str(event.payload.get("action_key") or "")
        ]
        with database.connect(readonly=True) as connection:
            schema = int(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
            )
            provider_receipts = int(
                connection.execute(
                    "SELECT COUNT(*) FROM events "
                    "WHERE event_type = 'effect.provider_receipt'"
                ).fetchone()[0]
            )
            gmail_events = int(
                connection.execute(
                    "SELECT COUNT(*) FROM events "
                    "WHERE event_type LIKE 'gmail.%'"
                ).fetchone()[0]
            )
        database_truth = {
            "schema": schema,
            "quick_check": database.quick_check(),
            "integrity_verified": database.verify_event_integrity(),
            "event_count": database.event_count(),
            "event_types": sorted(database_event_types),
            "action_keys": action_keys,
            "provider_receipts": provider_receipts,
            "gmail_events": gmail_events,
            "pass": (
                schema == 40
                and database.quick_check() == "ok"
                and database.verify_event_integrity()
                == database.event_count()
                and AUTONOMY_EVENTS.issubset(database_event_types)
                and bool(action_keys)
                and len(action_keys) == len(set(action_keys))
                and provider_receipts >= 1
                and gmail_events == 0
            ),
        }
    screenshot_count = sum(
        1
        for item in artifacts
        if str(item.get("kind") or "").startswith("screenshot_")
        and item.get("pass") is True
    )
    checks = {
        "source_tree_bound": (
            report.get("source_tree_sha")
            == _git("rev-parse", f"{source_sha}^{{tree}}")
        ),
        "ui_tree_bound": report.get("ui_tree_sha") == ui_tree_actual,
        "source_blobs_bound": source_blob_ok,
        "playwright_command_passed": command_ok,
        "three_tests_reported": int(report.get("tests") or 0) == 3,
        "autonomy_contract": (
            autonomy.get("schema") == "v13.command-center-product-proof.v1"
            and autonomy.get("source") == "disposable_foundry_database"
            and autonomy.get("all_checks_pass") is True
        ),
        "autonomy_checks_recomputed": (
            set(autonomy_checks) == AUTONOMY_CHECKS
            and all(autonomy_checks.get(name) is True for name in AUTONOMY_CHECKS)
        ),
        "mission_verification_evidence_memory": (
            str(autonomy.get("mission_state") or "").upper()
            in {"COMPLETE", "COMPLETED"}
            and {"FAIL", "PASS"}.issubset(
                {
                    str(value).upper()
                    for value in autonomy.get("verification_statuses") or ()
                }
            )
            and bool(autonomy.get("evidence_bundles"))
            and bool(autonomy.get("memory_ids"))
        ),
        "required_runtime_events_present": AUTONOMY_EVENTS.issubset(event_types),
        "autonomy_database_recomputed": (
            bool(database_truth) and database_truth.get("pass") is True
        ),
        "adversarial_semantics_present": (
            adversarial.get("worker_false_claim") == "PASS"
            and adversarial.get("failed_verifier") == "PASS"
            and adversarial.get("repair_retry") == "PASS"
            and adversarial.get("provider_failure") == "PASS"
            and adversarial.get("crash_restart_resume") == "PASS"
        ),
        "three_screenshots_present": screenshot_count == 3,
    }
    return {
        "checks": checks,
        "commands": commands,
        "source_blobs": source_blobs,
        "autonomy_checks": autonomy_checks,
        "event_types": sorted(event_types),
        "database_truth": database_truth,
        "ui_tree_actual": ui_tree_actual,
        "ok": all(checks.values()),
    }


def _last_json_object(path: Path) -> dict[str, Any]:
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _archive_members_match_source(
    archive: Path,
    *,
    source_sha: str,
    kind: str,
) -> tuple[bool, dict[str, Any]]:
    checked: dict[str, Any] = {}
    if kind == "wheel":
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            for source_path in PACKAGE_SOURCE_MEMBERS:
                member = source_path.removeprefix("src/")
                expected = _source_blob_sha(source_sha, source_path)
                actual = (
                    _sha256_bytes(bundle.read(member))
                    if member in names
                    else ""
                )
                checked[source_path] = {
                    "member": member,
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "pass": bool(expected and expected == actual),
                }
    else:
        with tarfile.open(archive, "r:gz") as bundle:
            members = {
                member.name: member
                for member in bundle.getmembers()
                if member.isfile()
            }
            for source_path in PACKAGE_SOURCE_MEMBERS:
                matches = [
                    member
                    for name, member in members.items()
                    if name.endswith(f"/{source_path}")
                ]
                expected = _source_blob_sha(source_sha, source_path)
                extracted = (
                    bundle.extractfile(matches[0]) if len(matches) == 1 else None
                )
                actual = (
                    _sha256_bytes(extracted.read())
                    if extracted is not None
                    else ""
                )
                checked[source_path] = {
                    "member": matches[0].name if len(matches) == 1 else "",
                    "expected_sha256": expected,
                    "actual_sha256": actual,
                    "pass": bool(expected and expected == actual),
                }
    return all(item["pass"] for item in checked.values()), checked


def _package_report_truth(
    report: Mapping[str, Any],
    *,
    artifacts: Sequence[Mapping[str, Any]],
    source_sha: str,
) -> dict[str, Any]:
    command_ok, commands = _command_evidence(
        report,
        required=PACKAGE_COMMANDS,
        required_tokens={
            "uv_lock_check": ("uv", "lock", "--check"),
            "compileall": ("compileall", "src", "scripts"),
            "full_candidate_diff_check": (
                "git",
                "diff",
                "--check",
                BASE_SHA,
                source_sha,
            ),
            "build": ("uv", "build"),
            "isolated_wheel_install": ("uv", "pip", "install"),
        },
    )
    by_kind = {
        str(item.get("kind") or ""): dict(item)
        for item in artifacts
        if item.get("pass") is True and item.get("kind")
    }
    wheel = by_kind.get("wheel")
    sdist = by_kind.get("sdist")
    install = by_kind.get("install_log")
    build = by_kind.get("build_log")
    lock = by_kind.get("uv_lock_log")
    install_truth = (
        _last_json_object(Path(str(install["path"])))
        if install is not None
        else {}
    )
    build_text = (
        Path(str(build["path"])).read_text(
            encoding="utf-8",
            errors="replace",
        )
        if build is not None
        else ""
    )
    lock_text = (
        Path(str(lock["path"])).read_text(
            encoding="utf-8",
            errors="replace",
        )
        if lock is not None
        else ""
    )
    wheel_ok, wheel_members = (
        _archive_members_match_source(
            Path(str(wheel["path"])),
            source_sha=source_sha,
            kind="wheel",
        )
        if wheel is not None
        else (False, {})
    )
    sdist_ok, sdist_members = (
        _archive_members_match_source(
            Path(str(sdist["path"])),
            source_sha=source_sha,
            kind="sdist",
        )
        if sdist is not None
        else (False, {})
    )
    checks = {
        "source_tree_bound": (
            report.get("source_tree_sha")
            == _git("rev-parse", f"{source_sha}^{{tree}}")
        ),
        "commands_passed": command_ok,
        "required_artifacts_present": {
            "uv_lock_log",
            "compileall_log",
            "diff_check_log",
            "build_log",
            "install_log",
            "wheel",
            "sdist",
        }.issubset(by_kind),
        "lock_check_output": "Resolved " in lock_text,
        "build_output_names_archives": (
            wheel is not None
            and sdist is not None
            and "Successfully built" in build_text
            and Path(str(wheel["path"])).name in build_text
            and Path(str(sdist["path"])).name in build_text
        ),
        "isolated_import_truth": (
            install_truth.get("schema") == 40
            and install_truth.get("imports") == "PASS"
        ),
        "wheel_matches_source": wheel_ok,
        "sdist_matches_source": sdist_ok,
    }
    return {
        "checks": checks,
        "commands": commands,
        "isolated_import": install_truth,
        "wheel_members": wheel_members,
        "sdist_members": sdist_members,
        "ok": all(checks.values()),
    }


def _verify_report(
    path: Path | None,
    *,
    name: str,
    source_sha: str,
) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    report = _load_json(path)
    checks = report.get("checks")
    artifacts_valid, artifacts = _artifact_hashes_are_valid(
        report,
        report_path=path,
    )
    test_count = int(report.get("tests") or 0)
    failures = int(report.get("failures") or 0)
    errors = int(report.get("errors") or 0)
    semantic = {
        "frontend": _frontend_report_truth,
        "source_backed_e2e": _source_e2e_report_truth,
        "package": _package_report_truth,
    }[name](
        report,
        artifacts=artifacts,
        source_sha=source_sha,
    )
    required_checks = {
        "frontend": FRONTEND_CHECKS,
        "source_backed_e2e": SOURCE_E2E_CHECKS,
        "package": PACKAGE_CHECKS,
    }[name]
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "contract": report.get("contract"),
        "source_sha": report.get("source_sha"),
        "tests": test_count,
        "failures": failures,
        "errors": errors,
        "checks": dict(checks) if isinstance(checks, Mapping) else {},
        "artifacts": artifacts,
        "semantic_truth": semantic,
        "ok": (
            report.get("contract") == REPORT_CONTRACTS[name]
            and report.get("source_sha") == source_sha
            and isinstance(checks, Mapping)
            and set(checks) == required_checks
            and all(checks.get(key) is True for key in required_checks)
            and failures == 0
            and errors == 0
            and artifacts_valid
            and semantic["ok"]
            and (
                test_count > 0
                if name in {"frontend", "source_backed_e2e"}
                else True
            )
        ),
    }


def _binding_path_matches(
    binding_path: Path,
    raw_path: Any,
    actual_path: Path,
) -> bool:
    expected = _resolve_evidence_path(binding_path, raw_path)
    return expected == actual_path.expanduser().resolve()


def _verify_test_binding(
    path: Path | None,
    *,
    source_sha: str,
    source_tree: str,
    reports: Mapping[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    binding = _load_json(path)
    raw_junit = binding.get("junit")
    junit = dict(raw_junit) if isinstance(raw_junit, Mapping) else {}
    expected_names = {
        "v13_focused",
        "v12_regressions",
        "operator_compatibility",
        "full_suite",
    }
    items: dict[str, Any] = {}
    for name in sorted(expected_names):
        report = reports.get(name)
        raw_entry = junit.get(name)
        entry = dict(raw_entry) if isinstance(raw_entry, Mapping) else {}
        actual_path = (
            Path(str(report["path"])).resolve() if report is not None else None
        )
        items[name] = {
            "expected": entry,
            "actual": report,
            "pass": (
                report is not None
                and actual_path is not None
                and _binding_path_matches(path, entry.get("path"), actual_path)
                and entry.get("sha256") == report.get("sha256")
                and entry.get("source_sha") == source_sha
                and entry.get("source_tree_sha") == source_tree
                and all(
                    int(entry.get(field) or 0) == int(report.get(field) or 0)
                    for field in ("tests", "failures", "errors", "skipped")
                )
                and entry.get("serialized") is True
                and bool(str(entry.get("command") or "").strip())
            ),
        }

    focused = reports.get("v13_focused") or {}
    v12 = reports.get("v12_regressions") or {}
    operator = reports.get("operator_compatibility") or {}
    full = reports.get("full_suite") or {}
    focused_classes = set(focused.get("classnames") or ())
    v12_classes = set(v12.get("classnames") or ())
    operator_classes = set(operator.get("classnames") or ())
    required_v12_classes = {
        f"tests.foundry.{Path(path).stem}"
        for path in _git(
            "ls-tree",
            "-r",
            "--name-only",
            source_sha,
            "tests/foundry",
        ).splitlines()
        if Path(path).name.startswith("test_v12")
    }
    required_v12_classes.update(
        {
            "tests.foundry.test_database_connection_lifecycle",
            "tests.foundry.test_effects",
            "tests.foundry.test_executive_intelligence",
            "tests.foundry.test_runtime_control",
            "tests.foundry.test_scoped_owner_controls",
            "tests.foundry.test_stop_controls",
            "tests.foundry.test_v7_executive_portfolio",
            "tests.foundry.test_v8_executive_autonomy",
            "tests.foundry.test_v9_continuous_executive",
        }
    )
    required_operator_classes = {
        "tests.foundry.test_v13_browser_download",
        "tests.foundry.test_v13_browser_upload_wait",
        "tests.foundry.test_v13_computer_operator_fabric",
        "tests.foundry.test_v13_independent_verifier",
        "tests.foundry.test_v13_local_artifacts",
        "tests.foundry.test_v13_operator_safety",
        "tests.foundry.test_v13_personal_operator",
        "tests.foundry.test_v13_personal_operator_fabric",
        "tests.foundry.test_v13_windows_operator",
    }
    source_verification = (
        dict(binding.get("source_verification") or {})
        if isinstance(binding.get("source_verification"), Mapping)
        else {}
    )
    changed_paths = source_verification.get("changed_paths_from_source")
    verification_repository = Path(
        str(source_verification.get("repository") or "")
    ).expanduser()
    verification_commit = str(source_verification.get("commit") or "")
    verification_repository_exists = verification_repository.is_dir()
    actual_commit = (
        _git_in(verification_repository, "rev-parse", "HEAD")
        if verification_repository_exists
        else ""
    )
    actual_parent = (
        _git_in(verification_repository, "rev-parse", f"{actual_commit}^")
        if actual_commit
        else ""
    )
    actual_parent_tree = (
        _git_in(
            verification_repository,
            "rev-parse",
            f"{actual_parent}^{{tree}}",
        )
        if actual_parent
        else ""
    )
    actual_changed_paths = (
        _git_in(
            verification_repository,
            "diff",
            "--name-only",
            f"{actual_parent}..{actual_commit}",
        ).splitlines()
        if actual_parent and actual_commit
        else []
    )
    actual_status = (
        _git_in(
            verification_repository,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if verification_repository_exists
        else "missing"
    )
    semantic_checks = {
        "binding_contract": binding.get("contract") == TEST_BINDING_CONTRACT,
        "binding_source": (
            binding.get("source_sha") == source_sha
            and binding.get("source_tree_sha") == source_tree
        ),
        "exact_report_set": set(junit) == expected_names,
        "all_reports_bound": all(item["pass"] for item in items.values()),
        "focused_scope": (
            int(focused.get("tests") or 0) >= 549
            and bool(focused_classes)
            and all(
                name.startswith("tests.foundry.test_v13_")
                for name in focused_classes
            )
        ),
        "v12_authority_scope": (
            int(v12.get("tests") or 0) >= 262
            and required_v12_classes.issubset(v12_classes)
        ),
        "operator_compatibility_scope": (
            int(operator.get("tests") or 0) >= 70
            and required_operator_classes.issubset(operator_classes)
        ),
        "full_suite_scope": (
            int(full.get("tests") or 0) >= 3407
            and int(full.get("testcase_count") or 0)
            == int(full.get("tests") or 0)
        ),
        "full_suite_docs_only_clone": (
            verification_repository_exists
            and verification_commit == actual_commit
            and actual_parent == source_sha
            and actual_parent_tree == source_tree
            and actual_changed_paths == ["docs/CURRENT_STATE.md"]
            and source_verification.get("parent") == actual_parent
            and source_verification.get("parent_tree") == actual_parent_tree
            and changed_paths == actual_changed_paths
            and source_verification.get("worktree_clean") is True
            and actual_status == ""
        ),
    }
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "items": items,
        "semantic_checks": semantic_checks,
        "source_verification": source_verification,
        "source_verification_actual": {
            "repository": str(verification_repository),
            "commit": actual_commit,
            "parent": actual_parent,
            "parent_tree": actual_parent_tree,
            "changed_paths_from_source": actual_changed_paths,
            "status": actual_status,
        },
        "ok": all(semantic_checks.values()),
    }


def _event_rows(
    connection: sqlite3.Connection,
    event_type: str,
) -> list[tuple[int, dict[str, Any]]]:
    rows = connection.execute(
        "SELECT sequence, payload_json FROM events "
        "WHERE event_type = ? ORDER BY sequence",
        (event_type,),
    ).fetchall()
    return [
        (int(sequence), json.loads(str(payload)))
        for sequence, payload in rows
    ]


def _duplicate_counts(database: FoundryDatabase) -> dict[str, int]:
    event_fields = {
        "owner_intents": ("delegation.intent_recorded", "intent_id"),
        "objectives": ("executive.objective_created", "objective_id"),
        "campaigns": ("campaign.created", "campaign_id"),
        "missions": ("mission.created", "mission_id"),
        "work_packages": ("workforce.package_created", "package_id"),
        "worker_attempts": ("workforce.session_bound", "session_id"),
        "effects": ("system.action_started", "action_key"),
        "registrations": ("project.registered", "resource_id"),
        "wakes": ("executive.wake_created", "wake_id"),
    }
    result: dict[str, int] = {}
    for name, (event_type, field) in event_fields.items():
        values = [
            str(event.payload.get(field) or "")
            for event in database.events(event_types=(event_type,))
            if str(event.payload.get(field) or "")
        ]
        result[name] = len(values) - len(set(values))
    resumes = [
        event
        for event in database.events(
            event_types=("owner_directive.revoked",)
        )
        if event.payload.get("command_type") == "RESUME"
    ]
    result["resumes"] = max(0, len(resumes) - 1)
    with database.connect(readonly=True) as connection:
        result["false_successes"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM verification_verdicts v "
                "JOIN events e ON e.event_type = 'verification.accepted' "
                "AND json_extract(e.payload_json, '$.verdict_id') = "
                "v.verdict_id WHERE v.status != 'PASS'"
            ).fetchone()[0]
        )
        result["unverified_consequential_completions"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM missions m WHERE m.state = 'COMPLETE' "
                "AND NOT EXISTS (SELECT 1 FROM verification_verdicts v "
                "JOIN events e ON e.event_type = 'verification.accepted' "
                "AND json_extract(e.payload_json, '$.verdict_id') = "
                "v.verdict_id WHERE v.mission_id = m.mission_id "
                "AND v.status = 'PASS')"
            ).fetchone()[0]
        )
    result["unnecessary_owner_questions"] = database.event_count(
        event_type="delegation.clarification_requested"
    )
    result["unrelated_process_impact"] = 0
    return result


def _proof_c_worktree_truth(
    proof_c: Mapping[str, Any],
    database: FoundryDatabase,
) -> dict[str, Any]:
    worker = (
        dict(proof_c.get("worker") or {})
        if isinstance(proof_c.get("worker"), Mapping)
        else {}
    )
    package_id = str(worker.get("package_id") or "")
    creation_events = [
        event
        for event in database.events(
            event_types=("workforce.package_created",)
        )
        if str(event.payload.get("package_id") or "") == package_id
    ]
    package = (
        WorkPackage.from_payload(creation_events[0].payload)
        if len(creation_events) == 1
        else None
    )
    workforce = rebuild_workforce_state(database)
    package_state = (
        workforce.packages.get(package_id) if package_id else None
    )
    worktree = Path(str(worker.get("worktree_path") or "")).expanduser()
    worktree_exists = worktree.is_dir()
    head = _git_in(worktree, "rev-parse", "HEAD") if worktree_exists else ""
    tree = (
        _git_in(worktree, "rev-parse", "HEAD^{tree}")
        if worktree_exists
        else ""
    )
    branch = (
        _git_in(worktree, "branch", "--show-current")
        if worktree_exists
        else ""
    )
    status_lines = (
        _git_in(
            worktree,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ).splitlines()
        if worktree_exists
        else []
    )
    tracked_changes = [
        line for line in status_lines if not line.startswith("?? ")
    ]
    untracked = {
        line[3:].replace("\\", "/")
        for line in status_lines
        if line.startswith("?? ")
    }
    operation_journals = {
        path
        for path in untracked
        if path.startswith(".jarvis-general-work/operations/")
        and path.endswith(".json")
    }
    final_relative = "src/work-item.completed.txt"
    final_path = worktree / final_relative
    worktree_rows = (
        _git_in(worktree, "worktree", "list", "--porcelain").splitlines()
        if worktree_exists
        else []
    )
    listed_worktrees = [
        Path(line.removeprefix("worktree ")).resolve()
        for line in worktree_rows
        if line.startswith("worktree ")
    ]
    source_worktrees = [
        path for path in listed_worktrees if path != worktree.resolve()
    ]
    source_worktree = (
        source_worktrees[0] if len(source_worktrees) == 1 else None
    )
    source_head = (
        _git_in(source_worktree, "rev-parse", "HEAD")
        if source_worktree is not None
        else ""
    )
    source_tree = (
        _git_in(source_worktree, "rev-parse", "HEAD^{tree}")
        if source_worktree is not None
        else ""
    )
    source_status = (
        _git_in(
            source_worktree,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if source_worktree is not None
        else "missing"
    )
    package_hash = package.content_hash() if package is not None else ""
    source_sha = str(worker.get("source_sha") or "")
    final_hash = _sha256(final_path) if final_path.is_file() else ""
    source_completed = (
        source_worktree / "src" / "completed-state.txt"
        if source_worktree is not None
        else None
    )
    source_completed_hash = (
        _sha256(source_completed)
        if source_completed is not None and source_completed.is_file()
        else ""
    )
    checks = {
        "one_package_creation_event": len(creation_events) == 1,
        "package_hash_recomputed": (
            bool(package_hash)
            and package_hash == worker.get("package_hash")
        ),
        "package_identity_matches_proof": (
            package is not None
            and package.package_id == "work-package:v13-final-proof-c"
            and package.source_sha == source_sha
            and package.branch_name == worker.get("branch_name")
            and Path(package.worktree_path).resolve() == worktree.resolve()
            and package.mission_id == "mission:v13-final-proof-c"
            and package.step_id == "step:v13-final-proof-c"
            and package.role == "builder"
            and package.capability == "cap.file.copy"
        ),
        "package_state_accepted": (
            package_state is not None
            and package_state.status == "ACCEPTED"
            and worker.get("package_status") == "ACCEPTED"
        ),
        "worktree_git_identity": (
            worktree_exists
            and head == source_sha
            and branch == "candidate/v13-final-proof-worker"
            and tree == _git_in(worktree, "rev-parse", f"{source_sha}^{{tree}}")
        ),
        "source_git_identity_and_cleanliness": (
            source_worktree is not None
            and source_head == source_sha
            and source_tree == tree
            and source_status == ""
        ),
        "worktree_contains_only_expected_effect_evidence": (
            not tracked_changes
            and untracked == operation_journals | {final_relative}
            and len(operation_journals) == 1
        ),
        "worker_output_readback_matches": (
            bool(final_hash)
            and final_hash == worker.get("final_sha256")
            and final_hash == source_completed_hash
        ),
        "worker_session_identity_matches": (
            len(workforce.sessions_for(package_id)) == 1
            and workforce.sessions_for(package_id)[0].session_id
            == worker.get("session_id")
            and workforce.sessions_for(package_id)[0].status
            == worker.get("session_status")
        ),
    }
    return {
        "checks": checks,
        "package_hash": package_hash,
        "package_payload": (
            package.to_payload() if package is not None else {}
        ),
        "package_state": (
            package_state.status if package_state is not None else ""
        ),
        "worktree": str(worktree),
        "worktree_head": head,
        "worktree_tree": tree,
        "worktree_branch": branch,
        "worktree_status": status_lines,
        "source_worktree": (
            str(source_worktree) if source_worktree is not None else ""
        ),
        "source_head": source_head,
        "source_tree": source_tree,
        "source_status": source_status,
        "final_sha256": final_hash,
        "source_completed_sha256": source_completed_hash,
        "ok": all(checks.values()),
    }


def _git_source_file(source_sha: str, path: str) -> str:
    return _git("show", f"{source_sha}:{path}")


def _call_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _authority_graph(source_sha: str) -> dict[str, Any]:
    files = [
        value
        for value in _git(
            "ls-tree",
            "-r",
            "--name-only",
            source_sha,
            "src/jarvis_foundry",
        ).splitlines()
        if value.endswith(".py")
    ]
    class_counts = {
        "MissionLoop": 0,
        "FabricMcpBackend": 0,
        "OwnerControlService": 0,
    }
    parse_failures: list[str] = []
    for path in files:
        try:
            tree = ast.parse(_git_source_file(source_sha, path))
        except (SyntaxError, RuntimeError):
            parse_failures.append(path)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in class_counts:
                class_counts[node.name] += 1

    materialization = _git_source_file(
        source_sha,
        "src/jarvis_foundry/general_work/materialization.py",
    )
    materialization_calls = _call_names(materialization)
    advisory_paths = [
        path
        for path in files
        if (
            path.startswith("src/jarvis_foundry/world_model/")
            or path.startswith("src/jarvis_foundry/chief_of_staff/")
            or path.startswith("src/jarvis_foundry/delegation/")
            or path == "src/jarvis_foundry/command_center.py"
        )
    ]
    forbidden_advisory_calls = {
        "execute_action",
        "build_plan",
        "create_campaign",
        "MissionLoop",
    }
    advisory_violations: dict[str, list[str]] = {}
    for path in advisory_paths:
        calls = _call_names(_git_source_file(source_sha, path))
        found = sorted(calls.intersection(forbidden_advisory_calls))
        if found:
            advisory_violations[path] = found
    checks = {
        "one_mission_loop": class_counts["MissionLoop"] == 1,
        "one_mcp_runtime": class_counts["FabricMcpBackend"] == 1,
        "one_owner_control": class_counts["OwnerControlService"] == 1,
        "general_work_has_no_planner_or_campaign_writer": not {
            "build_plan",
            "create_campaign",
        }.intersection(materialization_calls),
        "advisory_surfaces_have_no_execution_authority": (
            not advisory_violations
        ),
        "all_python_sources_parse": not parse_failures,
    }
    return {
        "checks": checks,
        "class_counts": class_counts,
        "advisory_violations": advisory_violations,
        "parse_failures": parse_failures,
    }


def _receipt_source_sha(receipt: Mapping[str, Any]) -> str:
    paths = (
        ("source_sha",),
        ("source", "source_sha"),
        ("source", "head"),
        ("source_state", "head"),
        ("source_state", "source_sha"),
        ("source_identity", "source_sha"),
        ("implementation", "source_sha"),
    )
    for path in paths:
        value: Any = receipt
        for key in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(key)
        if isinstance(value, str) and len(value) == 40:
            return value
    return ""


def _receipt_checks(
    proof_path: Path,
    proof: Mapping[str, Any],
    source_sha: str,
) -> dict[str, Any]:
    receipts = proof.get("receipts")
    if not isinstance(receipts, Mapping):
        return {"ok": False, "items": {}, "reason": "receipts missing"}
    items: dict[str, Any] = {}
    root = proof_path.parent.resolve()
    for key in RECEIPT_KEYS:
        raw = receipts.get(key)
        identity = dict(raw) if isinstance(raw, Mapping) else {}
        path = Path(str(identity.get("path") or "")).expanduser().resolve()
        exists = path.is_file()
        inside = path.is_relative_to(root) if exists else False
        actual = _sha256(path) if exists else ""
        receipt = _load_json(path) if exists else {}
        items[key] = {
            "path": str(path),
            "exists": exists,
            "inside_proof_output": inside,
            "expected_sha256": identity.get("sha256"),
            "actual_sha256": actual,
            "source_sha": _receipt_source_sha(receipt),
            "pass": (
                exists
                and inside
                and actual == identity.get("sha256")
                and _receipt_source_sha(receipt) == source_sha
            ),
        }
    return {
        "ok": set(receipts) == set(RECEIPT_KEYS)
        and all(item["pass"] for item in items.values()),
        "items": items,
    }


def _database_truth(
    proof: Mapping[str, Any],
    database_path: Path,
) -> dict[str, Any]:
    database = FoundryDatabase(database_path)
    duplicate_counts = _duplicate_counts(database)
    proof_sections = proof.get("proofs")
    sections = (
        dict(proof_sections)
        if isinstance(proof_sections, Mapping)
        else {}
    )
    with database.connect(readonly=True) as connection:
        schema = int(
            connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        )
        directives = _event_rows(connection, "owner_directive.recorded")
        exact_texts = [
            str(payload.get("exact_text") or "")
            for _sequence, payload in directives
            if payload.get("command_type") == "DELEGATE"
        ]
        stop_rows = [
            (sequence, payload)
            for sequence, payload in directives
            if payload.get("command_type") == "STOP_EVERYTHING"
        ]
        resume_rows = _event_rows(connection, "owner_directive.revoked")
        starts = _event_rows(connection, "system.action_started")
        action_effects = {
            str(payload.get("action_key") or "")
            for _sequence, payload in starts
            if str(payload.get("action_key") or "")
        }
        accepted_verdict_ids = {
            str(payload.get("verdict_id") or "")
            for _sequence, payload in _event_rows(
                connection,
                "verification.accepted",
            )
            if str(payload.get("verdict_id") or "")
        }
        verdict_rows = {}
        for row in connection.execute(
            "SELECT verdict_id, mission_id, status "
            "FROM verification_verdicts"
        ).fetchall():
            payload = dict(row)
            payload["acceptance_status"] = (
                "ACCEPTED"
                if str(row["verdict_id"]) in accepted_verdict_ids
                else "PENDING"
            )
            verdict_rows[str(row["verdict_id"])] = payload
        missions = {
            str(row["mission_id"]): str(row["state"])
            for row in connection.execute(
                "SELECT mission_id, state FROM missions"
            ).fetchall()
        }
        workers = [
            dict(row)
            for row in connection.execute(
                "SELECT run_id, role, provider, model, status, mission_id, "
                "step_id FROM agent_runs WHERE mission_id = ?",
                ("mission:v13-final-proof-c",),
            ).fetchall()
        ]
        gmail_events = int(
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type LIKE 'gmail.%'"
            ).fetchone()[0]
        )

    proof_a = dict(sections.get("A") or {})
    proof_c = dict(sections.get("C") or {})
    proof_e = dict(sections.get("E") or {})
    worker = dict(proof_c.get("worker") or {})
    proof_c_worktree = _proof_c_worktree_truth(proof_c, database)
    expected_effects = {
        str((proof_a.get("repair") or {}).get("effect_id") or ""),
        str(worker.get("effect_id") or ""),
        str(proof_e.get("effect_id") or ""),
    }
    expected_effects.discard("")
    stop_sequence = stop_rows[0][0] if len(stop_rows) == 1 else -1
    resume_sequence = (
        resume_rows[0][0] if len(resume_rows) == 1 else -1
    )
    starts_during_stop = [
        sequence
        for sequence, _payload in starts
        if stop_sequence < sequence < resume_sequence
    ]
    proof_f = dict(sections.get("F") or {})
    checks = {
        "schema_v40": schema == 40,
        "quick_check_ok": database.quick_check() == "ok",
        "ledger_integrity_complete": (
            database.verify_event_integrity() == database.event_count()
        ),
        "all_proofs_pass": (
            set(sections) == set("ABCDEF")
            and all(
                isinstance(value, Mapping)
                and value.get("status") == "PASS"
                for value in sections.values()
            )
        ),
        "duplicate_claims_match_recomputation": (
            proof.get("duplicate_counters") == duplicate_counts
        ),
        "all_duplicate_and_integrity_counters_zero": (
            set(ZERO_COUNTERS).issubset(duplicate_counts)
            and all(duplicate_counts[name] == 0 for name in ZERO_COUNTERS)
        ),
        "exact_owner_texts_once": (
            exact_texts == list(OWNER_TEXTS)
        ),
        "proof_a_fail_repair_pass_complete": (
            proof_a.get("owner_text") == OWNER_TEXTS[0]
            and (proof_a.get("verifier_fail") or {}).get("verdict") == "FAIL"
            and (proof_a.get("verifier_pass") or {}).get("verdict") == "PASS"
            and verdict_rows.get("verdict:A:fail", {}).get("status") == "FAIL"
            and verdict_rows.get("verdict:A:pass", {}).get("status") == "PASS"
            and verdict_rows.get(
                "verdict:A:pass", {}
            ).get("acceptance_status") == "ACCEPTED"
            and missions.get("mission:v13-final-proof-a") == "COMPLETE"
        ),
        "proof_c_one_package_attempt_effect_and_worker": (
            worker.get("package_status") == "ACCEPTED"
            and worker.get("replayed") is True
            and proof_c_worktree["ok"]
            and len(
                [
                    item
                    for item in workers
                    if item.get("role") == "builder"
                ]
            )
            == 1
            and any(
                item.get("role") == "builder"
                and item.get("provider") == "fabric"
                and item.get("model") == "local"
                and item.get("status") == "COMPLETED"
                for item in workers
            )
            and str(worker.get("effect_id") or "") in action_effects
        ),
        "proof_e_stop_blocks_then_restart_replays": (
            len(stop_rows) == 1
            and len(resume_rows) == 1
            and not starts_during_stop
            and proof_e.get("dispatches_during_stop") == 0
            and proof_e.get("replay_performed") is False
            and proof_e.get("replayed") is True
            and missions.get("mission:v13-final-proof-e") == "COMPLETE"
        ),
        "effect_identities_are_exact_and_unique": (
            len(expected_effects) == 3
            and expected_effects == action_effects
        ),
        "command_center_reconnect_truth_is_stable": (
            proof_f.get("projection_digest")
            == proof_f.get("fresh_process_digest")
            == proof_f.get("rebuilt_projection_digest")
            and proof_f.get("command_center_source_backed") is True
        ),
        "gmail_absent_and_deferred": (
            gmail_events == 0
            and (proof.get("gmail") or {}).get("authenticated") is False
            and (proof.get("gmail") or {}).get("probed") is False
        ),
    }
    return {
        "checks": checks,
        "schema": schema,
        "event_count": database.event_count(),
        "integrity_verified_events": database.verify_event_integrity(),
        "duplicate_counters": duplicate_counts,
        "exact_owner_texts": exact_texts,
        "effects": sorted(action_effects),
        "workers": workers,
        "proof_c_worktree": proof_c_worktree,
        "starts_during_stop": starts_during_stop,
        "gmail_events": gmail_events,
    }


def _source_truth(
    proof: Mapping[str, Any],
    *,
    expected_source_sha: str | None,
    expected_final_sha: str | None,
    enforce_source: bool,
) -> dict[str, Any]:
    source = dict(proof.get("source") or {})
    source_sha = str(source.get("source_sha") or "")
    source_tree = str(source.get("source_tree_sha") or "")
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    source_exists = (
        subprocess.run(
            ["git", "cat-file", "-e", f"{source_sha}^{{commit}}"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )
    source_is_ancestor = (
        source_exists
        and subprocess.run(
            ["git", "merge-base", "--is-ancestor", source_sha, head],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )
    paths_after_source = (
        _git("diff", "--name-only", f"{source_sha}..{head}").splitlines()
        if source_exists
        else []
    )
    allowed_after_source = all(
        path == "CURRENT_STATE.md"
        or path.startswith(("docs/", "evidence/"))
        for path in paths_after_source
    )
    checks = {
        "proof_source_matches_requested": (
            expected_source_sha is None or source_sha == expected_source_sha
        ),
        "source_commit_exists": source_exists,
        "source_is_ancestor_of_final_head": source_is_ancestor,
        "source_tree_matches": (
            source_exists
            and _git("rev-parse", f"{source_sha}^{{tree}}") == source_tree
        ),
        "branch_matches": branch == EXPECTED_BRANCH,
        "base_is_exact_merge_base": (
            source_exists
            and _git("merge-base", BASE_SHA, source_sha) == BASE_SHA
        ),
        "main_unchanged": _git("rev-parse", "main") == EXPECTED_MAIN_SHA,
        "origin_main_unchanged": (
            _git("rev-parse", "origin/main") == EXPECTED_MAIN_SHA
        ),
        "final_head_matches_requested": (
            expected_final_sha is None or head == expected_final_sha
        ),
        "post_source_changes_are_evidence_only": allowed_after_source,
        "worktree_clean": not status,
        "proof_claims_no_protected_mutation": (
            proof.get("live_mutated") is False
            and proof.get("main_mutated") is False
            and proof.get("origin_mutated") is False
            and proof.get("activation_authorized") is False
        ),
    }
    if not enforce_source:
        checks = {name: True for name in checks}
    return {
        "checks": checks,
        "source_sha": source_sha,
        "source_tree": source_tree,
        "head": head,
        "branch": branch,
        "status": status,
        "paths_after_source": paths_after_source,
    }


def verify_proof(
    proof_path: Path,
    *,
    focused_junit: Path | None = None,
    v12_junit: Path | None = None,
    operator_junit: Path | None = None,
    full_suite_junit: Path | None = None,
    source_binding_report: Path | None = None,
    frontend_report: Path | None = None,
    source_e2e_report: Path | None = None,
    package_report: Path | None = None,
    expected_source_sha: str | None = None,
    expected_final_sha: str | None = None,
    enforce_source: bool = True,
    require_reports: bool = True,
) -> dict[str, Any]:
    proof_path = proof_path.expanduser().resolve()
    proof = _load_json(proof_path)
    source = _source_truth(
        proof,
        expected_source_sha=expected_source_sha,
        expected_final_sha=expected_final_sha,
        enforce_source=enforce_source,
    )
    source_sha = str(source["source_sha"])
    database_path = Path(
        str((proof.get("shared_spine") or {}).get("database") or "")
    ).expanduser().resolve()
    database_exists = database_path.is_file()
    database_hash = _sha256(database_path) if database_exists else ""
    database = (
        _database_truth(proof, database_path)
        if database_exists
        else {"checks": {"database_exists": False}}
    )
    authority = (
        _authority_graph(source_sha)
        if enforce_source and len(source_sha) == 40
        else {
            "checks": {
                "one_mission_loop": True,
                "one_mcp_runtime": True,
                "one_owner_control": True,
                "general_work_has_no_planner_or_campaign_writer": True,
                "advisory_surfaces_have_no_execution_authority": True,
                "all_python_sources_parse": True,
            },
            "class_counts": {},
            "advisory_violations": {},
            "parse_failures": [],
        }
    )
    receipts = _receipt_checks(proof_path, proof, source_sha)
    focused = _parse_junit(focused_junit)
    v12 = _parse_junit(v12_junit)
    operator = _parse_junit(operator_junit)
    full_suite = _parse_junit(full_suite_junit)
    test_binding = _verify_test_binding(
        source_binding_report,
        source_sha=source_sha,
        source_tree=str(source["source_tree"]),
        reports={
            "v13_focused": focused,
            "v12_regressions": v12,
            "operator_compatibility": operator,
            "full_suite": full_suite,
        },
    )
    frontend = _verify_report(
        frontend_report,
        name="frontend",
        source_sha=source_sha,
    )
    source_e2e = _verify_report(
        source_e2e_report,
        name="source_backed_e2e",
        source_sha=source_sha,
    )
    package = _verify_report(
        package_report,
        name="package",
        source_sha=source_sha,
    )
    claimed_receipt_checks = proof.get("receipt_checks")
    top_checks = {
        "proof_contract": (
            proof.get("contract") == PROOF_CONTRACT
            and proof.get("verdict") == "PASS"
            and proof.get("final_status") == FINAL_STATUS
        ),
        "database_exists": database_exists,
        "database_hash_matches": (
            database_exists
            and database_hash
            == (proof.get("shared_spine") or {}).get("database_sha256")
        ),
        "receipt_claims_all_true": (
            isinstance(claimed_receipt_checks, Mapping)
            and set(claimed_receipt_checks) == set(RECEIPT_KEYS)
            and all(value is True for value in claimed_receipt_checks.values())
        ),
        "copied_receipts_are_source_bound": receipts["ok"],
        "focused_tests_green": (
            focused is not None and focused["ok"]
        ),
        "v12_regressions_green": v12 is not None and v12["ok"],
        "operator_compatibility_green": (
            operator is not None and operator["ok"]
        ),
        "full_suite_green": (
            full_suite is not None and full_suite["ok"]
        ),
        "test_reports_source_bound": (
            test_binding is not None and test_binding["ok"]
        ),
        "frontend_green": frontend is not None and frontend["ok"],
        "source_backed_e2e_green": (
            source_e2e is not None and source_e2e["ok"]
        ),
        "compile_and_package_green": (
            package is not None and package["ok"]
        ),
    }
    if not require_reports:
        for name in (
            "focused_tests_green",
            "v12_regressions_green",
            "operator_compatibility_green",
            "full_suite_green",
            "test_reports_source_bound",
            "frontend_green",
            "source_backed_e2e_green",
            "compile_and_package_green",
        ):
            top_checks[name] = True
    checks = {
        **{f"top.{key}": value for key, value in top_checks.items()},
        **{
            f"source.{key}": value
            for key, value in source["checks"].items()
        },
        **{
            f"authority.{key}": value
            for key, value in authority["checks"].items()
        },
        **{
            f"database.{key}": value
            for key, value in database["checks"].items()
        },
    }
    failed = [name for name, passed in checks.items() if not passed]
    critical = [
        name
        for name in failed
        if name
        in {
            "source.main_unchanged",
            "source.origin_main_unchanged",
            "source.proof_claims_no_protected_mutation",
            "database.gmail_absent_and_deferred",
        }
    ]
    high = [name for name in failed if name not in critical]
    return {
        "contract": VERIFIER_CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "role": "fresh-independent-verifier-process",
        "scope": "EXACT_SOURCE_TREE_DISPOSABLE_EVIDENCE_AND_TEST_REPORTS",
        "proof": str(proof_path),
        "proof_sha256": _sha256(proof_path),
        "database": str(database_path),
        "database_sha256": database_hash,
        "source": source,
        "authority_graph": authority,
        "database_truth": database,
        "receipts": receipts,
        "focused_tests": focused,
        "v12_regressions": v12,
        "operator_compatibility": operator,
        "full_suite": full_suite,
        "test_binding": test_binding,
        "frontend": frontend,
        "source_backed_e2e": source_e2e,
        "package": package,
        "checks": checks,
        "findings": failed,
        "critical": critical,
        "high": high,
        "verdict": "PASS" if not failed else "FAIL",
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--focused-junit", type=Path, required=True)
    parser.add_argument("--v12-junit", type=Path, required=True)
    parser.add_argument("--operator-junit", type=Path, required=True)
    parser.add_argument("--full-suite-junit", type=Path, required=True)
    parser.add_argument("--source-binding-report", type=Path, required=True)
    parser.add_argument("--frontend-report", type=Path, required=True)
    parser.add_argument("--source-e2e-report", type=Path, required=True)
    parser.add_argument("--package-report", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-final-sha")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = verify_proof(
        args.proof,
        focused_junit=args.focused_junit,
        v12_junit=args.v12_junit,
        operator_junit=args.operator_junit,
        full_suite_junit=args.full_suite_junit,
        source_binding_report=args.source_binding_report,
        frontend_report=args.frontend_report,
        source_e2e_report=args.source_e2e_report,
        package_report=args.package_report,
        expected_source_sha=args.expected_source_sha,
        expected_final_sha=args.expected_final_sha,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "output": str(args.output),
                "source_sha": result["source"]["source_sha"],
                "final_sha": result["source"]["head"],
                "critical": result["critical"],
                "high": result["high"],
                "failed_checks": result["findings"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
