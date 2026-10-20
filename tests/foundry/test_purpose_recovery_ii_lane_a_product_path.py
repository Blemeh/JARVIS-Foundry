"""Adversarial Lane A proofs through worker_loop, not the intelligence helper.

The harness supplies only a high-level standing GOAL and registered clones of
real owner repositories. It does not plant the winning TODO, task id, file, or
recipe, and it does not call run_executive_intelligence_cycle.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.fabric.catalog import record_outcome as record_fabric_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a3 import A3AdmissionPolicy
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


STANDING_GOAL = (
    "Keep improving my active projects while I'm away. "
    "Find what is worth improving among my registered projects and take the best next step."
)
REAL_JARVIS = Path(
    r"C:\temp\JARVIS\purpose-recovery-ii-a-executive-intelligence-20260917"
)
REAL_OPERATOROS = Path(r"C:\Users\b\Documents\OperatorOS")
REAL_GRUPI = Path(r"C:\Users\b\Documents\GitHub\Grupi-5-11A")
EVIDENCE = Path(
    r"C:\temp\JARVIS\purpose-recovery-ii-a-executive-intelligence-20260917"
    r"\var\purpose-recovery-ii-20260917\lane-a\PRODUCT_PATH_PROOF.json"
)


def _clone(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--local", "--quiet", str(src), str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    return dest


def _ready(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "home" / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-09-18T00:00:00Z", "standing_mandate": True},
        source_type="owner",
        source_id="lane-a-owner",
        idempotency_key="system.start-build",
    ))
    seed_builtins(database)
    for capability in A3AdmissionPolicy().required_fabric_capabilities:
        record_fabric_outcome(
            database,
            capability,
            success=True,
            verified=VerifyVerdict.MATCH,
            note="Lane A product-path fabric readiness",
            at="2026-09-18T00:00:00Z",
        )
    secret = b"test-secret-32-bytes-minimum!!"
    svc = OwnerControlService(database, secret=secret, owner_id="owner")
    sig = sign_command(secret, "GOAL", "GLOBAL", STANDING_GOAL, "standing-product")
    svc.apply("GOAL", target="GLOBAL", exact_text=STANDING_GOAL, nonce="standing-product", signature=sig)
    return database


def _config(tmp_path: Path) -> WorkerRuntimeConfig:
    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        lab_scripted_hermes=True,
        runtime_state_root=str(tmp_path / "runtime"),
        initiative_a3_enabled=True,
        initiative_a3={
            "enabled": True,
            "sources": [],
            "bounds": {"do_nothing_score": 0.10},
            "worktree_parent": str(tmp_path / "worktrees"),
        },
    )
    config.validate()
    return config


def _write_evidence(payload: dict) -> None:
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _ledger(database: FoundryDatabase, *types: str) -> list[Event]:
    return list(database.events(event_types=types))


def test_worker_loop_discovers_from_real_registered_repos_without_a_task_recipe(tmp_path: Path) -> None:
    assert REAL_JARVIS.is_dir() and REAL_OPERATOROS.is_dir() and REAL_GRUPI.is_dir()
    jarvis = _clone(REAL_JARVIS, tmp_path / "projects" / "jarvis")
    operatoros = _clone(REAL_OPERATOROS, tmp_path / "projects" / "operatoros")
    database = _ready(tmp_path)
    database.append(registration_event(inspect_git_resource(jarvis, resource_id="resource-jarvis")))
    database.append(registration_event(inspect_git_resource(operatoros, resource_id="resource-operatoros")))

    first = run_cycle(
        database,
        FoundryPaths(database.path.parent),
        _config(tmp_path),
        loop_id="lane-a-product-1",
        instance_id="worker-product-1",
    )
    intents = _ledger(database, "executive.standing_intent_recorded")
    assembled = _ledger(database, "executive.context_assembled")
    decisions = _ledger(database, "executive.decision_recorded")
    candidates = []
    with database.connect(readonly=True) as connection:
        candidates = [dict(row) for row in connection.execute("SELECT * FROM candidates").fetchall()]
    selected = None
    rejected = []
    truth = {}
    if decisions:
        payload = decisions[-1].payload
        truth = dict(payload.get("decision_truth") or {})
        rejected = list(payload.get("rejected") or []) + list(payload.get("deferred") or [])
        selected = database.row("candidates", "candidate_id", payload.get("candidate_id") or "")
    selected_objective = str((selected or {}).get("objective") or "")
    from jarvis_foundry.executive.autonomy import rebuild_executive_state
    state = rebuild_executive_state(database)
    owner_goal_objectives = [oid for oid in state.objectives if oid.startswith("owner-goal:")]

    wakes = _ledger(database, "executive.next_action_scheduled", "executive.wake_created")
    diagnoses = _ledger(database, "executive.wait_diagnosis_recorded")
    judgments = _ledger(database, "executive.new_project_judgment_recorded")
    measured = _ledger(database, "executive.outcome_measured")
    calibrations = _ledger(database, "executive.calibration_recorded")

    second = None
    if str(first.get("outcome") or "") == "PASSED":
        second = run_cycle(
            database,
            FoundryPaths(database.path.parent),
            _config(tmp_path),
            loop_id="lane-a-product-2",
            instance_id="worker-product-2",
        )

    proof = {
        "entrypoint": "jarvis_foundry.runtime.worker_loop.run_cycle",
        "owner_intent": STANDING_GOAL,
        "registered_projects": [str(jarvis), str(operatoros)],
        "first_cycle": first,
        "second_cycle": second,
        "standing_intents": len(intents),
        "context_assembled": bool(assembled),
        "candidate_count": len(candidates),
        "candidate_objectives": [str(row.get("objective") or "")[:180] for row in candidates[:12]],
        "selected_objective": selected_objective[:240],
        "rejected_count": len(rejected),
        "decision_truth": {
            "why_chosen": truth.get("why_chosen"),
            "next_action": truth.get("next_action"),
            "wait_reason": truth.get("wait_reason"),
            "owner_attention_required": truth.get("owner_attention_required"),
            "alternatives_rejected_count": len(truth.get("alternatives_rejected") or []),
        },
        "wait_diagnoses": [event.payload.get("classification") for event in diagnoses],
        "new_project_judgment": judgments[-1].payload if judgments else {},
        "next_action_events": [event.event_type for event in wakes],
        "outcome_measured": bool(measured),
        "calibration_recorded": bool(calibrations),
        "owner_goal_objectives": owner_goal_objectives,
        "did_harness_supply_task": False,
    }
    _write_evidence(proof)

    assert intents, proof
    assert assembled, proof
    assert not owner_goal_objectives
    assert candidates, "normal observation produced no candidates from real registered repos"
    assert "owner-goal:" not in selected_objective
    assert "lane-a-unscripted" not in selected_objective
    if selected_objective:
        assert "Resolve tracked TODO in work.py: rename variable for style" not in selected_objective
    if str(first.get("outcome")) == "WAITING":
        assert diagnoses, proof
        assert diagnoses[-1].payload.get("classification") in {"JUSTIFIED_WAIT", "STALL", "ACTION"}
    if second is not None:
        assert str(second.get("outcome")) in {"PASSED", "WAITING", "FAILED", "BLOCKED"}


def test_worker_loop_justified_wait_when_no_registered_projects(tmp_path: Path) -> None:
    database = _ready(tmp_path)
    report = run_cycle(
        database,
        FoundryPaths(database.path.parent),
        _config(tmp_path),
        loop_id="lane-a-product-wait",
        instance_id="worker-product-wait",
    )
    diagnoses = _ledger(database, "executive.wait_diagnosis_recorded")
    payload = {
        "cycle": report,
        "classifications": [event.payload.get("classification") for event in diagnoses],
    }
    (EVIDENCE.parent / "WAIT_PROOF.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8",
    )
    assert str(report.get("outcome")) == "WAITING"
    assert "no registered project sources" in str(report.get("reason") or "")
    assert diagnoses
    assert all(event.payload.get("classification") != "STALL" for event in diagnoses)
    assert any(event.payload.get("classification") == "JUSTIFIED_WAIT" for event in diagnoses)


def test_worker_loop_records_stall_when_selected_repo_cannot_continue(tmp_path: Path) -> None:
    queue = _clone(REAL_OPERATOROS, tmp_path / "projects" / "operatoros-dirty")
    (queue / "LANE_A_DIRTY_MARKER.txt").write_text("dirty", encoding="utf-8")
    database = _ready(tmp_path)
    database.append(registration_event(inspect_git_resource(queue, resource_id="resource-operatoros-dirty")))
    report = run_cycle(
        database,
        FoundryPaths(database.path.parent),
        _config(tmp_path),
        loop_id="lane-a-product-stall",
        instance_id="worker-product-stall",
    )
    diagnoses = _ledger(database, "executive.wait_diagnosis_recorded")
    with database.connect(readonly=True) as connection:
        candidate_rows = [
            dict(row) for row in connection.execute(
                "SELECT candidate_id, objective, candidate_type, status FROM candidates"
            ).fetchall()
        ]
    proof_path = EVIDENCE.parent / "STALL_PROOF.json"
    payload = {
        "cycle": report,
        "classifications": [event.payload.get("classification") for event in diagnoses],
        "reasons": [event.payload.get("reason") for event in diagnoses],
        "candidates": [
            {
                "type": row.get("candidate_type"),
                "status": row.get("status"),
                "objective": str(row.get("objective") or "")[:180],
            }
            for row in candidate_rows
        ],
    }
    proof_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    assert str(report.get("outcome")) == "WAITING"
    assert any(event.payload.get("classification") == "STALL" for event in diagnoses), payload
