"""AST scan for broad exception handlers in the R4 trusted kernel."""

from __future__ import annotations

import argparse
import ast
import sys
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, write_json


def broad_handler(node: ast.ExceptHandler) -> str | None:
    if node.type is None:
        return "bare_except"
    name = ast.unparse(node.type)
    if name in {"BaseException", "Exception"}:
        return name
    return None


def scan_kernel(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    kernel_root = root / "src" / "jarvis_foundry" / "kernel"
    for path in sorted(kernel_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            kind = broad_handler(node)
            if kind is None:
                continue
            findings.append(
                {
                    "path": rel,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "handler": kind,
                    "severity": "S2",
                    "reason": "broad exception handler can swallow failure detail",
                }
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R4_NO_SILENT_FAILURE.json")
    args = parser.parse_args(argv)
    started = time.monotonic()
    findings = scan_kernel(args.root)
    result = {
        "schema": "jarvis-r4-no-silent-failure-v1",
        "total_findings": len(findings),
        "findings": findings,
        "wall_seconds": round(time.monotonic() - started, 3),
        "exit_code": 1 if findings else 0,
    }
    if record_enabled():
        write_json(args.output, result)
    print(f"no-silent-failure findings: {len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
