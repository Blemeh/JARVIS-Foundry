"""AST import checker for R4 architecture rules."""

from __future__ import annotations

import argparse
import ast
import sys
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, write_json

KERNEL_ROOT = "src/jarvis_foundry/kernel/"
ADAPTER_PREFIXES = (
    "src/jarvis_foundry/connectors/",
    "src/jarvis_foundry/command_center.py",
    "src/jarvis_foundry/voice.py",
    "src/jarvis_foundry/reporting/",
    "src/jarvis_foundry/roles/",
)


def module_of_file(rel_path: str) -> list[str]:
    parts = Path(rel_path).with_suffix("").parts
    src_index = parts.index("src")
    return list(parts[src_index + 1 :])


def imported_modules(rel_path: str, source: str) -> list[str]:
    tree = ast.parse(source, filename=rel_path)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def collect_violations(root: Path) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    src_root = root / "src" / "jarvis_foundry"
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        modules = imported_modules(rel, source)
        if rel.startswith(KERNEL_ROOT):
            for module in modules:
                if module == "jarvis_foundry" or not module.startswith("jarvis_foundry"):
                    continue
                leaf = module.split(".", 2)[2] if len(module.split(".")) > 2 else module
                if leaf in {
                    "connectors",
                    "command_center",
                    "voice",
                    "reporting",
                    "roles",
                }:
                    violations.append(
                        {
                            "rule": "R4-ARCH-01",
                            "kind": "kernel_to_adapter_import",
                            "path": rel,
                            "imported_module": module,
                            "reason": "trusted kernel must not import adapters/connectors/UI/voice",
                        }
                    )
        if rel.startswith(ADAPTER_PREFIXES):
            for module in modules:
                if module.startswith("jarvis_foundry.kernel"):
                    violations.append(
                        {
                            "rule": "R4-ARCH-02",
                            "kind": "adapter_to_kernel_state_import",
                            "path": rel,
                            "imported_module": module,
                            "reason": "adapter must not import kernel internals to mutate state",
                        }
                    )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R4_ARCHITECTURE_RULES.json")
    args = parser.parse_args(argv)
    started = time.monotonic()
    violations = collect_violations(args.root)
    result = {
        "schema": "jarvis-r4-architecture-rules-v1",
        "total_violations": len(violations),
        "violations": violations,
        "wall_seconds": round(time.monotonic() - started, 3),
        "exit_code": 1 if violations else 0,
    }
    if record_enabled():
        write_json(args.output, result)
    for violation in violations:
        print(
            f"{violation['rule']} {violation['kind']}: {violation['path']} "
            f"-> {violation['imported_module']}"
        )
    print(f"architecture violations: {len(violations)}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
