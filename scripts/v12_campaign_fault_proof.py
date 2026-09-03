"""Disposable V12 campaign-runtime fault proof.

The proof deliberately uses synthetic mission/verifier receipts and a local
SQLite ledger.  It never calls a real provider, scheduler, live install, or
external effect.  ``full`` runs a hard-kill subprocess, a fresh-process
restart, provider outage/recovery, due waits, parallel accepted children and a
join, plus independent deadline and STOP/RESUME campaigns.
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

# Keep the disposable proof runnable from a fresh checkout without requiring
# an editable install.  The subprocess inherits the same explicit source
# root, so this never reaches a live installation.
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import create_objective, rebuild_executive_state
from jarvis_foundry.executive.campaigns import (
    apply_replan,
    bind_child_mission,
    create_campaign,
    declare_child_mission,
    evaluate_joins,
    rebuild_campaign_state,
    record_child_state,
    startup_resume_pass,
)
from jarvis_foundry.mission.verification import final_outcome_history, record_final_outcome

HARD_EXIT = 86
PROOF_NOW = "2021-01-01T00:00:00Z"


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: dict[str, Any],
    key: str,
    *,
    source_id: str = "v12-proof",
) -> None:
    database.append(
        Event.create(
            event_type,
            payload,
            source_type=(
                "supervisor"
                if event_type.startswith("verification")
                or event_type == "mission.completed"
                else "test"
            ),
            source_id=source_id,
            idempotency_key=key,
        )
    )


def _accepted_mission(database: FoundryDatabase, mission_id: str) -> str:
    _append(
        database,
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": f"accepted {mission_id}",
            "resource_id": None,
            "state": "PLANNED",
            "acceptance_criteria": ["accepted final outcome"],
            "next_action": "done",
        },
        f"mission.created:{mission_id}",
    )
    for index, state in enumerate(
        ("ORIENTING", "ACTION_SELECTION", "READY", "EXECUTING", "VERIFYING", "ACCEPTING", "COMPLETE")
    ):
        _append(
            database,
            "mission.state_changed",
            {"mission_id": mission_id, "state": state, "next_action": "done"},
            f"mission.state_changed:{mission_id}:{index}",
        )
    _append(
        database,
        "verification.started",
        {
            "verdict_id": f"verdict:{mission_id}",
            "mission_id": mission_id,
            "subject_type": "mission_step",
            "subject_id": "step",
            "verifier_run_id": f"verifier-run:{mission_id}",
            "builder_run_id": f"builder-run:{mission_id}",
            "independence": {"proof": "separate"},
            "criteria": ["accepted final outcome"],
        },
        f"verification.started:{mission_id}",
    )
    _append(
        database,
        "verification.passed",
        {"verdict_id": f"verdict:{mission_id}", "findings": [], "evidence": {}},
        f"verification.passed:{mission_id}",
    )
    _append(
        database,
        "verification.accepted",
        {
            "decision": "ACCEPT",
            "verdict_id": f"verdict:{mission_id}",
            "mission_id": mission_id,
        },
        f"verification.accepted:{mission_id}",
    )
    _append(
        database,
        "mission.completed",
        {"mission_id": mission_id, "objective": f"accepted {mission_id}", "outcome": "PASSED"},
        f"mission.completed:{mission_id}",
    )
    finalized = record_final_outcome(database, mission_id=mission_id)
    if not finalized.get("finalized"):
        raise RuntimeError(f"accepted proof mission did not finalize: {mission_id}")
    return str(finalized["outcome_id"])


def _open(root: Path) -> FoundryDatabase:
    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    return database


def _ensure_fixture(root: Path) -> dict[str, str]:
    database = _open(root)
    objective_id = "objective-v12-proof-main"
    create_objective(
        database,
        "V12 disposable multi-mission campaign",
        objective_id=objective_id,
        success_criteria=("all campaign children accepted",),
    )
    first_outcome = (
        _accepted_mission(database, "mission-v12-parallel-a")
        if not database.row("missions", "mission_id", "mission-v12-parallel-a")
        else str(next(iter(final_outcome_history(database, mission_id="mission-v12-parallel-a")), {}).get("outcome_id") or "")
    )
    second_outcome = (
        _accepted_mission(database, "mission-v12-parallel-b")
        if not database.row("missions", "mission_id", "mission-v12-parallel-b")
        else str(next(iter(final_outcome_history(database, mission_id="mission-v12-parallel-b")), {}).get("outcome_id") or "")
    )
    create_campaign(
        database,
        objective_id,
        campaign_id="campaign-v12-proof-main",
        join_conditions={
            "parallel-join": {
                "children": ["parallel-a", "parallel-b"],
                "mode": "ALL",
            }
        },
    )
    declare_child_mission(
        database,
        "campaign-v12-proof-main",
        child_id="parallel-a",
        mission_id="mission-v12-parallel-a",
        join_ids=("parallel-join",),
        sibling_group="parallel",
    )
    declare_child_mission(
        database,
        "campaign-v12-proof-main",
        child_id="parallel-b",
        mission_id="mission-v12-parallel-b",
        join_ids=("parallel-join",),
        sibling_group="parallel",
    )
    declare_child_mission(database, "campaign-v12-proof-main", child_id="time-child")
    declare_child_mission(
        database,
        "campaign-v12-proof-main",
        child_id="provider-child",
        provider_id="provider-v12-proof",
    )
    state = rebuild_campaign_state(database)
    if state.children["time-child"].status != "WAITING":
        record_child_state(
            database,
            "time-child",
            "WAITING",
            wait_kind="TIME",
            wait_until="2020-01-01T00:00:00Z",
            reason="disposable proof due wait",
        )
    if not any(
        event.event_type == "provider.outage"
        and event.payload.get("provider_id") == "provider-v12-proof"
        for event in database.events()
    ):
        _append(
            database,
            "provider.outage",
            {"provider_id": "provider-v12-proof", "status": "UNAVAILABLE"},
            "provider.outage:v12-proof",
        )
    return {
        "objective_id": objective_id,
        "campaign_id": "campaign-v12-proof-main",
        "first_outcome": first_outcome,
        "second_outcome": second_outcome,
    }


def _hard_kill(root: Path) -> int:
    _ensure_fixture(root)
    database = _open(root)

    def kill_after_checkpoint(phase: str) -> None:
        if "after checkpoint" in phase:
            os._exit(HARD_EXIT)

    startup_resume_pass(
        database,
        now=PROOF_NOW,
        resume_id="v12-hard-kill",
        fault_hook=kill_after_checkpoint,
    )
    return 0


def _resume(root: Path) -> dict[str, Any]:
    database = _open(root)
    report = startup_resume_pass(database, now=PROOF_NOW, resume_id="v12-restart")
    state = rebuild_campaign_state(database)
    return {
        "report": report.to_payload(),
        "campaign_status": state.campaigns["campaign-v12-proof-main"].status,
        "time_child_status": state.children["time-child"].status,
        "provider_child_status": state.children["provider-child"].status,
    }


def _run_full(root: Path) -> dict[str, Any]:
    fixture = _ensure_fixture(root)
    script = Path(__file__).resolve()
    env = dict(os.environ)
    source_root = str(SOURCE_ROOT)
    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
    killed = subprocess.run(
        [sys.executable, str(script), "--mode", "hard-kill", "--root", str(root)],
        cwd=source_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if killed.returncode != HARD_EXIT:
        raise RuntimeError(f"hard-kill proof returned {killed.returncode}: {killed.stderr[-1000:]}")
    restarted = _resume(root)
    database = _open(root)
    _append(
        database,
        "provider.health_sample",
        {
            "provider_id": "provider-v12-proof",
            "health": "HEALTHY",
            "auth_state": "READY",
            "availability": "AVAILABLE",
            "status": "READY",
        },
        "provider.health_sample:v12-proof-recovery",
    )
    after_provider = startup_resume_pass(database, now=PROOF_NOW, resume_id="v12-provider-recovery")
    time_mission_outcome = _accepted_mission(database, "mission-v12-time")
    provider_mission_outcome = _accepted_mission(database, "mission-v12-provider")
    bind_child_mission(database, "time-child", "mission-v12-time")
    bind_child_mission(database, "provider-child", "mission-v12-provider")
    after_bind = startup_resume_pass(database, now=PROOF_NOW, resume_id="v12-bind-after-recovery")
    joined = evaluate_joins(database, "campaign-v12-proof-main")
    main_state = rebuild_campaign_state(database)

    # Independent deadline campaign proves both bounded replan and explicit
    # escalation after the owner leaves the expired deadline unchanged.
    create_objective(
        database,
        "V12 deadline proof objective",
        objective_id="objective-v12-proof-deadline",
        success_criteria=("deadline reviewed",),
    )
    create_campaign(
        database,
        "objective-v12-proof-deadline",
        campaign_id="campaign-v12-proof-deadline",
        deadline="2020-01-01T00:00:00Z",
        max_replans=1,
    )
    declare_child_mission(database, "campaign-v12-proof-deadline", child_id="deadline-child")
    deadline_first = startup_resume_pass(database, now=PROOF_NOW, resume_id="v12-deadline-replan")
    apply_replan(database, "campaign-v12-proof-deadline", deadline="2020-01-01T00:00:00Z")
    deadline_second = startup_resume_pass(database, now=PROOF_NOW, resume_id="v12-deadline-escalate")

    # STOP is independent from cancellation: all exact child state is parked,
    # then a fresh startup pass resumes it after the marker is cleared.
    create_objective(
        database,
        "V12 stop proof objective",
        objective_id="objective-v12-proof-stop",
        success_criteria=("stop is reversible",),
    )
    create_campaign(database, "objective-v12-proof-stop", campaign_id="campaign-v12-proof-stop")
    declare_child_mission(database, "campaign-v12-proof-stop", child_id="stop-child")
    stop_path = root / "EMERGENCY_STOP"
    stop_path.write_text("v12 disposable STOP", encoding="utf-8")
    stopped = startup_resume_pass(database, stop_path=stop_path, resume_id="v12-stop")
    stop_path.unlink()
    resumed = startup_resume_pass(database, stop_path=stop_path, resume_id="v12-stop-resume")
    final_state = rebuild_campaign_state(database)

    return {
        "proof": "V12 campaign runtime disposable multi-mission fault proof",
        "root": str(root),
        "fixture": fixture,
        "process_kill": {"returncode": killed.returncode, "expected": HARD_EXIT},
        "restart": restarted,
        "provider_recovery": after_provider.to_payload(),
        "post_bind_resume": after_bind.to_payload(),
        "parallel_join": {
            "result": joined,
            "campaign_status": main_state.campaigns["campaign-v12-proof-main"].status,
            "accepted_children": [
                child.child_id
                for child in main_state.children.values()
                if child.campaign_id == "campaign-v12-proof-main"
                and child.status == "SUCCEEDED"
            ],
            "time_outcome": time_mission_outcome,
            "provider_outcome": provider_mission_outcome,
        },
        "deadline": {
            "replan": deadline_first.to_payload(),
            "escalation": deadline_second.to_payload(),
            "campaign_status": final_state.campaigns["campaign-v12-proof-deadline"].status,
        },
        "stop_resume": {
            "stop": stopped.to_payload(),
            "resume": resumed.to_payload(),
            "campaign_status": final_state.campaigns["campaign-v12-proof-stop"].status,
            "child_status": final_state.children["stop-child"].status,
        },
        "objective_status": rebuild_executive_state(database).objectives[fixture["objective_id"]].status,
        "event_count": database.event_count(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("full", "hard-kill", "resume"), default="full")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = (args.root or Path(tempfile.mkdtemp(prefix="jarvis-v12-campaign-proof-"))).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        if args.mode == "hard-kill":
            return _hard_kill(root)
        if args.mode == "resume":
            print(json.dumps(_resume(root), indent=2, sort_keys=True))
            return 0
        print(json.dumps(_run_full(root), indent=2, sort_keys=True))
        return 0
    except Exception as error:  # pragma: no cover - CLI boundary
        print(json.dumps({"proof": "BLOCKED", "root": str(root), "error": f"{type(error).__name__}: {error}"}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
