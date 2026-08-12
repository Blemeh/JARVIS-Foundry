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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .database import FoundryDatabase
from .discovery_candidates import score_candidate
from .events import Event
from .live_ingestion import run_ingestion_cycle
from .mission.loop import MissionLoop
from .mission.proposal import ProposalError
from .reconcile import reconcile
from .vault_retrieval import batch_support
from .research_queue import detect_research_items, generate_research_candidates

DEFAULT_CAPABILITY = "write_registered_worktree_files"
DEFAULT_MIN_SCORE = 0.35


def _ensure_monitors(db: FoundryDatabase, mission_id: str, *,
                     vault_root: str | Path | None = None,
                     handoff_dir: str | Path | None = None,
                     research_dirs: Iterable[str | Path] = (),
                     repo_paths: Iterable[str | Path] = (),
                     ) -> dict[str, str]:
    """Register (deduped) the REAL observed sources in the durable
    monitors registry (DIR-2026-08-12 item 6: monitored-source
    acceptance requires the registry, never direct ingestion alone).

    Returns {source_kind: monitor_id}. Registration is idempotent:
    an existing (source_kind, source_ref) row is reused. The ledger
    (system) is always a monitored source; nothing else is invented
    when no roots are provided.
    """
    from .discovery_monitors import register_monitor

    sources: list[tuple[str, str]] = []
    if vault_root:
        sources.append(("vault", str(Path(vault_root).resolve())))
    if handoff_dir:
        sources.append(("handoffs", str(Path(handoff_dir).resolve())))
    for directory in research_dirs:
        sources.append(("research", str(Path(directory).resolve())))
    for repo in repo_paths:
        sources.append(("repo", str(Path(repo).resolve())))
    sources.append(("system", "live-ledger"))
    with db.connect() as connection:
        existing = {
            (row["source_kind"], row["source_ref"]): row["monitor_id"]
            for row in connection.execute(
                "SELECT monitor_id, source_kind, source_ref FROM monitors"
            ).fetchall()}
    monitor_ids: dict[str, str] = {}
    for kind, ref in sources:
        if (kind, ref) in existing:
            monitor_ids[kind] = existing[(kind, ref)]
        else:
            monitor_ids[kind] = register_monitor(db, mission_id, kind, ref)
    return monitor_ids


def _maintenance_candidates(db: FoundryDatabase, *,
                            mission_id: str,
                            vault_root: str | Path | None = None,
                            handoff_dir: str | Path | None = None,
                            research_dirs: Iterable[str | Path] = (),
                            repo_paths: Iterable[str | Path] = (),
                            ) -> dict[str, Any]:
    """Create evidence-backed MAINTENANCE candidates from measured
    observations (directive Phase 7: 'create maintenance work when
    needed'). Signals: unsupported claims (SOURCE-KIND-AWARE since
    V4-S1 — the pre-S1 vault-only check falsely marked 894/920 live
    handoff/repo/system claims UNSUPPORTED), stale claims, open
    research items. Dedup via the candidate pipeline; honest no-op when
    no signal clears the thresholds. The objective carries the measured
    numbers AND the calibrated scoring terms (fix/unknown/root cause/
    risk/today) so genuine maintenance work passes the selection bar.
    """
    claims = db.all("knowledge_claims")
    with db.connect() as connection:
        support = batch_support(connection, claims, vault_root=vault_root,
                                handoff_dir=handoff_dir,
                                research_dirs=research_dirs,
                                repo_paths=repo_paths)
    unsupported = sum(1 for value in support.values()
                      if value == "UNSUPPORTED")
    with db.connect() as connection:
        stale = connection.execute(
            "SELECT COUNT(*) FROM knowledge_claims WHERE fresh_until IS NOT "
            "NULL AND fresh_until <= ?",
            (datetime.now(timezone.utc).isoformat(),)
        ).fetchone()[0]
        open_items = connection.execute(
            "SELECT COUNT(*) FROM research_queue WHERE status = 'OPEN'"
        ).fetchone()[0]
    counts = {"unsupported": unsupported, "stale": stale,
              "open_items": open_items}
    if unsupported >= 5 or stale >= 1 or open_items >= 5:
        from .discovery_candidates import generate_candidate
        objective = (
            f"Maintenance: verify and fix the {unsupported} unsupported "
            f"claims ({stale} stale, {open_items} open research items) — "
            f"root cause unknown, regression risk today")
        result = generate_candidate(db, mission_id, source="maintenance",
                                    objective=objective,
                                    evidence=[{"counts": counts}])
        return {"created": result.get("created", False),
                "candidate_id": result.get("candidate_id"),
                "duplicate_of": result.get("duplicate_of"), "counts": counts}
    return {"created": False, "counts": counts}


