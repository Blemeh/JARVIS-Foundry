"""Fresh R11 verifier subprocess: reproduces every real cycle without builder context."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

from common import EVIDENCE_UTC, safe_clear, write_json
from run_cycles import produce_cycles


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--mirror-root", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--expected-work-package-sha", required=True)
    parser.add_argument("--verifier-id", required=True)
    args = parser.parse_args(argv)
    started = time.monotonic()
    evidence_dir = Path(args.evidence_dir)
    mirror_root = Path(args.mirror_root)
    safe_clear(mirror_root)
    mirror_root.mkdir(parents=True, exist_ok=True)

    reproduced = produce_cycles(mirror_root / "proving", args.python, EVIDENCE_UTC)
    counts = reproduced["counts"]
    rollback = reproduced["rollback"]
    waiting = reproduced["waiting"]
    restart = reproduced["restart"]
    cycle_checks = all(
        record["verification"]["verdict"] == "PASS"
        for record in reproduced["cycles"]
        if record["outcome"] == "promoted"
    )
    reproduction_ok = bool(
        counts["cycles"] >= 3
        and counts["promoted"] >= 2
        and counts["r1_promotions"] >= 1
        and counts["r2_promotions"] >= 1
        and counts["rejected"] >= 1
        and rollback["independent_probe_ok"]
        and waiting["honest_waiting"]
        and restart["restart_cycle"].get("clone_replayed_without_duplicate_effect")
        and reproduced["ledger"]["chain_valid"]
        and cycle_checks
    )

    tests = subprocess.run(
        [args.python, "-m", "pytest", "tests/remaster/r11", "-q"],
        cwd=str(args.worktree),
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    summary = ""
    for line in reversed(tests.stdout.splitlines()):
        if "passed" in line or "failed" in line:
            summary = line.strip()
            break
    work_package = (
        Path(args.worktree) / "docs" / "remaster" / "work_packages" / "R11_WORK_PACKAGE.json"
    )
    work_package_sha = _sha256_file(work_package)
    evidence_files = sorted(evidence_dir.rglob("*"))
    evidence_bytes = sum(
        path.stat().st_size
        for path in evidence_files
        if path.is_file()
        and path.name not in {"R11_BUILDER_REPORT.json", "R11_ARTIFACT_MANIFEST.json"}
        and not path.name.startswith("R11_PROGRESS_")
    )
    passed = bool(
        work_package_sha == args.expected_work_package_sha.lower()
        and reproduction_ok
        and tests.returncode == 0
        and evidence_bytes < 8_388_608
        and evidence_files
    )
    payload = {
        "schema": "jarvis-r11-fresh-verifier-reproduction-v1",
        "verifier_id": args.verifier_id,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "passed": passed,
        "work_package_sha256": work_package_sha,
        "expected_work_package_sha256": args.expected_work_package_sha.lower(),
        "reproduction": {
            "counts": counts,
            "cycle_verdicts_ok": cycle_checks,
            "rollback_independent_probe_ok": rollback["independent_probe_ok"],
            "waiting_honest": waiting["honest_waiting"],
            "restart_no_duplicate_effect": restart["restart_cycle"].get(
                "clone_replayed_without_duplicate_effect"
            ),
            "ledger_chain_valid": reproduced["ledger"]["chain_valid"],
            "ledger_event_count": reproduced["ledger"]["event_count"],
        },
        "focused_tests": {
            "exit_code": tests.returncode,
            "summary": summary,
        },
        "evidence_file_count": len(evidence_files),
        "evidence_bytes": evidence_bytes,
        "evidence_under_budget": evidence_bytes < 8_388_608,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    output = evidence_dir / "R11_VERIFIER_REPRODUCTION.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, payload)
    print(
        f"verifier {args.verifier_id}: passed={passed} "
        f"cycles={counts['cycles']} promoted={counts['promoted']} "
        f"rejected={counts['rejected']} evidence_bytes={evidence_bytes} "
        f"tests={tests.returncode}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
