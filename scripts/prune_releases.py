"""Prune old immutable release folders: ``python scripts/prune_releases.py``.

Dry-run by default; nothing is deleted without ``--apply``. Protection rules live
in ``jarvis_foundry.deployment.retention`` (CURRENT, PREVIOUS, rollback markers,
the database's live release slot, and the newest ``--keep-recent`` releases are
never candidates).

    python scripts/prune_releases.py                  # show what would go
    python scripts/prune_releases.py --measure        # also size each candidate (slow)
    python scripts/prune_releases.py --apply          # delete the candidates

``--apply`` refuses to run while the owner STOP sentinel exists, and refuses when
the database's live release cannot be read (pass ``--skip-db-check`` only if you
know the database is unavailable on purpose). It mutates the live install's
``releases`` directory only; pointers, database and Scheduler are untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.deployment.layout import default_live_foundry_home, install_root  # noqa: E402
from jarvis_foundry.deployment.retention import (  # noqa: E402
    apply_release_retention,
    plan_release_retention,
)


def _database_active(root: Path, foundry_home: Path) -> tuple[list[str], str | None]:
    """Return (protected SHAs from the live DB slot, error text if unreadable)."""
    try:
        from jarvis_foundry.deployment.release import release_authority_report

        report = release_authority_report(root=root, foundry_home=foundry_home)
    except Exception as error:  # noqa: BLE001 - reported to the caller
        return [], f"{type(error).__name__}: {error}"
    if report.get("status") in {"FOUNDRY_HOME_MISSING", "DATABASE_MISSING"}:
        return [], f"release authority unavailable ({report.get('status')})"
    shas = [str(report[key]) for key in ("current", "previous", "database_active_source_sha") if report.get(key)]
    return shas, None


def _gib(size: int) -> str:
    return f"{size / 1024**3:.1f} GiB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, help="install root (default: %%LOCALAPPDATA%%\\JARVIS)")
    parser.add_argument("--foundry-home", type=Path, help="foundry home (default: live)")
    parser.add_argument("--keep-recent", type=int, default=3)
    parser.add_argument("--measure", action="store_true", help="size every candidate (slow)")
    parser.add_argument("--apply", action="store_true", help="actually delete the candidates")
    parser.add_argument("--skip-db-check", action="store_true", help="do not require the live DB slot to be readable")
    parser.add_argument("--json", type=Path, help="write the plan/audit to this file")
    args = parser.parse_args(argv)

    root = install_root(args.root)
    home = (args.foundry_home or default_live_foundry_home()).resolve()
    db_shas, db_error = _database_active(root, home)
    if db_error and args.apply and not args.skip_db_check:
        print(f"REFUSED: {db_error}. The live release slot is a protection input; "
              "fix that or pass --skip-db-check.", file=sys.stderr)
        return 2

    plan = plan_release_retention(
        root, keep_recent=args.keep_recent, database_active=db_shas, measure=args.measure,
    )
    if plan.refusal:
        print(f"REFUSED: {plan.refusal}", file=sys.stderr)
        return 2

    print(f"install root : {plan.root}")
    print(f"protected    : {len(plan.protected)} release(s)")
    for sha, reasons in sorted(plan.protected.items()):
        print(f"  keep {sha[:12]}  {'; '.join(dict.fromkeys(reasons))}")
    size = f" ({_gib(plan.candidate_bytes)})" if args.measure else ""
    print(f"candidates   : {len(plan.candidates)} release(s){size}")
    if db_error:
        print(f"note         : live DB slot not read ({db_error})")

    payload: dict = {"plan": plan.to_payload()}
    exit_code = 0
    if args.apply:
        from jarvis_foundry.paths import FoundryPaths

        stop_active = FoundryPaths.discover(home).emergency_stop.exists()
        try:
            audit = apply_release_retention(plan, stop_active=stop_active)
        except RuntimeError as error:
            print(f"REFUSED: {error}", file=sys.stderr)
            return 2
        payload["audit"] = audit
        print(f"deleted      : {len(audit['deleted'])}; skipped: {len(audit['skipped'])}")
        for item in audit["skipped"]:
            print(f"  skipped {item['sha'][:12]}  {item['reason']}")
        exit_code = 0 if not audit["skipped"] else 1
    else:
        print("dry run only; re-run with --apply to delete the candidates")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
