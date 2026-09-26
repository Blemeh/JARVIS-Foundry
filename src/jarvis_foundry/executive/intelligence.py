"""Lane A executive-intelligence orchestration over existing authorities.

This is not a second Executive. It assembles owner/project context, feeds
registered projects into the existing observation/candidate path, lets
``ExecutiveDecisionService`` choose, records decision truth, measures
outcomes, and schedules the next wake through the existing Continuous
Executive wake authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..clock import isoformat
from ..control.broker import emergency_stop_reason
from ..database import FoundryDatabase
from ..events import Event, EventConflict, canonical_json
from ..platform.windows_subprocess import headless_run_kwargs
from ..v3_fault import invoke_fault_hook
from .calibration import (
    merge_predicted_vs_actual,
    outcome_from_mission,
    record_calibration,
    record_outcome_measurement,
    rebuild_calibration,
)
from .decisions import (
    ExecutiveDecision,
    ExecutiveDecisionService,
    decision_truth_payload,
    diagnose_wait_or_stall,
)
from .goals import active_goals
from .predictions import PREDICTION_VERSION
from .standing_intent import (
    looks_like_new_project_opportunity,
    standing_intent_payload,
)


def _utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        **headless_run_kwargs(),
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


_GITHUB_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


def _github_repository_from_remote(remote: str) -> str | None:
    """Return a sanitized owner/repository pair for a GitHub origin URL."""
    value = str(remote or "").strip()
    if not value:
        return None

    scp = re.fullmatch(
        r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?",
        value,
        flags=re.IGNORECASE,
    )
    if scp:
        owner, repository = scp.groups()
    else:
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme.lower() not in {"https", "ssh"}
                or (parsed.hostname or "").lower() != "github.com"
                or parsed.port is not None
                or parsed.query
                or parsed.fragment
                or parsed.password
                or (parsed.scheme.lower() == "https" and parsed.username)
                or (parsed.scheme.lower() == "ssh" and parsed.username != "git")
            ):
                return None
        except ValueError:
            return None
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2:
            return None
        owner, repository = parts
        if repository.lower().endswith(".git"):
            repository = repository[:-4]

    if not all(_GITHUB_REPOSITORY_PART.fullmatch(part) for part in (owner, repository)):
        return None
    return f"{owner}/{repository}"


def _github_repository_for_root(root: Path) -> str | None:
    # Keep the remote URL in process only. Source identity and durable evidence
    # contain the sanitized GitHub owner/repository pair, never URL credentials.
    return _github_repository_from_remote(_git(root, "remote", "get-url", "origin"))


def _with_registered_github_sources(
    database: FoundryDatabase,
    sources: Sequence[Any],
) -> tuple[Any, ...]:
    """Attach one bounded GitHub watch to each registered project source."""
    from ..discovery_sources import SourceSpec

    resolved = tuple(sources)
    registered_ids = {
        str(row.get("resource_id") or "")
        for row in registered_project_rows(database)
        if row.get("resource_id")
    }
    seen_repositories: set[str] = set()
    seen_source_ids = {str(getattr(source, "source_id", "")) for source in resolved}
    for source in resolved:
        if str(getattr(source, "source_kind", "")) == "github":
            config = dict(getattr(source, "config", {}) or {})
            repository = str(config.get("repo") or source.source_identity).strip().rstrip("/")
            if repository.lower().endswith(".git"):
                repository = repository[:-4]
            if repository:
                seen_repositories.add(repository.casefold())

    additions: list[Any] = []
    for source in resolved:
        if str(getattr(source, "source_kind", "")) != "project":
            continue
        config = dict(getattr(source, "config", {}) or {})
        resource_id = str(config.get("resource_id") or config.get("project_id") or "")
        if not resource_id or resource_id not in registered_ids:
            continue
        root = Path(str(config.get("root") or source.source_identity or ""))
        if not root.is_dir():
            continue
        repository = _github_repository_for_root(root)
        repository_key = repository.casefold() if repository else ""
        source_id = f"github:{resource_id}"
        if (
            not repository
            or repository_key in seen_repositories
            or source_id in seen_source_ids
        ):
            continue
        project_id = str(
            config.get("project_id") or config.get("resource_id") or source.source_id
        )
        additions.append(SourceSpec(
            source_id=source_id,
            source_kind="github",
            source_identity=repository,
            poll_interval_s=300,
            config={
                "repo": repository,
                "target_project": project_id,
                "max_items": 10,
                "recent_days": 30,
            },
            enabled=True,
        ))
        seen_repositories.add(repository_key)
        seen_source_ids.add(source_id)
    return resolved + tuple(additions)


def registered_project_rows(database: FoundryDatabase) -> list[dict[str, Any]]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT resource_id, kind, root_path, resolved_root, "
            "baseline_json, readiness_state, updated_at FROM resources "
            "ORDER BY resource_id"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["baseline"] = json.loads(str(item.pop("baseline_json") or "{}"))
        except (TypeError, ValueError):
            item["baseline"] = {}
        out.append(item)
    return out


def assemble_project_inventory(database: FoundryDatabase) -> list[dict[str, Any]]:
    """Project understanding the Executive can actually consume."""
    inventory: list[dict[str, Any]] = []
    for row in registered_project_rows(database):
        root = Path(str(row.get("resolved_root") or row.get("root_path") or ""))
        baseline = dict(row.get("baseline") or {})
        purpose = ""
        readme = root / "README.md"
        pyproject = root / "pyproject.toml"
        if readme.is_file():
            purpose = readme.read_text(encoding="utf-8", errors="replace").splitlines()[:8]
            purpose = " ".join(line.strip() for line in purpose if line.strip())[:400]
        elif pyproject.is_file():
            purpose = pyproject.read_text(encoding="utf-8", errors="replace")[:400]
        head = _git(root, "rev-parse", "HEAD") if root.is_dir() else str(baseline.get("head") or "")
        branch = _git(root, "branch", "--show-current") if root.is_dir() else str(baseline.get("branch") or "")
        inventory.append({
            "resource_id": row.get("resource_id"),
            "kind": row.get("kind"),
            "root": str(root),
            "purpose": purpose or str(baseline.get("purpose") or ""),
            "branch": branch or "UNKNOWN",
            "head": head or str(baseline.get("head") or "UNKNOWN"),
            "readiness_state": row.get("readiness_state"),
            "health": "READY" if row.get("readiness_state") == "READY" else str(row.get("readiness_state") or "UNKNOWN"),
            "maintenance_status": "active" if root.is_dir() else "missing",
        })
    return inventory


def observation_sources_from_registered_projects(
    database: FoundryDatabase,
    *,
    required_capabilities: Sequence[str] = (),
    poll_interval_s: int = 1,
) -> tuple[Any, ...]:
    """Turn registered git resources into A2/A3 project observation sources."""
    from ..discovery_sources import SourceSpec
    specs: list[Any] = []
    for row in assemble_project_inventory(database):
        root = Path(str(row.get("root") or ""))
        if not root.is_dir():
            continue
        resource_id = str(row.get("resource_id") or root.name)
        project_id = resource_id
        specs.append(SourceSpec(
            source_id=f"project:{resource_id}",
            source_kind="project",
            source_identity=str(root),
            poll_interval_s=max(1, int(poll_interval_s)),
            config={
                "root": str(root),
                "project_id": project_id,
                "resource_id": resource_id,
                "max_files": 80,
                "max_dirs": 80,
                "required_capabilities": list(required_capabilities),
            },
            enabled=True,
        ))
    return _with_registered_github_sources(database, specs)


def project_roots_from_sources(sources: Sequence[Any]) -> dict[str, str]:
    roots: dict[str, str] = {}
    for spec in sources:
        if str(getattr(spec, "source_kind", "")) != "project":
            continue
        config = dict(spec.config or {})
        project_id = str(
            config.get("project_id") or config.get("resource_id") or spec.source_id
        )
        root = str(config.get("root") or spec.source_identity or "")
        if project_id and root:
            roots[project_id] = root
    return roots


def _with_default_world_context_source(sources: Sequence[Any]) -> tuple[Any, ...]:
    """Keep durable WorldModel context in the canonical A2 observation feed.

    Runtime source configuration may add or replace project/external inputs,
    but omitting a world source must not disconnect already-recorded owner and
    system observations from Executive discovery. Historical mission summaries
    and repeated-failure patterns stay contextual; they do not become fresh
    autonomous work through this default source.
    """
    resolved = tuple(sources)
    if any(str(getattr(source, "source_kind", "")) == "world" for source in resolved):
        return resolved
    from ..discovery_sources import SourceSpec

    return resolved + (SourceSpec(
        source_id="world-model",
        source_kind="world",
        source_identity="world-model",
        poll_interval_s=300,
        config={
            "include_unverified_signals": False,
            "include_stale_mission_context": False,
            "include_repeated_failures": False,
        },
        enabled=True,
    ),)


def prepare_executive_observation(
    database: FoundryDatabase,
    *,
    cycle_id: str,
) -> dict[str, Any]:
    """Assemble standing intent and project inventory on the product path."""
    try:
        from .autonomy import sync_owner_goals
        sync_owner_goals(database)
    except Exception:
        pass
    intents = record_standing_intents(database)
    inventory = assemble_project_inventory(database)
    owner_context: dict[str, Any] = {}
    try:
        from .owner_context import load_owner_context
        owner_context = load_owner_context(database)
    except Exception:
        owner_context = {}
    _append(
        database,
        "executive.context_assembled",
        {
            "cycle_id": cycle_id,
            "standing_intents": intents,
            "projects": [
                {
                    "resource_id": row.get("resource_id"),
                    "purpose": row.get("purpose"),
                    "branch": row.get("branch"),
                    "head": row.get("head"),
                    "health": row.get("health"),
                }
                for row in inventory
            ],
            "owner_context_authority": owner_context.get("authority"),
            "owner_context_goal_count": len(owner_context.get("goals") or ()),
            "owner_context_project_count": len(owner_context.get("projects") or ()),
        },
        key=f"executive.context_assembled:{cycle_id}",
    )
    return {
        "standing_intents": intents,
        "inventory": inventory,
        "owner_context": owner_context,
    }


def judge_new_project_opportunity(
    database: FoundryDatabase,
    decision: ExecutiveDecision | None,
) -> dict[str, Any]:
    judgment: dict[str, Any] = {"judgment": "NONE"}
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT candidate_id, objective, candidate_type FROM candidates "
            "ORDER BY rowid DESC LIMIT 20"
        ).fetchall()
    for row in rows:
        objective = str(row["objective"] or "")
        if looks_like_new_project_opportunity(
            objective, candidate_type=str(row["candidate_type"] or ""),
        ):
            selected = (
                decision is not None
                and decision.candidate_id == str(row["candidate_id"])
                and decision.kind == "SELECT"
            )
            judgment = {
                "candidate_id": str(row["candidate_id"]),
                "judgment": "BUILD" if selected else "REJECT / DEFER",
                "reason": (
                    decision.reason if selected and decision is not None else
                    "speculative new-project work lacks evidence of owner value"
                ),
            }
            break
    _append(
        database,
        "executive.new_project_judgment_recorded",
        {
            "decision_id": None if decision is None else decision.decision_id,
            **judgment,
        },
        key=f"executive.new_project_judgment_recorded:{judgment.get('candidate_id') or 'none'}",
    )
    return judgment


def record_product_wait_diagnosis(
    database: FoundryDatabase,
    *,
    cycle_id: str,
    decision: ExecutiveDecision | None = None,
    admission: Any = None,
    reason: str = "",
) -> dict[str, Any]:
    """Record WAIT vs STALL from the worker/Executive continuation seam."""
    admission_mission = getattr(admission, "mission_id", None) if admission is not None else None
    admission_status = str(getattr(admission, "status", "") or "")
    admission_reason = str(getattr(admission, "reason", "") or reason)
    if (
        decision is not None
        and decision.kind == "SELECT"
        and not admission_mission
    ):
        lowered = f"{admission_status} {admission_reason}".lower()
        if any(token in lowered for token in ("wip", "budget", "stop", "veto", "pause")):
            diagnosis = {
                "classification": "JUSTIFIED_WAIT",
                "reason": admission_reason or decision.reason,
            }
        else:
            diagnosis = {
                "classification": "STALL",
                "reason": (
                    "useful executable work was selected but continuation "
                    f"did not produce a mission: {admission_reason or decision.reason}"
                ),
            }
    elif decision is not None:
        diagnosis = diagnose_wait_or_stall(decision, rejected=decision.rejected)
    else:
        diagnosis = {
            "classification": "JUSTIFIED_WAIT",
            "reason": reason or "no eligible candidate",
        }
    _append(
        database,
        "executive.wait_diagnosis_recorded",
        {"cycle_id": cycle_id, "decision_id": None if decision is None else decision.decision_id, **diagnosis},
        key=f"executive.wait_diagnosis_recorded:{cycle_id}",
    )
    return diagnosis


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    source_id: str = "executive-intelligence",
) -> str:
    event = Event.create(
        event_type,
        dict(payload),
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=key,
    )
    try:
        return str(database.append(event)[0])
    except EventConflict:
        return ""


def record_standing_intents(database: FoundryDatabase) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = []
    for goal in active_goals(database):
        payload = standing_intent_payload(goal.objective, directive_id=goal.goal_id)
        _append(
            database,
            "executive.standing_intent_recorded",
            payload,
            key=(
                "executive.standing_intent_recorded:"
                f"{goal.goal_id}:{payload['kind']}"
            ),
        )
        intents.append(payload)
    return intents


def recognize_research_gap(decision: ExecutiveDecision) -> bool:
    if decision.kind != "SELECT" or decision.score is None:
        reason = str(decision.reason or "").lower()
        return "evidence" in reason or "confidence" in reason or "research" in reason
    score = decision.score
    if score.composite < 0.55:
        return True
    unknown = [factor for factor in score.factors if factor.kind == "UNKNOWN"]
    return len(unknown) >= 2


def apply_research_findings(
    database: FoundryDatabase,
    *,
    candidate_id: str,
    cycle_id: str,
    stance: str,
    summary: str,
    provenance: str,
    contradicting: bool = False,
    supporting: bool = False,
    conflicting: bool = False,
    confidence: float = 0.7,
) -> dict[str, Any]:
    """Record DATA_ONLY research findings on the canonical ResearchQueue.

    Goal A owns the decision that research is needed and how findings change
    later scoring. Goal B owns browser/action execution; this helper never
    calls browser APIs.
    """
    from ..research_queue import (
        complete_candidate_research,
        enqueue_candidate_research,
        start_candidate_research,
    )

    material = {
        "candidate_id": candidate_id,
        "stance": stance,
        "summary": summary,
        "provenance": provenance,
    }
    content_hash = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    enqueued = enqueue_candidate_research(
        database,
        candidate_id=candidate_id,
        cycle_id=cycle_id,
        pass_number=1,
        budget_used=0.25,
        source_ref=provenance,
        evidence={
            "authority": "DATA_ONLY",
            "stance": stance,
            "summary": summary,
            "provenance": provenance,
            "contradicting": contradicting,
            "supporting": supporting,
            "conflicting": conflicting,
            "confidence": confidence,
        },
    )
    research_id = str(enqueued.get("research_id") or "")
    if not research_id or str(enqueued.get("status") or "") in {
        "PASS_LIMIT", "BUDGET_BLOCKED",
    }:
        return enqueued
    start_candidate_research(database, research_id=research_id)
    return complete_candidate_research(
        database,
        research_id=research_id,
        status="COMPLETED",
        evidence={
            "authority": "DATA_ONLY",
            "stance": stance,
            "summary": summary,
            "provenance": provenance,
            "contradicting": contradicting,
            "supporting": supporting,
            "conflicting": conflicting,
            "confidence": confidence,
        },
        content_hash=content_hash,
        source_ref=provenance,
    )


def measure_outcome(
    database: FoundryDatabase,
    *,
    decision_id: str,
    candidate_id: str,
    mission_id: str,
    value_predicted: float | None,
    value_realized: float | None,
    effort_predicted: float | None = None,
    effort_actual: float | None = None,
    risk_predicted: str | None = None,
    risk_actual: str | None = None,
    outcome: str = "PASSED",
    source: str = "initiative_observation",
    objective: str = "",
) -> dict[str, Any]:
    actual = {
        "value_predicted": value_predicted,
        "value_realized": value_realized,
        "effort_predicted": effort_predicted,
        "effort_actual": effort_actual,
        "risk_predicted": risk_predicted,
        "risk_actual": risk_actual,
        "prediction_version": PREDICTION_VERSION,
        "measured_at": isoformat(),
        "outcome": outcome,
    }
    if mission_id:
        try:
            derived = outcome_from_mission(database, mission_id)
            for key, value in derived.items():
                if value is not None and actual.get(key) is None:
                    actual[key] = value
        except Exception:
            pass
    actual = merge_predicted_vs_actual(
        database, actual, decision_id=decision_id, candidate_id=candidate_id,
    )
    record_calibration(
        database,
        decision_id=decision_id,
        candidate_id=candidate_id,
        mission_id=mission_id or f"unexecuted:{candidate_id}",
        source=source,
        objective=objective,
        outcome=outcome,
        actual=actual,
        source_id="executive-intelligence",
    )
    record_outcome_measurement(
        database,
        decision_id=decision_id,
        candidate_id=candidate_id,
        mission_id=mission_id,
        actual=actual,
        outcome=outcome,
        source_id="executive-intelligence",
    )
    return actual


def schedule_next_action_after_outcome(
    database: FoundryDatabase,
    *,
    mission_id: str,
    outcome: str,
    reason: str = "",
) -> dict[str, Any]:
    """Create an existing-authority wake so PASS is not silence."""
    from .continuous import WakeCondition, _objective_for_mission, create_wake

    now = _utc()
    wake_id = f"next-action:{mission_id}"
    kind = "WAIT_UNTIL_TIME"
    wait_reason = reason or (
        f"mission {mission_id} {outcome}; select next valuable action"
    )
    # Bind the wake to the mission's real Executive objective when one owns it.
    # A synthesized ``post-outcome:{mission_id}`` objective advertised
    # continuation that the Executive could never actually select, so the
    # wake looked scheduled while no next cycle could consume it.
    wake_objective_id = _objective_for_mission(database, mission_id)
    wake = WakeCondition(
        wake_id=wake_id,
        objective_id=wake_objective_id,
        kind=kind,
        reason=wait_reason,
        created_at=isoformat(now),
        not_before=isoformat(now),
    )
    create_wake(database, wake, controller="supervisor")
    _append(
        database,
        "executive.next_action_scheduled",
        {
            "mission_id": mission_id,
            "outcome": outcome,
            "wake_id": wake_id,
            "objective_id": wake_objective_id,
            "objective_bound": not wake_objective_id.startswith("mission:"),
            "reason": wait_reason,
        },
        key=f"executive.next_action_scheduled:{mission_id}",
    )
    return {
        "wake_id": wake_id,
        "objective_id": wake_objective_id,
        "reason": wait_reason,
        "kind": kind,
    }


@dataclass(frozen=True, slots=True)
class IntelligenceCycle:
    cycle_id: str
    standing_intents: tuple[dict[str, Any], ...]
    inventory: tuple[dict[str, Any], ...]
    sources: tuple[str, ...]
    observation: dict[str, Any]
    decision: ExecutiveDecision | None
    decision_truth: dict[str, Any]
    wait_diagnosis: dict[str, Any]
    research: dict[str, Any] = field(default_factory=dict)
    new_project_judgment: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        decision = self.decision
        return {
            "cycle_id": self.cycle_id,
            "standing_intents": list(self.standing_intents),
            "inventory": list(self.inventory),
            "sources": list(self.sources),
            "observation": self.observation,
            "decision": None if decision is None else {
                "kind": decision.kind,
                "decision_id": decision.decision_id,
                "reason": decision.reason,
                "candidate_id": decision.candidate_id,
                "rejected": list(decision.rejected),
                "deferred": list(decision.deferred),
            },
            "decision_truth": self.decision_truth,
            "wait_diagnosis": self.wait_diagnosis,
            "research": self.research,
            "new_project_judgment": self.new_project_judgment,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryAdmission:
    """One canonical discovery -> SELECT -> admission pass.

    This is the product seam between the Executive's observed candidates and
    the runtime's executable work.  It is not a selector: it reuses the
    existing A2 observation driver, the existing ExecutiveDecisionService, and
    the existing admission authority, and it reports what actually happened so
    the caller can render an honest ACTION / JUSTIFIED_WAIT / STALL.
    """

    status: str
    reason: str
    mission_id: str | None = None
    decision_id: str | None = None
    admission_status: str | None = None
    observed: bool = False


def discover_and_admit_next_work(
    database: FoundryDatabase,
    *,
    cycle_id: str,
    resource_id: str,
    capability: str = "write_registered_worktree_files",
    sources: Sequence[Any] = (),
    required_capabilities: Sequence[str] = (),
    stop_path: str | Path | None = None,
    policy: Any | None = None,
    project_roots: Mapping[str, Any] | None = None,
    worktree_parent: str | Path | None = None,
    bounds: Mapping[str, Any] | None = None,
    fault_hook: Callable[[str], None] | None = None,
    research_wake_consumed: int = 0,
) -> DiscoveryAdmission:
    """Observe registered projects and admit the Executive's selected work.

    Returns MISSION when an admitted objective produced a dispatchable mission,
    CONTINUATION when an already-admitted objective continued, JUSTIFIED_WAIT
    when no worthwhile work exists, and STALL when useful work was selected but
    could not become a mission.

    ``research_wake_consumed`` is forwarded to the A2 observation cycle: a
    research-completion wake this Executive cycle consumed is itself the
    meaningful change, so the cycle must reach a fresh evaluation rather than
    short-circuiting on an unchanged source digest.
    """
    from ..initiative_a2 import InitiativeA2Bounds, run_continuous_observation_cycle
    from ..initiative_a3 import (
        A3AdmissionPolicy,
        InitiativeAdmissionAuthority,
        active_initiative_objective,
        continue_initiative_objective,
        pending_initiative_decision,
    )

    if policy is None:
        policy = A3AdmissionPolicy()
    bounds_raw = dict(bounds or {})
    a2_bounds = InitiativeA2Bounds(**{
        name: value for name, value in bounds_raw.items()
        if name in InitiativeA2Bounds.__dataclass_fields__
    })
    prepare_executive_observation(database, cycle_id=cycle_id)
    configured_sources = tuple(sources) or observation_sources_from_registered_projects(
        database,
        required_capabilities=tuple(required_capabilities),
    )
    resolved_sources = _with_default_world_context_source(
        _with_registered_github_sources(database, configured_sources)
    )
    if not resolved_sources:
        reason = "V11 A3 has no registered project sources"
        record_product_wait_diagnosis(database, cycle_id=cycle_id, reason=reason)
        judge_new_project_opportunity(database, None)
        return DiscoveryAdmission("JUSTIFIED_WAIT", reason)

    roots = dict(project_roots or {}) or project_roots_from_sources(resolved_sources)
    authority = InitiativeAdmissionAuthority(
        database,
        worktree_parent=str(worktree_parent or ""),
        project_roots=roots,
        policy=policy,
        stop_path=stop_path,
    )
    invoked = (lambda stage: invoke_fault_hook(fault_hook, stage)) if fault_hook else (lambda stage: None)
    decision = None
    admission = None
    active_objective = active_initiative_objective(database)
    # A research-completion wake released this cycle is new durable evidence
    # about an ALREADY admitted objective's candidate.  Continuing that objective
    # blindly would ignore the findings, so the A2 evaluation runs first and the
    # continuation only happens when the fresh evaluation still selects it.
    if active_objective is not None and int(research_wake_consumed or 0) > 0:
        report = run_continuous_observation_cycle(
            database,
            sources=resolved_sources,
            resource_id=resource_id,
            capability=capability,
            bounds=a2_bounds,
            stop_path=stop_path,
            fault_hook=fault_hook,
            research_wake_consumed=int(research_wake_consumed or 0),
            research_trigger_id=cycle_id,
        )
        decision = report.get("decision")
        invoked("post research-wake re-evaluation")
        if getattr(decision, "kind", None) == "SELECT":
            from .autonomy import rebuild_executive_state, set_objective_status

            state = rebuild_executive_state(database)
            current = state.objectives.get(active_objective)
            current_candidate_id = str(
                (current.provenance if current is not None else {}).get("candidate_id")
                or ""
            )
            selected_candidate_id = str(getattr(decision, "candidate_id", "") or "")
            if selected_candidate_id == current_candidate_id:
                admission = continue_initiative_objective(
                    database,
                    active_objective,
                    cycle_id=f"initiative-executive-{cycle_id}",
                )
            else:
                set_objective_status(
                    database,
                    active_objective,
                    "SUSPENDED",
                    reason=(
                        "fresh research decision selected a different candidate "
                        f"({selected_candidate_id})"
                    ),
                    evidence_refs=(str(getattr(decision, "decision_id", "") or ""),),
                )
                admission = authority.admit(
                    decision,
                    replacing_objective_id=active_objective,
                )
                if admission.admitted and admission.objective_id:
                    admission = continue_initiative_objective(
                        database,
                        admission.objective_id,
                        cycle_id=f"initiative-executive-{cycle_id}",
                    )
        else:
            reason = f"V11 A2 WAIT after research: {report.get('reason') or 'no eligible candidate'}"
            from .autonomy import set_objective_status

            set_objective_status(
                database,
                active_objective,
                "SUSPENDED",
                reason=reason,
                evidence_refs=(str(getattr(decision, "decision_id", "") or ""),),
            )
            record_product_wait_diagnosis(
                database, cycle_id=cycle_id,
                decision=decision if hasattr(decision, "kind") else None,
                reason=reason,
            )
            judge_new_project_opportunity(
                database, decision if hasattr(decision, "kind") else None,
            )
            return DiscoveryAdmission(
                "JUSTIFIED_WAIT", reason,
                decision_id=getattr(decision, "decision_id", None),
                observed=True,
            )
    elif active_objective is not None:
        admission = continue_initiative_objective(
            database, active_objective, cycle_id=f"initiative-executive-{cycle_id}",
        )
    else:
        decision = pending_initiative_decision(database)
        if decision is None:
            report = run_continuous_observation_cycle(
                database,
                sources=resolved_sources,
                resource_id=resource_id,
                capability=capability,
                bounds=a2_bounds,
                stop_path=stop_path,
                fault_hook=fault_hook,
                research_wake_consumed=int(research_wake_consumed or 0),
            )
            decision = report.get("decision")
            if getattr(decision, "kind", None) != "SELECT":
                reason = f"V11 A2 WAIT: {report.get('reason') or 'no eligible candidate'}"
                record_product_wait_diagnosis(
                    database, cycle_id=cycle_id,
                    decision=decision if hasattr(decision, "kind") else None,
                    reason=reason,
                )
                judge_new_project_opportunity(
                    database, decision if hasattr(decision, "kind") else None,
                )
                return DiscoveryAdmission(
                    "JUSTIFIED_WAIT", reason,
                    decision_id=getattr(decision, "decision_id", None),
                    observed=True,
                )
            invoked("post initiative.selection")
        admission = authority.admit(decision)
        if admission.admitted and admission.objective_id:
            invoked("post initiative.objective_created")
            admission = continue_initiative_objective(
                database, admission.objective_id,
                cycle_id=f"initiative-executive-{cycle_id}",
            )
    if admission is not None and admission.mission_id:
        invoked("post initiative.mission_created")
        return DiscoveryAdmission(
            "MISSION", admission.reason,
            mission_id=admission.mission_id,
            decision_id=getattr(decision, "decision_id", None),
            admission_status=admission.status,
        )
    status = admission.status if admission is not None else "WAIT"
    reason = f"V11 A3 {status}: {admission.reason if admission else 'no admission result'}"
    record_product_wait_diagnosis(
        database, cycle_id=cycle_id,
        decision=decision if hasattr(decision, "kind") else None,
        admission=admission, reason=reason,
    )
    judge_new_project_opportunity(
        database, decision if hasattr(decision, "kind") else None,
    )
    return DiscoveryAdmission(
        "STALL", reason,
        decision_id=getattr(decision, "decision_id", None),
        admission_status=status,
    )


def run_executive_intelligence_cycle(
    database: FoundryDatabase,
    *,
    cycle_id: str,
    resource_id: str,
    sources: Sequence[Any] = (),
    capability: str = "write_registered_worktree_files",
    required_capabilities: Sequence[str] = (),
    stop_path: str | Path | None = None,
    research_findings: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | str | None = None,
) -> IntelligenceCycle:
    """Observe registered projects and let the existing Executive decide.

    ``research_findings`` is an optional map of candidate_id → finding dict
    used when Goal B's research execution is stubbed. Findings still travel
    through the canonical ResearchQueue.
    """
    stopped = emergency_stop_reason(database, stop_path)
    if stopped:
        truth = {
            "why_chosen": f"STOP active: {stopped}",
            "alternatives_rejected": [],
            "learned": {},
            "next_action": "WAIT until STOP cleared",
            "wait_reason": stopped,
            "owner_attention_required": True,
        }
        return IntelligenceCycle(
            cycle_id=cycle_id,
            standing_intents=(),
            inventory=(),
            sources=(),
            observation={"outcome": "STOPPED", "reason": stopped},
            decision=None,
            decision_truth=truth,
            wait_diagnosis={"classification": "JUSTIFIED_WAIT", "reason": stopped},
        )

    intents = record_standing_intents(database)
    inventory = assemble_project_inventory(database)
    owner_context: dict[str, Any] = {}
    try:
        from .owner_context import load_owner_context
        owner_context = load_owner_context(database)
    except Exception:
        owner_context = {}
    _append(
        database,
        "executive.context_assembled",
        {
            "cycle_id": cycle_id,
            "standing_intents": intents,
            "projects": [
                {"resource_id": row.get("resource_id"), "purpose": row.get("purpose"),
                 "branch": row.get("branch"), "head": row.get("head"),
                 "health": row.get("health")}
                for row in inventory
            ],
            "owner_context_authority": owner_context.get("authority"),
            "owner_context_goal_count": len(owner_context.get("goals") or ()),
            "owner_context_project_count": len(owner_context.get("projects") or ()),
        },
        key=f"executive.context_assembled:{cycle_id}",
    )
    resolved_sources = tuple(sources) or observation_sources_from_registered_projects(
        database, required_capabilities=required_capabilities,
    )
    from ..initiative_a2 import InitiativeA2Bounds, run_initiative_a2_cycle

    def _noop_research_reader(_database, _candidate):
        return {
            "status": "UNKNOWN",
            "source_evidence": [],
            "unresolved_unknowns": [],
            "budget_used": 0.0,
        }

    bounds = InitiativeA2Bounds(do_nothing_score=0.28, candidate_limit=20)
    observation: dict[str, Any] = {"outcome": "SKIPPED", "reason": "no project sources"}
    if resolved_sources:
        observation = run_initiative_a2_cycle(
            database,
            cycle_id=f"{cycle_id}-observe",
            sources=list(resolved_sources),
            resource_id=resource_id,
            capability=capability,
            bounds=bounds,
            stop_path=stop_path,
            now=now,
            research_reader=_noop_research_reader,
        )

    research_report: dict[str, Any] = {"applied": []}
    findings = dict(research_findings or {})
    if findings:
        for candidate_id, finding in findings.items():
            applied = apply_research_findings(
                database,
                candidate_id=str(candidate_id),
                cycle_id=f"{cycle_id}-research",
                stance=str(finding.get("stance") or "CONTEXT"),
                summary=str(finding.get("summary") or ""),
                provenance=str(finding.get("provenance") or "research-finding"),
                contradicting=bool(finding.get("contradicting")),
                supporting=bool(finding.get("supporting")),
                conflicting=bool(finding.get("conflicting")),
                confidence=float(finding.get("confidence") or 0.7),
            )
            research_report["applied"].append(applied)

    observed_decision = observation.get("decision")
    service = ExecutiveDecisionService(database, min_action_score=0.32, wait_margin=0.04)
    if findings:
        decision = service.evaluate_a2(
            resource_id=resource_id,
            loop_id=cycle_id,
            capability=capability,
            governor=bounds.governor(),
        )
    elif isinstance(observed_decision, ExecutiveDecision):
        decision = observed_decision
    else:
        decision = service.evaluate_a2(
            resource_id=resource_id,
            loop_id=cycle_id,
            capability=capability,
            governor=bounds.governor(),
        )
    truth = decision_truth_payload(decision)
    if research_report["applied"]:
        truth["learned"] = {
            "research_findings_consumed": True,
            "count": len(research_report["applied"]),
        }
    _append(
        database,
        "executive.decision_truth_recorded",
        {"cycle_id": cycle_id, **truth},
        key=f"executive.decision_truth_recorded:{cycle_id}",
    )
    diagnosis = diagnose_wait_or_stall(decision, rejected=decision.rejected)
    _append(
        database,
        "executive.wait_diagnosis_recorded",
        {"cycle_id": cycle_id, **diagnosis},
        key=f"executive.wait_diagnosis_recorded:{cycle_id}",
    )
    new_project = judge_new_project_opportunity(database, decision)

    return IntelligenceCycle(
        cycle_id=cycle_id,
        standing_intents=tuple(intents),
        inventory=tuple(inventory),
        sources=tuple(spec.source_id for spec in resolved_sources),
        observation=observation,
        decision=decision,
        decision_truth=truth,
        wait_diagnosis=diagnosis,
        research=research_report,
        new_project_judgment=new_project,
    )
