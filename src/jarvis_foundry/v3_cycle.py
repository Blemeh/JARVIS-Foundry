"""V3 Discovery and Building: unified discovery cycle (WP3).

``run_discovery_cycle`` is the opt-in experimental path behind
``discovery.cycle_enabled``. It performs observe -> score -> portfolio ->
select -> compile in ONE cycle record. When the flag is false, production
keeps today's split unchanged: ``v3_loop.run_one_loop`` observes then
WAITs honestly, ``ExecutiveDecisionService`` selects in worker_loop.

Reuse contract (no second scheduler/mission engine/evidence store):
observation reuses ``v3_loop`` helpers, scoring reuses ``v3_scoring``,
selection reuses ``ExecutiveDecisionService``, materialization reuses
``materialize_selection``. The cycle adds orchestration + events only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .database import FoundryDatabase
from .events import Event
from .v3_scoring import POLICY_VERSION, ScoringService, evaluate_portfolio


@dataclass(frozen=True)
class CycleReport:
    loop_id: str
    outcome: str  # COMPILED | WAITING | STOPPED | BLOCKED
    decision_id: str | None = None
    mission_id: str | None = None
    candidate_id: str | None = None
    observed: int = 0
    candidates_created: int = 0
    scored: int = 0
    eligible: int = 0
    rejected: int = 0
    degraded_sources: list[str] = field(default_factory=list)
    reason: str | None = None
    outcome_id: str | None = None
    next_selection_id: str | None = None
    lineage: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "loop_id": self.loop_id,
            "outcome": self.outcome,
            "decision_id": self.decision_id,
            "mission_id": self.mission_id,
            "candidate_id": self.candidate_id,
            "observed": self.observed,
            "candidates_created": self.candidates_created,
            "scored": self.scored,
            "eligible": self.eligible,
            "rejected": self.rejected,
            "degraded_sources": list(self.degraded_sources),
            "reason": self.reason,
            "outcome_id": self.outcome_id,
            "next_selection_id": self.next_selection_id,
            "lineage": dict(self.lineage),
        }


def run_discovery_cycle(
    database: FoundryDatabase,
    *,
    loop_id: str,
    resource_id: str = "resource-wfm-optimizer",
    capability: str = "write_registered_worktree_files",
    source_id: str = "v3-cycle",
    observe: "Any | None" = None,
) -> CycleReport:
    """One unified discovery iteration.

    ``observe`` injects the existing observation step (v3_loop's
    run_one_loop with selection disabled). In production the caller
    passes a partial; tests pass a stub. Isolated per-source failure
    lands in ``degraded_sources`` — the cycle continues.
    """
    from .control.broker import emergency_stop_reason

    # Step 1 — safety gate before anything else.
    stop = emergency_stop_reason(database, None)
    if stop:
        lineage = {"loop_id": loop_id, "status": "STOPPED"}
        database.append(Event.create(
            "discovery.cycle_completed",
            {"loop_id": loop_id, "outcome": "STOPPED", "observed": 0,
             "candidates_created": 0, "decision_id": None,
             "degraded_sources": [], "reason": f"STOP active: {stop}",
             "lineage": lineage},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"discovery.cycle_completed:{loop_id}"))
        return CycleReport(loop_id=loop_id, outcome="STOPPED",
                           reason=f"STOP active: {stop}", lineage=lineage)

    degraded: list[str] = []
    observed = 0
    created = 0

    # Steps 2-3 — observe + generate candidates via the EXISTING path.
    if observe is not None:
        try:
            observation = observe(database)
            if isinstance(observation, dict):
                observed_map = observation.get("observed", {})
                observed = (sum(observed_map.values())
                            if isinstance(observed_map, dict) else 0)
                created = int(observation.get("candidates_created", 0))
        except Exception as error:  # noqa: BLE001 - degrade, don't abort
            degraded.append(f"observe:{type(error).__name__}")

    # Observation is untrusted and may race with an owner STOP.  Do not score,
    # append portfolio state, select, or materialize after the second gate.
    stop = emergency_stop_reason(database, None)
    if stop:
        lineage = {"loop_id": loop_id, "status": "STOPPED",
                   "observed": observed, "candidates_created": created}
        database.append(Event.create(
            "discovery.cycle_completed",
            {"loop_id": loop_id, "outcome": "STOPPED", "observed": observed,
             "candidates_created": created, "decision_id": None,
             "degraded_sources": degraded,
             "reason": f"STOP active after observation: {stop}",
             "lineage": lineage},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"discovery.cycle_completed:{loop_id}"))
        return CycleReport(loop_id=loop_id, outcome="STOPPED",
                           observed=observed, candidates_created=created,
                           degraded_sources=degraded,
                           reason=f"STOP active after observation: {stop}",
                           lineage=lineage)

    policy_version = POLICY_VERSION
    try:
        from .v3_scoring import load_active_policy
        policy_version = load_active_policy(database)["policy_version"]
    except ValueError as error:
        database.append(Event.create(
            "discovery.cycle_completed",
            {"loop_id": loop_id, "outcome": "BLOCKED", "observed": observed,
             "candidates_created": created, "decision_id": None,
             "degraded_sources": degraded,
             "reason": str(error),
             "lineage": {"loop_id": loop_id, "status": "BLOCKED"}},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"discovery.cycle_completed:{loop_id}"))
        return CycleReport(loop_id=loop_id, outcome="BLOCKED", observed=observed,
                           candidates_created=created, reason=str(error),
                           lineage={"loop_id": loop_id, "status": "BLOCKED"})

    # Step 4-5 — score every NEW candidate under the registry policy.
    stop = emergency_stop_reason(database, None)
    if stop:
        lineage = {"loop_id": loop_id, "status": "STOPPED",
                   "observed": observed, "candidates_created": created}
        database.append(Event.create(
            "discovery.cycle_completed",
            {"loop_id": loop_id, "outcome": "STOPPED", "observed": observed,
             "candidates_created": created, "decision_id": None,
             "degraded_sources": degraded,
             "reason": f"STOP active before scoring: {stop}",
             "lineage": lineage},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"discovery.cycle_completed:{loop_id}"))
        return CycleReport(loop_id=loop_id, outcome="STOPPED",
                           observed=observed, candidates_created=created,
                           degraded_sources=degraded,
                           reason=f"STOP active before scoring: {stop}",
                           lineage=lineage)
    service = ScoringService(database)
    decisions = service.score_all_eligible()
    eligible = [d for d in decisions if d["status"] == "ELIGIBLE"]

    # Step 5b — portfolio projection event.
    portfolio = evaluate_portfolio(
        database, loop_id,
        [{**d, "status": d["status"]} for d in decisions],
        policy_version=policy_version)

    # Step 6 — selection stays with ExecutiveDecisionService.
    from .executive.decisions import ExecutiveDecisionService
    from .executive.eligibility import enable_admission_authority

    executive = ExecutiveDecisionService(
        database, min_action_score=float(
            _threshold(database, policy_version)),
        wait_margin=float(_wait_margin(database, policy_version)),
        source_id=source_id,
    )
    epoch = executive.ensure_activation()
    enable_admission_authority(database, epoch, source_id=source_id)
    decision = executive.evaluate(
        resource_id=resource_id, capability=capability, loop_id=loop_id)

    if decision.kind != "SELECT" or decision.candidate_id is None:
        lineage = {"loop_id": loop_id, "status": "WAITING",
                   "decision_id": decision.decision_id}
        database.append(Event.create(
            "discovery.cycle_completed",
            {"loop_id": loop_id, "outcome": "WAITING", "observed": observed,
             "candidates_created": created, "decision_id": decision.decision_id,
             "degraded_sources": degraded,
             "reason": getattr(decision, "reason", None)
             or decision.kind, "lineage": lineage},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"discovery.cycle_completed:{loop_id}"))
        return CycleReport(
            loop_id=loop_id, outcome="WAITING",
            decision_id=decision.decision_id,
            observed=observed, candidates_created=created,
            scored=len(decisions), eligible=len(eligible),
            rejected=int(portfolio["rejected_count"]),
            degraded_sources=degraded,
            reason=getattr(decision, "reason", None) or decision.kind,
            lineage=lineage)

    # Step 7 — compile through the fail-closed canonical materializer.
    stop = emergency_stop_reason(database, None)
    if stop:
        lineage = {"loop_id": loop_id, "status": "STOPPED",
                   "decision_id": decision.decision_id,
                   "candidate_id": decision.candidate_id}
        database.append(Event.create(
            "discovery.cycle_completed",
            {"loop_id": loop_id, "outcome": "STOPPED", "observed": observed,
             "candidates_created": created,
             "decision_id": decision.decision_id,
             "degraded_sources": degraded,
             "reason": f"STOP active before materialization: {stop}",
             "lineage": lineage},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"discovery.cycle_completed:{loop_id}"))
        return CycleReport(loop_id=loop_id, outcome="STOPPED",
                           decision_id=decision.decision_id,
                           candidate_id=decision.candidate_id,
                           observed=observed, candidates_created=created,
                           scored=len(decisions), eligible=len(eligible),
                           rejected=int(portfolio["rejected_count"]),
                           degraded_sources=degraded,
                           reason=f"STOP active before materialization: {stop}",
                           lineage=lineage)
    compiled = compile_mission(
        database, candidate_id=decision.candidate_id,
        objective=decision.score.objective if decision.score else "",
        resource_id=resource_id, capability=capability,
        decision_id=decision.decision_id, activation_epoch=epoch,
        source_id=source_id)

    outcome = "COMPILED" if compiled.get("materialized") else "BLOCKED"
    lineage = {
        "loop_id": loop_id,
        "candidate_id": decision.candidate_id,
        "decision_id": decision.decision_id,
        "mission_id": compiled.get("mission_id"),
        "status": outcome,
        "outcome_id": None,
        "next_selection_id": None,
    }
    database.append(Event.create(
        "discovery.cycle_lineage_recorded", lineage,
        source_type="supervisor", source_id=source_id,
        idempotency_key=f"discovery.cycle_lineage_recorded:{loop_id}"))
    database.append(Event.create(
        "discovery.cycle_completed",
        {"loop_id": loop_id, "outcome": outcome, "observed": observed,
         "candidates_created": created,
         "decision_id": decision.decision_id,
         "degraded_sources": degraded,
         "mission_id": compiled.get("mission_id"),
         "reason": compiled.get("blocked_reason"),
         "lineage": lineage},
        source_type="supervisor", source_id=source_id,
        idempotency_key=f"discovery.cycle_completed:{loop_id}"))
    return CycleReport(
        loop_id=loop_id, outcome=outcome,
        decision_id=decision.decision_id,
        mission_id=compiled.get("mission_id"),
        candidate_id=decision.candidate_id,
        observed=observed, candidates_created=created,
        scored=len(decisions), eligible=len(eligible),
        rejected=int(portfolio["rejected_count"]),
        degraded_sources=degraded,
        reason=compiled.get("blocked_reason"), lineage=lineage)


def _threshold(database: FoundryDatabase, policy_version: str) -> float:
    """min_total_score from the registered policy; 0.40 fallback."""
    row = database.row("scoring_policy_registry", "policy_version",
                       policy_version)
    if not row:
        return 0.40
    import json
    try:
        thresholds = json.loads(row["thresholds_json"] or "{}")
    except ValueError:
        return 0.40
    return float(thresholds.get("min_total_score", 0.40))


def _wait_margin(database: FoundryDatabase, policy_version: str) -> float:
    """comparative_wait_margin from the registered policy; 0.05 fallback."""
    row = database.row("scoring_policy_registry", "policy_version",
                       policy_version)
    if not row:
        return 0.05
    import json
    try:
        thresholds = json.loads(row["thresholds_json"] or "{}")
    except ValueError:
        return 0.05
    return float(thresholds.get("comparative_wait_margin", 0.05))


def compile_mission(
    database: FoundryDatabase,
    *,
    candidate_id: str,
    objective: str,
    resource_id: str,
    capability: str = "write_registered_worktree_files",
    decision_id: str,
    activation_epoch: str = "",
    source_id: str = "v3-cycle",
) -> dict[str, Any]:
    """MissionCompiler: deterministic SELECT -> mission transformation.

    Fail-closed wrapper around ``materialize_selection``: missing
    provenance/verifier plan/rollback surfaces as
    executive.materialization_blocked, never as an invented mission.
    Duplicate prevention is structural: materialize_selection is
    idempotent per decision_id AND approve_candidate dedupes by the
    candidate's covered objective, so one fingerprint can never yield
    two mission.created events.
    """
    from .executive.missions import materialize_selection

    result = materialize_selection(
        database,
        candidate_id=candidate_id,
        objective=objective,
        resource_id=resource_id,
        capability=capability,
        decision_id=decision_id,
        activation_epoch=activation_epoch,
        source_id=source_id,
    )
    if result.get("materialized"):
        database.append(Event.create(
            "executive.decision_materialized",
            {"decision_id": decision_id, "decision": "SELECT",
             "candidate_id": candidate_id,
             "mission_id": result["mission_id"]},
            source_type="supervisor", source_id=source_id,
            idempotency_key=f"materialize:{decision_id}"))
    return result
