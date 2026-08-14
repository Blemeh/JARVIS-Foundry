"""AST scan for broad exception handlers in the trusted kernel (R3-D11).

Broad ``except BaseException`` / ``except Exception`` / bare ``except``
handlers in the trusted kernel are recorded as no-silent-failure findings.
Existing source is never edited in R3; findings are repaired in R4.
"""

from __future__ import annotations

import argparse
import ast
import sys
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, write_json

KERNEL_FILES = {
    "src/jarvis_foundry/events.py",
    "src/jarvis_foundry/projections.py",
    "src/jarvis_foundry/database.py",
    "src/jarvis_foundry/owner_controls.py",
    "src/jarvis_foundry/effects.py",
    "src/jarvis_foundry/errors.py",
    "src/jarvis_foundry/observability.py",
}
KERNEL_DIRS = (
    "src/jarvis_foundry/control/",
    "src/jarvis_foundry/supervisor/estop.py",
    "src/jarvis_foundry/supervisor/leases.py",
    "src/jarvis_foundry/supervisor/controls.py",
)


def is_kernel(rel_path: str) -> bool:
    return rel_path in KERNEL_FILES or any(rel_path.startswith(prefix) for prefix in KERNEL_DIRS)


def broad_handler(node: ast.ExceptHandler) -> str | None:
    if node.type is None:
        return "bare_except"
    name = ast.unparse(node.type)
    if name in {"BaseException", "Exception"}:
        return name
    return None


def scan_file(rel_path: str, source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tree = ast.parse(source, filename=rel_path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        kind = broad_handler(node)
        if kind is None:
            continue
        findings.append(
            {
                "path": rel_path,
                "line": node.lineno,
                "end_line": node.end_lineno,
                "handler": kind,
                "severity": "S2",
                "reason": "broad exception handler can swallow failure detail",
                "disposition": "REPAIR_IN_R4",
            }
        )
    return findings


def scan_kernel(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    src_root = root / "src" / "jarvis_foundry"
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if not is_kernel(rel):
            continue
        findings.extend(scan_file(rel, path.read_text(encoding="utf-8")))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R3_NO_SILENT_FAILURE.json")
    args = parser.parse_args(argv)
    started = time.monotonic()
    findings = scan_kernel(args.root)
    result = {
        "schema": "jarvis-r3-no-silent-failure-v1",
        "total_findings": len(findings),
        "findings": findings,
        "wall_seconds": round(time.monotonic() - started, 3),
        "exit_code": 1 if findings else 0,
    }
    if record_enabled():
        write_json(args.output, result)
    for finding in findings:
        print(f"{finding['handler']} at {finding['path']}:{finding['line']} ({finding['reason']})")
    print(f"no-silent-failure findings: {len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
