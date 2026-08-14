"""Mutation testing with A/B proof (R3-D07/C07).

The frozen mutant manifest is applied to an isolated mirror of the R3-owned
sources plus the trusted kernel; the live worktree is never modified.
For every mutant the same selected tests must pass on clean code and fail
on the mutant for the intended reason (the expected test node must appear
in the failing output).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from common import EVIDENCE_DIR, REPO_ROOT, record_enabled, run, sha256_file, write_json

MANIFEST = REPO_ROOT / "scripts" / "remaster" / "r3" / "frozen_mutants.json"


def copy_mirror(root: Path, target: Path) -> None:
    for relative in (
        Path("src/jarvis_foundry"),
        Path("tests/remaster/r3"),
        Path("scripts/remaster/r3"),
    ):
        shutil.copytree(
            root / relative,
            target / relative,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copy2(root / "pyproject.toml", target / "pyproject.toml")


def apply_mutant(mirror: Path, mutant: dict[str, Any]) -> str | None:
    path = mirror / mutant["target"]
    text = path.read_text(encoding="utf-8")
    anchor = mutant["anchor"]
    if anchor not in text:
        return f"anchor not found in {mutant['target']}"
    path.write_text(text.replace(anchor, mutant["replacement"], 1), encoding="utf-8")
    return None


def run_selection(python: Path, mirror: Path, pytest_args: list[str], label: str) -> dict[str, Any]:
    env = {
        "PYTHONPATH": ";".join(
            [
                str(mirror / "src"),
                str(mirror / "scripts" / "remaster" / "r3"),
            ]
        ),
        "PYTHONHASHSEED": "0",
    }
    result = run(
        [str(python), "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", *pytest_args],
        cwd=mirror,
        timeout_seconds=600,
        env=env,
    )
    return {**result, "label": label}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R3_MUTATION.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R3_MUTATION_RAW.json")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--max-mutants", type=int, default=0)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    mutants = manifest["mutants"]
    if args.max_mutants > 0:
        mutants = mutants[: args.max_mutants]
    started = time.monotonic()
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-r3-mutants-"))
    try:
        mirror = temp_dir / "mirror"
        mirror.mkdir()
        copy_mirror(args.root, mirror)

        full_baseline = run_selection(
            args.python,
            mirror,
            ["tests/remaster/r3"],
            "full_baseline",
        )
        results: list[dict[str, Any]] = []
        for mutant in mutants:
            fresh = temp_dir / f"mirror-{mutant['id']}"
            shutil.copytree(mirror, fresh)
            baseline = run_selection(args.python, fresh, mutant["pytest_args"], "mutant_baseline")
            blocked = apply_mutant(fresh, mutant)
            if blocked is not None:
                results.append(
                    {
                        "id": mutant["id"],
                        "status": "BLOCKED",
                        "blocked_reason": blocked,
                    }
                )
                continue
            mutant_result = run_selection(args.python, fresh, mutant["pytest_args"], "mutant")
            expected_present = mutant["expected_test"] in mutant_result["output"]
            killed = (
                baseline["exit_code"] == 0 and mutant_result["exit_code"] != 0 and expected_present
            )
            if baseline["exit_code"] != 0:
                print(f"BASELINE FAIL {mutant['id']}:\n{baseline['output'][:1500]}")
            results.append(
                {
                    "id": mutant["id"],
                    "target": mutant["target"],
                    "status": "KILLED" if killed else "UNKILLED",
                    "baseline_exit_code": baseline["exit_code"],
                    "mutant_exit_code": mutant_result["exit_code"],
                    "expected_test_present": expected_present,
                    "intended_reason": mutant["reason"],
                    "baseline_wall_seconds": baseline["wall_seconds"],
                    "mutant_wall_seconds": mutant_result["wall_seconds"],
                }
            )
            if not killed:
                print(
                    f"UNKILLED {mutant['id']}: baseline={baseline['exit_code']} "
                    f"mutant={mutant_result['exit_code']} "
                    f"expected_present={expected_present}"
                )

        killed_count = sum(1 for r in results if r["status"] == "KILLED")
        total = len(mutants)
        payload = {
            "schema": "jarvis-r3-mutation-v1",
            "manifest": str(args.manifest.relative_to(args.root).as_posix()),
            "manifest_sha256": sha256_file(args.manifest),
            "baseline_tests": full_baseline["label"],
            "baseline_exit_code": full_baseline["exit_code"],
            "baseline_wall_seconds": full_baseline["wall_seconds"],
            "killed": killed_count,
            "total": total,
            "score_percent": round(100.0 * killed_count / total, 1) if total else 0.0,
            "results": results,
            "wall_seconds": round(time.monotonic() - started, 3),
        }
        if record_enabled():
            write_json(args.raw, payload)
            write_json(
                args.output,
                {k: v for k, v in payload.items() if k != "results"},
            )
        print(
            f"mutation score: {killed_count}/{total} killed "
            f"baseline_exit={full_baseline['exit_code']} "
            f"wall={payload['wall_seconds']}s"
        )
        return 0 if killed_count == total and full_baseline["exit_code"] == 0 else 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
