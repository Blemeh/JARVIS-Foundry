from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.portfolio.budget import Budget
from jarvis_foundry.kernel.portfolio.records import (
    CandidateAction,
    MonitorCursor,
    Observation,
    PortfolioProject,
    ProjectRisk,
    ResearchClaim,
    ResearchSource,
    SourceRef,
)


def make_store(root: Path) -> PortfolioStore:
    """Build a fresh in-memory-backed portfolio store for a test."""

    return PortfolioStore(root / "portfolio.jsonl")


from jarvis_foundry.kernel.portfolio.store import PortfolioStore

FIXED_UTC = "2026-08-15T16:30:00Z"


@pytest.fixture
def fixed_time() -> str:
    return FIXED_UTC


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock(FIXED_UTC)


@pytest.fixture
def store(tmp_path: Path) -> PortfolioStore:
    return PortfolioStore(tmp_path / "portfolio")


@pytest.fixture
def source_ref(fixed_time: str) -> SourceRef:
    return SourceRef(
        source_type="fixture",
        source_id="r7-test-source",
        captured_at=fixed_time,
        content_hash="a" * 64,
        locator="proving/r7-test.txt",
        capture_method="read-only fixture",
        trust_class="owner-controlled",
        freshness_seconds=3600,
    )


def make_project(
    project_id: str = "leadgen",
    *,
    status: str = "ACTIVE",
    expected_value: float = 0.7,
    urgency: float = 0.5,
    risk: float = 0.3,
    opportunity_cost: float = 0.25,
    fixed_time: str = FIXED_UTC,
    source_ref: SourceRef | None = None,
) -> PortfolioProject:
    ref = source_ref or SourceRef(
        source_type="fixture",
        source_id="portfolio-source",
        captured_at=fixed_time,
        content_hash="b" * 64,
        locator="proving/portfolio.json",
        capture_method="read-only fixture",
        trust_class="owner-controlled",
        freshness_seconds=86400,
    )
    return PortfolioProject(
        project_id=project_id,
        title=project_id.title(),
        canonical_target=f"proving/{project_id}",
        goal_id=f"goal-{project_id}",
        goal_title=f"Advance {project_id}",
        status=status,
        deadline=None,
        resources=("builder", "bounded-local"),
        cost_usd=0.0,
        expected_value=expected_value,
        urgency=urgency,
        information_gain=0.5,
        reversibility=0.8,
        opportunity_cost=opportunity_cost,
        risk=risk,
        dependencies=(),
        risks=(
            ProjectRisk(
                risk_id=f"risk-{project_id}-1",
                title="dependency drift",
                severity="medium",
                likelihood="medium",
                source_ref=ref,
            ),
        ),
        source_ref=ref,
        observed_at=fixed_time,
    )


def make_candidate(
    candidate_id: str = "leadgen-repair",
    *,
    project_id: str = "leadgen",
    objective: str = "Repair the sales worklist export lineage",
    status: str = "READY",
    expected_value: float = 0.8,
    uncertainty: float = 0.1,
    urgency: float = 0.6,
    information_gain: float = 0.4,
    reversibility: float = 0.9,
    risk: float = 0.2,
    cost_usd: float = 0.0,
    budget_effects: int = 1,
    priority_boost: int = 0,
    fixed_time: str = FIXED_UTC,
    source_ref: SourceRef | None = None,
) -> CandidateAction:
    ref = source_ref or SourceRef(
        source_type="fixture",
        source_id="candidate-source",
        captured_at=fixed_time,
        content_hash="c" * 64,
        locator="proving/candidate.json",
        capture_method="read-only fixture",
        trust_class="owner-controlled",
        freshness_seconds=3600,
    )
    return CandidateAction(
        candidate_id=candidate_id,
        project_id=project_id,
        objective=objective,
        action_class="bounded_repair",
        canonical_target=f"proving/{project_id}",
        evidence_refs=(ref,),
        expected_value=expected_value,
        uncertainty=uncertainty,
        urgency=urgency,
        information_gain=information_gain,
        reversibility=reversibility,
        opportunity_cost=0.2,
        risk=risk,
        cost_usd=cost_usd,
        budget_effects=budget_effects,
        status=status,
        priority_boost=priority_boost,
        created_at=fixed_time,
    )


