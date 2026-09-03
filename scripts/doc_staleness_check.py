"""JARVIS documentation staleness check (deterministic).

Verifies the current-truth surfaces against the repository:
1. docs/CURRENT_STATE.md documentation-state base == the immediate parent of
   the current docs-only commit (the source state the prose describes)
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
    "tests/acceptance/README.md",
)

# These are the complete documentation surfaces that may change in the final
# reconciliation commit. Keeping the set here makes the freshness rule
# authoritative instead of duplicating a second, subtly different allowlist.
DOC_ONLY_ALLOWED_PATHS = frozenset({
    "docs/CURRENT_STATE.md",
    "docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md",
    "docs/handoffs/V10_FULL_CONVERGENCE_20260901.md",
    "docs/handoffs/V10_INTEGRATION_MANIFEST_20260901.md",
    "docs/handoffs/V11_A2_CANDIDATE_20260902.md",
    "docs/handoffs/V11_INTEGRATION_BASE2_20260902.md",
    "docs/handoffs/V11_PRECONVERGENCE_20260902.md",
    "docs/handoffs/V12_CAPABILITY_DISCOVERY_20260902.md",
    "docs/handoffs/V12_SELF_EXPANSION_CONVERGENCE_20260903.md",
    "docs/handoffs/V12_EFFECTS_MCP_CONVERGENCE_20260903.md",
    "docs/roadmap/JARVIS_1_0_EXECUTION_STATE.yaml",
    "tests/acceptance/README.md",
})

CURRENT_CANDIDATE_SCHEMA_RE = re.compile(
    r"CURRENT/CANDIDATE SCHEMA:\s*v(\d+)",
    re.IGNORECASE,
)
DOCUMENTATION_STATE_SHA_RE = re.compile(
    r"DOCUMENTATION STATE BASE SHA:\s*`?([0-9a-f]{7,64})`?",
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


def documentation_state_sha(head: str | None = None) -> str:
    """Return the source commit that a docs-only HEAD is allowed to describe.

    A document cannot contain the SHA of the commit that contains the document
    without a self-referential hash.  The repository contract therefore makes
    the current-state commit the final docs-only commit and pins the prose to
    its immediate parent.  Any later code commit, or a docs commit that also
    changes source, fails closed instead of accepting an arbitrary historical
    SHA.
    """
    commit = (head or _git_head()).strip()
    parent = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", f"{commit}^"],
        capture_output=True, text=True, check=False,
    )
    return parent.stdout.strip() if parent.returncode == 0 else commit


def validate_documentation_pin(text: str, state_sha: str) -> list[str]:
    failures: list[str] = []
    match = DOCUMENTATION_STATE_SHA_RE.search(text)
    if match is None:
        failures.append("CURRENT_STATE.md missing DOCUMENTATION STATE BASE SHA marker")
    elif not (match.group(1).lower() == state_sha.lower()
              or state_sha.lower().startswith(match.group(1).lower())):
        failures.append(
            "CURRENT_STATE.md documentation-state pin is stale: "
            f"named {match.group(1)}, expected {state_sha}")
    return failures


def _schema_version() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from jarvis_foundry.migrations import SCHEMA_VERSION  # type: ignore[import-not-found]
    return SCHEMA_VERSION


def main() -> int:
    failures: list[str] = []
    head = _git_head()
    schema = _schema_version()
    text = CURRENT_STATE.read_text(encoding="utf-8")

    state_sha = documentation_state_sha(head)
    failures.extend(validate_documentation_pin(text, state_sha))
    # The pin is meaningful only when the current commit is a docs-only
    # reconciliation.  A source change after the pinned commit must force a
    # new CURRENT_STATE update rather than being hidden by a 20-commit window.
    changed = subprocess.run(
        ["git", "-C", str(REPO), "diff-tree", "--no-commit-id", "--name-only",
         "-r", head], capture_output=True, text=True, check=False).stdout.splitlines()
    unexpected = sorted(
        path for path in changed if path and path not in DOC_ONLY_ALLOWED_PATHS
    )
    if unexpected:
        failures.append(
            "CURRENT_STATE.md must be the final docs-only commit; changed paths: "
            + ", ".join(unexpected[:8]))
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
