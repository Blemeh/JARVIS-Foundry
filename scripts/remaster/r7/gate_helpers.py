"""Shared real-data flows for the R7 gates."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_UTC,
    PROVING_ROOT,
    STAGED_ROOT,
    safe_clear,
)

from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.events import EventKindRegistry
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.portfolio.budget import Budget
from jarvis_foundry.kernel.portfolio.missions import MissionIntegrator
from jarvis_foundry.kernel.portfolio.records import (
    CandidateAction,
    Observation,
    PortfolioProject,
    ResearchSource,
    SourceRef,
)
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def fixed_clock() -> FixedClock:
    return FixedClock(EVIDENCE_UTC)


def new_store(name: str) -> PortfolioStore:
    root = PROVING_ROOT / f"portfolio-{name}"
    safe_clear(root)
    return PortfolioStore(root)


def new_ledger(name: str) -> Ledger:
    registry = (
        EventKindRegistry()
        .register("goal.created", required_keys=("goal_id",))
        .register("mission.created", required_keys=("mission_id",))
    )
    path = PROVING_ROOT / name
    safe_clear(path)
    path.mkdir(parents=True, exist_ok=True)
    return Ledger(path / "kernel.db", registry=registry)


def new_budget(name: str, *, max_calls: int = 64) -> Budget:
    return Budget(
        budget_key=name,
        max_calls=max_calls,
        max_tokens=20_000,
        max_cost_usd=0.0,
    )


def staged_manifest() -> dict[str, Any]:
    path = STAGED_ROOT / "staging_manifest.json"
    if not path.is_file():
        raise RuntimeError("stage_sources.py must run before gates")
    return json.loads(path.read_text(encoding="utf-8"))


def project_from_manifest(project_id: str, manifest: dict[str, Any]) -> PortfolioProject:
    entry = manifest["projects"][project_id]
    dirty = int(entry["porcelain_count"]) > 0
    source = SourceRef(
        source_type="git-status",
        source_id=project_id,
        captured_at=EVIDENCE_UTC,
        content_hash=str(entry["git_status_sha256"]),
        locator=f"PROVING_AREA/staged/projects/{project_id}/git_status.txt",
        capture_method="read-only git status",
        trust_class="owner-controlled",
        freshness_seconds=86_400,
    )
    return PortfolioProject(
        project_id=project_id,
        title=project_id.replace("-", " ").title(),
        canonical_target=str(entry["path"]),
        goal_id=f"goal-{project_id}",
        goal_title=f"Advance {project_id.replace('-', ' ').title()}",
        status="ACTIVE",
        deadline=None,
        resources=("builder", "bounded-local"),
        cost_usd=0.0,
        expected_value=0.65 if dirty else 0.5,
        urgency=0.7 if dirty else 0.4,
        information_gain=0.5,
        reversibility=0.8,
        opportunity_cost=0.3,
        risk=0.4 if dirty else 0.3,
        dependencies=(),
        risks=(),
        source_ref=source,
        observed_at=EVIDENCE_UTC,
        notes=f"dirty={dirty}",
    )


def observations_from_manifest(manifest: dict[str, Any], fixed_time: str) -> list[Observation]:
    observations: list[Observation] = []
    for project_id, entry in manifest["projects"].items():
        dirty = int(entry["porcelain_count"]) > 0
        ref = SourceRef(
            source_type="git-status",
            source_id=project_id,
            captured_at=fixed_time,
            content_hash=str(entry["git_status_sha256"]),
            locator=f"PROVING_AREA/staged/projects/{project_id}/git_status.txt",
            capture_method="read-only git status",
            trust_class="owner-controlled",
            freshness_seconds=86_400,
        )
        observations.append(
            Observation(
                observation_id=f"obs-{project_id}-status",
                source_type="git-status",
                source_id=project_id,
                captured_at=fixed_time,
                content_hash=str(entry["git_status_sha256"]),
                evidence_refs=(ref,),
                observed_value=(
                    f"{project_id} has {entry['porcelain_count']} uncommitted changes"
                    if dirty
                    else f"{project_id} working tree is clean"
                ),
                meaningful_change=dirty,
                change_hash=str(entry["git_status_sha256"]) if dirty else None,
                project_id=project_id,
            )
        )
    return observations


def candidate_from_discovery(
    discovery, project: PortfolioProject, fixed_time: str
) -> CandidateAction:
    return CandidateAction(
        candidate_id=discovery.candidate_id,
        project_id=project.project_id,
        objective=discovery.objective,
        action_class="bounded_review",
        canonical_target=project.canonical_target,
        evidence_refs=discovery.evidence_refs,
        expected_value=0.7,
        uncertainty=0.2,
        urgency=0.6,
        information_gain=0.5,
        reversibility=0.9,
        opportunity_cost=0.2,
        risk=0.3,
        cost_usd=0.0,
        budget_effects=1,
        status="READY",
        priority_boost=0,
        created_at=fixed_time,
    )


def research_source_from_staged(
    source_id: str, relative: str, *, trust_class: str = "owner-controlled"
) -> ResearchSource:
    path = STAGED_ROOT / relative
    return ResearchSource(
        source_id=source_id,
        locator=str(path),
        capture_method="read-only staged source",
        captured_at=EVIDENCE_UTC,
        content_hash="",
        trust_class=trust_class,
        freshness_seconds=86_400,
    )


def public_probe(source_id: str, url: str) -> dict[str, Any]:
    """Bounded read-only public fetch; unavailable sources are BLOCKED, never fake."""
    result: dict[str, Any] = {
        "source_id": source_id,
        "url": url,
        "status": "BLOCKED",
        "detail": "public fetch unavailable or refused",
        "content_hash": None,
        "size_bytes": None,
    }
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "jarvis-r7-research/0.1"})
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(262_144 + 1)
        if len(data) > 262_144:
            data = data[:262_144]
        result["status"] = "OK"
        result["content_hash"] = __import__("hashlib").sha256(data).hexdigest()
        result["size_bytes"] = len(data)
        result["detail"] = "public read-only fetch succeeded"
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        result["detail"] = f"public fetch unavailable: {type(exc).__name__}"
    return result


def monitor_proving_copy(source_name: str) -> tuple[Path, Path]:
    """Create a mutable proving-area copy of a real staged source."""
    staging = STAGED_ROOT / "projects" / "jarvis-foundry"
    candidates = [
        staging / "git_status.txt",
        staging / "last_commit.txt",
    ]
    source = next((path for path in candidates if path.is_file()), staging / "head.txt")
    mutable_root = PROVING_ROOT / "monitor-sources"
    safe_clear(mutable_root)
    mutable_root.mkdir(parents=True, exist_ok=True)
    mutable = mutable_root / f"{source_name}.txt"
    if source.is_file():
        shutil.copy2(source, mutable)
    else:
        mutable.write_text("baseline\n", encoding="utf-8")
    return source, mutable


def mission_integration_report(
    store: PortfolioStore,
    ledger: Ledger,
    decision,
    project: PortfolioProject,
) -> dict[str, Any]:
    integrator = MissionIntegrator(ledger, store=store, clock=fixed_clock())
    goal = integrator.sync_goal(
        goal_id=project.goal_id,
        title=project.goal_title,
        objective=project.title,
        status="ACTIVE",
    )
    mission = integrator.create_mission(
        goal_id=project.goal_id,
        objective=decision.reason[:200],
        candidate_id=decision.selected_candidate_id or "",
        decision_id=decision.decision_id,
        project_target=project.canonical_target,
        expected_target=decision.canonical_target or project.canonical_target,
    )
    replay = integrator.create_mission(
        goal_id=project.goal_id,
        objective=decision.reason[:200],
        candidate_id=decision.selected_candidate_id or "",
        decision_id=decision.decision_id,
        project_target=project.canonical_target,
        expected_target=decision.canonical_target or project.canonical_target,
    )
    return {
        "goal_created": goal.created,
        "mission_id": mission.mission_id,
        "ledger_event_id": mission.ledger_event_id,
        "ledger_header_hash": mission.ledger_header_hash,
        "chain_valid": ledger.verify_chain().valid,
        "mission_count": sum(
            1 for event in ledger.replay() if event.event_type == "mission.created"
        ),
        "replay_mission_id": replay.mission_id,
        "replay_idempotent": replay.mission_id == mission.mission_id,
        "reconciliation": integrator.reconcile_target(project.canonical_target),
    }