def _top_scorable(db: FoundryDatabase, *, budget: int,
                  score_cap: int = 100) -> list[dict[str, Any]]:
    """The best-scored NEW/SCORED candidates (budget-bounded output).

    Scores the NEWEST candidates first (rowid DESC): the loop selects
    work from FRESH signals — maintenance candidates and new research
    items — while older unconsumed candidates age out (they would have
    been consumed if valuable). Live-loop findings 2026-08-12: an
    ASC-by-rowid cap starved the newest high-value candidates twice.
    """
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT candidate_id, objective, status FROM candidates "
            "WHERE status IN ('NEW', 'SCORED') ORDER BY rowid DESC "
            f"LIMIT {max(1, int(score_cap))}").fetchall()
    scored: list[dict[str, Any]] = []
    for row in rows:
        score = score_candidate(db, row[0])
        scored.append({"candidate_id": row[0], "objective": row[1],
                       "status": row[2], "score": score})
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:max(1, int(budget))]


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
                 research_dirs: Iterable[str | Path] = (),
                 repo_paths: Iterable[str | Path] | None = None,
                 target_root: str | Path | None = None,
                 instructions_builder: Callable[[str, Path], str] | None = None,
                 ) -> dict[str, Any]:
    """Run one complete real loop. Returns the loop report dict."""
    report: dict[str, Any] = {"loop_id": loop_id}

    # 0. MANDATORY MODEL ROUTING (DIR-2026-08-12): every real role must
    # resolve to opencode_go + deepseek-v4-flash with no fallbacks. A
    # non-compliant router refuses the loop BEFORE any observation,
    # mutation or dispatch (dispatch would have to be BLOCKED, never
    # substituted with another model).
    router.enforce_directive_routing()

    # 1+2. OBSERVE + RECORD EVIDENCE (live ingestion, bounded)
    cycle = run_ingestion_cycle(
        db, sources=["vault", "handoffs", "repo", "system"],
        vault_root=vault_root, handoff_dir=handoff_dir,
        repo_paths=list(repo_paths or []), mission_id=mission_id,
        max_items=max_items, max_bytes=max_bytes)
    report["observed"] = {k: v["claims_created"] for k, v in cycle.items()}

    # 3. GENERATE CANDIDATE (research queue → discovery pipeline)
    items = detect_research_items(db, vault_root=vault_root,
                                  handoff_dir=handoff_dir,
                                  research_dirs=research_dirs,
                                  repo_paths=tuple(repo_paths or ()))
    report["research_items"] = len(items)

    # 3c. POLL THE DURABLE MONITOR REGISTRY (DIR-2026-08-12 item 6) —
    # BEFORE the queue conversion, so genuinely new observations are
    # generated through the registry: every real observed source is
    # registered (deduped) and polled every cycle; the feed is the
    # cycle's OPEN, non-empty, NOT-yet-covered research items. Repeated
    # polls can never increase candidates (covered items are filtered
    # out; the pipeline records duplicates as REJECTED — V4-S5 round 2,
    # deleg_71efdc9e FAIL findings) and queue-REJECTED low-value items
    # never resurrect (status filter). The poll key is the loop_id, so
    # a crash-retried cycle dedupes while distinct cycles poll
    # distinctly. Honest WAITING cycles still poll on schedule (this
    # block runs before scoring).
    from .discovery_candidates import covered_objective
    from .discovery_monitors import poll_monitor
    monitor_ids = _ensure_monitors(
        db, mission_id, vault_root=vault_root, handoff_dir=handoff_dir,
        research_dirs=research_dirs, repo_paths=tuple(repo_paths or ()))
    poll_items: list[dict[str, Any]] = []
    for item in items[:max(1, int(max_items))]:
        if item.get("status") != "OPEN":
            continue  # resolved/REJECTED items never resurrect
        statement = (item.get("evidence") or {}).get("claim_statement") \
            if isinstance(item.get("evidence"), dict) else None
        objective = statement or item.get("claim_id", "")
        if not objective or not str(objective).strip():
            continue
        if covered_objective(db, objective) is not None:
            continue  # already covered — polls must not duplicate work
        poll_items.append({
            "mission_id": mission_id,
            "source": f"monitor:{item.get('reason', 'observation')}",
            "objective": objective,
            "evidence": [{"item_id": item.get("item_id"),
                          "reason": item.get("reason")}]})
    report["monitors"] = {}
    for kind, monitor_id in monitor_ids.items():
        result = poll_monitor(db, monitor_id, poll_items, poll_key=loop_id)
        report["monitors"][kind] = {
            "monitor_id": monitor_id,
            "polled": True,
            "items_seen": result["items_seen"],
            "candidates_found": result["candidates_found"]}

    # the queue then resolves every remaining OPEN item (the
    # poll-generated objectives resolve as already covered)
    candidates = generate_research_candidates(db)
    report["candidates_created"] = candidates["candidates_created"]

    # 3b. MAINTENANCE WORK when needed (measured signals; deduped through
    # the same candidate pipeline)
    maintenance = _maintenance_candidates(
        db, mission_id=mission_id, vault_root=vault_root,
        handoff_dir=handoff_dir, research_dirs=research_dirs,
        repo_paths=tuple(repo_paths or ()))
    report["maintenance"] = maintenance["counts"]
    report["maintenance_candidate_created"] = maintenance["created"]

    # 4. SCORE + SELECT/REJECT
    top = _top_scorable(db, budget=score_budget)
    report["scored"] = len(top)
    selected = next((c for c in top if c["score"] >= min_score), None)
    if selected is None:
        report["outcome"] = "WAITING"
        if top:
            top_score = f"{top[0]['score']:.2f}"
        else:
            top_score = "none (no candidates scored in this pass)"
        report["reason"] = (f"no candidate scored >= {min_score} "
                            f"(top {top_score}) — honest "
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

    # 9. MEASURE OUTCOME — ONLY on PASSED: a BLOCKED/FAILED mission must
    # not record mission.completed nor mark the candidate EXECUTED (the
    # candidate stays APPROVED so a later pass can still complete it;
    # live-loop finding 2026-08-12: the first live loop recorded
    # mission.completed for a BLOCKED mission — fixed here)
    if outcome.outcome == "PASSED":
        db.append(Event.create(
            "mission.state_changed",
            {"mission_id": new_mission, "state": "COMPLETE",
             "next_action": None},
            source_type="supervisor", source_id="v3-loop",
            idempotency_key=f"mission.state_changed:{new_mission}:COMPLETE"))
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
