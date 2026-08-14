"""Canonical AST-exact span audit for R2_FORENSIC_FINDINGS.json.

Every line range cited in the findings is checked against the AST spans of
functions/classes in the audited baseline. Ranges that start exactly at an AST
node start must end exactly at that node's end_lineno (the AST-exact standard
used for the corrected spans). The script prints OK or MISMATCH and returns
exit code 1 when any mismatch exists.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MAIN = Path(r"C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry")
FINDINGS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_FORENSIC_FINDINGS.json"

PROSE_RANGE = re.compile(r"([\w./-]+\.py):(\d+)(?:-(\d+))?")

# Tokens that must never appear in findings or summary prose.
STALE_TOKENS = [
    "130-141", "48-119", "48-167", "281-318", "91-99", "75-79", "28-31",
    "72-89", "574-585", "573-585", "44-68", "73-81", "343 match", "95 exact",
    "67-99", "344 substring matches, word-boundary 311", "word-boundary method: 311",
    "91 event_type equality", "1729-1739", "58-64", "160-190", "548-552",
    "117-124", "40-42", "143-148", "306-354", "269-302", "portfolio.* (8)",
    "connector.* (4)", "correlation.* (2)",
]


def node_spans(rel: str) -> list[tuple[str, int, int]]:
    if not rel.endswith(".py"):
        return []
    full = MAIN / rel
    if not full.is_file():
        return []
    tree = ast.parse(full.read_text(encoding="utf-8"))
    spans: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            spans.append((node.name, node.lineno, node.end_lineno or node.lineno))
    return spans


def line_count(rel: str) -> int:
    full = MAIN / rel
    if not full.is_file():
        return 0
    return len(full.read_text(encoding="utf-8").splitlines())


def prose_checks(entry: dict) -> list[str]:
    """Verify file.py:start-end mentions in prose against AST node spans."""
    problems: list[str] = []
    text = entry.get("measured_evidence", "")
    for match in PROSE_RANGE.finditer(text):
        rel = match.group(1).replace("\\", "/")
        if not rel.endswith(".py") or not (MAIN / rel).is_file():
            continue
        start = int(match.group(2))
        end = int(match.group(3)) if match.group(3) else None
        if end is None:
            continue
        spans = node_spans(rel)
        by_start = {s: (n, e) for n, s, e in spans}
        if start in by_start:
            name, ast_end = by_start[start]
            if ast_end != end:
                problems.append(
                    f"PROSE_MISMATCH {entry['id']} {rel}:{start}-{end} "
                    f"{name} AST {start}-{ast_end}"
                )
    return problems


def main() -> int:
    data = json.loads(FINDINGS.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    checks = 0
    for entry in data["findings"]:
        for p in entry.get("paths", []):
            rel = p["path"]
            spans = node_spans(rel)
            by_start: dict[int, list[tuple[str, int, int]]] = {}
            for name, start, end in spans:
                by_start.setdefault(start, []).append((name, start, end))
            for r in p.get("line_ranges", []):
                if isinstance(r, dict):
                    start, end = r["start"], r["end"]
                else:
                    start, end = r[0], r[1]
                checks += 1
                kind = r.get("kind") if isinstance(r, dict) else None
                if kind == "node":
                    candidates = by_start.get(start, [])
                    if not candidates:
                        msg = f"MISSING_NODE {entry['id']} {rel} {start}-{end}"
                        print(msg)
                        mismatches.append(msg)
                        continue
                    for name, s, e in candidates:
                        if e == end:
                            print(f"OK {entry['id']} {rel} {name} {start}-{end}")
                        else:
                            msg = (
                                f"MISMATCH {entry['id']} {rel} {name} "
                                f"cited {start}-{end} AST {s}-{e}"
                            )
                            print(msg)
                            mismatches.append(msg)
                elif kind == "line":
                    lc = line_count(rel)
                    if lc == 0 or end > lc or start < 1 or start > end:
                        msg = (
                            f"LINE_BOUNDS {entry['id']} {rel} {start}-{end} "
                            f"file_lines={lc}"
                        )
                        print(msg)
                        mismatches.append(msg)
                    else:
                        print(f"OK {entry['id']} {rel} line {start}-{end}")
                else:
                    msg = f"NO_KIND {entry['id']} {rel} {start}-{end}"
                    print(msg)
                    mismatches.append(msg)
        for msg in prose_checks(entry):
            print(msg)
            mismatches.append(msg)
        ev = entry.get("measured_evidence", "")
        for token in STALE_TOKENS:
            if token in ev:
                msg = f"STALE_PROSE {entry['id']}: {token!r}"
                print(msg)
                mismatches.append(msg)
    print(f"checked={checks} mismatches={len(mismatches)}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
