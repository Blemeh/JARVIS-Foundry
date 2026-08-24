"""Disposable multi-project V7 portfolio lab.

The lab creates a temporary ledger, feeds the executive selector several
project/work classes, replays the ledger into a fresh process, and prints a
small JSON evidence record. It never opens a configured/live JARVIS home.
"""
from __future__ import annotations

import json
import gc
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.evolution.executive_portfolio import (
    PortfolioPolicy,
    claim_selection,
    configure_portfolio_policy,
    portfolio_snapshot,
    record_portfolio_outcome,
    register_research,
    register_unknown,
    register_work_item,
    release_selection_claim,
    select_next_work,
)
from jarvis_foundry.evolution.portfolio import register_project
from jarvis_foundry.supervisor.authority import SUPERVISOR_CONTROLLER


def _work(database: FoundryDatabase, item_id: str, kind: str, project_id: str,
          score: float, *, owner_only: bool = False) -> None:
    register_work_item(
        database,
        item_id=item_id,
        kind=kind,
        objective=f"Lab objective: {item_id}",
        project_id=project_id,
        expected_value=score,
        urgency=score,
        information_gain=score,
        confidence=0.75,
        effort=0.2,
        cost_usd=0.0,
        risk=0.1,
        reversibility=0.8,
        opportunity_cost=0.1,
        owner_only=owner_only,
        requires_workers=0,
        requires_verifier=False,
        source_id="v7-lab",
    )


def _replay(source: FoundryDatabase, target: FoundryDatabase) -> None:
    target.initialize()
    for event in source.events():
        target.append(Event.create(
            event.event_type,
            event.payload,
            occurred_at=event.recorded_at,
            source_type=event.source_type,
            source_id=event.source_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            idempotency_key=event.idempotency_key,
        ))


def run_lab() -> dict[str, object]:
    base_time = datetime(2026, 8, 23, tzinfo=timezone.utc)
    with TemporaryDirectory(prefix="jarvis-v7-portfolio-lab-") as root:
        database = FoundryDatabase(Path(root) / "lab.db")
        database.initialize()
        configure_portfolio_policy(
            database,
            PortfolioPolicy(
                max_concurrent_missions=2,
                max_workers=3,
                max_verifiers=1,
                revisit_seconds=60.0,
                fairness_after_cycles=1,
                aging_step=0.2,
                max_aging_bonus=0.4,
                calibration_alpha=0.5,
                calibration_cap=0.15,
            ),
            source_id="v7-lab",
        )
        for project_id, value in (("alpha", 0.9), ("beta", 0.7), ("gamma", 0.6)):
            register_project(
                database,
                controller=SUPERVISOR_CONTROLLER,
                project_id=project_id,
                name=f"Lab project {project_id}",
                goals=[{"id": f"{project_id}-goal", "text": "verified progress"}],
                ev=value,
                urgency=value,
                information_gain=value,
                reversibility=0.8,
                opportunity_cost=0.1,
                provenance={"source": "v7-disposable-lab"},
                source_type="supervisor",
                source_id="v7-lab",
            )
        _work(database, "repair-alpha", "REPAIR", "alpha", 0.86)
        _work(database, "research-beta", "RESEARCH", "beta", 0.62)
        _work(database, "maintenance-gamma", "MAINTENANCE", "gamma", 0.55)
        register_research(
            database,
            research_id="provider-health",
            question="Which provider is healthy for the next lab action?",
            why_matters="routing evidence",
            evidence_needed=["local health sample"],
            confidence_threshold=0.8,
            project_id="beta",
            source_id="v7-lab",
        )
        register_unknown(
            database,
            unknown_id="owner-approval",
            question="Is the gamma dependency approved?",
            why_matters="release safety",
            evidence_needed=["owner directive"],
            project_id="gamma",
            owner_only=True,
            source_id="v7-lab",
        )

        decisions: list[dict[str, object]] = []
        outcomes: list[dict[str, object]] = []
        for index in range(8):
            now = base_time + timedelta(seconds=index)
            decision = select_next_work(
                database,
                loop_id=f"lab-{index}",
                now=now,
                source_id="v7-lab",
            )
            decisions.append({
                "loop_id": decision.loop_id,
                "decision": decision.kind,
                "class": decision.selected_class,
                "entity_id": decision.entity_id,
                "reason": decision.reason,
            })
            if decision.kind != "SELECT" or not decision.entity_id:
                continue
            claim = claim_selection(database, decision, now=now, source_id="v7-lab")
            if not claim.get("acquired"):
                continue
            status = "FAILED" if index == 2 else "PASSED"
            actual_value = 0.35 if status == "FAILED" else 0.9
            record_portfolio_outcome(
                database,
                outcome_id=f"lab-outcome-{index}",
                selection_id=decision.decision_id,
                entity_id=decision.entity_id,
                kind=decision.selected_class,
                predicted={"expected_value": decision.expected_value or 0.0},
                actual={"actual_value": actual_value},
                status=status,
                project_id=decision.selected_item.get("project_id"),
                actual_cost_usd=0.0,
                source_id="v7-lab",
            )
            outcomes.append({"entity_id": decision.entity_id, "status": status})
            release_selection_claim(database, decision.entity_id, source_id="v7-lab")

        fresh = FoundryDatabase(Path(root) / "restarted.db")
        _replay(database, fresh)
        original = portfolio_snapshot(database)
        restarted = portfolio_snapshot(fresh)
        selected_classes = Counter(
            str(row["class"]) for row in decisions if row["decision"] == "SELECT"
        )
        wait_reasons = Counter(
            str(row["reason"]) for row in decisions if row["decision"] == "WAIT"
        )
        result: dict[str, object] = {
            "temporary_root": str(root),
            "project_count": len(original["projects"]),
            "decision_count": len(decisions),
            "selected_classes": dict(selected_classes),
            "wait_reasons": dict(wait_reasons),
            "outcome_count": len(outcomes),
            "event_count": database.event_count(),
            "restart_outcomes": restarted["outcomes"],
            "restart_claims": len(restarted["claims"]),
            "owner_only_present": any(
                "owner_only_fact" in str(row.get("reason"))
                for row in decisions
                if row["decision"] == "WAIT"
            ),
            "replay_consistent": (
                restarted["outcomes"] == original["outcomes"]
                and restarted["projects"] == original["projects"]
                and not restarted["claims"]
            ),
            "decisions": decisions,
        }
        if not result["replay_consistent"] or result["project_count"] != 3:
            raise RuntimeError(f"V7 lab invariant failed: {result}")
        # FoundryDatabase's read helpers use sqlite connection context managers
        # whose close is finalized by Python; collect before TemporaryDirectory
        # removes the Windows database files.
        del fresh, database
        gc.collect()
        return result


def main() -> None:
    print(json.dumps(run_lab(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
