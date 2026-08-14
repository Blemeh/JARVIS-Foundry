"""AST import checker for remaster architecture rules (R3-D08).

Rule 1: trusted kernel modules must not import adapters, connectors, the
command center, voice, or reporting.
Rule 2: adapter modules must not import canonical kernel state modules that
would let them mutate state directly.

Violations are recorded as findings with exact paths; the gate exits nonzero
when any violation exists.
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
ADAPTER_PREFIXES = (
    "src/jarvis_foundry/connectors/",
    "src/jarvis_foundry/command_center.py",
    "src/jarvis_foundry/voice.py",
    "src/jarvis_foundry/reporting/",
    "src/jarvis_foundry/roles/",
)
KERNEL_STATE_MODULES = {
    "jarvis_foundry.database",
    "jarvis_foundry.events",
    "jarvis_foundry.projections",
    "jarvis_foundry.effects",
    "jarvis_foundry.owner_controls",
    "jarvis_foundry.migrations",
    "jarvis_foundry.supervisor.estop",
    "jarvis_foundry.supervisor.leases",
    "jarvis_foundry.supervisor.controls",
    "jarvis_foundry.control.broker",
    "jarvis_foundry.control.policy",
    "jarvis_foundry.control.capability_broker",
    "jarvis_foundry.control.action_envelope",
    "jarvis_foundry.control.registration",
}


def is_kernel(rel_path: str) -> bool:
    if rel_path in KERNEL_FILES:
        return True
    return any(rel_path.startswith(prefix) for prefix in KERNEL_DIRS)


def is_adapter(rel_path: str) -> bool:
    return any(rel_path.startswith(prefix) for prefix in ADAPTER_PREFIXES)


def module_of_file(rel_path: str) -> list[str]:
    parts = Path(rel_path).with_suffix("").parts
    src_index = parts.index("src")
    return list(parts[src_index + 1 :])


def resolve_import(rel_path: str, node: ast.ImportFrom) -> str | None:
    module_parts = module_of_file(rel_path)
    package_parts = module_parts[:-1]
    level = node.level or 0
    if level:
        if level >= len(package_parts):
            base: list[str] = []
        else:
            base = package_parts[: len(package_parts) - level + 1]
        if node.module:
            base = base + node.module.split(".")
        return ".".join(base) if base else None
    return node.module


def imported_modules(rel_path: str, source: str) -> list[str]:
    tree = ast.parse(source, filename=rel_path)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    modules.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            resolved = resolve_import(rel_path, node)
            if resolved:
                modules.append(resolved)
    return modules


def collect_violations(root: Path) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    src_root = root / "src" / "jarvis_foundry"
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        modules = imported_modules(rel, source)
        if is_kernel(rel):
            for module in modules:
                if any(
                    module.startswith(f"jarvis_foundry.{prefix}")
                    for prefix in (
                        "connectors.",
                        "command_center",
                        "voice",
                        "reporting.",
                        "roles.",
                    )
                ) or module in {
                    "jarvis_foundry.connectors",
                    "jarvis_foundry.command_center",
                    "jarvis_foundry.voice",
                    "jarvis_foundry.reporting",
                    "jarvis_foundry.roles",
                }:
                    violations.append(
                        {
                            "rule": "R3-ARCH-01",
                            "kind": "kernel_to_adapter_import",
                            "path": rel,
                            "imported_module": module,
                            "reason": "trusted kernel must not import adapters/connectors/UI",
                        }
                    )
        if is_adapter(rel):
            kernel_state_leaf_names = {part.split(".")[-1] for part in KERNEL_STATE_MODULES}
            for module in modules:
                is_kernel_state = module in KERNEL_STATE_MODULES
                is_kernel_state_leaf = (
                    module.startswith("jarvis_foundry.")
                    and len(module.split(".")) >= 2
                    and module.split(".")[-1] in kernel_state_leaf_names
                )
                if is_kernel_state or is_kernel_state_leaf:
                    violations.append(
                        {
                            "rule": "R3-ARCH-02",
                            "kind": "adapter_to_kernel_state_import",
                            "path": rel,
                            "imported_module": module,
                            "reason": "adapter must not import kernel internals that mutate canonical state",
                        }
                    )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R3_ARCHITECTURE_RULES.json")
    args = parser.parse_args(argv)
    started = time.monotonic()
    violations = collect_violations(args.root)
    result = {
        "schema": "jarvis-r3-architecture-rules-v1",
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
