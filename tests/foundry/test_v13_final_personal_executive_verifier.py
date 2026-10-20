from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path

from scripts import v13_final_personal_executive_convergence_proof as proof
from scripts import v13_final_personal_executive_verifier as verifier
from scripts.v13_final_personal_executive_verifier import (
    RECEIPT_KEYS,
    verify_proof,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event

from test_v13_final_personal_executive_convergence import _receipts


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _proof_fixture(tmp_path: Path) -> Path:
    source_sha = "1" * 40
    spine = proof._run_spine_proof(
        tmp_path / "runtime",
        _receipts(),
    )
    output = tmp_path / "output"
    receipt_root = output / "receipts"
    receipt_root.mkdir(parents=True)
    receipt_identities = {}
    for key in RECEIPT_KEYS:
        path = receipt_root / f"{key}.json"
        path.write_text(
            json.dumps({"source_sha": source_sha}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        receipt_identities[key] = {
            "path": str(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    report = proof.build_report(
        source={
            "source_sha": source_sha,
            "source_tree_sha": "2" * 40,
        },
        spine=spine,
        receipt_checks={key: True for key in RECEIPT_KEYS},
        copied_receipts=receipt_identities,
    )
    report_path = output / "proof.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


def test_final_verifier_recomputes_database_truth_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    proof_path = _proof_fixture(tmp_path)

    verified = verify_proof(
        proof_path,
        enforce_source=False,
        require_reports=False,
    )

    assert verified["verdict"] == "PASS"
    assert verified["critical"] == []
    assert verified["high"] == []
    assert all(verified["checks"].values())
    assert len(verified["database_truth"]["effects"]) == 3
    assert verified["database_truth"]["starts_during_stop"] == []

    tampered = json.loads(proof_path.read_text(encoding="utf-8"))
    tampered["duplicate_counters"]["effects"] = 1
    tampered_path = proof_path.with_name("proof-tampered.json")
    tampered_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    rejected = verify_proof(
        tampered_path,
        enforce_source=False,
        require_reports=False,
    )

    assert rejected["verdict"] == "FAIL"
    assert (
        rejected["checks"][
            "database.duplicate_claims_match_recomputation"
        ]
        is False
    )
    assert "database.duplicate_claims_match_recomputation" in rejected["high"]

    package_tampered = json.loads(proof_path.read_text(encoding="utf-8"))
    package_tampered["proofs"]["C"]["worker"]["package_hash"] = "0" * 64
    package_tampered_path = proof_path.with_name(
        "proof-package-tampered.json"
    )
    package_tampered_path.write_text(
        json.dumps(package_tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    package_rejected = verify_proof(
        package_tampered_path,
        enforce_source=False,
        require_reports=False,
    )

    assert package_rejected["verdict"] == "FAIL"
    assert (
        package_rejected["database_truth"]["proof_c_worktree"]["checks"][
            "package_hash_recomputed"
        ]
        is False
    )
    assert (
        package_rejected["checks"][
            "database.proof_c_one_package_attempt_effect_and_worker"
        ]
        is False
    )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_test_report_binding_rejects_an_unbound_junit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_sha = _git("rev-parse", "HEAD")
    source_tree = _git("rev-parse", "HEAD^{tree}")
    report_paths = {
        name: tmp_path / f"{name}.xml"
        for name in (
            "v13_focused",
            "v12_regressions",
            "operator_compatibility",
            "full_suite",
        )
    }
    for name, path in report_paths.items():
        path.write_text(f"<testsuites name={json.dumps(name)}/>\n")

    v12_classes = {
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
    v12_classes.update(
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
    operator_classes = {
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
    reports = {
        "v13_focused": {
            "path": str(report_paths["v13_focused"]),
            "sha256": _sha256(report_paths["v13_focused"]),
            "tests": 549,
            "failures": 0,
            "errors": 0,
            "skipped": 1,
            "classnames": ["tests.foundry.test_v13_example"],
        },
        "v12_regressions": {
            "path": str(report_paths["v12_regressions"]),
            "sha256": _sha256(report_paths["v12_regressions"]),
            "tests": 262,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "classnames": sorted(v12_classes),
        },
        "operator_compatibility": {
            "path": str(report_paths["operator_compatibility"]),
            "sha256": _sha256(report_paths["operator_compatibility"]),
            "tests": 70,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "classnames": sorted(operator_classes),
        },
        "full_suite": {
            "path": str(report_paths["full_suite"]),
            "sha256": _sha256(report_paths["full_suite"]),
            "tests": 3407,
            "testcase_count": 3407,
            "failures": 0,
            "errors": 0,
            "skipped": 2,
            "classnames": ["tests.foundry", "tests.convergence"],
        },
    }
    binding_path = tmp_path / "source-binding.json"
    verification_repository = tmp_path / "verification-repository"
    verification_repository.mkdir()
    verification_commit = "d" * 40

    def fake_git_in(
        _repository: Path,
        *args: str,
        check: bool = True,
    ) -> str:
        del check
        if args == ("rev-parse", "HEAD"):
            return verification_commit
        if args == ("rev-parse", f"{verification_commit}^"):
            return source_sha
        if args == ("rev-parse", f"{source_sha}^{{tree}}"):
            return source_tree
        if args == (
            "diff",
            "--name-only",
            f"{source_sha}..{verification_commit}",
        ):
            return "docs/CURRENT_STATE.md"
        if args == (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(verifier, "_git_in", fake_git_in)
    binding = {
        "contract": verifier.TEST_BINDING_CONTRACT,
        "source_sha": source_sha,
        "source_tree_sha": source_tree,
        "junit": {
            name: {
                "path": path.name,
                "sha256": reports[name]["sha256"],
                "source_sha": source_sha,
                "source_tree_sha": source_tree,
                "tests": reports[name]["tests"],
                "failures": reports[name]["failures"],
                "errors": reports[name]["errors"],
                "skipped": reports[name]["skipped"],
                "serialized": True,
                "command": f"pytest {name}",
            }
            for name, path in report_paths.items()
        },
        "source_verification": {
            "repository": str(verification_repository),
            "commit": verification_commit,
            "parent": source_sha,
            "parent_tree": source_tree,
            "changed_paths_from_source": ["docs/CURRENT_STATE.md"],
            "worktree_clean": True,
        },
    }
    binding_path.write_text(
        json.dumps(binding, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    accepted = verifier._verify_test_binding(
        binding_path,
        source_sha=source_sha,
        source_tree=source_tree,
        reports=reports,
    )

    assert accepted is not None
    assert accepted["ok"] is True

    binding["junit"]["v13_focused"]["sha256"] = "0" * 64
    binding_path.write_text(
        json.dumps(binding, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rejected = verifier._verify_test_binding(
        binding_path,
        source_sha=source_sha,
        source_tree=source_tree,
        reports=reports,
    )

    assert rejected is not None
    assert rejected["ok"] is False
    assert rejected["items"]["v13_focused"]["pass"] is False


def test_frontend_truth_parses_vitest_instead_of_trusting_checks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tree = "a" * 40
    ui_tree = "b" * 40

    def fake_git(*args: str, check: bool = True) -> str:
        del check
        return ui_tree if args[-1].endswith(":ui/command-center") else tree

    monkeypatch.setattr(verifier, "_git", fake_git)
    vitest_path = tmp_path / "vitest.json"
    vitest = {
        "success": True,
        "numTotalTests": 2,
        "numPassedTests": 2,
        "numFailedTests": 0,
        "numPendingTests": 0,
        "testResults": [
            {
                "assertionResults": [
                    {"status": "passed"},
                    {"status": "passed"},
                ]
            }
        ],
    }
    vitest_path.write_text(json.dumps(vitest), encoding="utf-8")
    artifacts = [
        {"kind": "vitest_json", "path": str(vitest_path), "pass": True},
        {"kind": "typecheck_log", "path": "typecheck.log", "pass": True},
        {"kind": "lint_log", "path": "lint.log", "pass": True},
        {"kind": "build_log", "path": "build.log", "pass": True},
        {"kind": "dist_html", "path": "index.html", "pass": True},
        {"kind": "dist_javascript", "path": "index.js", "pass": True},
        {"kind": "dist_css", "path": "index.css", "pass": True},
    ]
    report = {
        "source_tree_sha": tree,
        "ui_tree_sha": ui_tree,
        "tests": 2,
        "commands": {
            "unit_tests": {
                "command": "vitest run --reporter=json",
                "exit_code": 0,
            },
            "typecheck": {
                "command": "tsc -b --noEmit",
                "exit_code": 0,
            },
            "lint": {
                "command": "eslint src e2e --max-warnings 0",
                "exit_code": 0,
            },
            "production_build": {
                "command": "tsc -b && vite build",
                "exit_code": 0,
            },
        },
    }

    accepted = verifier._frontend_report_truth(
        report,
        artifacts=artifacts,
        source_sha="1" * 40,
    )
    assert accepted["ok"] is True

    vitest["success"] = False
    vitest_path.write_text(json.dumps(vitest), encoding="utf-8")
    rejected = verifier._frontend_report_truth(
        report,
        artifacts=artifacts,
        source_sha="1" * 40,
    )
    assert rejected["ok"] is False
    assert rejected["checks"]["vitest_success"] is False


def test_package_truth_matches_built_archives_to_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_bytes = {
        path: f"{path}\n".encode()
        for path in verifier.PACKAGE_SOURCE_MEMBERS
    }

    def fake_blob(_source_sha: str, path: str) -> str:
        return hashlib.sha256(source_bytes[path]).hexdigest()

    def fake_git(*args: str, check: bool = True) -> str:
        del args, check
        return "c" * 40

    monkeypatch.setattr(verifier, "_source_blob_sha", fake_blob)
    monkeypatch.setattr(verifier, "_git", fake_git)
    wheel = tmp_path / "jarvis_foundry-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as bundle:
        for path, content in source_bytes.items():
            bundle.writestr(path.removeprefix("src/"), content)
    sdist = tmp_path / "jarvis_foundry-0.1.0.tar.gz"
    staging = tmp_path / "staging" / "jarvis_foundry-0.1.0"
    for path, content in source_bytes.items():
        target = staging / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    with tarfile.open(sdist, "w:gz") as bundle:
        bundle.add(staging, arcname=staging.name)
    lock = tmp_path / "lock.log"
    lock.write_text("Resolved 69 packages\n", encoding="utf-8")
    build = tmp_path / "build.log"
    build.write_text(
        f"Successfully built {sdist}\nSuccessfully built {wheel}\n",
        encoding="utf-8",
    )
    install = tmp_path / "install.log"
    install.write_text(
        json.dumps({"schema": 41, "imports": "PASS"}) + "\n",
        encoding="utf-8",
    )
    artifacts = [
        {"kind": "uv_lock_log", "path": str(lock), "pass": True},
        {"kind": "compileall_log", "path": "compile.log", "pass": True},
        {"kind": "diff_check_log", "path": "diff.log", "pass": True},
        {"kind": "build_log", "path": str(build), "pass": True},
        {"kind": "install_log", "path": str(install), "pass": True},
        {"kind": "wheel", "path": str(wheel), "pass": True},
        {"kind": "sdist", "path": str(sdist), "pass": True},
    ]
    report = {
        "source_tree_sha": "c" * 40,
        "commands": {
            "uv_lock_check": {
                "command": "uv lock --check",
                "exit_code": 0,
            },
            "compileall": {
                "command": "python -m compileall -q src scripts",
                "exit_code": 0,
            },
            "full_candidate_diff_check": {
                "command": (
                    "git diff --check "
                    f"{verifier.BASE_SHA}..{'1' * 40}"
                ),
                "exit_code": 0,
            },
            "build": {
                "command": "uv build --out-dir dist",
                "exit_code": 0,
            },
            "isolated_wheel_install": {
                "command": "uv pip install --python verify wheel",
                "exit_code": 0,
            },
        },
    }

    accepted = verifier._package_report_truth(
        report,
        artifacts=artifacts,
        source_sha="1" * 40,
    )
    assert accepted["ok"] is True

    install.write_text(
        json.dumps({"schema": 39, "imports": "PASS"}) + "\n",
        encoding="utf-8",
    )
    rejected = verifier._package_report_truth(
        report,
        artifacts=artifacts,
        source_sha="1" * 40,
    )
    assert rejected["ok"] is False
    assert rejected["checks"]["isolated_import_truth"] is False


def test_source_e2e_truth_reads_browser_semantics_and_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tree = "e" * 40
    ui_tree = "f" * 40

    def fake_git(*args: str, check: bool = True) -> str:
        del check
        return ui_tree if args[-1].endswith(":ui/command-center") else tree

    source_blob = "9" * 64
    monkeypatch.setattr(verifier, "_git", fake_git)
    monkeypatch.setattr(
        verifier,
        "_source_blob_sha",
        lambda _source_sha, _path: source_blob,
    )
    database_path = tmp_path / "autonomy.db"
    database = FoundryDatabase(database_path)
    database.initialize()
    for index, event_type in enumerate(sorted(verifier.AUTONOMY_EVENTS)):
        payload = {"status": "PASS"}
        if event_type == "system.action_started":
            payload.update(
                {
                    "action_key": "effect:source-e2e",
                    "target_ref": "artifact:source-e2e",
                    "system_id": "fixture",
                    "kind": "file.write",
                }
            )
        elif event_type == "system.action_completed":
            payload["action_key"] = "effect:source-e2e"
        elif event_type == "owner_directive.recorded":
            payload.update(
                {
                    "directive_id": "directive:source-e2e",
                    "command_type": "STOP_EVERYTHING",
                    "target": "GLOBAL",
                    "exact_text": "STOP",
                }
            )
        elif event_type == "owner_directive.revoked":
            payload.update(
                {
                    "directive_id": "directive:source-e2e",
                    "command_type": "RESUME",
                }
            )
        elif event_type in {"verification.failed", "verification.passed"}:
            payload["verdict_id"] = "verdict:source-e2e"
        database.append(
            Event.create(
                event_type,
                payload,
                source_type="supervisor",
                source_id="source-e2e-test",
                idempotency_key=f"source-e2e:{index}:{event_type}",
            )
        )
    autonomy_path = tmp_path / "autonomy-proof.json"
    autonomy = {
        "schema": "v13.command-center-product-proof.v1",
        "source": "disposable_foundry_database",
        "database": str(database_path),
        "mission_state": "COMPLETE",
        "verification_statuses": ["FAIL", "PASS"],
        "evidence_bundles": ["evidence:1"],
        "memory_ids": ["memory:1"],
        "event_types": sorted(verifier.AUTONOMY_EVENTS),
        "checks": {name: True for name in verifier.AUTONOMY_CHECKS},
        "all_checks_pass": True,
        "adversarial_coverage": {
            "worker_false_claim": "PASS",
            "failed_verifier": "PASS",
            "repair_retry": "PASS",
            "provider_failure": "PASS",
            "crash_restart_resume": "PASS",
        },
    }
    autonomy_path.write_text(json.dumps(autonomy), encoding="utf-8")
    artifacts = [
        {
            "kind": "autonomy_proof",
            "path": str(autonomy_path),
            "pass": True,
        },
        {
            "kind": "autonomy_database",
            "path": str(database_path),
            "pass": True,
        },
        {"kind": "screenshot_overview", "path": "one.png", "pass": True},
        {"kind": "screenshot_work", "path": "two.png", "pass": True},
        {"kind": "screenshot_mission", "path": "three.png", "pass": True},
    ]
    report = {
        "source_tree_sha": tree,
        "ui_tree_sha": ui_tree,
        "tests": 3,
        "commands": {
            "playwright_live": {
                "command": "node scripts/run-playwright-live.mjs",
                "exit_code": 0,
            },
        },
        "source_blobs": {
            path: source_blob
            for path in {
                "scripts/lane3_autonomy_fixture_server.py",
                "ui/command-center/e2e/live-autonomy-proof.spec.ts",
                "ui/command-center/scripts/run-playwright-live.mjs",
            }
        },
    }

    accepted = verifier._source_e2e_report_truth(
        report,
        artifacts=artifacts,
        source_sha="1" * 40,
    )
    assert accepted["ok"] is True

    autonomy["checks"]["stop_resume"] = False
    autonomy_path.write_text(json.dumps(autonomy), encoding="utf-8")
    rejected = verifier._source_e2e_report_truth(
        report,
        artifacts=artifacts,
        source_sha="1" * 40,
    )
    assert rejected["ok"] is False
    assert rejected["checks"]["autonomy_checks_recomputed"] is False
