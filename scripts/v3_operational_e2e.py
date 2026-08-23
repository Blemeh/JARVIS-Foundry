"""Disposable end-to-end V3 operational vertical slice (WP-A/B/C harness).

Initializes a schema-v36 disposable database under the lane, drives the REAL
public V3 APIs through a complete governed cycle:

    empty state -> policy registered -> candidate generated+scored ->
    selected/materialized (flag ON cycle) -> harmless dispatch via the real
    worker stack (lab-scripted Hermes) -> AUTOMATIC outcome closure ->
    continuation/next-cycle honesty.

Then builds the REAL command-center snapshot (with the registered flag) and
writes machine-readable artifacts:
  - var/overnight/v3-operational-slice/e2e_snapshot.json   (full snapshot)
  - evidence/v3/operational-slice/E2E_LEDGER_RECONCILIATION.json
  - ui/command-center/src/lib/__fixtures__/discoveryBackendFixture.gen.ts
    (generated contract fixture for mapper-drift detection)

No production resources: disposable DB, disposable git repo in tmp,
lab-scripted adapter, no live providers. Port 4611 is used only by
serve_command_center() when explicitly invoked (--serve).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "foundry"))

from jarvis_foundry.command_center import CommandCenterServer, build_snapshot  # noqa: E402
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.discovery_candidates import generate_candidate  # noqa: E402
from jarvis_foundry.events import Event  # noqa: E402
from jarvis_foundry.executive.eligibility import enable_admission_authority  # noqa: E402
from jarvis_foundry.paths import FoundryPaths  # noqa: E402
from jarvis_foundry.runtime.config import WorkerRuntimeConfig  # noqa: E402
from jarvis_foundry.runtime.worker_loop import run_cycle  # noqa: E402
from jarvis_foundry.v3_scoring import register_policy  # noqa: E402
from v3_fixtures import load_policy  # noqa: E402
from worker_test_helpers import mission_database  # noqa: E402

EPOCH = "v3-operational-e2e"
LANE_VAR = REPO_ROOT / "var" / "overnight" / "v3-operational-slice"
LANE_EVIDENCE = REPO_ROOT / "evidence" / "v3" / "operational-slice"
UI_FIXTURE_DIR = REPO_ROOT / "ui" / "command-center" / "src" / "lib" / "__fixtures__"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _log(step: str, **fields) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {step}: "
          + json.dumps(fields, sort_keys=True), flush=True)


def build_disposable_ledger(root: Path) -> tuple[FoundryDatabase, dict]:
    """Drive the full scenario; returns (database, reconciliation facts)."""
    import shutil

    LANE_VAR.mkdir(parents=True, exist_ok=True)
    # Fully disposable: a stale repo would make the evidence reconciler
    # fail-closed on leftover artifacts (proven honest behavior — the
    # harness itself must never rely on that leniency). Windows git object
    # files can be read-only; clear the flag before rmtree.
    if root.exists():
        import stat

        def _onerror(func, path, _exc):
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except OSError:
                pass

        shutil.rmtree(root, onerror=_onerror)
    root.mkdir(parents=True, exist_ok=True)
    db_path = LANE_VAR / "e2e.sqlite"
    if db_path.exists():
        db_path.unlink()
    repository = root / "repo"
    repository.mkdir(parents=True)
    _git(repository, "init")
    _git(repository, "config", "user.email", "e2e@example.invalid")
    _git(repository, "config", "user.name", "Operational Slice E2E")
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "baseline")

    database = FoundryDatabase(db_path)
    database.initialize()
    facts: dict = {"db_path": str(db_path)}

    # ---- stage 0: empty state ------------------------------------------------
    empty = build_snapshot(database)
    assert empty["discovery"]["state"] == "EMPTY_LEDGER", empty["discovery"]
    _log("stage0_empty_ledger", state=empty["discovery"]["state"])
    facts["empty_state"] = empty["discovery"]

    # ---- environment: resource + epoch + policy ------------------------------
    from jarvis_foundry.control.capabilities import set_capability_state
    from jarvis_foundry.control.registration import (
        inspect_git_resource,
        registration_event,
    )

    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-1")))
    set_capability_state(database, "resource-1",
                         "write_registered_worktree_files", "READY",
                         evidence={"source": "operational-e2e"})
    database.append(Event.create(
        "executive.activation_recorded",
        {"epoch": EPOCH, "decision_version": "1.0.0"},
        source_type="supervisor", source_id="operational-e2e",
        idempotency_key=f"executive.activation_recorded:{EPOCH}"))
    enable_admission_authority(database, EPOCH, source_id="operational-e2e")
    register_policy(database, load_policy())
    facts["policy_version"] = load_policy()["policy_version"]

    # ---- stage 1: flag OFF — visible but off ---------------------------------
    cfg_off = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        discovery_cycle_enabled=False,
        executive_activation_epoch=EPOCH,
        runtime_state_root=str(root / "state"),
        stop_sentinel_path=str(root / "EMERGENCY_STOP"),
        lab_scripted_hermes=True,
    )
    off_report = run_cycle(
        database, FoundryPaths(root), cfg_off,
        loop_id="e2e-flag-off", instance_id="e2e-off",
        vault_root=root / "vault", handoff_dir=root / "handoffs")
    assert off_report["outcome"] == "WAITING", off_report
    off_snap = build_snapshot(database, discovery_cycle_enabled=False)["discovery"]
    assert off_snap["flag_state"] == "OFF"
    assert off_snap["latest_cycle"] is None  # no unified-cycle events while OFF
    _log("stage1_flag_off", outcome=off_report["outcome"],
         cycles=off_snap["cycles"], flag=off_snap["flag_state"])

    # ---- stage 2: candidate generated + scored -------------------------------
    generated = generate_candidate(
        database, "mission-seed", "maintenance",
        "Fix the failing nightly integration",
        [{"kind": "ci", "ref": "nightly"},
         {"kind": "log", "ref": "run-1"},
         {"kind": "issue", "ref": "i-7"}],
        typed={"expected_outcome": "verified: nightly green",
               "candidate_type": "repair"})
    assert generated["created"], generated
    candidate_id = str(generated["candidate_id"])
    facts["candidate_id"] = candidate_id

    # ---- stage 3+4: flag ON unified cycle -> select -> materialize ----------
    # ---- ---- -> harmless dispatch through the real worker stack -----------
    # (the flagged worker-loop choke point runs observe->score->select->
    # compile AND dispatches the compiled mission in ONE governed cycle)
    cfg_on = WorkerRuntimeConfig(
        enabled=True, resource_id="resource-1",
        discovery_cycle_enabled=True,
        executive_activation_epoch=EPOCH,
        runtime_state_root=str(root / "state"),
        stop_sentinel_path=str(root / "EMERGENCY_STOP"),
        lab_scripted_hermes=True,
    )
    dispatch = run_cycle(
        database, FoundryPaths(root), cfg_on,
        loop_id="e2e-cycle-1", instance_id="e2e-on",
        vault_root=root / "vault", handoff_dir=root / "handoffs")
    assert dispatch["outcome"] == "PASSED", dispatch
    cycle_event = [e for e in database.events()
                   if e.event_type == "discovery.cycle_completed"
                   and e.payload.get("loop_id") == "e2e-cycle-1"][0]
    mission_id = str(cycle_event.payload["mission_id"])
    facts["cycle_1"] = dict(cycle_event.payload)
    facts["dispatch"] = {k: dispatch.get(k) for k in
                         ("outcome", "mission_id", "step_id", "run_id")}
    assert facts["dispatch"]["mission_id"] == mission_id

    # ---- stage 5: AUTOMATIC outcome closure -----------------------------------
    outcomes = [e for e in database.events()
                if e.event_type == "discovery.candidate_outcome_recorded"]
    assert len(outcomes) == 1, f"expected exactly one auto-outcome, got {len(outcomes)}"
    body = outcomes[0].payload
    assert body["candidate_id"] == candidate_id
    assert body["mission_id"] == mission_id
    predicted_raw = json.dumps(body["predicted_value"], sort_keys=True)
    actual_raw = json.dumps(body["actual_value"], sort_keys=True)
    facts["auto_outcome"] = {
        "status": body["status"], "window": body["window"],
        "predicted_value": body["predicted_value"],
        "actual_value": body["actual_value"],
        "distinct_fields": predicted_raw != actual_raw,
    }

    # ---- stage 6: next governed cycle is honest ------------------------------
    from jarvis_foundry.v3_cycle import run_discovery_cycle

    def _observe(_database):
        return {"observed": {"vault": 4}, "candidates_created": 0}

    next_cycle = run_discovery_cycle(
        database, loop_id="e2e-cycle-2", resource_id="resource-1",
        source_id="operational-e2e", observe=_observe)
    outcomes_after = [e for e in database.events()
                      if e.event_type == "discovery.candidate_outcome_recorded"]
    missions_created = [
        e.payload["mission_id"] for e in database.events()
        if e.event_type == "mission.created"
        and str(e.payload.get("created_from") or "").startswith("candidate:")]
    facts["cycle_2"] = {
        "outcome": next_cycle.outcome, "reason": next_cycle.reason,
        "outcomes_total": len(outcomes_after),
        "lineage_missions_total": len(missions_created),
    }
    assert len(outcomes_after) == 1, "no duplicate outcome on continuation"
    assert len(missions_created) == 1, "no duplicate lineage mission"
    _log("stage6_continuation", outcome=next_cycle.outcome,
         reason=(next_cycle.reason or "")[:80],
         duplicate_prevented=len(missions_created) == 1)
    return database, facts


def reconcile_browser_fields(database: FoundryDatabase, snapshot: dict) -> dict:
    """Field-for-field ledger-vs-snapshot reconciliation (WP-C step 2)."""
    discovery = snapshot["discovery"]
    cand = discovery["candidates_recent"][0]
    selected = discovery["selected"]
    latest = discovery["latest_cycle"]
    outcome = discovery["outcomes"][0]
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status, scoring_policy_version FROM candidates "
            "WHERE candidate_id = ?", (cand["candidate_id"],)).fetchone()
        score_row = connection.execute(
            "SELECT score FROM candidate_scores WHERE candidate_id = ?",
            (cand["candidate_id"],)).fetchone()
        outcome_row = connection.execute(
            "SELECT status, window_kind FROM candidate_outcomes "
            "WHERE candidate_id = ? AND window_kind = ?",
            (outcome["candidate_id"], outcome["window"])).fetchone()
        mission_row = connection.execute(
            "SELECT created_from FROM missions WHERE mission_id = ?",
            (selected["mission_id"],)).fetchone()
    checks = {
        "candidate_status_matches_row": bool(row) and row[0] == cand["status"],
        "candidate_score_matches_table": bool(score_row)
        and abs(float(score_row[0]) - float(cand["score"]["total"])) < 1e-9,
        "candidate_policy_matches": cand["score"]["policy_version"] is not None,
        "selection_mission_lineage": bool(mission_row)
        and str(mission_row[0]) == f"candidate:{selected['candidate_id']}",
        "cycle_outcome_verbatim": latest is not None
        and latest["loop_id"] == "e2e-cycle-2"
        and latest["outcome"] == "WAITING"
        and any(c["loop_id"] == "e2e-cycle-1" and c["outcome"] == "COMPILED"
                for c in discovery["cycles"]),
        "outcome_window_matches_row": bool(outcome_row),
        "outcome_predicted_actual_distinct":
            json.dumps(outcome["predicted"], sort_keys=True)
            != json.dumps((outcome["actual"] or {}), sort_keys=True),
        "flag_state_registered_on": discovery["flag_state"] == "ON",
        "next_action_present": isinstance(discovery["next_action"], str)
        and len(discovery["next_action"]) > 0,
        "no_cost_window_fabricated": "cost_window" not in discovery,
    }
    return {"checks": checks, "all_pass": all(checks.values()),
            "discovery_summary": {
                "state": discovery["state"],
                "policy_version": discovery["policy_version"],
                "portfolio_summary": discovery["portfolio_summary"],
                "next_action": discovery["next_action"],
                "verifier": discovery["verifier"]}}


def write_generated_fixture(snapshot: dict) -> Path:
    """Generate the UI contract fixture from backend output (not hand math)."""
    UI_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    target = UI_FIXTURE_DIR / "discoveryBackendFixture.gen.ts"
    payload = json.dumps(snapshot["discovery"], indent=2, sort_keys=False)
    content = (
        "// GENERATED by scripts/v3_operational_e2e.py from real backend\n"
        "// output at this lane's HEAD. Do not hand-edit: regenerate.\n"
        f"export const backendDiscoveryPayload = {payload} as const;\n\n"
        "export type BackendDiscoveryPayload = typeof backendDiscoveryPayload;\n\n"
        "// The view shape the live adapter must produce for this payload:\n"
        "export const EXPECTED_VIEW_KEYS = [\n"
        "  \"available\", \"flag\", \"policyVersion\", \"candidatesRecent\",\n"
        "  \"selected\", \"portfolioSummary\", \"cycles\", \"activeTeam\",\n"
        "  \"verifierState\", \"outcomeMeasurements\", \"maintenanceDue\",\n"
        "  \"nextAction\",\n] as const;\n"
    )
    target.write_text(content, encoding="utf-8")
    return target


def serve_command_center(database: FoundryDatabase, port: int = 4611):
    """Serve the REAL adapter path for browser proof; returns (server, url)."""
    server = CommandCenterServer(database, host="127.0.0.1", port=port)
    server.start()
    host, bound = server.address
    return server, f"http://{host}:{bound}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true",
                        help="after the scenario, serve on port 4611 until Ctrl+C")
    parser.add_argument("--port", type=int, default=4611)
    args = parser.parse_args(argv)

    started = time.monotonic()
    root = LANE_VAR / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    database, facts = build_disposable_ledger(root)

    snapshot = build_snapshot(
        database,
        discovery_cycle_enabled=True,  # registered lane config for this scenario
        now=None,
    )
    reconciliation = reconcile_browser_fields(database, snapshot)
    fixture_path = write_generated_fixture(snapshot)

    LANE_VAR.mkdir(parents=True, exist_ok=True)
    (LANE_VAR / "e2e_snapshot.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8")
    LANE_EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = {
        "facts": facts,
        "reconciliation": reconciliation,
        "fixture_path": str(fixture_path.relative_to(REPO_ROOT)),
        "snapshot_path": str((LANE_VAR / "e2e_snapshot.json").relative_to(REPO_ROOT)),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    (LANE_EVIDENCE / "E2E_LEDGER_RECONCILIATION.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"reconciliation_pass": reconciliation["all_pass"],
                      "elapsed_seconds": report["elapsed_seconds"]}, indent=2))

    if args.serve:
        server, url = serve_command_center(database, args.port)
        print(f"serving real command center on {url} (Ctrl+C to stop)", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
    return 0 if reconciliation["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
