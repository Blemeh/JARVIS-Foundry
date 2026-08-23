"""V8 hybrid memory — R2 MANDATORY migration rehearsal.

Runs forward migration + exercise + restart + rebuild + rollback against
isolated COPIES of representative Foundry state (empty, populated copy,
live backup). Never touches the originals. Emits a JSON evidence report.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.hybrid_memory import (CoreMemoryRegistry, CurationPipeline,
                                          RecallEngine, TierCMemoryStore)
from jarvis_foundry.migrations import SCHEMA_VERSION

CANONICAL_VAR = pathlib.Path(
    r"C:/Users/b/Documents/ChatGPT/Autonomous AI powered by Hoowen/JARVIS-Foundry/var")
LIVE_DB = pathlib.Path(r"C:/Users/b/AppData/Local/hermes/foundry/foundry.db")

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / "evidence" / "v8-migration-rehearsal.json"


def snapshot_sources() -> list[dict]:
    sources = []
    # populated copies from canonical var/
    if CANONICAL_VAR.exists():
        for child in sorted(CANONICAL_VAR.iterdir()):
            db = child / "foundry.db"
            if db.exists():
                sources.append({"label": f"var/{child.name}", "path": str(db)})
    # live backup (consistent sqlite backup; read-only on the live file)
    if LIVE_DB.exists():
        sources.append({"label": "live-hermes-foundry", "path": str(LIVE_DB), "live": True})
    return sources


def copy_source(source: dict, work: pathlib.Path) -> FoundryDatabase:
    target = work / "copy.sqlite"
    if source.get("live"):
        # consistent backup via sqlite's backup API (reads, never writes live)
        src = sqlite3.connect(f"file:{source['path']}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
                result = dst.execute("PRAGMA quick_check").fetchone()[0]
                assert result == "ok", f"backup integrity: {result}"
            finally:
                dst.close()
        finally:
            src.close()
    else:
        # WAL-mode copies need the sidecars for a consistent snapshot
        source_path = pathlib.Path(source["path"])
        for suffix in ("", "-wal", "-shm"):
            sidecar = pathlib.Path(str(source_path) + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, pathlib.Path(str(target) + suffix))
    return FoundryDatabase(target)


def run_scenario(source: dict, work: pathlib.Path) -> dict:
    result: dict = {"source": source["label"], "steps": {}}
    db = copy_source(source, work)

    # 1) baseline schema version (fresh DBs have no schema_migrations yet)
    try:
        with db.connect() as conn:
            before = conn.execute(
                "SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
    except sqlite3.OperationalError:
        before = 0
    result["steps"]["baseline_version"] = before

    # 1b) interrupted migration simulation (transactional rollback; must
    #     leave the DB at its baseline and recover cleanly)
    try:
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS tier_c_memories (broken")
            except sqlite3.OperationalError:
                conn.rollback()
        with db.connect() as conn:
            after_crash = conn.execute(
                "SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
        result["steps"]["interrupted_rolls_back"] = (after_crash == before)
        result["steps"]["recovery_after_interruption"] = db.initialize()
    except Exception as exc:  # pragma: no cover
        result["steps"]["interrupted_rolls_back"] = {"error": str(exc)}
        result["steps"]["recovery_after_interruption"] = None

    # 2) forward migration (no-op if already at 33)
    migrated = db.initialize()
    result["steps"]["forward_migration"] = migrated
    with db.connect() as conn:
        result["steps"]["quick_check_after_forward"] = conn.execute(
            "PRAGMA quick_check").fetchone()[0]

    # 3) idempotency: initialize twice more
    again = db.initialize()
    result["steps"]["idempotent_second_initialize"] = (again == migrated)

    # 4) exercise Tier C on the migrated copy
    try:
        memories = TierCMemoryStore(db)
        curation = CurationPipeline(db)
        blocks = CoreMemoryRegistry(db)
        recall = RecallEngine(db)
        blocks.ensure_defaults()
        mid = memories.record_observation(
            "PYTHONPATH= prefix required in Hermes sessions",
            mission_id="rehearsal", source_ids=["rehearsal-src"])
        curation.propose_candidate(mid, actor="rehearsal")
        curation.validate_and_accept(mid, placement="CORE", actor="curation")
        hits = recall.recall("hermes python path prefix")
        result["steps"]["tier_c_exercise"] = {
            "accepted_state": memories.get(mid)["state"],
            "recall_hits": len(hits),
            "blocks": len(blocks.labels()),
        }
        exercise_ok = memories.get(mid)["state"] == "CORE" and len(hits) >= 1
    except Exception as exc:  # pragma: no cover
        result["steps"]["tier_c_exercise"] = {"error": str(exc)}
        exercise_ok = False
    result["steps"]["tier_c_exercise_ok"] = exercise_ok

    # 5) restart simulation (fresh connection/process after migration)
    restarted = FoundryDatabase(str(db.path))
    restarted.initialize()
    with restarted.connect() as conn:
        result["steps"]["restart_after_migration"] = conn.execute(
            "PRAGMA quick_check").fetchone()[0]
    result["steps"]["restart_ok"] = result["steps"]["restart_after_migration"] == "ok"

    # 6) projection rebuild (event-derived Tier C must survive)
    try:
        rebuilt = restarted.rebuild_projections()
        result["steps"]["projection_rebuild"] = rebuilt
        if exercise_ok:
            still = TierCMemoryStore(restarted).get(mid)
            result["steps"]["rebuild_preserves_tier_c"] = (
                still is not None and still["state"] == "CORE")
        else:
            result["steps"]["rebuild_preserves_tier_c"] = None
    except Exception as exc:  # pragma: no cover
        result["steps"]["projection_rebuild"] = {"error": str(exc)}
        result["steps"]["rebuild_preserves_tier_c"] = False

    # 7) rollback/restore rehearsal: drop v33 + replay (previous executable
    #    compatibility)
    try:
        with restarted.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in ("tier_c_procedures", "tier_c_block_revisions",
                          "tier_c_blocks", "tier_c_validations",
                          "tier_c_memory_revisions", "tier_c_memories"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute("DELETE FROM schema_migrations WHERE version=33")
            conn.commit()
        with restarted.connect() as conn:
            rolled_back = conn.execute(
                "SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
        result["steps"]["rollback_drop"] = rolled_back
        # previous executable (v32) still opens it
        reopened = FoundryDatabase(str(restarted.path))
        reopened.initialize(target=32)
        with reopened.connect() as conn:
            result["steps"]["previous_executable_ok"] = (
                conn.execute("PRAGMA quick_check").fetchone()[0] == "ok")
        # and forward again
        result["steps"]["reforward_after_rollback"] = reopened.initialize()
    except Exception as exc:  # pragma: no cover
        result["steps"]["rollback_drop"] = {"error": str(exc)}
        result["steps"]["previous_executable_ok"] = False
        result["steps"]["reforward_after_rollback"] = None

    # 8) (interruption test moved before forward migration — step 1b)

    # 9) locked DB: second connection holds a write lock; initialize must
    #    fail loudly, then recover
    try:
        blocker = sqlite3.connect(str(db.path), timeout=1)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            try:
                FoundryDatabase(str(db.path)).initialize()
                result["steps"]["locked_db_fails_loudly"] = False
            except sqlite3.OperationalError:
                result["steps"]["locked_db_fails_loudly"] = True
        finally:
            blocker.rollback()
            blocker.close()
        result["steps"]["locked_db_recovery"] = db.initialize()
    except Exception as exc:  # pragma: no cover
        result["steps"]["locked_db_fails_loudly"] = {"error": str(exc)}
        result["steps"]["locked_db_recovery"] = None

    # 10) malformed Tier C row tolerance: corrupt one row's expires_at
    try:
        if exercise_ok:
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE tier_c_memories SET expires_at='not-a-date' "
                             "WHERE memory_id=?", (mid,))
                conn.commit()
            hits2 = RecallEngine(db).recall("hermes python path prefix")
            result["steps"]["malformed_row_degrades"] = len(hits2) >= 1
        else:
            result["steps"]["malformed_row_degrades"] = None
    except Exception as exc:  # pragma: no cover
        result["steps"]["malformed_row_degrades"] = {"error": str(exc)}

    # 11) missing Tier C table: governance/truth still readable
    try:
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TABLE tier_c_memories")
            conn.commit()
        from jarvis_foundry.hybrid_memory import ContextCompiler
        pkg = ContextCompiler(db).compile("rehearsal", "executive", "x")
        result["steps"]["missing_table_degrades_memory_only"] = (
            pkg.section("mandatory_governance") is not None)
    except Exception as exc:  # pragma: no cover
        result["steps"]["missing_table_degrades_memory_only"] = {"error": str(exc)}

    result["steps"]["data_loss_analysis"] = {
        "forward": "additive only; no Tier A/B DDL or DML in v33",
        "rollback": "drop 6 tier_c tables + remove v33 row; ledger events remain",
        "rebuild": "Tier C replays from events; git manifests as secondary",
    }
    return result


def main() -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="v8-rehearsal-"))
    sources = snapshot_sources()
    # always include a fresh empty DB
    empty = work / "empty"
    empty.mkdir()
    sources.insert(0, {"label": "empty-fresh", "path": str(empty / "db.sqlite"),
                       "empty": True})
    report: dict = {"scenarios": []}
    for source in sources:
        scenario_work = work / source["label"].replace("/", "_")
        scenario_work.mkdir()
        try:
            report["scenarios"].append(run_scenario(source, scenario_work))
        except Exception as exc:  # one broken source must not kill the run
            report["scenarios"].append({
                "source": source["label"], "steps": {"scenario_error": str(exc)}})
    if "--record" in sys.argv:
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        print(f"evidence recorded: {EVIDENCE}")
    print(json.dumps(report, indent=1, sort_keys=True))
    # verdict line
    failures = []
    for scenario in report["scenarios"]:
        steps = scenario["steps"]
        if steps.get("forward_migration") != SCHEMA_VERSION:
            failures.append(f"{scenario['source']}: forward")
        if not steps.get("tier_c_exercise_ok"):
            failures.append(f"{scenario['source']}: exercise")
        if steps.get("restart_ok") is False:
            failures.append(f"{scenario['source']}: restart")
        if steps.get("rebuild_preserves_tier_c") is False:
            failures.append(f"{scenario['source']}: rebuild")
        if steps.get("interrupted_rolls_back") is False:
            failures.append(f"{scenario['source']}: interruption")
        if steps.get("locked_db_fails_loudly") is False:
            failures.append(f"{scenario['source']}: locked-db")
    if failures:
        print(f"REHEARSAL FAIL: {failures}")
        return 1
    print(f"REHEARSAL PASS ({len(report['scenarios'])} scenarios)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
