"""Strict typing gate and typed public-interface measurement for R4."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, run, sha256_file, write_json


def count_mypy_errors(output: str) -> int:
    return sum(1 for line in output.splitlines() if ": error:" in line)


def function_metrics(tree: ast.Module) -> dict[str, int]:
    total = 0
    with_params = 0
    any_annotated = 0
    fully_annotated = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        total += 1
        params = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        real = [parameter for parameter in params if parameter.arg not in ("self", "cls")]
        if any(parameter.annotation is not None for parameter in real) or node.returns is not None:
            any_annotated += 1
        if not real:
            continue
        with_params += 1
        if all(parameter.annotation is not None for parameter in real) and node.returns is not None:
            fully_annotated += 1
    return {
        "functions_total": total,
        "functions_with_params": with_params,
        "functions_any_annotation": any_annotated,
        "functions_fully_annotated": fully_annotated,
    }


def measure_kernel_ast(root: Path) -> dict[str, int]:
    totals = {
        "functions_total": 0,
        "functions_with_params": 0,
        "functions_any_annotation": 0,
        "functions_fully_annotated": 0,
    }
    for path in (root / "src" / "jarvis_foundry" / "kernel").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        metrics = function_metrics(tree)
        for key in totals:
            totals[key] += metrics[key]
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R4_MYPY.json")
    parser.add_argument(
        "--typed-output", type=Path, default=EVIDENCE_DIR / "R4_TYPED_INTERFACES.json"
    )
    args = parser.parse_args(argv)
    root = args.root
    kernel = [str((root / "src" / "jarvis_foundry" / "kernel").as_posix())]
    result = run(
        [sys.executable, "-m", "mypy", "--strict", *kernel],
        cwd=root,
        timeout_seconds=1800,
    )
    ast_metrics = measure_kernel_ast(root)
    untyped = ast_metrics["functions_with_params"] - ast_metrics["functions_fully_annotated"]
    payload = {
        "schema": "jarvis-r4-mypy-v1",
        "files": kernel,
        "exit_code": result["exit_code"],
        "wall_seconds": result["wall_seconds"],
        "error_count": count_mypy_errors(result["output"]),
    }
    gate_exit = 0 if result["exit_code"] == 0 and untyped == 0 else 1
    typed_payload = {
        "schema": "jarvis-r4-typed-interfaces-v1",
        "exit_code": gate_exit,
        "wall_seconds": result["wall_seconds"],
        **ast_metrics,
        "untyped_parameterized_functions": untyped,
        "all_functions_have_return_annotations": (
            ast_metrics["functions_any_annotation"] == ast_metrics["functions_total"]
        ),
        "100_percent_typed_public_interfaces": (
            untyped == 0
            and ast_metrics["functions_any_annotation"] == ast_metrics["functions_total"]
        ),
    }
    if record_enabled():
        raw = EVIDENCE_DIR / "R4_MYPY_RAW.txt"
        raw.write_text(result["output"], encoding="utf-8")
        payload["raw_output"] = raw.name
        payload["raw_output_sha256"] = sha256_file(raw)
        write_json(args.output, payload)
        write_json(args.typed_output, typed_payload)
    print(
        f"kernel mypy strict: exit={result['exit_code']} errors={payload['error_count']} "
        f"typed_interfaces={typed_payload['100_percent_typed_public_interfaces']} "
        f"wall={result['wall_seconds']}s"
    )
    return 0 if result["exit_code"] == 0 and untyped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
