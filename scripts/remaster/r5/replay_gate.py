"""R5-G01: deterministic zero-to-current replay on complete historical samples."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    ISOLATED_V7_COPY,
    LIVE_COPY,
    LIVE_R0_COPY,
    record_enabled,
    sha256_file,
    write_json,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=LIVE_COPY)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R5_REPLAY.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R5_REPLAY_RAW.txt")
    args = parser.parse_args(argv)

    from jarvis_foundry.kernel.compat.replay import replay_legacy, replay_signature

    started = time.monotonic()
    lines: list[str] = []
    sample_results: dict[str, dict[str, Any]] = {}
    primary: dict[str, Any] | None = None
    for name, source in (
        ("live_snapshot", args.source),
        ("live_r0_frozen", LIVE_R0_COPY),
        ("isolated_v7_frozen", ISOLATED_V7_COPY),
    ):
        first = replay_legacy(source)
        second = replay_legacy(source)
        identical = replay_signature(first) == replay_signature(second)
        payload = first.to_dict()
        payload["repeated_run_identical"] = identical
        payload["second_head_hash"] = second.head_hash
        sample_results[name] = payload
        lines.append(
            f"{name}: rows={first.row_count} refused={first.refused_count} "
            f"chain_valid={first.chain_valid} projection_ok={first.projection_invariant_ok} "
            f"repeated_identical={identical} head={first.head_hash}"
        )
        if name == "live_snapshot":
            primary = payload

    # Deterministic refusal of an unknown/unsupported row on a synthetic copy.
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-r5-replay-refusal-"))
    refusal_sample = temp_dir / "refusal.db"
    connection = sqlite3.connect(refusal_sample)
    connection.executescript(
        "CREATE TABLE events ("
        "sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, "
        "event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, "
        "source_type TEXT NOT NULL, source_id TEXT NOT NULL, correlation_id TEXT NOT NULL, "
        "causation_id TEXT, idempotency_key TEXT NOT NULL UNIQUE, schema_version INTEGER NOT NULL, "
        "payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL"
        ");"
    )
    payload_text = "{}"
    payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    connection.execute(
        "INSERT INTO events (event_id, event_type, occurred_at, recorded_at, source_type, "
        "source_id, correlation_id, causation_id, idempotency_key, schema_version, "
        "payload_json, payload_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "mystery-1",
            "mystery.kind",
            "2026-08-15T00:00:00Z",
            "2026-08-15T00:00:00Z",
            "gate",
            "gate",
            "mystery-1",
            None,
            "mystery:1",
            1,
            payload_text,
            payload_hash,
        ),
    )
    connection.commit()
    connection.close()
    refusal = replay_legacy(refusal_sample)
    refused_unknown = (
        refusal.refused_count == 1 and "compatibility catalog" in refusal.refusals[0].reason
    )
    lines.append(
        f"unknown_row_refusal: refused={refused_unknown} reason={refusal.refusals[0].reason if refusal.refusals else 'none'}"
    )
    shutil.rmtree(temp_dir, ignore_errors=True)

    passed = bool(
        primary
        and primary["passed"]
        and primary["repeated_run_identical"]
        and primary["chain_valid"]
        and primary["refused_count"] == 0
        and refused_unknown
        and all(item["repeated_run_identical"] for item in sample_results.values())
    )
    raw_text = "\n".join(lines) + "\n"
    payload = {
        "schema": "jarvis-r5-replay-v1",
        "sources": {name: sample_results[name] for name in sample_results},
        "primary": primary,
        "unknown_row_refusal": refused_unknown,
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
