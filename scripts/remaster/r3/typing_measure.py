"""Strict typing measurement and owned-file gate (R3-D03/C03).

``--owned`` runs mypy strict on files created by R3 and exits nonzero on
errors. ``--baseline`` measures the legacy whole-repo and trusted-kernel
typing state honestly (mypy strict error counts plus AST annotation
coverage) and records the exact numbers as evidence; legacy errors are
REPAIR_IN_R4 findings and strictness is never lowered.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, run, sha256_file, write_json

TRUSTED_KERNEL_FILES = [
    "src/jarvis_foundry/events.py",
    "src/jarvis_foundry/projections.py",
    "src/jarvis_foundry/database.py",
    "src/jarvis_foundry/owner_controls.py",
    "src/jarvis_foundry/effects.py",
    "src/jarvis_foundry/errors.py",
    "src/jarvis_foundry/observability.py",
    "src/jarvis_foundry/control/action_envelope.py",
    "src/jarvis_foundry/control/broker.py",
    "src/jarvis_foundry/control/capability_broker.py",
    "src/jarvis_foundry/control/policy.py",
    "src/jarvis_foundry/control/registration.py",
    "src/jarvis_foundry/supervisor/estop.py",
    "src/jarvis_foundry/supervisor/leases.py",
    "src/jarvis_foundry/supervisor/controls.py",
]


def owned_files(root: Path) -> list[str]:
    scripts = sorted((root / "scripts" / "remaster" / "r3").glob("*.py"))
    tests = sorted((root / "tests" / "remaster" / "r3").glob("*.py"))
    return [
        "src/jarvis_foundry/errors.py",
        "src/jarvis_foundry/observability.py",
        *[str(path.relative_to(root).as_posix()) for path in scripts],
        *[str(path.relative_to(root).as_posix()) for path in tests],
    ]


def count_mypy_errors(output: str) -> int:
    return sum(1 for line in output.splitlines() if ": error:" in line)


def run_mypy(root: Path, files: list[str], label: str, record: bool) -> dict[str, Any]:
    result = run(
        [sys.executable, "-m", "mypy", "--strict", *files],
        cwd=root,
        timeout_seconds=1800,
    )
    payload: dict[str, Any] = {
        "label": label,
        "files": files,
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "error_count": count_mypy_errors(result["output"]),
    }
    if record:
        raw_name = f"R3_MYPY_{label.upper()}_RAW.txt"
        raw = EVIDENCE_DIR / raw_name
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw_name
        payload["raw_output_sha256"] = sha256_file(raw)
    return payload


def function_metrics(tree: ast.Module) -> tuple[int, int, int, int]:
    total = 0
    with_params = 0
    any_annotated = 0
    fully_annotated = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        total += 1
        params = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        real = [p for p in params if p.arg not in ("self", "cls")]
        if any(p.annotation is not None for p in real) or node.returns is not None:
            any_annotated += 1
        if not real:
            continue
        with_params += 1
        if all(p.annotation is not None for p in real) and node.returns is not None:
            fully_annotated += 1
    return total, with_params, any_annotated, fully_annotated


def measure_ast(root: Path, label: str, file_paths: list[str] | None = None) -> dict[str, Any]:
    totals = {
        "functions_total": 0,
        "functions_with_params": 0,
        "functions_any_annotation": 0,
        "functions_all_params_and_return_annotated": 0,
    }
    counted_files = 0
    if file_paths:
        scan_paths = [root / path for path in file_paths]
    else:
        scan_roots = [
            root / "src" / "jarvis_foundry",
            root / "tests" / "foundry",
            root / "tests" / "acceptance",
        ]
        scan_paths = [
            path
            for scan_root in scan_roots
            if scan_root.is_dir()
            for path in scan_root.rglob("*.py")
            if "__pycache__" not in path.parts
        ]
    for path in scan_paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        counted_files += 1
        total, with_params, any_annotated, fully_annotated = function_metrics(tree)
        totals["functions_total"] += total
        totals["functions_with_params"] += with_params
        totals["functions_any_annotation"] += any_annotated
        totals["functions_all_params_and_return_annotated"] += fully_annotated
    return {
        "scope": label,
        "python_files": counted_files,
        **totals,
        "untyped_with_params": totals["functions_with_params"] - totals["functions_any_annotation"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("owned", "baseline"), required=True)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.root
    if args.output is None:
        args.output = EVIDENCE_DIR / (
            "R3_TYPING_OWNED.json" if args.mode == "owned" else "R3_TYPING_BASELINE.json"
        )

    if args.mode == "owned":
        files = owned_files(root)
        result = run_mypy(root, files, "owned", record=record_enabled())
        if record_enabled():
            write_json(args.output, {"schema": "jarvis-r3-typing-owned-v1", **result})
        print(
            f"owned mypy strict: exit={result['exit_code']} errors="
            f"{result['error_count']} wall={result['wall_seconds']}s"
        )
        return 0 if result["exit_code"] == 0 else 1

    kernel_result = run_mypy(root, TRUSTED_KERNEL_FILES, "trusted_kernel", record=record_enabled())
    repo_result = run_mypy(root, ["src/jarvis_foundry"], "whole_repo", record=record_enabled())
    ast_whole = measure_ast(root, "whole_repo")
    ast_kernel = measure_ast(root, "trusted_kernel", file_paths=TRUSTED_KERNEL_FILES)
    payload = {
        "schema": "jarvis-r3-typing-baseline-v1",
        "trusted_kernel": {
            **kernel_result,
            "ast": ast_kernel,
            "100_percent_typed_public_interfaces": ast_kernel["untyped_with_params"] == 0,
        },
        "whole_repo": {**repo_result, "ast": ast_whole},
        "r2_comparison": {
            "r2_reported": "1440/1701 parameterized functions annotated",
            "current_any_annotation": (
                f"{ast_whole['functions_any_annotation']}/{ast_whole['functions_with_params']}"
            ),
            "current_all_params_and_return_annotated": (
                f"{ast_whole['functions_all_params_and_return_annotated']}/"
                f"{ast_whole['functions_with_params']}"
            ),
        },
        "finding": {
            "id": "R3-F-TYPING",
            "severity": "S2",
            "disposition": "REPAIR_IN_R4",
            "title": "Trusted kernel is not 100% typed under mypy strict",
            "evidence": (
                f"trusted_kernel mypy strict errors={kernel_result['error_count']}; "
                f"whole_repo mypy strict errors={repo_result['error_count']}; "
                f"whole-repo AST any-annotated="
                f"{ast_whole['functions_any_annotation']}/"
                f"{ast_whole['functions_with_params']}; fully annotated="
                f"{ast_whole['functions_all_params_and_return_annotated']}/"
                f"{ast_whole['functions_with_params']}"
            ),
        },
    }
    if record_enabled():
        write_json(args.output, payload)
    print(
        "baseline: "
        f"kernel_errors={kernel_result['error_count']} "
        f"repo_errors={repo_result['error_count']} "
        f"ast_annotated={ast_whole['functions_any_annotation']}/"
        f"{ast_whole['functions_with_params']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
