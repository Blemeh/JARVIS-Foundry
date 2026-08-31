"""Read-only Lane 2 runtime truth and disposable recovery rehearsal.

This probe deliberately has no promotion/retarget path.  It inspects the
configured runtime, then uses SQLite's backup API to rebuild/reconcile a
temporary copy.  A final SHA may be supplied for parity checking once Lane 1
delivers it; the live release and Scheduler are never changed here.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.status import production_status
from jarvis_foundry.reconcile import inspect as inspect_ledger


def _git_head(repo: Path | None) -> str | None:
    if repo is None or not (repo / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _rehearse(database_path: Path) -> dict[str, Any]:
    if not database_path.is_file():
        return {"status": "NOT_TESTED", "reason": "live database missing"}
    with tempfile.TemporaryDirectory(prefix="jarvis-lane2-recovery-") as raw:
        clone_path = Path(raw) / "foundry.db"
        source = FoundryDatabase(database_path)
        source.backup_to(clone_path)
        clone = FoundryDatabase(clone_path)
        # The only writes in this probe are to the disposable backup.  This
        # exercises restart-safe projection rebuild without touching live.
        schema = clone.initialize()
        event_count = clone.rebuild_projections()
        report = inspect_ledger(clone).to_json()
        return {
            "status": "PASS" if report.get("consistent") else "FAIL",
            "path": "<temporary disposable copy>",
            "schema": schema,
            "integrity": clone.quick_check(readonly=True),
            "event_count": event_count,
            "reconciliation": report,
            "live_mutated": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lane2_runtime_truth")
    parser.add_argument("--home", type=Path, default=None,
                        help="Foundry home to inspect")
    parser.add_argument("--install-root", type=Path, default=None,
                        help="JARVIS install root containing CURRENT")
    parser.add_argument("--source-repo", type=Path, default=None,
                        help="optional source checkout for canonical SHA")
    parser.add_argument("--expected-sha", default=None,
                        help="optional Lane 1 SHA; read-only parity check")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.install_root is not None:
        os.environ["JARVIS_INSTALL_ROOT"] = str(args.install_root.resolve())
    status = production_status(home=args.home, source_repo=args.source_repo)
    source_sha = _git_head(args.source_repo)
    expected = str(args.expected_sha).strip() if args.expected_sha else None
    preflight_values = {
        "expected_sha": expected,
        "canonical_source_sha": source_sha,
        "installed_release_sha": status.get("current_release"),
    }
    known = [value for value in preflight_values.values() if value]
    preflight_values["status"] = (
        "NOT_TESTED" if expected is None else
        "PASS" if len(known) == 3 and len(set(known)) == 1 else "FAIL"
    )
    database_path = Path(str(status["live_db"]))
    result: dict[str, Any] = {
        "scope": "read-only live inspection + disposable DB rehearsal",
        "production_mutation": False,
        "status": status,
        "sha_preflight": preflight_values,
        "disposable_recovery_rehearsal": _rehearse(database_path),
    }
    encoded = json.dumps(result, indent=2, sort_keys=True, default=str)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
