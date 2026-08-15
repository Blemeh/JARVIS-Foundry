"""R5-D04: independently defined invariants compared against replay output."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, record_enabled, sample_specs, sha256_file, write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R5_INVARIANTS.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R5_INVARIANTS_RAW.txt")
    args = parser.parse_args(argv)

    from jarvis_foundry.kernel.compat.replay import replay_legacy

    started = time.monotonic()
    lines: list[str] = []
    samples: dict[str, dict[str, Any]] = {}
    for name, path in sample_specs().items():
        result = replay_legacy(path)
        samples[name] = {
            "invariant_report": result.invariant_report.to_dict(),
            "projection_invariant_ok": result.projection_invariant_ok,
            "projection_mismatches": list(result.projection_mismatches),
            "chain_valid": result.chain_valid,
            "chain_sequence_count": result.chain_sequence_count,
            "projection_snapshot_sha256": result.projection_snapshot_sha256,
            "row_count": result.row_count,
        }
        lines.append(
            f"{name}: invariants_ok={result.invariant_report.ok} "
            f"projection_ok={result.projection_invariant_ok} "
            f"chain_sequence={result.chain_sequence_count}/{result.row_count} "
            f"snapshot={result.projection_snapshot_sha256}"
        )
    passed = all(
        item["invariant_report"]["ok"]
        and item["projection_invariant_ok"]
        and item["chain_valid"]
        and item["chain_sequence_count"] == item["row_count"]
        for item in samples.values()
    )
    raw_text = "\n".join(lines) + "\n"
    payload = {
        "schema": "jarvis-r5-invariants-v1",
        "samples": samples,
        "passed": passed,
        "wall_seconds": round(time.monotonic() - started, 3),
        "exit_code": 0,
    }
    if record_enabled():
        args.raw.write_text(raw_text, encoding="utf-8")
        payload["raw_output"] = args.raw.name
        payload["raw_output_sha256"] = sha256_file(args.raw)
        write_json(args.output, payload)
    print(raw_text)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
