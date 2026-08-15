"""R5-G02: byte-identical preservation of every historical event row."""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    record_enabled,
    sample_specs,
    sha256_file,
    write_json,
)


def _row_stream_digest(path: Path) -> tuple[str, int]:
    from jarvis_foundry.kernel.canonical import canonical_bytes
    from jarvis_foundry.kernel.compat.readers import LegacyEventsReader

    reader = LegacyEventsReader(path)
    rows = reader.read_rows()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_bytes(row.to_dict()))
        digest.update(b"\n")
    return digest.hexdigest(), len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R5_PRESERVATION.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R5_PRESERVATION_RAW.txt")
    args = parser.parse_args(argv)

    started = time.monotonic()
    samples: dict[str, dict[str, Any]] = {}
    lines: list[str] = []
    for name, path in sample_specs().items():
        before = sha256_file(path)
        before_size = path.stat().st_size
        sidecars_before = _sidecars(path)
        row_digest, row_count = _row_stream_digest(path)
        after = sha256_file(path)
        after_size = path.stat().st_size
        sidecars_after = _sidecars(path)
        unchanged = before == after and before_size == after_size
        sidecars_created = sorted(set(sidecars_after) - set(sidecars_before))
        samples[name] = {
            "source": str(path),
            "file_sha256_before": before,
            "file_sha256_after": after,
            "size_before": before_size,
            "size_after": after_size,
            "byte_identical": unchanged,
            "row_stream_sha256": row_digest,
            "row_count": row_count,
            "sidecars_before": sidecars_before,
            "sidecars_after": sidecars_after,
            "sidecars_created_during_read": sidecars_created,
        }
        lines.append(
            f"{name}: rows={row_count} byte_identical={unchanged} "
            f"row_stream_sha256={row_digest} sidecars_created={sidecars_created}"
        )
    passed = all(
        item["byte_identical"] and not item["sidecars_created_during_read"]
        for item in samples.values()
    )
    raw_text = "\n".join(lines) + "\n"
    payload = {
        "schema": "jarvis-r5-preservation-v1",
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


def _sidecars(path: Path) -> list[str]:
    return sorted(
        child.name
        for child in path.parent.glob(f"{path.name}-*")
        if child.name in {f"{path.name}-wal", f"{path.name}-shm"}
    )


if __name__ == "__main__":
    sys.exit(main())
