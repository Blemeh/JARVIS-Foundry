"""V11 A2 inert initiative cycle.

This module is a narrow orchestration seam over existing JARVIS organs:
World Intelligence observations, discovery candidates/monitors, the canonical
research queue, Fabric readiness, and ``ExecutiveDecisionService``.  It ends
at ``ELIGIBLE / READY_FOR_ADMISSION`` and intentionally has no dependency on
objective, mission, effect, worktree, or provider-write APIs.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .clock import isoformat
from .connectors.github_research import GitHubResearchConnector
from .connectors.web_research import PlaywrightBrowserConnector, TrafilaturaExtractor
from .control.broker import emergency_stop_reason
from .database import FoundryDatabase
from .discovery_sources import SourceSpec, content_hash, run_source_observation_cycle
from .events import Event, canonical_json
from .executive.decisions import ExecutiveDecisionService, a2_precondition
from .executive.scoring import A2GovernorBounds
from .research_queue import (
    complete_candidate_research,
    enqueue_candidate_research,
    start_candidate_research,
)


ResearchReader = Callable[[FoundryDatabase, Mapping[str, Any]], dict[str, Any]]
FaultHook = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class InitiativeA2Bounds:
    """Machine-rate limits for one observation/judgment pass."""

    source_limit: int = 8
    candidate_limit: int = 20
    research_candidate_limit: int = 4
    research_passes_per_candidate: int = 1
    research_budget_per_candidate: float = 1.0
    research_budget_per_cycle: float = 4.0
    candidate_ttl_hours: float = 168.0
    research_near_threshold_margin: float = 0.20
    autonomous_wip_cap: int = 1
    do_nothing_score: float = 0.45

    def __post_init__(self) -> None:
        if not 1 <= int(self.source_limit) <= 16:
            raise ValueError("source_limit must be within [1, 16]")
        if not 1 <= int(self.candidate_limit) <= 100:
            raise ValueError("candidate_limit must be within [1, 100]")
        if not 0 <= int(self.research_candidate_limit) <= int(self.candidate_limit):
            raise ValueError("research_candidate_limit exceeds candidate bound")
        if int(self.research_passes_per_candidate) != 1:
            raise ValueError("V11 A2 allows one normal research pass per candidate")
        if int(self.autonomous_wip_cap) != 1:
            raise ValueError("V11 A2 autonomous WIP cap is fixed at 1")
        for name in (
            "research_budget_per_candidate", "research_budget_per_cycle",
            "candidate_ttl_hours", "research_near_threshold_margin",
            "do_nothing_score",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.research_budget_per_cycle < self.research_budget_per_candidate:
            raise ValueError("cycle research budget must cover one candidate pass")
        if not 0.0 <= self.do_nothing_score <= 1.0:
            raise ValueError("do_nothing_score must be within [0, 1]")

    def governor(self) -> A2GovernorBounds:
        return A2GovernorBounds(
            autonomous_wip_cap=self.autonomous_wip_cap,
            candidate_limit=self.candidate_limit,
            research_passes_per_candidate=self.research_passes_per_candidate,
            research_budget_per_candidate=self.research_budget_per_candidate,
            research_budget_per_cycle=self.research_budget_per_cycle,
            candidate_ttl_hours=self.candidate_ttl_hours,
            do_nothing_score=self.do_nothing_score,
        )


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _stop_reason(database: FoundryDatabase, stop_path: str | Path | None) -> str | None:
    return emergency_stop_reason(database, stop_path)


def _cycle_row(database: FoundryDatabase, cycle_id: str) -> dict[str, Any] | None:
    return database.row("initiative_cycles", "cycle_id", cycle_id)


def _append_stage(
    database: FoundryDatabase,
    cycle_id: str,
    stage: str,
    *,
    status: str = "RUNNING",
) -> None:
    database.append(Event.create(
        "initiative.cycle_stage_changed",
        {"cycle_id": cycle_id, "stage": stage, "status": status},
        source_type="supervisor",
        source_id="v11-a2-initiative",
        idempotency_key=f"initiative.cycle_stage_changed:{cycle_id}:{stage}",
    ))


def _candidate_rows(database: FoundryDatabase, limit: int) -> list[dict[str, Any]]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT * FROM candidates WHERE source = 'initiative_observation' "
            "AND status IN ('NEW', 'SCORED', 'ELIGIBLE') "
            "ORDER BY rowid DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def _research_source(database: FoundryDatabase, candidate_id: str) -> dict[str, Any] | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT cs.source_kind, cs.source_identity, "
            "COALESCE(cs.source_ref, wo.source_ref) AS source_ref, "
            "wo.payload_json, wo.provenance_json "
            "FROM candidate_sources cs "
            "LEFT JOIN world_observations wo "
            "ON wo.observation_id = cs.observation_id "
            "WHERE cs.candidate_id = ? ORDER BY cs.rowid LIMIT 1",
            (candidate_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    for key in ("payload_json", "provenance_json"):
        try:
            result[key.removesuffix("_json")] = json.loads(str(result.pop(key) or "{}"))
        except (TypeError, ValueError):
            result[key.removesuffix("_json")] = {}
    return result


def default_research_reader(
    database: FoundryDatabase,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Perform one read-only, source-bounded research pass."""
    candidate_id = str(candidate.get("candidate_id") or "")
    source = _research_source(database, candidate_id)
    if source is None:
        return {"status": "UNKNOWN", "evidence": {"authority": "DATA_ONLY", "reason": "source lineage unavailable"}}
    kind = str(source.get("source_kind") or "")
    payload = dict(source.get("payload") or {})
    source_ref = str(source.get("source_ref") or source.get("source_identity") or "")
    evidence: dict[str, Any] = {
        "authority": "DATA_ONLY" if kind != "world" else "SYSTEM_OBSERVATION",
        "source_kind": kind,
        "source_ref": source_ref,
    }
    status = "UNKNOWN"
    if kind == "github":
        repo = str(payload.get("repo") or "")
        if payload.get("kind") == "workflow_run" and repo and payload.get("run_id"):
            endpoint = f"/repos/{repo}/actions/runs/{payload['run_id']}"
            attempt, document = GitHubResearchConnector().read_api("github.check.read", endpoint)
        elif payload.get("kind") == "issue" and repo and payload.get("number"):
            endpoint = f"/repos/{repo}/issues/{payload['number']}"
            attempt, document = GitHubResearchConnector().read_api("github.issue.read", endpoint)
        else:
            endpoint = f"/repos/{repo}" if repo else ""
            if not endpoint:
                return {"status": "UNKNOWN", "evidence": {**evidence, "reason": "invalid GitHub lineage"}, "source_ref": source_ref}
            attempt, document = GitHubResearchConnector().read_api("github.repo.read", endpoint)
        status = "COMPLETED" if attempt.outcome == "COMPLETED" and document is not None else "BLOCKED"
        evidence.update({
            "provider_ref": attempt.provider_ref,
            "response_hash": (attempt.after_state or {}).get("response_hash"),
            "outcome": attempt.outcome,
            "error_code": attempt.error_code,
            "summary": {
                key: document.get(key) for key in (
                    "id", "number", "name", "status", "conclusion", "state",
                    "updated_at", "head_sha", "html_url",
                ) if isinstance(document, dict) and key in document
            },
        })
    elif kind == "web":
        parsed = urlsplit(source_ref)
        if parsed.scheme != "https" or not parsed.hostname:
            return {"status": "BLOCKED", "evidence": {**evidence, "reason": "invalid web lineage"}, "source_ref": source_ref}
        browser = PlaywrightBrowserConnector(allowed_hosts=(parsed.hostname,))
        capture = browser.read(source_ref, allowed_hosts=(parsed.hostname,))
        extraction = TrafilaturaExtractor().extract(capture)
        status = "COMPLETED" if extraction.success and extraction.page is not None else "BLOCKED"
        evidence.update({
            "source_sha256": getattr(extraction.page, "source_sha256", None),
            "safe_text_sha256": extraction.safe_text_sha256,
            "outcome": extraction.attempt.outcome,
            "error_code": extraction.attempt.error_code,
            "title": str(extraction.metadata.get("title") or "")[:240],
        })
    elif kind == "project":
        root = Path(source_ref.split("#", 1)[0])
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "diff", "--stat", "--no-ext-diff"],
                capture_output=True, check=False, timeout=15,
            )
            status = "COMPLETED" if result.returncode == 0 else "BLOCKED"
            evidence.update({
                "git_returncode": result.returncode,
                "diff_stat_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "bytes": len(result.stdout),
            })
        except (OSError, subprocess.TimeoutExpired) as error:
            status = "BLOCKED"
            evidence["error"] = type(error).__name__
    elif kind == "world":
        status = "COMPLETED"
        evidence["observation_hash"] = _hash(payload)
    else:
        evidence["reason"] = f"research unsupported for source kind {kind or 'unknown'}"
    return {
        "status": status,
        "evidence": evidence,
        "source_ref": source_ref,
        "content_hash": content_hash(evidence)[7:],
        "budget_used": 1.0,
    }


