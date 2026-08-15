"""Fresh verifier subprocess entry point for R8 missions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", required=True, type=Path)
    parser.add_argument("--clone", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--verdict", required=True, type=Path)
    parser.add_argument("--verifier-id", default="fresh-verifier-r8")
    parser.add_argument("--verified-at", default="")
    args = parser.parse_args(argv)

    clone_src = args.clone.resolve() / "src"
    if clone_src.is_dir():
        sys.path.insert(0, str(clone_src))
    from jarvis_foundry.orchestration.ingest import ingest_result
    from jarvis_foundry.orchestration.mission_context import MissionContext
    from jarvis_foundry.orchestration.verify import Verifier

    mission = MissionContext.from_dict(json.loads(args.mission.read_text(encoding="utf-8")))
    outcome = ingest_result(args.result)
    if not outcome.valid or outcome.result is None:
        payload: dict[str, Any] = {
            "schema": "jarvis-r8-verifier-verdict-v1",
            "verdict": "REJECTED",
            "issues": [{"field": "result", "detail": reason} for reason in outcome.reasons],
            "verifier_id": args.verifier_id,
            "verified_at": args.verified_at,
            "test_evidence": {},
        }
        args.verdict.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 1

    verifier = Verifier(
        python=Path(sys.executable),
        verifier_id=args.verifier_id,
        verified_at=args.verified_at,
    )
    verdict = verifier.verify(args.clone, outcome.result, list(mission.required_tests))
    payload = {
        "schema": "jarvis-r8-verifier-verdict-v1",
        **verdict.to_dict(),
    }
    args.verdict.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0 if verdict.verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
