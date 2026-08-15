"""Durable kernel Mission/MissionStep integration via the R4 ledger."""

from __future__ import annotations

from pathlib import Path

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent
from jarvis_foundry.kernel.ledger import AppendResult, Ledger
from jarvis_foundry.kernel.portfolio.errors import PortfolioError, TargetMismatchError
from jarvis_foundry.kernel.portfolio.provenance import run_git
from jarvis_foundry.kernel.portfolio.records import MissionLink
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def mission_registry() -> EventKindRegistry:
    return (
        EventKindRegistry()
        .register("goal.created", required_keys=("goal_id",))
        .register("mission.created", required_keys=("mission_id",))
    )


class MissionIntegrator:
    def __init__(
        self,
        ledger: Ledger,
        *,
        store: PortfolioStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.ledger = ledger
        self.store = store
        self._clock = clock if clock is not None else SystemClock()

    def sync_goal(
        self,
        goal_id: str,
        title: str,
        objective: str,
        status: str = "ACTIVE",
    ) -> AppendResult:
        event = LedgerEvent.create(
            event_type="goal.created",
            payload={
                "goal_id": goal_id,
                "title": title,
                "objective": objective,
                "status": status,
                "created_at": self._clock.now_utc(),
            },
            source_type="r7-portfolio",
            source_id=goal_id,
            idempotency_key=f"goal.created:{goal_id}",
            event_id=f"goal-created-{goal_id}",
            occurred_at=self._clock.now_utc(),
        )
        return self.ledger.append(event)

    def create_mission(
        self,
        *,
        goal_id: str,
        objective: str,
        candidate_id: str,
        decision_id: str,
        project_target: str,
        expected_target: str,
        priority: int = 1,
        status: str = "PLANNED",
    ) -> MissionLink:
        if not self._goal_exists(goal_id):
            raise PortfolioError(f"mission requires an existing kernel goal: {goal_id}")
        if project_target != expected_target:
            raise TargetMismatchError(
                f"canonical target binding refused: decision target {expected_target!r} "
                f"differs from requested dispatch target {project_target!r}"
            )
        now = self._clock.now_utc()
        mission_id = (
            f"mission-r7-{canonical_hash([goal_id, objective, candidate_id, decision_id])[:16]}"
        )
        reconciliation = self.reconcile_target(project_target)
        event = LedgerEvent.create(
            event_type="mission.created",
            payload={
                "mission_id": mission_id,
                "goal_id": goal_id,
                "objective": objective,
                "status": status,
                "priority": priority,
                "created_at": now,
                "source_candidate_id": candidate_id,
                "decision_id": decision_id,
                "project_target": project_target,
                "reconciliation": reconciliation,
            },
            source_type="r7-portfolio",
            source_id=mission_id,
            idempotency_key=f"mission.created:{mission_id}",
            event_id=f"mission-created-{mission_id}",
            occurred_at=now,
        )
        result = self.ledger.append(event)
        event = result.event
        link = MissionLink(
            mission_id=mission_id,
            goal_id=goal_id,
            candidate_id=candidate_id,
            decision_id=decision_id,
            ledger_event_id=event.event_id,
            ledger_header_hash=event.header_hash,
            created_at=now,
        )
        if self.store is not None:
            self.store.append_mission_link(link, idempotency_key=f"mission_link:{mission_id}")
        return link

    def _goal_exists(self, goal_id: str) -> bool:
        return any(
            event.event_type == "goal.created" and event.payload.get("goal_id") == goal_id
            for event in self.ledger.replay()
        )

    def reconcile_target(self, canonical_target: str) -> dict[str, object]:
        path = Path(canonical_target)
        exists = path.is_dir()
        head = run_git(path, ["rev-parse", "HEAD"]) if exists else ""
        is_git_repo = bool(head) or bool(run_git(path, ["rev-parse", "--is-inside-work-tree"]))
        porcelain = run_git(path, ["status", "--porcelain"]) if exists else ""
        return {
            "target": canonical_target,
            "exists": exists,
            "is_git_repo": is_git_repo,
            "head": head or None,
            "porcelain_count": len([line for line in porcelain.splitlines() if line.strip()]),
            "captured_at": self._clock.now_utc(),
        }

    def validate_target(self, expected: str, actual: str) -> None:
        if expected != actual:
            raise TargetMismatchError(
                f"selected target {expected!r} differs from execution target {actual!r}"
            )


__all__ = ["MissionIntegrator", "TargetMismatchError", "mission_registry"]
