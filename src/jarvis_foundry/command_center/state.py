"""Consolidated Command Center snapshot over the isolated proving stores."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

from .directives import DirectiveService
from .ledger import DirectiveLedger
from .proving import ProvingLayout

FRESHNESS_WINDOW_SECONDS = 86_400


def _json_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return canonical_hash({"payload": encoded.decode("utf-8")})


def _read_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _read_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _record_hash(record: Mapping[str, Any]) -> str:
    return _json_hash(record)


def _evidence_refs(record: Mapping[str, Any]) -> list[dict[str, str]]:
    refs = record.get("evidence_refs")
    if isinstance(refs, list):
        return [item for item in refs if isinstance(item, dict)]
    source = record.get("source_ref")
    if isinstance(source, dict):
        return [source]
    return []


def _provenance(record: Mapping[str, Any]) -> list[dict[str, str]]:
    refs = _evidence_refs(record)
    if refs:
        return refs
    kind = str(record.get("kind", ""))
    source = str(record.get("source", ""))
    return [{"kind": kind, "source": source}]


def _record_entries(
    section: str,
    records: Iterable[Mapping[str, Any]],
    *,
    id_key: str = "id",
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in records:
        payload = dict(record)
        identifier = str(payload.get(id_key, payload.get("record_id", "")) or "")
        entries.append(
            {
                "record_id": identifier,
                "kind": section,
                "payload": payload,
                "json_hash": _record_hash(payload),
                "evidence_refs": _evidence_refs(payload),
                "provenance": _provenance(payload),
            }
        )
    return entries


def _flatten(
    sections: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for section in (
        "goals",
        "projects",
        "missions",
        "candidate_decisions",
        "rejected_alternatives",
        "workers",
        "verification",
        "findings",
        "releases",
        "capabilities",
        "costs",
        "resources",
        "incidents",
        "waiting_blockers",
        "next_actions",
    ):
        result.extend(sections.get(section, []))
    return result


def build_r10_snapshot(
    layout: ProvingLayout,
    *,
    now: str | None = None,
    directive_service: DirectiveService | None = None,
) -> dict[str, Any]:
    """Deterministic projection of every durable proving store."""
    from datetime import UTC, datetime

    generated_at = now or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    portfolio = PortfolioStore(layout.portfolio)
    ledger = DirectiveLedger(layout.directive_ledger)
    service = directive_service or DirectiveService(portfolio, ledger)
    control = service.state_dict()

    capabilities_store = _read_dict(layout.capabilities)
    capabilities_states = capabilities_store.get("states", {})
    adapters = capabilities_store.get("adapters", {})
    unavailable = capabilities_store.get("unavailable", [])
    missions = _read_list(layout.missions)
    findings = _read_list(layout.findings)
    releases_store = _read_dict(layout.releases)
    releases = releases_store.get("releases", [])
    voice = _read_dict(layout.voice)

    goals: list[dict[str, Any]] = [
        {
            "goal_id": str(project.goal_id),
            "title": project.goal_title,
            "project_id": project.project_id,
            "status": project.status,
            "source_ref": project.source_ref.to_dict(),
        }
        for project in portfolio.projects()
    ]
    projects: list[dict[str, Any]] = [
        {
            "project_id": project.project_id,
            "title": project.title,
            "status": project.status,
            "canonical_target": project.canonical_target,
            "goal_id": project.goal_id,
            "expected_value": project.expected_value,
            "urgency": project.urgency,
            "risk": project.risk,
            "resources": list(project.resources),
            "cost_usd": project.cost_usd,
            "source_ref": project.source_ref.to_dict(),
        }
        for project in portfolio.projects()
    ]
    decisions: list[dict[str, Any]] = [
        {
            "decision_id": decision.decision_id,
            "project_id": decision.project_id,
            "selected_candidate_id": decision.selected_candidate_id,
            "status": decision.status,
            "reason": decision.reason,
            "portfolio_hash": decision.portfolio_hash,
            "control_hash": decision.control_hash,
            "created_at": decision.created_at,
            "alternatives": [
                {
                    "candidate_id": alternative.candidate_id,
                    "total_score": alternative.total_score,
                    "uncertainty": alternative.uncertainty,
                    "reason": alternative.reason,
                }
                for alternative in decision.alternatives
            ],
        }
        for decision in portfolio.decisions()
    ]
    rejected: list[dict[str, Any]] = []
    for decision in decisions:
        for alternative in decision["alternatives"]:
            if alternative["candidate_id"] != decision.get("selected_candidate_id"):
                rejected.append(
                    {
                        "candidate_id": alternative["candidate_id"],
                        "decision_id": decision["decision_id"],
                        "reason": alternative.get("reason", "not selected"),
                        "total_score": alternative["total_score"],
                        "status": "REJECTED_ALTERNATIVE",
                        "source": "portfolio selection decision",
                    }
                )
    for discovery in portfolio.discoveries():
        if discovery.status in {"REJECTED", "ROLLED_BACK", "SUPERSEDED"}:
            rejected.append(
                {
                    "candidate_id": discovery.candidate_id,
                    "decision_id": "",
                    "reason": discovery.rejection_reason or discovery.status,
                    "total_score": None,
                    "status": discovery.status,
                    "source": "portfolio discovery record",
                }
            )

    mission_entries: list[dict[str, Any]] = [
        {
            "mission_id": mission.get("mission_id", ""),
            "kind": mission.get("kind", ""),
            "status": mission.get("status", "UNKNOWN"),
            "objective": mission.get("objective", ""),
            "worker_id": mission.get("worker_id", ""),
            "recommended_next_action": mission.get("recommended_next_action", ""),
            "verdict": mission.get("verdict"),
            "ledger_events": mission.get("ledger_events", []),
            "rollback": mission.get("rollback"),
            "reconciliation": mission.get("reconciliation"),
            "evidence_refs": mission.get("evidence_refs", []),
        }
        for mission in missions
    ]
    workers: list[dict[str, Any]] = [
        {
            "worker_id": mission["worker_id"],
            "mission_id": mission["mission_id"],
            "role": mission.get("kind", "builder"),
            "status": mission.get("status", "UNKNOWN"),
            "live": False,
        }
        for mission in mission_entries
        if mission["worker_id"]
    ]
    verification: list[dict[str, Any]] = [
        {
            "verification_id": f"verdict-{mission['mission_id']}",
            "mission_id": mission["mission_id"],
            "verdict": mission["verdict"]["verdict"]
            if isinstance(mission["verdict"], dict)
            else None,
            "verifier_id": (
                mission["verdict"].get("verifier_id")
                if isinstance(mission["verdict"], dict)
                else None
            ),
            "verified_at": (
                mission["verdict"].get("verified_at")
                if isinstance(mission["verdict"], dict)
                else None
            ),
            "issues": mission["verdict"].get("issues", [])
            if isinstance(mission["verdict"], dict)
            else [],
            "evidence_refs": mission.get("evidence_refs", []),
        }
        for mission in mission_entries
        if isinstance(mission.get("verdict"), dict)
    ]

    capability_entries: list[dict[str, Any]] = []
    for capability_id, state in sorted(capabilities_states.items()):
        capability_entries.append(
            {
                "capability_id": capability_id,
                "name": state.get("name", capability_id),
                "domain": state.get("domain", ""),
                "status": state.get("status", "UNKNOWN"),
                "reason": state.get("reason", ""),
                "system_id": state.get("system_id", ""),
                "timeline": state.get("timeline", []),
                "source": "R9 capabilities_live registry evidence",
            }
        )

    incident_entries: list[dict[str, Any]] = []
    for capability in capability_entries:
        if capability["status"] in {"BLOCKED", "NOT_TESTED", "DEGRADED", "REVOKED"}:
            incident_entries.append(
                {
                    "incident_id": f"capability-{capability['capability_id']}",
                    "kind": "provider_failure",
                    "title": f"{capability['name']} unavailable",
                    "status": capability["status"],
                    "detail": capability["reason"],
                    "source": "R9 capabilities_live registry evidence",
                }
            )
    for adapter_key, adapter in sorted(adapters.items()):
        reason = adapter.get("reason", "")
        if reason and adapter.get("no_partial_effect"):
            incident_entries.append(
                {
                    "incident_id": f"adapter-{adapter_key}",
                    "kind": "provider_failure",
                    "title": adapter.get("operation", "adapter") + " refused",
                    "status": adapter.get("refusal", "REFUSED"),
                    "detail": reason,
                    "source": "R9 capability adapter evidence",
                }
            )
    for finding in findings:
        if finding.get("disposition") in {"OPEN_REPAIR", "OPEN_REWRITE", "OPEN_IMPLEMENT"}:
            incident_entries.append(
                {
                    "incident_id": f"finding-{finding.get('id', '')}",
                    "kind": "recorded_finding",
                    "title": finding.get("finding", ""),
                    "status": finding.get("status", "CONFIRMED"),
                    "detail": finding.get("disposition", ""),
                    "source": finding.get("source", ""),
                }
            )

    waiting: list[dict[str, Any]] = [
        {
            "item_id": mission["mission_id"],
            "kind": "mission",
            "status": mission["status"],
            "detail": mission.get("recommended_next_action", ""),
        }
        for mission in mission_entries
        if mission["status"] in {"WAITING", "BLOCKED"}
    ]
    waiting.extend(
        {
            "item_id": capability["capability_id"],
            "kind": "capability",
            "status": capability["status"],
            "detail": capability["reason"],
        }
        for capability in capability_entries
        if capability["status"] in {"BLOCKED", "NOT_TESTED"}
    )
    if control["stop_active"]:
        waiting.append(
            {
                "item_id": "GLOBAL",
                "kind": "control",
                "status": "STOPPED",
                "detail": "STOP directive is active; autonomous work is not eligible",
            }
        )

    next_actions: list[dict[str, Any]] = []
    for mission in mission_entries:
        if mission.get("recommended_next_action"):
            next_actions.append(
                {
                    "action_id": f"mission-{mission['mission_id']}",
                    "kind": "mission",
                    "title": mission["recommended_next_action"],
                    "priority": 2,
                }
            )
    program_next = str(releases_store.get("exact_next_autonomous_action", ""))
    if program_next:
        next_actions.append(
            {
                "action_id": "program-state",
                "kind": "program",
                "title": program_next,
                "priority": 1,
            }
        )
    if control["stop_active"]:
        next_actions.append(
            {
                "action_id": "control-stop",
                "kind": "control",
                "title": "STOP is active: no autonomous action until RESUME is recorded",
                "priority": 0,
            }
        )
    elif control["paused_projects"]:
        next_actions.append(
            {
                "action_id": "control-paused",
                "kind": "control",
                "title": f"paused targets: {', '.join(control['paused_projects'])}",
                "priority": 0,
            }
        )

    costs: dict[str, Any] = {
        "measurements": [
            {
                "measurement_id": f"budget-{state.budget_key}",
                "budget_key": state.budget_key,
                "max_calls": state.max_calls,
                "used_calls": state.used_calls,
                "max_tokens": state.max_tokens,
                "used_tokens": state.used_tokens,
                "max_cost_usd": state.max_cost_usd,
                "used_cost_usd": state.used_cost_usd,
                "status": "BUDGET_STATE",
            }
            for state in portfolio.budget_states()
        ],
        "measured_actual_total_usd": "UNKNOWN",
        "unknown_measurements": len(portfolio.budget_states()),
        "note": "No real provider USD receipts exist in R7-R9 evidence; amounts stay UNKNOWN",
    }

    resources: list[dict[str, Any]] = [
        {
            "resource_id": f"project-{project['project_id']}",
            "kind": "project",
            "target": project["canonical_target"],
            "detail": ", ".join(project["resources"]),
            "source": "R7 portfolio evidence",
        }
        for project in projects
    ]

    evidence_index: dict[str, Any] = {}
    sections = {
        "goals": _record_entries("goals", goals, id_key="goal_id"),
        "projects": _record_entries("projects", projects, id_key="project_id"),
        "missions": _record_entries("missions", mission_entries, id_key="mission_id"),
        "candidate_decisions": _record_entries(
            "candidate_decisions", decisions, id_key="decision_id"
        ),
        "rejected_alternatives": _record_entries(
            "rejected_alternatives", rejected, id_key="candidate_id"
        ),
        "workers": _record_entries("workers", workers, id_key="worker_id"),
        "verification": _record_entries("verification", verification, id_key="verification_id"),
        "findings": _record_entries("findings", findings, id_key="id"),
        "releases": _record_entries("releases", releases, id_key="stage"),
        "capabilities": _record_entries("capabilities", capability_entries, id_key="capability_id"),
        "costs": _record_entries("costs", costs["measurements"], id_key="measurement_id"),
        "resources": _record_entries("resources", resources, id_key="resource_id"),
        "incidents": _record_entries("incidents", incident_entries, id_key="incident_id"),
        "waiting_blockers": _record_entries("waiting_blockers", waiting, id_key="item_id"),
        "next_actions": _record_entries("next_actions", next_actions, id_key="action_id"),
    }
    for entries in sections.values():
        for entry in entries:
            evidence_index[entry["record_id"]] = entry
    timeline = _timeline(
        decisions=decisions,
        directive_events=ledger.events(),
        mission_entries=mission_entries,
        capability_entries=capability_entries,
        findings=findings,
        releases=releases,
    )

    max_recorded_at = _max_recorded_at(
        decisions,
        ledger.events(),
        mission_entries,
        capability_entries,
        findings,
        releases,
        voice,
    )
    stale = _is_stale(generated_at, max_recorded_at, now is not None)
    degraded = bool(
        control["stop_active"]
        or control["paused_projects"]
        or any(
            capability["status"] in {"DEGRADED", "BLOCKED", "REVOKED", "SUSPENDED"}
            for capability in capability_entries
        )
        or any(
            mission["status"] in {"BLOCKED", "FAILED", "ROLLED_BACK"} for mission in mission_entries
        )
    )
    provider_failure = bool(unavailable or incident_entries)
    empty = not (
        goals
        or projects
        or mission_entries
        or decisions
        or capability_entries
        or findings
        or releases
    )
    data_quality = {
        "empty": empty,
        "stale": stale,
        "degraded": degraded,
        "provider_failure": provider_failure,
        "freshness_window_seconds": FRESHNESS_WINDOW_SECONDS,
        "detail": {
            "unavailable_capabilities": len(unavailable),
            "incidents": len(incident_entries),
            "blocked_or_waiting": len(waiting),
        },
    }

    return {
        "schema": "jarvis-r10-command-center-state-v1",
        "generated_at": generated_at,
        "source_of_truth": {
            "kind": "isolated_proving_stores",
            "portfolio_store": str(layout.portfolio),
            "mission_ledger": str(layout.mission_ledger),
            "directive_ledger": str(layout.directive_ledger),
            "capability_registry": str(layout.capabilities),
            "findings_store": str(layout.findings),
            "policy": "UI and voice render and submit only; durable stores are authority",
        },
        "control": control,
        "goals": sections["goals"],
        "projects": sections["projects"],
        "missions": sections["missions"],
        "candidate_decisions": sections["candidate_decisions"],
        "rejected_alternatives": sections["rejected_alternatives"],
        "workers": sections["workers"],
        "verification": sections["verification"],
        "findings": sections["findings"],
        "releases": sections["releases"],
        "capabilities": sections["capabilities"],
        "costs": costs,
        "resources": sections["resources"],
        "evidence": {
            "count": len(evidence_index),
            "records": [
                {
                    "record_id": entry["record_id"],
                    "kind": entry["kind"],
                    "json_hash": entry["json_hash"],
                }
                for entry in evidence_index.values()
            ],
        },
        "health": {
            "supervisor_status": "ISOLATED_PROVING",
            "degraded": degraded,
            "stop_active": control["stop_active"],
            "paused_projects": control["paused_projects"],
            "stale": stale,
            "provider_failure": provider_failure,
            "live_workers": 0,
            "durable_worker_records": len(workers),
            "capabilities_total": len(capability_entries),
            "capabilities_not_ready": sum(
                1
                for capability in capability_entries
                if capability["status"] not in {"ACTIVE", "READY"}
            ),
        },
        "incidents": sections["incidents"],
        "waiting_blockers": sections["waiting_blockers"],
        "next_actions": sections["next_actions"],
        "timeline": timeline,
        "voice": voice,
        "data_quality": data_quality,
        "evidence_index": evidence_index,
    }


def _max_recorded_at(*groups: Any) -> str:
    timestamps: list[str] = []
    for group in groups:
        if isinstance(group, list):
            for item in group:
                if isinstance(item, dict):
                    for key in ("recorded_at", "created_at", "verified_at", "probe_utc"):
                        value = item.get(key)
                        if isinstance(value, str) and value:
                            timestamps.append(value)
    if not timestamps:
        return ""
    return max(timestamps)


def _is_stale(generated_at: str, max_recorded_at: str, has_now: bool) -> bool:
    if not has_now or not max_recorded_at:
        return False
    from datetime import datetime

    try:
        generated = datetime.fromisoformat(generated_at)
        latest = datetime.fromisoformat(max_recorded_at)
    except ValueError:
        return False
    delta = (generated - latest).total_seconds()
    return delta > FRESHNESS_WINDOW_SECONDS


def _timeline(
    *,
    decisions: list[dict[str, Any]],
    directive_events: list[Any],
    mission_entries: list[dict[str, Any]],
    capability_entries: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    releases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions):
        events.append(
            {
                "sequence": index + 1,
                "event_id": f"decision-{decision['decision_id']}",
                "event_type": "portfolio.decision",
                "recorded_at": decision.get("created_at", ""),
                "summary": decision.get("reason", "portfolio decision"),
                "decision": decision.get("selected_candidate_id"),
                "effect": decision.get("status"),
                "evidence_ref": {
                    "record_id": decision["decision_id"],
                    "kind": "candidate_decisions",
                },
            }
        )
    for index, event in enumerate(directive_events):
        events.append(
            {
                "sequence": 10_000 + index,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "recorded_at": event.recorded_at,
                "summary": event.payload.get("reason", event.event_type),
                "decision": event.payload.get("command_type"),
                "effect": "control state updated in PortfolioStore",
                "evidence_ref": {
                    "record_id": event.payload.get("directive_id", event.event_id),
                    "kind": "control",
                },
            }
        )
    for mission in mission_entries:
        for event in mission.get("ledger_events", []):
            if isinstance(event, dict):
                events.append(
                    {
                        "sequence": 20_000 + int(event.get("sequence", 0)),
                        "event_id": str(event.get("event_id", "")),
                        "event_type": str(event.get("event_type", "mission.event")),
                        "recorded_at": mission.get("recorded_at", ""),
                        "summary": mission.get("mission_id", ""),
                        "decision": mission.get("status"),
                        "effect": mission.get("recommended_next_action", ""),
                        "evidence_ref": {
                            "record_id": mission.get("mission_id", ""),
                            "kind": "missions",
                        },
                    }
                )
    for capability in capability_entries:
        for event in capability.get("timeline", []):
            if isinstance(event, dict):
                events.append(
                    {
                        "sequence": 30_000 + int(event.get("sequence", 0)),
                        "event_id": str(event.get("event_id", "")),
                        "event_type": str(event.get("event_type", "capability.event")),
                        "recorded_at": str(event.get("recorded_at", "")),
                        "summary": capability.get("name", ""),
                        "decision": capability.get("status"),
                        "effect": capability.get("reason", ""),
                        "evidence_ref": {
                            "record_id": capability.get("capability_id", ""),
                            "kind": "capabilities",
                        },
                    }
                )
    for index, finding in enumerate(findings):
        events.append(
            {
                "sequence": 40_000 + index,
                "event_id": f"finding-{finding.get('id', index)}",
                "event_type": "finding.recorded",
                "recorded_at": finding.get("recorded_at", "2026-08-16T04:30:00Z"),
                "summary": str(finding.get("finding", ""))[:120],
                "decision": finding.get("status"),
                "effect": finding.get("disposition"),
                "evidence_ref": {"record_id": finding.get("id", ""), "kind": "findings"},
            }
        )
    for release in releases:
        events.append(
            {
                "sequence": 50_000,
                "event_id": f"release-{release.get('stage', '')}",
                "event_type": "release.stage",
                "recorded_at": release.get("recorded_at", ""),
                "summary": f"{release.get('stage', '')} {release.get('status', '')}",
                "decision": release.get("commit", ""),
                "effect": release.get("verdict", ""),
                "evidence_ref": {"record_id": release.get("stage", ""), "kind": "releases"},
            }
        )
    for event in events:
        event["json_hash"] = _json_hash(
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "summary": event["summary"],
                "decision": event["decision"],
            }
        )
    events.sort(key=lambda item: (item.get("recorded_at", ""), item.get("sequence", 0)))
    return events


def record_lookup(snapshot: dict[str, Any], record_id: str) -> dict[str, Any] | None:
    entry = snapshot.get("evidence_index", {}).get(record_id)
    if entry is None:
        return None
    return dict(entry)


def evidence_for_record(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": entry["record_id"],
        "kind": entry["kind"],
        "payload": entry["payload"],
        "json_hash": entry["json_hash"],
        "evidence_refs": entry.get("evidence_refs", []),
        "provenance": entry.get("provenance", []),
        "policy": "evidence records identities, hashes, timestamps, provenance, and non-sensitive excerpts only",
    }
