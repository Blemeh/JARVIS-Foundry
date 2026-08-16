"""Seed isolated proving stores from real R7/R8/R9 gate evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.portfolio.records import (
    AlternativeScore,
    CandidateAction,
    DiscoveredCandidate,
    Observation,
    PortfolioProject,
    ProjectRisk,
    SelectionDecision,
    SourceRef,
)
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

from .proving import ProvingLayout

R10_EVIDENCE_UTC = "2026-08-16T12:00:00Z"


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _source_ref(evidence_root: Path, relative: str, source_id: str) -> SourceRef:
    path = evidence_root / relative
    return SourceRef(
        source_type="r10-gate-evidence",
        source_id=source_id,
        captured_at=R10_EVIDENCE_UTC,
        content_hash=_sha256_file(path),
        locator=str(Path("docs/remaster/evidence") / relative).replace("\\", "/"),
        capture_method="read-only evidence extraction",
        trust_class="owner-controlled",
        freshness_seconds=3600,
    )


def _read_program_state(evidence_root: Path) -> dict[str, Any]:
    path = evidence_root.parent / "PROGRAM_STATE.md"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match is None:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_findings(evidence_root: Path) -> list[dict[str, Any]]:
    path = evidence_root.parent / "FINDINGS.jsonl"
    if not path.is_file():
        return []
    findings: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            findings.append(payload)
    return findings


def _stage_release(evidence_root: Path, stage: str) -> dict[str, Any] | None:
    report = _read_json(evidence_root / stage / f"R{stage[1:]}_BUILDER_REPORT.json")
    if not report:
        return None
    frozen = report.get("frozen_base", {})
    return {
        "stage": stage,
        "status": report.get("builder_status", "NOT_TESTED"),
        "commit": frozen.get("commit", ""),
        "generated_utc": report.get("generated_utc", ""),
        "recorded_at": report.get("generated_utc", ""),
        "verdict": report.get("status_reason", ""),
        "evidence_refs": [
            {
                "source_type": "stage-builder-report",
                "source_id": f"R{stage[1:]}_BUILDER_REPORT.json",
                "captured_at": report.get("generated_utc", ""),
                "content_hash": _sha256_file(
                    evidence_root / stage / f"R{stage[1:]}_BUILDER_REPORT.json"
                ),
                "locator": f"docs/remaster/evidence/{stage}/R{stage[1:]}_BUILDER_REPORT.json",
            }
        ],
    }


def _seed_projects(store: PortfolioStore, evidence_root: Path) -> None:
    selection = _read_json(evidence_root / "r7" / "R7_SELECTION.json")
    discovery = _read_json(evidence_root / "r7" / "R7_DISCOVERY.json")
    projects: dict[str, dict[str, Any]] = {}
    for candidate in discovery.get("active_candidates", []):
        if not isinstance(candidate, dict):
            continue
        project_id = str(candidate.get("project_id", ""))
        if project_id:
            projects[project_id] = {
                "project_id": project_id,
                "objective": str(candidate.get("objective", "")),
            }
    canonical_target = ""
    decision = selection.get("decision", {})
    if isinstance(decision, dict):
        canonical_target = str(decision.get("canonical_target", ""))
    for project_id, info in sorted(projects.items()):
        ref = _source_ref(evidence_root, "r7/R7_DISCOVERY.json", f"r7-discovery-{project_id}")
        project = PortfolioProject(
            project_id=project_id,
            title=project_id.title(),
            canonical_target=(
                canonical_target
                if project_id == "operators" and canonical_target
                else f"proving/{project_id}"
            ),
            goal_id=f"goal-{project_id}",
            goal_title=f"Advance {project_id}",
            status="ACTIVE",
            deadline=None,
            resources=("builder", "bounded-local"),
            cost_usd=0.0,
            expected_value=0.7,
            urgency=0.5,
            information_gain=0.5,
            reversibility=0.8,
            opportunity_cost=0.25,
            risk=0.3,
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
            observed_at=R10_EVIDENCE_UTC,
            notes=str(info.get("objective", "")),
        )
        store.append_project(project)


def _seed_candidates(store: PortfolioStore, evidence_root: Path) -> None:
    discovery = _read_json(evidence_root / "r7" / "R7_DISCOVERY.json")
    monitor = _read_json(evidence_root / "r7" / "R7_MONITOR.json")
    seen: set[str] = set()
    for candidate in discovery.get("active_candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id", ""))
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        ref = _source_ref(evidence_root, "r7/R7_DISCOVERY.json", f"r7-discovery-{candidate_id}")
        record = CandidateAction(
            candidate_id=candidate_id,
            project_id=str(candidate.get("project_id", "")),
            objective=str(candidate.get("objective", "")),
            action_class="bounded_review",
            canonical_target=f"proving/{candidate.get('project_id', '')}",
            evidence_refs=(ref,),
            expected_value=0.7,
            uncertainty=0.2,
            urgency=0.5,
            information_gain=0.4,
            reversibility=0.8,
            opportunity_cost=0.3,
            risk=0.2,
            cost_usd=0.0,
            budget_effects=1,
            status="READY",
            created_at=R10_EVIDENCE_UTC,
        )
        store.append_candidate(record)
    change = monitor.get("change", {})
    if isinstance(change, dict) and change.get("candidate_id"):
        candidate_id = str(change["candidate_id"])
        if candidate_id not in seen:
            seen.add(candidate_id)
            ref = _source_ref(evidence_root, "r7/R7_MONITOR.json", f"r7-monitor-{candidate_id}")
            store.append_candidate(
                CandidateAction(
                    candidate_id=candidate_id,
                    project_id="jarvis-foundry",
                    objective="monitor change produced exactly one candidate",
                    action_class="monitored_change",
                    canonical_target="proving/jarvis-foundry",
                    evidence_refs=(ref,),
                    expected_value=0.6,
                    uncertainty=0.25,
                    urgency=0.5,
                    information_gain=0.4,
                    reversibility=0.8,
                    opportunity_cost=0.2,
                    risk=0.2,
                    cost_usd=0.0,
                    budget_effects=1,
                    status="READY",
                    created_at=R10_EVIDENCE_UTC,
                )
            )
    rollback = monitor.get("rollback", {})
    if isinstance(rollback, dict) and rollback.get("rejected_candidate"):
        store.append_discovery(
            DiscoveredCandidate(
                candidate_id=str(rollback["rejected_candidate"]),
                observation_id="",
                project_id="jarvis-foundry",
                objective="corrupt observation probe rejected on rollback",
                evidence_refs=(
                    _source_ref(evidence_root, "r7/R7_MONITOR.json", "r7-monitor-rollback"),
                ),
                status="REJECTED",
                duplicate_of=None,
                created_at=R10_EVIDENCE_UTC,
                rejection_reason=str(rollback.get("reason", "rejected")),
            )
        )


def _seed_observations(store: PortfolioStore, evidence_root: Path) -> None:
    discovery = _read_json(evidence_root / "r7" / "R7_DISCOVERY.json")
    for observation in discovery.get("observations", []):
        if not isinstance(observation, dict):
            continue
        observation_id = str(observation.get("observation_id", ""))
        if not observation_id:
            continue
        ref = _source_ref(evidence_root, "r7/R7_DISCOVERY.json", f"r7-observation-{observation_id}")
        store.append_observation(
            Observation(
                observation_id=observation_id,
                source_type="git-status",
                source_id=str(observation.get("project_id", "")),
                captured_at=R10_EVIDENCE_UTC,
                content_hash=ref.content_hash,
                evidence_refs=(ref,),
                observed_value=str(observation.get("objective", "")),
                meaningful_change=bool(observation.get("meaningful", True)),
                change_hash=(
                    str(observation["change_hash"]) if observation.get("change_hash") else None
                ),
                project_id=str(observation.get("project_id", "")),
            )
        )


def _seed_decisions(store: PortfolioStore, evidence_root: Path) -> None:
    selection = _read_json(evidence_root / "r7" / "R7_SELECTION.json")
    restart = _read_json(evidence_root / "r7" / "R7_RESTART.json")
    decisions: list[dict[str, Any]] = []
    decision = selection.get("decision")
    if isinstance(decision, dict):
        decisions.append(decision)
    first_run = restart.get("first_run")
    if isinstance(first_run, dict) and first_run.get("decision_id"):
        decisions.append(
            {
                "decision_id": first_run["decision_id"],
                "portfolio_hash": restart.get("staging_hash", ""),
                "control_hash": "",
                "status": first_run.get("decision_status", "SELECTED"),
                "selected_candidate_id": None,
                "project_id": None,
                "canonical_target": None,
                "reason": "R7 restart determinism decision",
                "alternatives": [],
                "uncertainty": {},
            }
        )
    for index, item in enumerate(decisions):
        ref = _source_ref(
            evidence_root,
            "r7/R7_SELECTION.json" if index == 0 else "r7/R7_RESTART.json",
            f"r7-decision-{item['decision_id']}",
        )
        alternatives = [
            AlternativeScore(
                candidate_id=str(alt.get("candidate_id", "")),
                total_score=float(alt.get("total_score", 0.0)),
                dimensions={},
                uncertainty=float(alt.get("uncertainty", 0.0)),
                opportunity_cost=float(alt.get("opportunity_cost", 0.0)),
                tradeoffs=tuple(str(value) for value in alt.get("tradeoffs", [])),
                reason=str(alt.get("reason", "")),
            )
            for alt in item.get("alternatives", [])
            if isinstance(alt, dict)
        ]
        record = SelectionDecision(
            decision_id=str(item["decision_id"]),
            portfolio_hash=str(item.get("portfolio_hash", ref.content_hash)),
            control_hash=str(item.get("control_hash", "")),
            budget_key="r7-portfolio",
            selected_candidate_id=item.get("selected_candidate_id"),
            project_id=item.get("project_id"),
            canonical_target=item.get("canonical_target"),
            status=str(item.get("status", "SELECTED")),
            reason=str(item.get("reason", "")),
            alternatives=tuple(alternatives),
            uncertainty={},
            supersedes=None,
            created_at=R10_EVIDENCE_UTC,
            idempotency_key=str(item["decision_id"]),
        )
        store.append_decision(record)


def _seed_missions(layout: ProvingLayout, evidence_root: Path) -> None:
    report = _read_json(evidence_root / "r8" / "R8_MACHINERY_REPORT.json")
    missions = report.get("missions", {})
    entries: list[dict[str, Any]] = []
    for key, mission in sorted(missions.items()):
        if not isinstance(mission, dict):
            continue
        report_path = str(mission.get("report_path", f"R8_MISSION_{key.upper()}.json"))
        mission_report = _read_json(evidence_root / "r8" / report_path)
        run = mission_report if isinstance(mission_report, dict) else mission.get("run", {})
        entries.append(
            {
                "mission_id": str(run.get("mission_id", f"mission-r8-{key}")),
                "kind": str(run.get("kind", key)),
                "status": str(mission_report.get("status", mission.get("status", "UNKNOWN")))
                if isinstance(mission_report, dict)
                else str(mission.get("status", "UNKNOWN")),
                "worker_id": str(run.get("worker_id", f"worker-mission-r8-{key}")),
                "objective": str(run.get("objective", "")),
                "recommended_next_action": str(
                    run.get("recommended_next_action", mission.get("recommended_next_action", ""))
                ),
                "recorded_at": str(
                    mission_report.get("generated_utc", report.get("generated_utc", ""))
                )
                if isinstance(mission_report, dict)
                else str(report.get("generated_utc", "")),
                "verdict": mission_report.get("verdict")
                if isinstance(mission_report, dict)
                else None,
                "ledger_events": (
                    mission_report.get("ledger", {}).get("events", [])
                    if isinstance(mission_report, dict)
                    else []
                ),
                "rollback": mission_report.get("rollback")
                if isinstance(mission_report, dict)
                else None,
                "reconciliation": (
                    mission_report.get("reconciliation")
                    if isinstance(mission_report, dict)
                    else None
                ),
                "evidence_refs": [
                    {
                        "source_type": "r8-mission-report",
                        "source_id": report_path,
                        "captured_at": mission_report.get("generated_utc", "")
                        if isinstance(mission_report, dict)
                        else report.get("generated_utc", ""),
                        "content_hash": _sha256_file(evidence_root / "r8" / report_path),
                        "locator": f"docs/remaster/evidence/r8/{report_path}",
                    }
                ],
            }
        )
    layout.missions.write_text(
        json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _seed_capabilities(layout: ProvingLayout, evidence_root: Path) -> None:
    lifecycle = _read_json(evidence_root / "r9" / "R9_LIFECYCLE.json")
    unavailable = _read_json(evidence_root / "r9" / "R9_UNAVAILABLE.json")
    states = lifecycle.get("capability_states", {})
    adapters = lifecycle.get("adapter_states", {})
    unavailable_list = unavailable.get("connectors", []) if isinstance(unavailable, dict) else []
    payload = {
        "states": states,
        "adapters": adapters,
        "unavailable": unavailable_list,
        "source": {
            "evidence": [
                "docs/remaster/evidence/r9/R9_LIFECYCLE.json",
                "docs/remaster/evidence/r9/R9_UNAVAILABLE.json",
            ],
            "captured_at": R10_EVIDENCE_UTC,
            "hashes": {
                "R9_LIFECYCLE.json": _sha256_file(evidence_root / "r9" / "R9_LIFECYCLE.json"),
                "R9_UNAVAILABLE.json": _sha256_file(evidence_root / "r9" / "R9_UNAVAILABLE.json"),
            },
        },
    }
    layout.capabilities.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _seed_findings(layout: ProvingLayout, evidence_root: Path) -> None:
    findings = _read_findings(evidence_root)
    layout.findings.write_text(
        json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _seed_releases(layout: ProvingLayout, evidence_root: Path) -> None:
    program = _read_program_state(evidence_root)
    releases: list[dict[str, Any]] = []
    for stage in ("r7", "r8", "r9"):
        release = _stage_release(evidence_root, stage)
        if release is not None:
            releases.append(release)
    payload: dict[str, Any] = {
        "releases": releases,
        "program_state": program,
        "exact_next_autonomous_action": str(program.get("exact_next_autonomous_action", "")),
        "current_stage": str(program.get("current_stage", "")),
        "stage_status": str(program.get("stage_status", "")),
    }
    layout.releases.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def seed_proving_stores(layout: ProvingLayout, evidence_root: Path) -> dict[str, Any]:
    layout.ensure()
    store = PortfolioStore(layout.portfolio)
    _seed_projects(store, evidence_root)
    _seed_candidates(store, evidence_root)
    _seed_observations(store, evidence_root)
    _seed_decisions(store, evidence_root)
    _seed_missions(layout, evidence_root)
    _seed_capabilities(layout, evidence_root)
    _seed_findings(layout, evidence_root)
    _seed_releases(layout, evidence_root)
    return {
        "schema": "jarvis-r10-seeding-v1",
        "evidence_utc": R10_EVIDENCE_UTC,
        "portfolio_events": store.event_count(),
        "projects": len(store.projects()),
        "candidates": len(store.candidates()),
        "decisions": len(store.decisions()),
        "missions": len(_read_json_store(layout.missions)),
        "capabilities": len(_read_json_store(layout.capabilities).get("states", {})),
        "findings": len(_read_json_store(layout.findings)),
        "releases": len(_read_json_store(layout.releases).get("releases", [])),
        "state_hash": store.state_hash(),
    }


def _read_json_store(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