def _run_research(
    database: FoundryDatabase,
    *,
    cycle_id: str,
    rows: Sequence[dict[str, Any]],
    bounds: InitiativeA2Bounds,
    research_reader: ResearchReader,
    stop_path: str | Path | None,
    resource_id: str,
) -> list[dict[str, Any]]:
    threshold = max(0.0, bounds.do_nothing_score - bounds.research_near_threshold_margin)
    preview = ExecutiveDecisionService(
        database, source_id="v11-a2-judgment",
    ).preview_a2(
        resource_id=resource_id,
        governor=bounds.governor(),
        candidate_limit=bounds.candidate_limit,
        resource_contention=0.0,
    )
    by_id = {str(row["candidate_id"]): row for row in rows}
    results: list[dict[str, Any]] = []
    remaining_budget = bounds.research_budget_per_cycle
    for score in preview:
        if len(results) >= bounds.research_candidate_limit:
            break
        if _stop_reason(database, stop_path):
            break
        if score.composite < threshold:
            continue
        # Research is evidence gathering, but it still must not bypass the
        # canonical Fabric/dependency/policy preconditions.  A candidate that
        # cannot currently survive those gates consumes no connector budget.
        if a2_precondition(score) is not None:
            continue
        row = by_id.get(score.candidate_id)
        if row is None:
            continue
        planned = min(bounds.research_budget_per_candidate, remaining_budget)
        if planned <= 0.0:
            break
        queued = enqueue_candidate_research(
            database,
            candidate_id=str(row["candidate_id"]),
            cycle_id=cycle_id,
            pass_number=1,
            budget_used=planned,
            per_candidate_budget=bounds.research_budget_per_candidate,
            cycle_budget=bounds.research_budget_per_cycle,
            max_normal_passes=bounds.research_passes_per_candidate,
            evidence={"preliminary_score": score.composite, "authority": "DATA_ONLY"},
        )
        if queued.get("status") != "ENQUEUED":
            continue
        if _stop_reason(database, stop_path):
            break
        started = start_candidate_research(
            database, research_id=str(queued["research_id"]),
        )
        if started.get("status") != "IN_PROGRESS":
            continue
        receipt = research_reader(database, row)
        used = min(planned, max(0.0, float(receipt.get("budget_used", planned))))
        completed = complete_candidate_research(
            database,
            research_id=str(queued["research_id"]),
            status=str(receipt.get("status") or "UNKNOWN"),
            evidence=dict(receipt.get("evidence") or {}),
            budget_used=used,
            source_ref=receipt.get("source_ref"),
            content_hash=receipt.get("content_hash"),
        )
        results.append(completed)
        remaining_budget -= used
    return results


