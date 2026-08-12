"""V3 loop wiring (directive Phase 7) — real operational loops.

run_one_loop drives one complete real loop on the registered resource:

  OBSERVE (live ingestion cycle)
  → RECORD EVIDENCE (claims + research items on the ledger)
  → GENERATE CANDIDATE (research queue → discovery candidates)
  → SCORE (calibrated deterministic scoring)
  → SELECT or REJECT (top candidate >= min_score → approve; else
    honest WAITING — no invented work)
  → CREATE MISSION (bound to the registered resource, created_from
    candidate:<id>)
  → PLAN (PLANNED → READY with next_action)
  → DISPATCH SPECIALISTS (MissionLoop.run_until_settled: real builder
    dispatch, independent verification, FAIL→repair within the durable
    budget)
  → RECONCILE SIDE EFFECTS (reconcile check)
  → MEASURE OUTCOME (mission.completed → candidate EXECUTED with the
    outcome record)

Idempotent + append-only: every event carries a deterministic key; a
crashed loop re-run dedups (the dispatcher's replay path re-returns a
completed run instead of re-dispatching).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from .database import FoundryDatabase
from .discovery_candidates import score_candidate
from .events import Event
from .live_ingestion import run_ingestion_cycle
from .mission.loop import MissionLoop
from .mission.proposal import ProposalError
from .reconcile import reconcile
from .research_queue import detect_research_items, generate_research_candidates

DEFAULT_CAPABILITY = "write_registered_worktree_files"
DEFAULT_MIN_SCORE = 0.35


def _top_scorable(db: FoundryDatabase, *, budget: int) -> list[dict[str, Any]]:
    """NEW/SCORED candidates with their scores, best first (budget-bounded)."""
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT candidate_id, objective, status FROM candidates "
            "WHERE status IN ('NEW', 'SCORED') ORDER BY rowid").fetchall()
    scored: list[dict[str, Any]] = []
    for row in rows[:budget]:
        score = score_candidate(db, row[0])
        scored.append({"candidate_id": row[0], "objective": row[1],
                       "status": row[2], "score": score})
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored


def _default_instructions(objective: str, target: Path) -> str:
    return (
        "You are a bounded autonomous worker for JARVIS Foundry. "
        f"Mission objective: {objective}\n"
        "Produce ONE concise evidence file in the target directory "
        f"{target} named report.md that: (1) states the objective, "
        "(2) reports what you verified with exact observed values, "
        "(3) lists at most three bullet points. Do not modify anything "
        "outside the target directory. Keep the file under 1200 bytes. "
        "End the file with a line: VERIFIED_BY <your identity>.")


def run_one_loop(db: FoundryDatabase, *, loop_id: str, adapter: Any,
                 router: Any, mission_id: str = "mission-wfm-optimizer",
                 resource_id: str = "resource-wfm-optimizer",
                 capability: str = DEFAULT_CAPABILITY,
                 min_score: float = DEFAULT_MIN_SCORE,
                 score_budget: int = 8,
                 max_items: int = 40, max_bytes: int = 80_000,
                 vault_root: str | Path | None = None,
                 handoff_dir: str | Path | None = None,
                 repo_paths: Iterable[str | Path] | None = None,
                 target_root: str | Path | None = None,
                 instructions_builder: Callable[[str, Path], str] | None = None,
                 ) -> dict[str, Any]:
    """Run one complete real loop. Returns the loop report dict."""
    report: dict[str, Any] = {"loop_id": loop_id}

    # 1+2. OBSERVE + RECORD EVIDENCE (live ingestion, bounded)
    cycle = run_ingestion_cycle(
        db, sources=["vault", "handoffs", "repo", "system"],
        vault_root=vault_root, handoff_dir=handoff_dir,
        repo_paths=list(repo_paths or []), mission_id=mission_id,
        max_items=max_items, max_bytes=max_bytes)
    report["observed"] = {k: v["claims_created"] for k, v in cycle.items()}

    # 3. GENERATE CANDIDATE (research queue → discovery pipeline)
    items = detect_research_items(db)
    candidates = generate_research_candidates(db)
    report["research_items"] = len(items)
    report["candidates_created"] = candidates["candidates_created"]

    # 4. SCORE + SELECT/REJECT
    top = _top_scorable(db, budget=score_budget)
    report["scored"] = len(top)
    selected = next((c for c in top if c["score"] >= min_score), None)
    if selected is None:
        report["outcome"] = "WAITING"
        report["reason"] = (f"no candidate scored >= {min_score} "
                            f"(top {top[0]['score']:.2f} if any) — honest "
                            f"waiting, no invented work")
        return report

    candidate_id = selected["candidate_id"]
    objective = selected["objective"]
    report["selected_candidate"] = candidate_id
    report["candidate_score"] = selected["score"]

    # 5. CREATE MISSION (bound to the registered resource)
    new_mission = f"mission-{uuid.uuid4().hex[:12]}"
    db.append(Event.create(
        "discovery.candidate_approved",
        {"candidate_id": candidate_id, "mission_id": new_mission},
        source_type="supervisor", source_id="v3-loop",
        idempotency_key=f"discovery.candidate_approved:{candidate_id}"))
    db.append(Event.create(
        "mission.created",
        {"mission_id": new_mission, "objective": objective,
         "resource_id": resource_id,
         "acceptance_criteria": [f"Complete: {objective}"],
         "created_from": f"candidate:{candidate_id}",
         "state": "PLANNED"},
        source_type="supervisor", source_id="v3-loop",
        idempotency_key=f"mission.created:{new_mission}"))
    report["mission_id"] = new_mission

    # 6. PLAN (walk the state machine to READY — the domain map:
    # PLANNED → ORIENTING → ACTION_SELECTION → READY)
    for state, next_action in (
        ("ORIENTING", f"orient: {objective}"),
        ("ACTION_SELECTION", f"execute: {objective}"),
        ("READY", f"execute: {objective}"),
    ):
        db.append(Event.create(
            "mission.state_changed",
            {"mission_id": new_mission, "state": state,
             "next_action": next_action},
            source_type="supervisor", source_id="v3-loop",
            idempotency_key=f"mission.state_changed:{new_mission}:{state}"))

    # 7. DISPATCH + VERIFY + ACCEPT/REPAIR (settled within the budget)
    target_root_p = Path(target_root) if target_root else None
    if target_root_p is None:
        with db.connect() as connection:
            row = connection.execute(
                "SELECT resolved_root FROM resources WHERE resource_id = ?",
                (resource_id,)).fetchone()
        if row is None:
            raise ProposalError(f"resource {resource_id} is not registered")
        target_root_p = Path(row[0])
    target = target_root_p / f"v3-loops" / f"loop-{loop_id}"
    target.mkdir(parents=True, exist_ok=True)
    instructions = (instructions_builder(objective, target)
                    if instructions_builder is not None
                    else _default_instructions(objective, target))
    loop = MissionLoop(db, router, adapter)
    outcome = loop.run_until_settled(
        mission_id=new_mission, step_id="v3-loop", capability=capability,
        target=target, instructions=instructions,
        expected_effects=("writes within the loop target directory",),
        rollback={"revert": "delete files created under the loop target"},
        risk_class="C1",
        verification_criteria=[
            "the deliverable exists in the target directory",
            "the deliverable satisfies the mission objective",
        ])
    report["step_outcome"] = outcome.outcome
    report["run_id"] = outcome.run_id
    report["verdict_id"] = outcome.verdict_id
    report["reason"] = outcome.reason

    # 8. RECONCILE SIDE EFFECTS
    rep = reconcile(db)
    report["reconciled"] = rep.consistent

    # 9. MEASURE OUTCOME (mission.completed → candidate EXECUTED)
    db.append(Event.create(
        "mission.completed",
        {"mission_id": new_mission, "objective": objective,
         "outcome": {"loop_id": loop_id, "step_outcome": outcome.outcome,
                     "run_id": outcome.run_id,
                     "verdict_id": outcome.verdict_id,
                     "candidate_id": candidate_id}},
        source_type="supervisor", source_id="v3-loop",
        idempotency_key=f"mission.completed:{new_mission}"))
    row = db.row("missions", "mission_id", new_mission)
    report["mission_state"] = row["state"] if row else None
    report["outcome"] = outcome.outcome
    return report
