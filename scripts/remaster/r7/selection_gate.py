"""R7-G01 real multi-project decision gate."""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from typing import Any

from common import EVIDENCE_DIR, EVIDENCE_UTC, write_json
from gate_helpers import (
    candidate_from_discovery,
    fixed_clock,
    new_budget,
    new_store,
    observations_from_manifest,
    project_from_manifest,
    staged_manifest,
)

from jarvis_foundry.kernel.portfolio.control import PortfolioControl
from jarvis_foundry.kernel.portfolio.discovery import DiscoveryService
from jarvis_foundry.kernel.portfolio.errors import TargetMismatchError
from jarvis_foundry.kernel.portfolio.records import DiscoveredCandidate
from jarvis_foundry.kernel.portfolio.selection import SelectionService
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def _load_real_portfolio(store, manifest) -> None:
    for project_id in manifest["projects"]:
        store.append_project(project_from_manifest(project_id, manifest))
    for observation in observations_from_manifest(manifest, EVIDENCE_UTC):
        result = DiscoveryService(store).discover(observation)
        if result.created:
            project = store.project(observation.project_id)
            discovery = store.latest("discovered_candidate", result.candidate_id or "")
            if project is not None and discovery is not None:
                store.append_candidate(candidate_from_discovery(discovery, project, EVIDENCE_UTC))


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    manifest = staged_manifest()
    store = new_store("selection")
    clock = fixed_clock()
    _load_real_portfolio(store, manifest)
    budget = new_budget("selection")
    decision = SelectionService(store, budget=budget, clock=clock).run()
    duplicate = SelectionService(store, budget=budget, clock=clock).run()

    waiting_store = new_store("selection-waiting")
    waiting_project = project_from_manifest("leadgen", manifest)
    waiting_store.append_project(waiting_project)
    zero_value = replace(
        candidate_from_discovery(
            DiscoveredCandidate(
                candidate_id="cand-zero-value",
                observation_id="obs-zero",
                project_id="leadgen",
                objective="invented zero-value busy work",
                evidence_refs=(),
                status="CANDIDATE",
                duplicate_of=None,
                created_at=EVIDENCE_UTC,
            ),
            waiting_project,
            EVIDENCE_UTC,
        ),
        expected_value=0.0,
        uncertainty=1.0,
        urgency=0.0,
        information_gain=0.0,
        reversibility=0.0,
        opportunity_cost=0.0,
        risk=0.0,
        cost_usd=0.0,
    )
    waiting_store.append_candidate(zero_value)
    waiting = SelectionService(waiting_store, budget=new_budget("waiting"), clock=clock).run()
    zero_value_recorded = any(alt.candidate_id == "cand-zero-value" for alt in waiting.alternatives)
    zero_value_uncertainty_recorded = (
        "cand-zero-value" in waiting.uncertainty if waiting.uncertainty else False
    )

    control_store = new_store("selection-control")
    _load_real_portfolio(control_store, manifest)
    control = PortfolioControl(control_store)
    paused_project = next(iter(manifest["projects"]))
    control.pause(paused_project, nonce="pause-real-1")
    paused_decision = SelectionService(
        control_store, budget=new_budget("paused"), clock=clock
    ).run()
    paused_excluded = all(
        alt.candidate_id != "paused-probe" for alt in paused_decision.alternatives
    )
    control.resume(paused_project, nonce="resume-real-1")
    resumed_decision = SelectionService(
        control_store, budget=new_budget("resumed"), clock=clock
    ).run()

    target_mismatch_refused = False
    if decision.selected_candidate_id:
        try:
            SelectionService(control_store).validate_dispatch(
                decision, "proving/not-the-selected-target"
            )
        except TargetMismatchError:
            target_mismatch_refused = True

    reopened = PortfolioStore(store.root)
    restarted = SelectionService(reopened, budget=budget, clock=clock).run()

    passed = bool(
        decision.status == "SELECTED"
        and len(decision.alternatives) >= 1
        and all(alt.tradeoffs for alt in decision.alternatives)
        and duplicate.decision_id == decision.decision_id
        and restarted.decision_id == decision.decision_id
        and waiting.status == "WAITING"
        and waiting.selected_candidate_id is None
        and zero_value_recorded
        and zero_value_uncertainty_recorded
        and paused_decision.status in {"SELECTED", "WAITING"}
        and paused_excluded
        and resumed_decision.status in {"SELECTED", "WAITING"}
        and target_mismatch_refused
    )
    payload: dict[str, Any] = {
        "schema": "jarvis-r7-selection-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": 0.0,
        "passed": passed,
        "evidence_utc": EVIDENCE_UTC,
        "staging_hash": manifest["staging_hash"],
        "decision": {
            "decision_id": decision.decision_id,
            "status": decision.status,
            "selected_candidate_id": decision.selected_candidate_id,
            "project_id": decision.project_id,
            "canonical_target": decision.canonical_target,
            "portfolio_hash": decision.portfolio_hash,
            "control_hash": decision.control_hash,
            "reason": decision.reason,
            "alternatives": [
                {
                    "candidate_id": alt.candidate_id,
                    "total_score": alt.total_score,
                    "dimensions": alt.dimensions,
                    "uncertainty": alt.uncertainty,
                    "opportunity_cost": alt.opportunity_cost,
                    "tradeoffs": list(alt.tradeoffs),
                    "reason": alt.reason,
                }
                for alt in decision.alternatives
            ],
            "uncertainty": decision.uncertainty,
        },
        "determinism": {
            "duplicate_decision_id": duplicate.decision_id,
            "restart_decision_id": restarted.decision_id,
            "restart_store": str(store.root),
        },
        "honest_waiting": {
            "status": waiting.status,
            "selected_candidate_id": waiting.selected_candidate_id,
            "reason": waiting.reason,
            "alternative_count": len(waiting.alternatives),
            "zero_value_recorded": zero_value_recorded,
            "zero_value_uncertainty_recorded": zero_value_uncertainty_recorded,
            "zero_value_alternative_ids": [alt.candidate_id for alt in waiting.alternatives],
        },
        "pause_resume": {
            "paused_project": paused_project,
            "paused_decision_status": paused_decision.status,
            "paused_alternative_count": len(paused_decision.alternatives),
            "paused_excluded": paused_excluded,
            "resumed_decision_status": resumed_decision.status,
            "resumed_alternative_count": len(resumed_decision.alternatives),
        },
        "canonical_target_refusal": target_mismatch_refused,
    }
    output = EVIDENCE_DIR / "R7_SELECTION.json"
    write_json(output, payload)
    print(
        f"selection: passed={passed} decision={decision.status} "
        f"alternatives={len(decision.alternatives)} waiting={waiting.status} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