def _event_delta_counts(database: FoundryDatabase, start_sequence: int) -> dict[str, int]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT event_type, COUNT(*) AS count FROM events "
            "WHERE sequence > ? GROUP BY event_type",
            (start_sequence,),
        ).fetchall()
    counts = {str(row[0]): int(row[1]) for row in rows}
    return {
        "objective_creations": counts.get("executive.objective_created", 0),
        "mission_creations": counts.get("mission.created", 0),
        "mutating_effects": sum(value for key, value in counts.items() if key.startswith("effect.") or key.startswith("system.action_")),
        "external_writes": sum(value for key, value in counts.items() if key.startswith("provider.write") or key.endswith(".write_completed")),
    }


def _judgment_metrics(database: FoundryDatabase, decision_id: str) -> dict[str, Any]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT outcome, reason_code, candidate_id, score FROM candidate_judgments "
            "WHERE decision_id = ? ORDER BY score DESC, candidate_id",
            (decision_id,),
        ).fetchall()
    distribution: dict[str, int] = {}
    for row in rows:
        if str(row[0]) == "REJECTED":
            code = str(row[1])
            distribution[code] = distribution.get(code, 0) + 1
    survivors = [row for row in rows if str(row[0]) == "ELIGIBLE"]
    return {
        "candidates_rejected": sum(str(row[0]) == "REJECTED" for row in rows),
        "candidates_deferred": sum(str(row[0]) in {"DEFERRED", "WAIT"} for row in rows),
        "candidates_eligible": len(survivors),
        "rejection_reason_distribution": distribution,
        "top_ranked_survivor": str(survivors[0][2]) if survivors else None,
    }