def make_observation(
    observation_id: str = "obs-1",
    *,
    meaningful_change: bool = True,
    content_hash: str = "d" * 64,
    change_hash: str = "e" * 64,
    observed_value: str = "sales worklist export lineage is incomplete",
    fixed_time: str = FIXED_UTC,
    source_ref: SourceRef | None = None,
) -> Observation:
    ref = source_ref or SourceRef(
        source_type="git-status",
        source_id="leadgen",
        captured_at=fixed_time,
        content_hash=content_hash,
        locator="proving/leadgen/status.txt",
        capture_method="read-only git status",
        trust_class="owner-controlled",
        freshness_seconds=900,
    )
    return Observation(
        observation_id=observation_id,
        source_type="git-status",
        source_id="leadgen",
        captured_at=fixed_time,
        content_hash=content_hash,
        evidence_refs=(ref,),
        observed_value=observed_value,
        meaningful_change=meaningful_change,
        change_hash=change_hash if meaningful_change else None,
    )


def make_research_source(
    source_id: str = "official-release-notes",
    *,
    captured_at: str = FIXED_UTC,
    content_hash: str = "f" * 64,
    freshness_seconds: int = 3600,
    trust_class: str = "official",
    status: str = "FRESH",
    locator: str = "proving/research/release-notes.md",
) -> ResearchSource:
    return ResearchSource(
        source_id=source_id,
        locator=locator,
        capture_method="read-only local source",
        captured_at=captured_at,
        content_hash=content_hash,
        trust_class=trust_class,
        freshness_seconds=freshness_seconds,
        status=status,
    )


def make_claim(
    claim_id: str = "claim-1",
    *,
    source_id: str = "official-release-notes",
    statement: str = "the demo dependency supports Python 3.13",
    polarity: str = "SUPPORTS",
    freshness_ok: bool = True,
    uncertainty: float = 0.1,
) -> ResearchClaim:
    return ResearchClaim(
        claim_id=claim_id,
        source_id=source_id,
        statement=statement,
        polarity=polarity,
        freshness_ok=freshness_ok,
        citation=f"{source_id}#{claim_id}",
        uncertainty=uncertainty,
    )


def make_budget(
    *,
    max_calls: int = 100,
    max_tokens: int = 10_000,
    max_cost_usd: float = 0.0,
) -> Budget:
    return Budget(
        budget_key="r7-budget",
        max_calls=max_calls,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
    )


def make_cursor(
    source_id: str = "project-health-feed",
    *,
    content_hash: str = "",
    change_hash: str | None = None,
    candidate_id: str | None = None,
    fixed_time: str = FIXED_UTC,
) -> MonitorCursor:
    return MonitorCursor(
        source_id=source_id,
        locator="proving/health.txt",
        last_captured_at=fixed_time,
        last_content_hash=content_hash,
        last_change_hash=change_hash,
        candidate_id=candidate_id,
        freshness_seconds=900,
    )


def kernel_registry() -> EventKindRegistry:
    return (
        EventKindRegistry()
        .register("goal.created", required_keys=("goal_id",))
        .register("mission.created", required_keys=("mission_id",))
    )


def kernel_ledger(path: Path) -> Ledger:
    path.mkdir(parents=True, exist_ok=True)
    return Ledger(path / "kernel.db", registry=kernel_registry())


def mission_event(
    mission_id: str,
    *,
    objective: str = "objective",
    idempotency_key: str | None = None,
) -> LedgerEvent:
    return LedgerEvent.create(
        event_type="mission.created",
        payload={"mission_id": mission_id, "objective": objective, "status": "PLANNED"},
        source_type="r7-test",
        source_id="r7",
        idempotency_key=idempotency_key or f"mission.created:{mission_id}",
        event_id=f"evt-{mission_id}",
        occurred_at=FIXED_UTC,
    )


def records_equal(first: Any, second: Any) -> bool:
    return first.to_dict() == second.to_dict()
