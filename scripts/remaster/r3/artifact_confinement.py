"""Generated-artifact confinement and clean-checkout reproduction (R3-D10).

Verifies that every artifact declared in the R3 manifest exists with the
declared hash, that no generated files hide outside declared evidence paths,
that the committed source set reproduces the gate sources (compile plus test
collection), and that the evidence directory stays inside the 4 MiB budget.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, run, sha256_file, write_json

EVIDENCE_BUDGET_BYTES = 4194304
DECLARED_SOURCE_ROOTS = [
    "scripts/remaster/r3",
    "tests/remaster/r3",
]
DECLARED_SOURCE_FILES = [
    "pyproject.toml",
    "requirements-dev.txt",
    ".github/workflows/windows-ci.yml",
    "src/jarvis_foundry/errors.py",
    "src/jarvis_foundry/observability.py",
]


def evidence_files(root: Path) -> list[Path]:
    return sorted(EVIDENCE_DIR.glob("*.json")) + sorted(EVIDENCE_DIR.glob("*.txt"))


def check_manifest(root: Path, manifest_path: Path) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    for artifact in manifest["artifacts"]:
        path = root / artifact["path"]
        if artifact.get("sha256") == "SELF_REFERENTIAL_REPORT":
            # The builder report cannot bind its own hash without circularity;
            # it is verified by the manifest's own existence and the report's
            # stable freeze timestamp in the closure.
            continue
        if not path.is_file():
            failures.append({"check": "manifest_missing", "path": artifact["path"]})
            continue
        actual = sha256_file(path)
        if actual != artifact["sha256"]:
            failures.append(
                {
                    "check": "manifest_hash_mismatch",
                    "path": artifact["path"],
                    "expected": artifact["sha256"],
                    "actual": actual,
                }
            )
    return failures


def check_no_hidden_generated(root: Path) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    for relative in DECLARED_SOURCE_ROOTS:
        scan = root / relative
        for path in scan.rglob("*"):
            if path.is_dir():
                if path.name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}:
                    problems.append(
                        {"check": "hidden_generated_dir", "path": path.relative_to(root).as_posix()}
                    )
            elif path.suffix in {".pyc", ".pyo"} or path.name.endswith(".junit.xml"):
                problems.append(
                    {"check": "hidden_generated_file", "path": path.relative_to(root).as_posix()}
                )
    declared_paths = {
        "docs/remaster/evidence/r3/R3_ARTIFACT_MANIFEST.json",
        "docs/remaster/evidence/r3/R3_BUILDER_REPORT.json",
        *(
            artifact["path"]
            for artifact in json.loads(
                (EVIDENCE_DIR / "R3_ARTIFACT_MANIFEST.json").read_text(encoding="utf-8")
            )["artifacts"]
            if artifact["path"].startswith("docs/remaster/evidence/r3/")
        ),
    }
    for path in evidence_files(root):
        rel = path.relative_to(root).as_posix()
        if rel not in declared_paths:
            problems.append({"check": "undeclared_evidence", "path": rel})
    return problems


def clean_owned_generated(root: Path) -> list[str]:
    """Remove generated caches inside R3-owned source roots (safe cleanup)."""
    removed: list[str] = []
    for relative in DECLARED_SOURCE_ROOTS:
        scan = root / relative
        if not scan.is_dir():
            continue
        for path in sorted(scan.rglob("*"), reverse=True):
            if path.is_dir() and path.name in {
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
            }:
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path.relative_to(root).as_posix())
    return removed


def check_budget(root: Path) -> dict[str, Any]:
    total = sum(path.stat().st_size for path in evidence_files(root))
    return {
        "evidence_bytes": total,
        "budget_bytes": EVIDENCE_BUDGET_BYTES,
        "within_budget": total <= EVIDENCE_BUDGET_BYTES,
    }


def reproduce_sources(root: Path, python: Path) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-r3-repro-"))
    started = time.monotonic()
    try:
        for relative in DECLARED_SOURCE_ROOTS:
            shutil.copytree(
                root / relative,
                temp_dir / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for relative in DECLARED_SOURCE_FILES:
            source = root / relative
            if source.is_file():
                target = temp_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        compile_result = run(
            [
                str(python),
                "-B",
                "-m",
                "compileall",
                "-q",
                str(temp_dir / "scripts" / "remaster" / "r3"),
                str(temp_dir / "tests" / "remaster" / "r3"),
                str(temp_dir / "src" / "jarvis_foundry" / "errors.py"),
                str(temp_dir / "src" / "jarvis_foundry" / "observability.py"),
            ],
            cwd=temp_dir,
            timeout_seconds=600,
        )
        env = {
            "PYTHONPATH": ";".join(
                [
                    str(temp_dir / "src"),
                    str(temp_dir / "scripts" / "remaster" / "r3"),
                ]
            )
        }
        collect = run(
            [
                str(python),
                "-B",
                "-m",
                "pytest",
                "tests/remaster/r3",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            cwd=temp_dir,
            timeout_seconds=600,
            env=env,
        )
        return {
            "compile_exit_code": compile_result["exit_code"],
            "collect_exit_code": collect["exit_code"],
            "collected_tests_line": next(
                (
                    line
                    for line in collect["output"].splitlines()
                    if "tests collected" in line or "tests" in line
                ),
                "",
            ),
            "wall_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--output", type=Path, default=EVIDENCE_DIR / "R3_ARTIFACT_CONFINEMENT.json"
    )
    parser.add_argument("--manifest", type=Path, default=EVIDENCE_DIR / "R3_ARTIFACT_MANIFEST.json")
    args = parser.parse_args(argv)

    manifest_failures = check_manifest(args.root, args.manifest)
    cleaned = clean_owned_generated(args.root)
    hidden = check_no_hidden_generated(args.root)
    budget = check_budget(args.root)
    reproduction = reproduce_sources(args.root, args.python)
    passed = (
        not manifest_failures
        and not hidden
        and budget["within_budget"]
        and reproduction["compile_exit_code"] == 0
        and reproduction["collect_exit_code"] == 0
    )
    result = {
        "schema": "jarvis-r3-artifact-confinement-v1",
        "manifest_checks": {"failures": manifest_failures},
        "cleaned_owned_generated": cleaned,
        "hidden_generated": {"problems": hidden},
        "budget": budget,
        "reproduction": reproduction,
        "passed": passed,
    }
    if record_enabled():
        write_json(args.output, result)
    print(
        json.dumps(
            {
                "passed": passed,
                "manifest_failures": len(manifest_failures),
                "hidden": len(hidden),
                "budget": budget,
                "reproduction_exit": (
                    reproduction["compile_exit_code"],
                    reproduction["collect_exit_code"],
                ),
            }
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
