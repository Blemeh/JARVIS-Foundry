"""Deterministic behavior probe for the micro target."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import score, split_tags
from workflow import run_pipeline

checks = {
    "split_clean": split_tags("a,, b") == ["a", "b"],
    "score_non_negative": score({"score": -1.0}) >= 0.0,
}

try:
    checks["pipeline_dedupe"] = (
        len(run_pipeline([{"id": 1, "score": 1.0}, {"id": 1, "score": 1.0}])) == 1
    )
except Exception:  # noqa: BLE001 - probe must stay honest
    checks["pipeline_dedupe"] = False

try:
    checks["pipeline_none_safe"] = run_pipeline([None, {"id": 2, "score": 2.0}])[0]["id"] == 2
except Exception:  # noqa: BLE001 - probe must stay honest
    checks["pipeline_none_safe"] = False

print(
    json.dumps(
        {
            "checks": checks,
            "passed": sum(1 for value in checks.values() if value),
            "total": len(checks),
        },
        sort_keys=True,
    )
)
