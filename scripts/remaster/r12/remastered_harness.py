"""Run equivalent relevant inputs through the REMASTERED JARVIS stack.

Executed as a subprocess from the remastered worktree; all stores live in
an isolated proving root under JARVIS_R12_TEMP.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from common import TEMP_BASE, safe_clear, write_json

from jarvis_foundry.command_center.briefings import generate_briefing
from jarvis_foundry.command_center.state import build_r10_snapshot
from jarvis_foundry.evolution_live.engine import EvolutionEngine
from jarvis_foundry.fault_campaign.isolation import (
    ProvingWorkspace,
    seed_capability,
    seed_micro_fixture,
    seed_portfolio,
    tree_hash_of,
)
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.portfolio.selection import SelectionService

FIXED_UTC = "2026-08-16T20:00:00Z"


def _portfolio(ws: ProvingWorkspace) -> dict[str, Any]:
    store = ws.portfolio("portfolio")
    seed_portfolio(store, fixed_utc=FIXED_UTC)
    service = SelectionService(store, clock=FixedClock(FIXED_UTC))
    decision = service.run()
    return {
        "selected_project_ids": [decision.project_id] if decision.selected_candidate_id else [],
        "decision_status": decision.status,
        "reason": decision.reason,
        "alternative_count": len(decision.alternatives),
    }


def _orchestration(ws: ProvingWorkspace) -> dict[str, Any]:
    from jarvis_foundry.fault_campaign.scenarios import _supervisor

    service = _supervisor(ws)
    service.start("s1")
    service.acquire_lease("worker-alpha", "s1")
    service.release_lease("worker-alpha", "s1")
    return {
        "supervisor_started": True,
        "lease_acquired_and_released": True,
        "running_count": service.running_count(),
    }


def _capability_lifecycle(ws: ProvingWorkspace) -> dict[str, Any]:
    registry = ws.capability_registry("capability")
    seed_capability(registry, "capability.files")
    snapshot = registry.snapshot()
    return {
        "capability_status": snapshot["states"]["capabilities"]["capability.files"]["status"],
        "event_count": len(registry.events()),
    }


def _briefings(ws: ProvingWorkspace) -> dict[str, Any]:
    briefing_ws = ws.fresh("briefing")
    registry = briefing_ws.capability_registry("capability")
    seed_capability(registry, "capability.files")
    write_json(
        briefing_ws.layout().capabilities,
        {
            "states": registry.snapshot().get("states", {}).get("capabilities", {}),
            "adapters": {},
            "unavailable": [],
        },
    )
    snapshot = build_r10_snapshot(briefing_ws.layout(), now=FIXED_UTC)
    en = generate_briefing(snapshot, "en")
    sq = generate_briefing(snapshot, "sq")
    return {
        "en_present": en["lang"] == "en" and bool(en["text"]),
        "sq_present": sq["lang"] == "sq" and bool(sq["text"]),
        "en_sample": en["text"].splitlines()[:3],
        "sq_sample": sq["text"].splitlines()[:3],
    }


def _evolution_cycle(ws: ProvingWorkspace) -> dict[str, Any]:
    store = ws.evolution("evolution")
    engine = EvolutionEngine(store, source_id="r12-comparison")
    fixture = seed_micro_fixture(ws, name="target")
    store.apply_live_files(fixture)
    live_hash = tree_hash_of(store.live_dir)
    engine.observe(
        controller="supervisor",
        cycle_id="r12-comparison-cycle",
        observation="micro target scoring flag present",
        baseline_ref="fixtures/target",
        baseline_hash=live_hash,
    )
    engine.hypothesize(
        controller="supervisor",
        cycle_id="r12-comparison-cycle",
        candidate_id="candidate-comparison",
        hypothesis="keep isolated target consistent",
        acceptance_criteria=["no_regression"],
        risk_class="R1",
        surfaces=["fixtures/target/values.py"],
        budget={"max_cost_usd": 0.0},
        authority_scope={"targets": ["fixtures/target"], "allowed_operations": ["replace_file"]},
    )
    engine.isolate_candidate(
        controller="supervisor",
        cycle_id="r12-comparison-cycle",
        candidate_id="candidate-comparison",
        patch_hash="p" * 64,
        artifact_hashes={"values.py": "a" * 64},
        isolation_clone_hash="c" * 64,
    )
    engine.evaluate(
        controller="supervisor",
        cycle_id="r12-comparison-cycle",
        candidate_id="candidate-comparison",
        baseline_metrics={"pytest_passed": 1, "pytest_failed": 0},
        candidate_metrics={"pytest_passed": 1, "pytest_failed": 0},
        improvement=True,
        guardrails_ok=True,
        delta=0,
    )
    engine.record_verification(
        controller="supervisor",
        cycle_id="r12-comparison-cycle",
        candidate_id="candidate-comparison",
        verifier_id="independent-verifier-comparison",
        verdict="PASS",
        evidence_refs=["fixtures/target/probe.py"],
        verification_request_hash="v" * 64,
    )
    engine.shadow(
        controller="supervisor",
        cycle_id="r12-comparison-cycle",
        candidate_id="candidate-comparison",
        shadow_hash="s" * 64,
        shadow_checks={"no_regression": True},
    )
    engine.canary_ready(
        controller="supervisor",
        cycle_id="r12-comparison-cycle",
        candidate_id="candidate-comparison",
        canary_hash="k" * 64,
        canary_checks={"health_ok": True},
        ready=True,
    )
    engine.promote(
        controller="supervisor",
        cycle_id="r12-comparison-cycle",
        candidate_id="candidate-comparison",
        release_id="release-comparison",
        live_before_hash=live_hash,
        live_after_hash=live_hash,
        artifact_hashes={"values.py": "a" * 64},
        outcome_before={"pytest_passed": 1, "pytest_failed": 0},
        outcome_after={"pytest_passed": 1, "pytest_failed": 0},
    )
    return {
        "outcome": "promoted",
        "last_event": store.cycle_state("r12-comparison-cycle")["last_event"],
    }


def main() -> int:
    root = TEMP_BASE / "comparison-remastered"
    safe_clear(root)
    workspace = ProvingWorkspace(root, fixed_utc=FIXED_UTC)
    output = {
        "schema": "jarvis-r12-remastered-comparison-v1",
        "domains": {
            "goals_portfolio": _portfolio(workspace),
            "mission_orchestration": _orchestration(workspace),
            "capability_lifecycle": _capability_lifecycle(workspace),
            "command_center_briefing": _briefings(workspace),
            "evolution_cycle": _evolution_cycle(workspace),
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
