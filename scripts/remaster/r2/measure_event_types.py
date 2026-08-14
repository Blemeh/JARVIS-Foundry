"""Canonical, reproducible event-type census for R2 metrics.

Static event-type counts are method-dependent (AST literal scans, partial
symbolic resolution, and exhaustive resolution give different totals). This
script pins one exact method:
- created: every string literal passed as the event_type argument to
  Event.create(...) in the audited source tree, plus Event(...) constructor
  calls with an event_type literal keyword;
- handled: every literal compared against event.event_type inside
  projections.apply_event (equality chains and in-tuple/set comparisons);
- prefix families: startswith families in apply_event;
- unhandled: created types not handled exactly and not covered by a prefix
  family.

The emitted JSON is the authoritative evidence for R2-F012 and
R2_METRICS.json event_coverage.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MAIN = Path(r"C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry")
SRC = MAIN / "src" / "jarvis_foundry"
OUT = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_EVENT_TYPE_COUNTS.json"


def iter_py(root: Path):
    for path in sorted(root.rglob("*.py")):
        yield path


def created_types() -> set[str]:
    created: set[str] = set()
    for path in iter_py(SRC):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Attribute) and func.attr == "create":
                name = "Event.create"
            elif isinstance(func, ast.Name) and func.id == "Event":
                name = "Event"
            if not name:
                continue
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    created.add(first.value)
            for kw in node.keywords:
                if kw.arg == "event_type" and isinstance(kw.value, ast.Constant):
                    created.add(kw.value.value)
    return created


def handled_and_prefixes() -> tuple[set[str], list[str]]:
    proj = SRC / "projections.py"
    tree = ast.parse(proj.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "apply_event"
    )
    handled: set[str] = set()
    prefixes: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute) and node.left.attr == "event_type":
            for comp in node.comparators:
                if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                    handled.add(comp.value)
                elif isinstance(comp, (ast.Tuple, ast.Set)):
                    for el in comp.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            handled.add(el.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "event_type"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            prefixes.append(node.args[0].value)
    return handled, sorted(set(prefixes))


def main() -> None:
    created = created_types()
    handled, prefixes = handled_and_prefixes()
    unhandled = {
        t
        for t in created
        if t not in handled and not any(t.startswith(p) for p in prefixes)
    }
    result = {
        "schema": "jarvis-r2-event-type-counts-v1",
        "method": (
            "AST literal census: Event.create/Event constructor string literals "
            "for created; equality/in-set literal comparisons in projections "
            "apply_event for handled; startswith families for prefixes"
        ),
        "counts": {
            "created_event_types": len(created),
            "exact_handled_types": len(handled),
            "prefix_families": prefixes,
            "unhandled_count": len(unhandled),
        },
        "unhandled_types": sorted(unhandled),
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"created={len(created)} handled={len(handled)} "
        f"prefixes={len(prefixes)} unhandled={len(unhandled)}"
    )


if __name__ == "__main__":
    main()
