"""Classify every structured line range in R2_FORENSIC_FINDINGS.json.

Each range becomes exactly one of:
- "node": the range starts at an AST function/class node start and must end at
  that node's end_lineno (verified by audit_r2_spans.py against the baseline);
- "line": an explicit line-level citation (exception handler, PRAGMA, SQL
  block, docstring, module constant, call-site line pair, etc.) whose start is
  not an AST node start; audit_r2_spans.py verifies file bounds and start<=end
  and requires the prose to cite the same range.

This makes the evidence contract explicit and closes the auditor blind spot
that let R2-F028's systems.py:53-56 pass unchecked.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MAIN = Path(r"C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry")
FINDINGS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_FORENSIC_FINDINGS.json"


def node_starts(rel: str) -> dict[int, int]:
    if not rel.endswith(".py"):
        return {}
    full = MAIN / rel
    if not full.is_file():
        return {}
    tree = ast.parse(full.read_text(encoding="utf-8"))
    starts: dict[int, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            starts[node.lineno] = node.end_lineno or node.lineno
    return starts


def main() -> None:
    data = json.loads(FINDINGS.read_text(encoding="utf-8"))
    classified = 0
    node_kind = 0
    line_kind = 0
    for entry in data["findings"]:
        for p in entry.get("paths", []):
            starts = node_starts(p["path"])
            if not p.get("line_ranges"):
                continue
            converted: list[dict] = []
            for r in p["line_ranges"]:
                if isinstance(r, dict):
                    start, end = r["start"], r["end"]
                else:
                    start, end = r[0], r[1]
                if start in starts:
                    r_kind = "node"
                    node_kind += 1
                else:
                    r_kind = "line"
                    line_kind += 1
                converted.append({"start": start, "end": end, "kind": r_kind})
                classified += 1
            p["line_ranges"] = converted
    FINDINGS.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"classified={classified} node={node_kind} line={line_kind}")


if __name__ == "__main__":
    main()
