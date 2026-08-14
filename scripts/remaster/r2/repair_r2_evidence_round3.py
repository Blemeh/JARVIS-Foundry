"""Round-3 deterministic repair of R2 forensic evidence.

R2 verifier round 2 (FAIL, preserved as R2_VERIFIER_ROUND2_VERDICT_DSV4.json)
found four residual accuracy defects:
1. R2-F011 still cited events.py:73-81 (validate is 73-82) and PRAGMA
   foreign_keys at database.py:28 (it is line 23).
2. The handler breakdown "91 equality + 10 in-set" is not AST-precise; the
   exact union is 100 distinct types from 92 equality literals + 10 in-set
   literals with 2 overlap.
3. Test-reference counts were method-dependent; this is now pinned by the
   frozen script measure_test_references.py and its emitted JSON.

This script rewrites only the affected strings/ranges and the summary.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FINDINGS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_FORENSIC_FINDINGS.json"
SUMMARY = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_SUMMARY.md"
COUNTS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_TEST_REFERENCE_COUNTS.json"

COUNTS_REF = (
    "311 word-boundary / 344 substring test-side references measured by the frozen script "
    "scripts/remaster/r2/measure_test_references.py (evidence: R2_TEST_REFERENCE_COUNTS.json)"
)


def set_ranges(entry: dict, path: str, ranges: list[list[int]]) -> None:
    for p in entry["paths"]:
        if p["path"] == path:
            p["line_ranges"] = ranges
            return
    raise KeyError(f"path not found: {path}")


def main() -> None:
    with FINDINGS.open(encoding="utf-8") as fh:
        data = json.load(fh)
    findings = {f["id"]: f for f in data["findings"]}

    # R2-F011: validate span 73-81 -> 73-82; PRAGMA foreign_keys line 28 -> 23.
    f = findings["R2-F011"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "Event.validate (events.py:73-81)", "Event.validate (events.py:73-82)"
    ).replace(
        "PRAGMA foreign_keys=ON (database.py:28)",
        "PRAGMA foreign_keys=ON (database.py:23)",
    )
    set_ranges(f, "src/jarvis_foundry/events.py", [[73, 82]])

    # R2-F001 and R2-F012: AST-precise handler breakdown.
    precise = (
        "92 literal elements in event_type equality comparisons plus 10 literal "
        "elements inside 3 in-tuple/set comparisons (2 overlap; 100 distinct "
        "handled event types by AST union)"
    )
    for fid in ("R2-F001", "R2-F012"):
        f = findings[fid]
        f["measured_evidence"] = f["measured_evidence"].replace(
            "91 event_type equality branches plus 10 literal types inside 3 in-tuple/set branches (100 distinct handled types) plus 3 startswith families",
            precise + " plus 3 startswith families",
        ).replace(
            "91 equality branches plus 10 literal types inside 3 in-tuple/set branches (100 distinct handled types) plus 3 startswith families",
            precise + " plus 3 startswith families",
        )

    # R2-F005 and R2-F006: pin test-reference counts to the frozen script.
    f = findings["R2-F005"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "Executive entry points have 344 substring test-side references (word-boundary method: 311) and no caller reachable from the installed watchdog path",
        "Executive entry points have " + COUNTS_REF + " and no caller reachable from the installed watchdog path",
    )
    f = findings["R2-F006"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "the only references are tests (344 substring matches, word-boundary 311)",
        "the only references are tests (" + COUNTS_REF + ")",
    )

    with FINDINGS.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")

    text = SUMMARY.read_text(encoding="utf-8")
    text = text.replace(
        "the only references are tests (344 substring matches, word-boundary 311) and call sites inside other",
        "the only references are tests (" + COUNTS_REF + ") and call sites inside other",
    )
    SUMMARY.write_text(text, encoding="utf-8", newline="\n")

    counts_sha = __import__("hashlib").sha256(COUNTS.read_bytes()).hexdigest()
    print("R2 evidence round-3 repaired")
    print(f"  counts json: {COUNTS.name} sha256={counts_sha}")


if __name__ == "__main__":
    main()
