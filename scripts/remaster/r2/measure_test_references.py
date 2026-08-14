"""Canonical, reproducible count of test-side references to executive names.

R2 verifier rounds 1 and 2 both flagged that a plain grep-style count of the 17
executive entry-point names in tests/foundry is method-dependent (word-boundary
vs substring, per-file vs per-line). This script pins one exact method and
emits the count so the finding is reproducible by re-running the same file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MAIN = Path(r"C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry")
TESTS = MAIN / "tests" / "foundry"
OUT = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_TEST_REFERENCE_COUNTS.json"

NAMES = [
    "tick_scheduler",
    "enable_scheduler",
    "select_projects",
    "run_one_loop",
    "run_step",
    "run_mission",
    "run_candidate",
    "run_cycle",
    "promote_release",
    "shadow_run",
    "run_evaluation",
    "sync_vault",
    "ingest_documents",
    "detect_research_items",
    "generate_briefing",
    "CommandCenterServer",
    "EvolutionControl",
]


def main() -> None:
    word_pat = re.compile(r"\b(?:" + "|".join(NAMES) + r")\b")
    sub_pat = re.compile("|".join(NAMES))
    per_file: dict[str, dict[str, int]] = {}
    word_total = 0
    sub_total = 0
    files = sorted(TESTS.glob("*.py"))
    for f in files:
        text = f.read_text(encoding="utf-8")
        w = len(word_pat.findall(text))
        s = len(sub_pat.findall(text))
        word_total += w
        sub_total += s
        per_file[f.name] = {"word_boundary": w, "substring": s}

    result = {
        "schema": "jarvis-r2-test-reference-counts-v1",
        "method": {
            "test_root": str(TESTS),
            "files_scanned": len(files),
            "names": NAMES,
            "word_boundary_pattern": word_pat.pattern,
            "substring_pattern": sub_pat.pattern,
        },
        "totals": {
            "files": len(files),
            "word_boundary": word_total,
            "substring": sub_total,
        },
        "per_file": per_file,
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"files={len(files)} word_boundary={word_total} substring={sub_total}")


if __name__ == "__main__":
    main()
