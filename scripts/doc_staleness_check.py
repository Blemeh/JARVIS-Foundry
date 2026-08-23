"""JARVIS documentation staleness check (deterministic).

Verifies the current-truth surfaces against the repository:
1. docs/CURRENT_STATE.md HEAD claim == `git rev-parse HEAD`
2. schema claim == jarvis_foundry.migrations.SCHEMA_VERSION (semantic markers)
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

CURRENT_CANDIDATE_SCHEMA_RE = re.compile(
    r"CURRENT/CANDIDATE SCHEMA:\s*v(\d+)",
    re.IGNORECASE,
)
V34_BASELINE_RE = re.compile(
    r"v34\b.*\bbaseline\b|\bbaseline\b.*\bv34\b",
    re.IGNORECASE,
)
V2_SCHEMA_RE = re.compile(
    r"V2 KNOWLEDGE SCHEMA:\s*v35\b",
    re.IGNORECASE,
)
V3_SCHEMA_RE = re.compile(
    r"V3 DISCOVERY/BUILDING SCHEMA:\s*v36\b",
    re.IGNORECASE,
)
CONTRADICTORY_CURRENT_V35_RE = re.compile(
    r"(?:current|candidate)\s*/?\s*candidate\s+schema\s*(?:is|=)\s*v35\b|"
    r"advances\s+SCHEMA_VERSION\s+to\s+v35\b|"
    r"CURRENT/CANDIDATE SCHEMA:\s*v35\b|"
    r"current\s+candidate\s+schema\s+is\s+v35\b",
    re.IGNORECASE,
)


def validate_schema_documentation(text: str, schema_version: int) -> list[str]:
    """Return schema documentation failures for CURRENT_STATE body text."""
    failures: list[str] = []
    match = CURRENT_CANDIDATE_SCHEMA_RE.search(text)
    if match is None:
        failures.append(
            "CURRENT_STATE.md missing explicit CURRENT/CANDIDATE SCHEMA marker")
    elif int(match.group(1)) != schema_version:
        failures.append(
            f"CURRENT_STATE.md CURRENT/CANDIDATE SCHEMA is v{match.group(1)}, "
            f"expected v{schema_version}")
    if not V34_BASELINE_RE.search(text):
        failures.append("CURRENT_STATE.md does not document v34 live baseline")
    if not V2_SCHEMA_RE.search(text):
        failures.append("CURRENT_STATE.md does not document V2 knowledge schema v35")
    if not V3_SCHEMA_RE.search(text):
        failures.append(
            "CURRENT_STATE.md does not document V3 discovery/building schema v36")
    if CONTRADICTORY_CURRENT_V35_RE.search(text):
        failures.append(
            "CURRENT_STATE.md contains contradictory current/candidate schema v35 claim")
    return failures


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

    depth = subprocess.run(
        ["git", "-C", str(REPO), "rev-list", "--count", f"{head}~20..{head}"],
        capture_output=True, text=True).stdout.strip()
    try:
        horizon = int(depth) if depth else 0
    except ValueError:
        horizon = 0
    pin_ok = False
    for i in range(horizon + 1):
        sha = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", f"HEAD~{i}"],
            capture_output=True, text=True).stdout.strip()
        if sha[:7] in text or sha in text:
            pin_ok = True
            break
    if not pin_ok:
        failures.append(f"CURRENT_STATE.md pin is stale: no HEAD..HEAD~{horizon} sha named")
    failures.extend(validate_schema_documentation(text, schema))

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
