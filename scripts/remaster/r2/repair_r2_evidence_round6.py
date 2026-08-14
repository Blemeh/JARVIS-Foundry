"""Round-6 deterministic repair of R2 forensic evidence.

R2 verifier round 5 (FAIL) found:
1. R2-F028 cited systems.py:53-56 as "registers probes" but that range is the
   docstring tail of register_system (46-64); probe registration lives in
   register_system via probe_ref, and probe execution in probe_system (67-88).
2. created_event_types=113 in metrics and R2-F012 prose is method-dependent;
   the frozen canonical census (measure_event_types.py) counts 112 distinct
   Event.create literals (handled 100, prefixes 3, unhandled 41), while an
   exhaustive-resolution method yields more.

This script applies the corrected ranges/prose, classifies every structured
range as node/line (classify_r2_ranges.py), and pins the metrics to the
canonical census.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FINDINGS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_FORENSIC_FINDINGS.json"
METRICS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_METRICS.json"


def set_ranges(entry: dict, path: str, ranges: list[dict]) -> None:
    for p in entry["paths"]:
        if p["path"] == path:
            p["line_ranges"] = ranges
            return
    raise KeyError(f"path not found: {path}")


def main() -> None:
    data = json.loads(FINDINGS.read_text(encoding="utf-8"))
    findings = {f["id"]: f for f in data["findings"]}

    # R2-F028: systems.py probe registration lives in register_system (46-64).
    f = findings["R2-F028"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "systems.py:53-56 registers probes",
        "systems.py register_system (46-64) records probe_ref with the system",
    )
    set_ranges(f, "src/jarvis_foundry/systems.py", [{"start": 46, "end": 64}])

    # R2-F012: created count is method-dependent; pin the canonical census.
    f = findings["R2-F012"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "Source creates 113 distinct event_type values;",
        "The frozen canonical census (scripts/remaster/r2/measure_event_types.py, evidence R2_EVENT_TYPE_COUNTS.json) counts 112 distinct Event.create event_type literals (method-dependent: exhaustive static resolution yields more);",
    )

    # R2-F004: pyproject.toml range 1-27 exceeds the 24-line file; the cited
    # content is the whole pyproject.toml quality-gate evidence.
    f = findings["R2-F004"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "pyproject.toml:1-27", "pyproject.toml:1-24"
    )
    set_ranges(f, "pyproject.toml", [{"start": 1, "end": 24}])

    FINDINGS.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    metrics["event_coverage"]["created_event_types"] = 112
    metrics["event_coverage"]["census_method"] = (
        "scripts/remaster/r2/measure_event_types.py; evidence "
        "docs/remaster/evidence/r2/R2_EVENT_TYPE_COUNTS.json; "
        "counts are literal-based and method-dependent (exhaustive resolution yields more)"
    )
    METRICS.write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print("R2 evidence round-6 repaired: systems.py span, created count pinned to canonical census")


if __name__ == "__main__":
    main()
