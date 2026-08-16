"""V8 canonical promotion — ROLLBACK DRILL (disposable, never touches live).

Chain: pre-V8 database copy -> apply v33 migration -> Tier C activity ->
verify V8 state -> restore the pre-V8 backup -> run the PREVIOUS
executable (canonical 93a3ddc code) against the restored DB -> verify
previous behavior + STOP state + no orphan Tier C.

Rollback strategy: RESTORE-FROM-BACKUP (no reverse migration exists; v33
is additive and the ledger is Tier C's source of truth, so the pre-V8
backup is the authoritative restore artifact).

Modes:
- PRE-promotion (default): snapshots the live DB (schema 32) as the
  pre-V8 copy. Passes while the live DB is still v32.
- POST-promotion (--pre-v8-source <path>): the live DB is already v33, so
  the pre-V8 copy must be the verified recovery artifact (e.g.
  C:/Users/b/AppData/Local/Temp/v8-promotion-recovery/pre-v8-foundry-backup.sqlite).
  The drill then proves the full restore chain against the ACTUAL
  recovery artifact. When the live DB is v33 and no source is given, the
  drill FAILS with a clear message (pre-promotion premises no longer hold)
  rather than silently testing the wrong thing.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / "evidence" / "v8-canonical-promotion" / "rollback-drill.json"

CANONICAL_ROOT = pathlib.Path(
    r"C:/Users/b/Documents/ChatGPT/Autonomous AI powered by Hoowen/JARVIS-Foundry")
CANONICAL_VENV_PY = CANONICAL_ROOT / ".venv" / "Scripts" / "python.exe"
LIVE_DB = pathlib.Path(r"C:/Users/b/AppData/Local/hermes/foundry/foundry.db")


def _live_schema() -> int:
    with sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True) as con:
        return int(con.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0])


def backup_live(target: pathlib.Path) -> None:
    """Consistent sqlite backup (read-only on the live file)."""
    src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            result = dst.execute("PRAGMA quick_check").fetchone()[0]
            assert result == "ok", result
        finally:
            dst.close()
    finally:
        src.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="V8 rollback drill")
    parser.add_argument("--pre-v8-source", type=pathlib.Path, default=None,
                        help="verified pre-V8 backup to use as the pre-V8 copy "
                             "(required once the live DB is already v33)")
    parser.add_argument("--record", action="store_true",
                        help="write the evidence JSON to evidence/v8-canonical-promotion/")
    args = parser.parse_args()

    work = pathlib.Path(tempfile.mkdtemp(prefix="v8-rollback-"))
    report: dict = {"strategy": "RESTORE-FROM-BACKUP (no reverse migration; "
                                "v33 is additive, ledger is Tier C's source of truth)"}
    steps: dict = {}

    # 1) pre-V8 copy: live snapshot (pre-promotion) OR the verified recovery
    #    artifact (post-promotion)
    live_schema = _live_schema()
    steps["live_schema"] = live_schema
    if args.pre_v8_source is not None:
        if not args.pre_v8_source.exists():
            print(f"ROLLBACK DRILL FAIL: --pre-v8-source not found: {args.pre_v8_source}")
            return 1
        pre = work / "pre-v8.sqlite"
        shutil.copy2(args.pre_v8_source, pre)
        steps["pre_v8_source"] = str(args.pre_v8_source)
    elif live_schema == 32:
        pre = work / "pre-v8.sqlite"
        backup_live(pre)
        steps["pre_v8_source"] = "live-snapshot"
    else:
        print("ROLLBACK DRILL FAIL: live DB is already schema %d; pass "
              "--pre-v8-source <verified pre-V8 backup> (post-promotion mode) "
              "or run this drill on a pre-promotion machine." % live_schema)
        return 1
    with sqlite3.connect(pre) as con:
        steps["pre_v8_schema"] = con.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        steps["pre_v8_stop_directives"] = [
            r[0] for r in con.execute(
                "SELECT command_type FROM directives WHERE active=1").fetchall()]
        steps["pre_v8_events"] = con.execute(
            "SELECT COUNT(*) FROM events").fetchone()[0]
        steps["pre_v8_quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]

    # 2) apply v33 migration to the copy via the real migration mechanism
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from jarvis_foundry.database import FoundryDatabase
    db = FoundryDatabase(pre)
    migrated = db.initialize()
    steps["forward_migration"] = migrated
    with sqlite3.connect(pre) as con:
        steps["post_migration_schema"] = con.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        steps["tier_c_tables"] = sorted(
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'tier_c_%'").fetchall())

    # 3) Tier C activity on the migrated copy
    from jarvis_foundry.hybrid_memory import (CoreMemoryRegistry,
                                              CurationPipeline,
                                              TierCMemoryStore)
    memories = TierCMemoryStore(db)
    curation = CurationPipeline(db)
    blocks = CoreMemoryRegistry(db)
    blocks.ensure_defaults()
    mid = memories.record_observation(
        "rollback drill lesson", source_ids=["drill"], created_by="drill")
    curation.propose_candidate(mid, actor="drill")
    curation.validate_and_accept(mid, placement="CORE", actor="curation")
    blocks.replace("current_projects", "rollback drill", actor="drill", reason="drill")
    with sqlite3.connect(pre) as con:
        steps["tier_c_activity_verified"] = con.execute(
            "SELECT COUNT(*) FROM tier_c_memories").fetchone()[0] >= 1 and \
            con.execute("SELECT COUNT(*) FROM tier_c_block_revisions").fetchone()[0] >= 2

    # 4) RESTORE: copy the pre-V8 backup back over the migrated copy
    restored = work / "restored.sqlite"
    shutil.copy2(pre, restored)
    with sqlite3.connect(restored) as con:
        steps["restored_schema"] = con.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        steps["restored_has_tier_c"] = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'tier_c_%'").fetchone()[0] > 0
        steps["restored_quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
        steps["restored_events"] = con.execute(
            "SELECT COUNT(*) FROM events").fetchone()[0]
        steps["restored_stop_directives"] = [
            r[0] for r in con.execute(
                "SELECT command_type FROM directives WHERE active=1").fetchall()]

    # 5) PREVIOUS EXECUTABLE (canonical 93a3ddc code + its venv) against the
    #    restored DB — restore must not depend on V8 code
    env = {"PYTHONPATH": str(CANONICAL_ROOT / "src")}
    probe = (
        "import sqlite3; con = sqlite3.connect(r'%s'); "
        "print('schema', con.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]); "
        "print('quick_check', con.execute('PRAGMA quick_check').fetchone()[0]); "
        "print('events', con.execute('SELECT COUNT(*) FROM events').fetchone()[0])" % restored)
    run = subprocess.run([str(CANONICAL_VENV_PY), "-c", probe],
                         capture_output=True, text=True, env=env, timeout=120)
    steps["previous_executable"] = {
        "returncode": run.returncode,
        "stdout": run.stdout.strip(),
        "stderr": run.stderr.strip()[-300:] if run.stderr else "",
    }
    previous_ok = (run.returncode == 0 and "schema 32" in run.stdout
                   and "quick_check ok" in run.stdout)

    # 6) old code can still open + initialize the restored DB (idempotent)
    run2 = subprocess.run(
        [str(CANONICAL_VENV_PY), "-c",
         "import sys; sys.path.insert(0, r'%s/src'); "
         "from jarvis_foundry.database import FoundryDatabase; "
         "db = FoundryDatabase(r'%s'); print('init', db.initialize())"
         % (CANONICAL_ROOT, restored)],
        capture_output=True, text=True, env=env, timeout=120)
    steps["previous_executable_initialize"] = {
        "returncode": run2.returncode, "stdout": run2.stdout.strip(),
        "stderr": run2.stderr.strip()[-300:] if run2.stderr else ""}

    if "--record" in sys.argv:
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps({"steps": steps}, indent=1, sort_keys=True),
                            encoding="utf-8")
        print(f"evidence recorded: {EVIDENCE}")
    print(json.dumps({"steps": steps}, indent=1, sort_keys=True))

    ok = (steps["pre_v8_schema"] == 32
          and steps["post_migration_schema"] == 33
          and len(steps["tier_c_tables"]) == 6
          and steps["tier_c_activity_verified"]
          and steps["restored_schema"] == 32
          and not steps["restored_has_tier_c"]
          and steps["restored_events"] == steps["pre_v8_events"]
          and steps["restored_stop_directives"] == steps["pre_v8_stop_directives"]
          and previous_ok
          and run2.returncode == 0)
    print(f"ROLLBACK DRILL {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
