"""Round-5 deterministic repair of R2 forensic evidence.

R2 verifier round 4 (FAIL) found:
1. Prose stale ranges not covered by the structured auditor: configure_owner
   548-552 (actual 548-553), enable_scheduler 117-124 (actual 117-123),
   reconciliation_matrix 143-148 (actual 143-145).
2. R2-F012 prose family counts were inaccurate: portfolio.* is 7, connector.*
   is 3, correlation.* is 3, and the handler breakdown prose still said
   "91 equality branches" instead of the AST-precise 92 equality literals +
   10 in-set literals (2 overlap, 100 distinct union).
3. 12 findings lacked an explicit R0 cross-reference (mapping or R2-ONLY).
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FINDINGS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_FORENSIC_FINDINGS.json"

R0_MAP = {
    "R2-F002": "R0-F016",
    "R2-F003": "R0-F017",
    "R2-F004": "R2-ONLY",
    "R2-F015": "R2-ONLY",
    "R2-F017": "R0-F004",
    "R2-F018": "R0-F004",
    "R2-F021": "R0-F015",
    "R2-F022": "R2-ONLY",
    "R2-F026": "R2-ONLY",
    "R2-F027": "R2-ONLY",
    "R2-F028": "R2-ONLY",
    "R2-F030": "R2-ONLY",
}


def main() -> None:
    data = json.loads(FINDINGS.read_text(encoding="utf-8"))
    findings = {f["id"]: f for f in data["findings"]}

    f = findings["R2-F013"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "command_center.py:548-552", "command_center.py:548-553"
    )

    f = findings["R2-F017"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "enable_scheduler (117-124)", "enable_scheduler (117-123)"
    )

    f = findings["R2-F028"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "capabilities/registry.py:143-148", "capabilities/registry.py:143-145"
    )

    f = findings["R2-F012"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "100 distinct exact types (91 equality branches plus 10 literal types inside 3 in-tuple/set branches)",
        "100 distinct exact types (AST: 92 literal elements in equality comparisons plus 10 literal elements inside 3 in-tuple/set comparisons, 2 overlap)",
    ).replace(
        "portfolio.* (8)", "portfolio.* (7)"
    ).replace(
        "connector.* (4)", "connector.* (3)"
    ).replace(
        "correlation.* (2)", "correlation.* (3)"
    )

    # R2-F001 prose also carries the handler breakdown; keep it consistent.
    f = findings["R2-F001"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "91 event_type equality branches plus 10 literal types inside 3 in-tuple/set branches (100 distinct handled types)",
        "92 literal elements in event_type equality comparisons plus 10 literal elements inside 3 in-tuple/set comparisons (2 overlap; 100 distinct handled types)",
    )

    for fid, ref in R0_MAP.items():
        if fid in findings:
            findings[fid]["r0_cross_ref"] = ref

    FINDINGS.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print("R2 evidence round-5 repaired: prose ranges, family counts, R0 cross-refs")
    print(f"  r0_cross_ref present: {sum(1 for x in data['findings'] if x.get('r0_cross_ref'))}/30")


if __name__ == "__main__":
    main()
