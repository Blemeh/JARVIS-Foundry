"""JARVIS documentation staleness check (deterministic).

Verifies the current-truth surfaces against the repository:
1. docs/CURRENT_STATE.md HEAD claim == `git rev-parse HEAD`
2. schema claim == jarvis_foundry.migrations.SCHEMA_VERSION
3. no forbidden stale phrases in current-status surfaces
4. referenced evidence paths exist

Exit 0 = consistent. Exit 1 = stale claim found (print each).
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
CURRENT_STATE = REPO / "docs" / "CURRENT_STATE.md"
FORBIDDEN = (
    "BUILD NOT STARTED",
    "IMPLEMENTATION: NOT STARTED",
    "IMPLEMENTATION NOT STARTED",
    "START BUILD RECEIVED: NO",
    "Designed, not built",
    "has not started implementation",
    "execution remains not started",
)
CURRENT_SURFACES = (
    "docs/CURRENT_STATE.md",
    "docs/architecture/00_READ_FIRST.md",
    "docs/architecture/05_POST_V1_ROADMAP.md",
)


def _git_head() -> str:
    out = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _schema_version() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from jarvis_foundry.migrations import SCHEMA_VERSION  # type: ignore[import-not-found]
    return SCHEMA_VERSION


def main() -> int:
    failures: list[str] = []
    head = _git_head()
    schema = _schema_version()
    text = CURRENT_STATE.read_text(encoding="utf-8")

    # The doc is committed AT head; its claim describes the tree it ships in,
    # so the claim may equal HEAD or HEAD^ (the last write). Any other commit
    # (code, schema, gates) makes the claim stale until the doc is updated.
    parent = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD^"],
        capture_output=True, text=True).stdout.strip()
    claimed = head[:7] in text or head in text or parent[:7] in text or parent in text
    if not claimed:
        failures.append(f"CURRENT_STATE.md does not name current HEAD {head[:7]} (or its parent)")
    if f"v{schema}" not in text and str(schema) not in text:
        failures.append(f"CURRENT_STATE.md does not name current schema v{schema}")

    for surface in CURRENT_SURFACES:
        path = REPO / surface
        if not path.exists():
            failures.append(f"current-status surface missing: {surface}")
            continue
        body = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN:
            if phrase in body:
                failures.append(f"{surface}: forbidden stale phrase '{phrase}'")

    for line in text.splitlines():
        m = re.search(r"`(evidence/[^`]+)`", line)
        if m and not (REPO / m.group(1)).exists():
            failures.append(f"CURRENT_STATE.md references missing path {m.group(1)}")

    for failure in failures:
        print(f"STALE: {failure}")
    if failures:
        print("DOCUMENTATION STALENESS CHECK: FAIL")
        return 1
    print("DOCUMENTATION STALENESS CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
