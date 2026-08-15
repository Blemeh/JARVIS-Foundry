"""R5-G05: dual-run/cutover rehearsal with zero loss and zero duplication."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    ISOLATED_V7_COPY,
    LIVE_COPY,
    record_enabled,
    sha256_file,
    write_json,
)


def _rehearse(source: Path, name: str, synthetic_count: int) -> dict[str, Any]:
    from jarvis_foundry.kernel.compat.cutover import rehearse_dual_run

    temp_dir = Path(tempfile.mkdtemp(prefix=f"jarvis-r5-cutover-{name}-"))
    copy_a = temp_dir / "a-legacy.db"
    copy_b = temp_dir / "b-legacy.db"
    shutil.copy2(source, copy_a)
    shutil.copy2(source, copy_b)
    first = rehearse_dual_run(
        copy_a,
        temp_dir / "a-r4.db",
        synthetic_count=synthetic_count,
        ledger_workdir=temp_dir / "a-work",
    )
    second = rehearse_dual_run(
        copy_b,
        temp_dir / "b-r4.db",
        synthetic_count=synthetic_count,
        ledger_workdir=temp_dir / "b-work",
    )
    result = {
        "sample": name,
        "first": first.to_dict(),
        "second": second.to_dict(),
        "idempotent_signatures_match": first.idempotency_signature == second.idempotency_signature,
    }
    shutil.rmtree(temp_dir, ignore_errors=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R5_CUTOVER.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R5_CUTOVER_RAW.txt")
    args = parser.parse_args(argv)

    started = time.monotonic()
    live = _rehearse(LIVE_COPY, "live", synthetic_count=10)
    isolated = _rehearse(ISOLATED_V7_COPY, "isolated", synthetic_count=5)
    lines = [
        (
            f"live: lossless={live['first']['lossless']} "
            f"duplicate_free={live['first']['duplicate_free']} "
            f"clean={live['first']['reconciliation_clean']} "
            f"chain_valid={live['first']['chain_valid']} "
            f"idempotent={live['idempotent_signatures_match']}"
        ),
        (
            f"isolated: lossless={isolated['first']['lossless']} "
            f"duplicate_free={isolated['first']['duplicate_free']} "
            f"clean={isolated['first']['reconciliation_clean']} "
            f"idempotent={isolated['idempotent_signatures_match']}"
        ),
    ]
    passed = bool(
        live["first"]["lossless"]
        and live["first"]["duplicate_free"]
        and live["first"]["reconciliation_clean"]
        and live["first"]["chain_valid"]
        and live["idempotent_signatures_match"]
        and isolated["first"]["lossless"]
        and isolated["first"]["duplicate_free"]
        and isolated["first"]["reconciliation_clean"]
        and isolated["idempotent_signatures_match"]
    )
    raw_text = "\n".join(lines) + "\n"
    payload = {
        "schema": "jarvis-r5-cutover-v1",
        "live": live,
        "isolated": isolated,
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