def run_initiative_a2_cycle(
    database: FoundryDatabase,
    *,
    cycle_id: str,
    sources: Sequence[SourceSpec],
    resource_id: str = "v11-a2-observation",
    capability: str = "cap.file.read",
    bounds: InitiativeA2Bounds | None = None,
    stop_path: str | Path | None = None,
    research_reader: ResearchReader = default_research_reader,
    fault_hook: FaultHook | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Run one replay-safe A2 pass and return WAIT/IDLE, never work."""
    if not cycle_id.strip():
        raise ValueError("cycle_id is required")
    policy = bounds or InitiativeA2Bounds()
    if len(sources) > policy.source_limit:
        raise ValueError("configured source count exceeds observation bound")
    stopped = _stop_reason(database, stop_path)
    if stopped:
        return {"cycle_id": cycle_id, "outcome": "STOPPED", "reason": stopped}
    existing = _cycle_row(database, cycle_id)
    if existing and str(existing.get("stage")) == "COMPLETED":
        try:
            metrics = json.loads(str(existing.get("metrics_json") or "{}"))
        except (TypeError, ValueError):
            metrics = {}
        return {"cycle_id": cycle_id, "outcome": str(existing.get("status") or "WAIT"), "metrics": metrics, "replayed": True}

    started = time.perf_counter()
    with database.connect(readonly=True) as connection:
        start_sequence = int(connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0])
    if existing is None:
        database.append(Event.create(
            "initiative.cycle_started",
            {
                "cycle_id": cycle_id,
                "status": "RUNNING",
                "started_at": isoformat(),
                "bounds": asdict(policy),
                "budgets": {
                    "observation_sources": policy.source_limit,
                    "candidate_count": policy.candidate_limit,
                    "research_per_candidate": policy.research_budget_per_candidate,
                    "research_per_cycle": policy.research_budget_per_cycle,
                    "research_passes": policy.research_passes_per_candidate,
                },
                "metrics": {},
            },
            source_type="supervisor",
            source_id="v11-a2-initiative",
            idempotency_key=f"initiative.cycle_started:{cycle_id}",
        ))

    try:
        observed = run_source_observation_cycle(
            database,
            cycle_id,
            tuple(sources),
            candidate_limit=policy.candidate_limit,
            candidate_ttl_hours=policy.candidate_ttl_hours,
            now=now,
            stop_path=stop_path,
        )
        _append_stage(database, cycle_id, "OBSERVATIONS_RECORDED")
        if fault_hook:
            fault_hook("after_observations")
        stopped = _stop_reason(database, stop_path)
        if stopped:
            return _complete_stopped(database, cycle_id, stopped, observed, started, start_sequence)

        _append_stage(database, cycle_id, "CANDIDATES_READY")
        if fault_hook:
            fault_hook("after_candidate_generation")
        rows = _candidate_rows(database, policy.candidate_limit)
        research = _run_research(
            database,
            cycle_id=cycle_id,
            rows=rows,
            bounds=policy,
            research_reader=research_reader,
            stop_path=stop_path,
            resource_id=resource_id,
        )
        _append_stage(database, cycle_id, "RESEARCHED")
        if fault_hook:
            fault_hook("after_research")
        stopped = _stop_reason(database, stop_path)
        if stopped:
            return _complete_stopped(database, cycle_id, stopped, observed, started, start_sequence)

        decision = ExecutiveDecisionService(
            database,
            source_id="v11-a2-judgment",
        ).evaluate_a2(
            resource_id=resource_id,
            capability=capability,
            loop_id=cycle_id,
            governor=policy.governor(),
            candidate_limit=policy.candidate_limit,
            resource_contention=0.0,
        )
        _append_stage(database, cycle_id, "JUDGED")
    except Exception as error:
        _append_stage(database, cycle_id, "INTERRUPTED", status="INTERRUPTED")
        raise

    decision_id = decision.decision_id
    judgment = _judgment_metrics(database, decision_id)
    with database.connect(readonly=True) as connection:
        duplicate_rows = int(connection.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT c.fingerprint FROM candidate_judgments j "
            "JOIN candidates c ON c.candidate_id = j.candidate_id "
            "WHERE j.decision_id = ? GROUP BY c.fingerprint "
            "HAVING COUNT(*) > 1)",
            (decision_id,),
        ).fetchone()[0])
    source_rows = list(observed.get("sources") or ())
    status = "IDLE" if source_rows and all(row.get("status") == "IDLE" for row in source_rows) else "WAIT"
    zero = _event_delta_counts(database, start_sequence)
    metrics: dict[str, Any] = {
        "source_classes_attempted": sorted({str(row.get("source_kind")) for row in source_rows}),
        "source_classes_ready": sorted({str(row.get("source_kind")) for row in source_rows if row.get("status") == "READY"}),
        "observations_recorded": int(observed.get("observations_recorded") or 0),
        "raw_candidates_generated": int(observed.get("raw_candidates") or observed.get("candidates_created") or 0),
        "unique_candidates": int(observed.get("candidates_created") or 0),
        "candidate_duplicates_suppressed": int(observed.get("duplicates_suppressed") or 0),
        "candidates_researched": len(research),
        **judgment,
        "cycles_completed": 1,
        "cycle_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "research_calls": len(research),
        "research_budget_used": round(sum(float(row.get("budget_used") or 0.0) for row in research), 4),
        **zero,
        "duplicate_candidate_records": duplicate_rows,
        "unbounded_loop_detected": False,
    }
    database.append(Event.create(
        "initiative.cycle_completed",
        {
            "cycle_id": cycle_id,
            "status": status,
            "completed_at": isoformat(),
            "budgets": {"research_used": metrics["research_budget_used"]},
            "metrics": metrics,
            "reason": decision.reason,
            "next_due_at": min((row.get("next_due_at") for row in source_rows if row.get("next_due_at")), default=None),
        },
        source_type="supervisor",
        source_id="v11-a2-initiative",
        idempotency_key=f"initiative.cycle_completed:{cycle_id}",
    ))
    return {
        "cycle_id": cycle_id,
        "outcome": status,
        "reason": decision.reason,
        "decision": decision,
        "metrics": metrics,
    }


def _complete_stopped(
    database: FoundryDatabase,
    cycle_id: str,
    reason: str,
    observed: Mapping[str, Any],
    started: float,
    start_sequence: int,
) -> dict[str, Any]:
    zero = _event_delta_counts(database, start_sequence)
    metrics = {
        "observations_recorded": int(observed.get("observations_recorded") or 0),
        "candidates_researched": 0,
        **zero,
        "cycle_duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    database.append(Event.create(
        "initiative.cycle_completed",
        {
            "cycle_id": cycle_id,
            "status": "STOPPED",
            "completed_at": isoformat(),
            "metrics": metrics,
            "budgets": {"research_used": 0.0},
            "reason": reason,
        },
        source_type="supervisor",
        source_id="v11-a2-initiative",
        idempotency_key=f"initiative.cycle_completed:{cycle_id}",
    ))
    return {"cycle_id": cycle_id, "outcome": "STOPPED", "reason": reason, "metrics": metrics}


__all__ = [
    "InitiativeA2Bounds",
    "default_research_reader",
    "run_initiative_a2_cycle",
]
