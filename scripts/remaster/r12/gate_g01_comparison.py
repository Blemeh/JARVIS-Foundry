"""R12-G01: integrated comparison gate."""

from __future__ import annotations

import sys
import time

from common import EVIDENCE_DIR, read_json, write_json


def main() -> int:
    started = time.monotonic()
    matrix = read_json(EVIDENCE_DIR / "R12_COMPARISON_MATRIX.json")
    domains = matrix.get("domains", [])
    divergences_ok = all(len(row.get("divergences", [])) > 0 for row in domains)
    domain_count_ok = matrix.get("domain_count") == 5
    passed = bool(
        matrix.get("passed")
        and domain_count_ok
        and divergences_ok
        and matrix.get("protected_main_mutated") is False
        and matrix.get("v7_snapshot", {}).get("file_count", 0) > 0
    )
    payload = {
        "schema": "jarvis-r12-g01-comparison-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "domain_count_ok": domain_count_ok,
        "all_domains_have_divergences": divergences_ok,
        "protected_main_mutated": matrix.get("protected_main_mutated"),
        "v7_snapshot_file_count": matrix.get("v7_snapshot", {}).get("file_count"),
        "v7_tree_hash": matrix.get("v7_snapshot", {}).get("tree_hash"),
        "domains": [row["domain"] for row in domains],
    }
    write_json(EVIDENCE_DIR / "R12_G01_COMPARISON.json", payload)
    print(f"g01 comparison: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
