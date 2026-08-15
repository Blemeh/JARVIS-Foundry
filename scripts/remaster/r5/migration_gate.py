"""R5-G03: migration idempotency, interruption/resume, and rollback rehearsal."""

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

from jarvis_foundry.kernel.compat.migrations import MIGRATION_STEPS


def _full_matrix(
    source: Path,
    prefix: str,
    *,
    interruption_steps: tuple[str, ...],
) -> dict[str, Any]:
    from jarvis_foundry.kernel.compat.migrations import MigrationRehearsal

    temp_dir = Path(tempfile.mkdtemp(prefix=f"jarvis-r5-migration-{prefix}-"))
    baseline_workdir = temp_dir / "baseline"
    baseline = MigrationRehearsal(source, baseline_workdir)
    baseline_result = baseline.run()
    second_workdir = temp_dir / "second"
    second = MigrationRehearsal(source, second_workdir)
    second_result = second.run()
    idempotent = baseline_result.artifact_hashes == second_result.artifact_hashes
    results: dict[str, Any] = {
        "source": str(source),
        "baseline": baseline_result.to_dict(),
        "second_run": second_result.to_dict(),
        "idempotent_across_fresh_runs": idempotent,
        "interruption_matrix": {},
    }
    for step in interruption_steps:
        workdir = temp_dir / f"interrupt-{step.lower()}"
        rehearsal = MigrationRehearsal(source, workdir)
        interrupted = rehearsal.run(interrupt_after=step)
        resumed = rehearsal.resume()
        results["interruption_matrix"][step] = {
            "interrupted": interrupted.interrupted,
            "interrupt_exit_code": interrupted.exit_code,
            "last_step_after_interrupt": interrupted.last_step,
            "resume_exit_code": resumed.exit_code,
            "resume_last_step": resumed.last_step,
            "artifact_hashes_match_baseline": resumed.artifact_hashes
            == baseline_result.artifact_hashes,
            "wall_seconds": round(interrupted.wall_seconds + resumed.wall_seconds, 3),
        }
    shutil.rmtree(temp_dir, ignore_errors=True)
    return results


def _rollback_rehearsal(source: Path) -> dict[str, Any]:
    from jarvis_foundry.kernel.compat.migrations import MigrationRehearsal

    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-r5-migration-rollback-"))
    work_source = temp_dir / "source.db"
    shutil.copy2(source, work_source)
    original = sha256_file(work_source)
    rehearsal = MigrationRehearsal(work_source, temp_dir / "work")
    rehearsal.run()
    report = rehearsal.rollback()
    report["source_before_any_rehearsal_sha256"] = original
    report["rollback_restores_original_bytes"] = report["source_after_sha256"] == original
    shutil.rmtree(temp_dir, ignore_errors=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R5_MIGRATION.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R5_MIGRATION_RAW.txt")
    args = parser.parse_args(argv)

    started = time.monotonic()
    live_full = _full_matrix(LIVE_COPY, "live", interruption_steps=("LEDGER",))
    isolated_full = _full_matrix(ISOLATED_V7_COPY, "isolated", interruption_steps=MIGRATION_STEPS)
    rollback = _rollback_rehearsal(LIVE_COPY)
    lines = [
        (
            f"live: idempotent={live_full['idempotent_across_fresh_runs']} "
            f"baseline_fingerprint="
            f"{live_full['baseline']['artifact_hashes'].get('run_fingerprint', 'n/a')}"
        ),
        f"isolated: idempotent={isolated_full['idempotent_across_fresh_runs']}",
    ]
    for step, item in isolated_full["interruption_matrix"].items():
        lines.append(
            f"interrupt {step}: interrupted={item['interrupted']} "
            f"resume_exit={item['resume_exit_code']} "
            f"hashes_match={item['artifact_hashes_match_baseline']}"
        )
    lines.append(
        f"rollback: restores_original_bytes={rollback['rollback_restores_original_bytes']} "
        f"previous_release_readable={rollback['previous_release_readable']}"
    )
    passed = (
        live_full["idempotent_across_fresh_runs"]
        and isolated_full["idempotent_across_fresh_runs"]
        and all(
            item["artifact_hashes_match_baseline"]
            for item in isolated_full["interruption_matrix"].values()
        )
        and rollback["rollback_restores_original_bytes"]
        and rollback["previous_release_readable"]
    )
    raw_text = "\n".join(lines) + "\n"
    payload = {
        "schema": "jarvis-r5-migration-v1",
        "live_full": live_full,
        "isolated_full": isolated_full,
        "rollback": rollback,
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
