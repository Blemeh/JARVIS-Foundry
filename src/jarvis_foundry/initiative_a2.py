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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .clock import isoformat
from .connectors.github_research import GitHubResearchConnector
from .connectors.web_research import PlaywrightBrowserConnector, TrafilaturaExtractor
from .control.broker import emergency_stop_reason
from .database import FoundryDatabase
from .discovery_sources import (
    SourceReadResult,
    SourceSpec,
    content_hash,
    run_source_observation_cycle,
)
from .discovery_monitors import (
    configure_monitor,
    configured_monitors,
    due_monitors,
    next_monitor_due_at,
)
from .events import Event, canonical_json
from .executive.decisions import ExecutiveDecisionService, a2_precondition
from .executive.scoring import A2GovernorBounds
from .platform.windows_subprocess import headless_run_kwargs
from .research_dossier import (
    ResearchDossierPolicy,
    research_candidate_dossier,
)


ResearchReader = Callable[[FoundryDatabase, Mapping[str, Any]], dict[str, Any]]
SourceReader = Callable[[SourceSpec, Callable[[], bool]], SourceReadResult]
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


def _candidate_rows(
    database: FoundryDatabase,
    limit: int,
    *,
    reconsider_dispositions: bool = False,
) -> list[dict[str, Any]]:
    statuses = (
        "('NEW', 'SCORED', 'ELIGIBLE', 'APPROVED', 'REJECTED', 'DEFERRED')"
        if reconsider_dispositions else "('NEW', 'SCORED', 'ELIGIBLE')"
    )
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT * FROM candidates WHERE source = 'initiative_observation' "
            f"AND status IN {statuses} "
            "ORDER BY rowid DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def _fabric_catalog_watermark(database: FoundryDatabase) -> str:
    """Return the latest append-only event sequence that can change Fabric."""
    from .fabric.catalog import DISABLED, OUTCOME, REGISTERED, SUPERSEDED

    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM events "
            "WHERE event_type IN (?, ?, ?, ?)",
            (REGISTERED, SUPERSEDED, DISABLED, OUTCOME),
        ).fetchone()
    return str(int(row[0] or 0))


def _last_fabric_catalog_watermark(database: FoundryDatabase) -> str | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT json_extract(payload_json, "
            "'$.source_digest.fabric_catalog_watermark') "
            "FROM events WHERE event_type = 'initiative.cycle_completed' "
            "AND json_extract(payload_json, "
            "'$.source_digest.fabric_catalog_watermark') IS NOT NULL "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    return str(row[0]) if row is not None else None


def _research_sources(database: FoundryDatabase, candidate_id: str) -> list[dict[str, Any]]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT cs.source_kind, cs.source_identity, "
            "COALESCE(cs.source_ref, wo.source_ref) AS source_ref, "
            "cs.content_hash, cs.observed_at, cs.fresh_until, cs.authority, "
            "wo.payload_json, wo.provenance_json "
            "FROM candidate_sources cs "
            "LEFT JOIN world_observations wo "
            "ON wo.observation_id = cs.observation_id "
            "WHERE cs.candidate_id = ? ORDER BY cs.rowid",
            (candidate_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("payload_json", "provenance_json"):
            try:
                item[key.removesuffix("_json")] = json.loads(
                    str(item.pop(key) or "{}")
                )
            except (TypeError, ValueError):
                item[key.removesuffix("_json")] = {}
        result.append(item)
    return result


def _evidence_expiry(
    source: Mapping[str, Any],
    *,
    hours: float,
    preserve_expired: bool = False,
) -> str:
    explicit = source.get("fresh_until")
    if explicit:
        try:
            parsed = datetime.fromisoformat(str(explicit).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if preserve_expired or parsed > datetime.now(timezone.utc):
                return str(explicit)
        except ValueError:
            pass
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).isoformat().replace("+00:00", "Z")


def _candidate_tokens(candidate: Mapping[str, Any]) -> set[str]:
    return {
        token.lower().strip(".,:;()[]{}")
        for token in str(candidate.get("objective") or "").split()
        if len(token.strip(".,:;()[]{}")) >= 5
    }


def _github_source_evidence(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any] | None:
    payload = dict(source.get("payload") or {})
    repo = str(payload.get("repo") or "")
    if payload.get("kind") == "workflow_run" and repo and payload.get("run_id"):
        endpoint = f"/repos/{repo}/actions/runs/{payload['run_id']}"
        action = "github.check.read"
    elif payload.get("kind") == "issue" and repo and payload.get("number"):
        endpoint = f"/repos/{repo}/issues/{payload['number']}"
        action = "github.issue.read"
    elif repo:
        endpoint = f"/repos/{repo}"
        action = "github.repo.read"
    else:
        return None
    attempt, document = GitHubResearchConnector().read_api(action, endpoint)
    if attempt.outcome != "COMPLETED" or not isinstance(document, dict):
        return None
    kind = str(payload.get("kind") or "repository")
    if kind == "issue":
        statement = (
            f"GitHub issue #{document.get('number')} in {repo} is "
            f"{document.get('state')}; title={str(document.get('title') or '')[:240]}; "
            f"updated_at={document.get('updated_at')}."
        )
        stance = "SUPPORTING"
        evidence_types = ["EXPECTED_BENEFIT", "PROJECT_CONTEXT", "FRESHNESS"]
        confidence = 0.82
    elif kind == "workflow_run":
        conclusion = str(document.get("conclusion") or "").lower()
        statement = (
            f"GitHub workflow {document.get('name') or payload.get('name')} in "
            f"{repo} has status={document.get('status')} and "
            f"conclusion={document.get('conclusion')} at head={document.get('head_sha')}."
        )
        stance = (
            "CONTRADICTING" if conclusion in {
                "failure", "timed_out", "cancelled", "startup_failure",
            } else "SUPPORTING" if conclusion == "success" else "CONTEXT"
        )
        evidence_types = [
            "EXPECTED_BENEFIT", "DEPENDENCY", "PROJECT_CONTEXT", "FRESHNESS",
        ]
        confidence = 0.85
    else:
        statement = (
            f"GitHub repository {repo} reports default_branch="
            f"{document.get('default_branch')}, open_issues="
            f"{document.get('open_issues_count')}, updated_at={document.get('updated_at')}."
        )
        stance = "CONTEXT"
        evidence_types = ["PROJECT_CONTEXT", "FRESHNESS"]
        confidence = 0.78
    return {
        "source_kind": "github",
        "source_ref": attempt.provider_ref or endpoint,
        "source_title": f"GitHub {kind} read",
        "statement": statement,
        "stance": stance,
        "claim_key": "candidate_viability",
        "confidence": confidence,
        "content_hash": (attempt.after_state or {}).get("response_hash"),
        "retrieved_at": datetime.now(timezone.utc),
        "fresh_until": _evidence_expiry(source, hours=72.0),
        "evidence_types": evidence_types,
        "outcome": attempt.outcome,
        "independent_source": f"github:{repo}",
    }


def _web_source_evidence(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any] | None:
    source_ref = str(source.get("source_ref") or source.get("source_identity") or "")
    parsed = urlsplit(source_ref)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    browser = PlaywrightBrowserConnector(allowed_hosts=(parsed.hostname,))
    capture = browser.read(source_ref, allowed_hosts=(parsed.hostname,))
    if capture.attempt.outcome != "COMPLETED" or capture.page is None:
        return None
    extraction = TrafilaturaExtractor().extract(capture)
    page = capture.page
    title = str(
        extraction.metadata.get("title")
        if extraction.success else page.title
    )[:240]
    excerpt = (
        " ".join(extraction.text.split())[:500]
        if extraction.success else
        f"HTTP {page.status}; page title '{page.title}'."
    )
    tokens = _candidate_tokens(candidate)
    overlap = sum(token in f"{title} {excerpt}".lower() for token in tokens)
    return {
        "source_kind": "web",
        "source_ref": page.final_url,
        "source_title": title,
        "statement": (
            f"The configured web source '{title or page.final_url}' "
            f"contains candidate-relevant material: {excerpt}"
        ),
        "stance": "SUPPORTING" if overlap else "CONTEXT",
        "claim_key": "candidate_viability",
        "confidence": (
            0.72 if overlap and extraction.success else
            0.62 if overlap else 0.50
        ),
        "content_hash": extraction.safe_text_sha256 or page.source_sha256,
        "retrieved_at": page.fetched_at,
        "fresh_until": _evidence_expiry(source, hours=72.0),
        "evidence_types": [
            "EXPECTED_BENEFIT", "PROJECT_CONTEXT", "FRESHNESS",
        ],
        "outcome": extraction.attempt.outcome,
        "independent_source": f"web:{parsed.hostname.lower()}",
    }


def _project_source_evidence(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any] | None:
    del candidate
    payload = dict(source.get("payload") or {})
    source_ref = str(source.get("source_ref") or source.get("source_identity") or "")
    root = Path(source_ref.split("#", 1)[0])
    if not root.is_dir():
        return None
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, check=False, timeout=15,
            **headless_run_kwargs(),
        )
        diff = subprocess.run(
            ["git", "-C", str(root), "diff", "--stat", "--no-ext-diff"],
            capture_output=True, check=False, timeout=15,
            **headless_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if head.returncode != 0 or diff.returncode != 0:
        return None
    marker = str(payload.get("text") or payload.get("kind") or "project observation")[:300]
    digest = hashlib.sha256(head.stdout + b"\x00" + diff.stdout).hexdigest()
    return {
        "source_kind": "local",
        "source_ref": source_ref,
        "source_title": f"Local project {payload.get('project_id') or root.name}",
        "statement": (
            f"Local project evidence records {marker}; HEAD="
            f"{head.stdout.decode('utf-8', errors='replace').strip()[:40]}; "
            f"working_tree_diff_bytes={len(diff.stdout)}."
        ),
        "stance": (
            "SUPPORTING" if payload.get("kind") in {
                "tracked_work_marker", "working_tree_drift",
            } else "CONTEXT"
        ),
        "claim_key": "candidate_viability",
        "confidence": 0.88,
        "content_hash": digest,
        "retrieved_at": datetime.now(timezone.utc),
        "fresh_until": _evidence_expiry(source, hours=24.0),
        "evidence_types": ["EFFORT", "DEPENDENCY", "PROJECT_CONTEXT", "FRESHNESS"],
        "outcome": "COMPLETED",
        "independent_source": f"local:{root.resolve()}",
    }


def _world_source_evidence(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any] | None:
    del candidate
    payload = dict(source.get("payload") or {})
    statement = str(
        payload.get("summary") or payload.get("title")
        or payload.get("recommended_action") or ""
    ).strip()
    if not statement:
        return None
    return {
        "source_kind": "world",
        "source_ref": source.get("source_ref") or source.get("source_identity"),
        "source_title": "JARVIS world observation",
        "statement": statement,
        "stance": "SUPPORTING" if payload.get("recommended_action") else "CONTEXT",
        "claim_key": "candidate_viability",
        "confidence": payload.get("confidence", 0.65),
        "content_hash": str(source.get("content_hash") or "").removeprefix("sha256:"),
        "retrieved_at": source.get("observed_at") or datetime.now(timezone.utc),
        "fresh_until": _evidence_expiry(
            source, hours=24.0, preserve_expired=True,
        ),
        "evidence_types": ["EXPECTED_BENEFIT", "PROJECT_CONTEXT", "FRESHNESS"],
        "outcome": "COMPLETED",
        "independent_source": "world:jarvis",
    }


def _source_evidence(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any] | None:
    kind = str(source.get("source_kind") or "").lower()
    if kind == "github":
        return _github_source_evidence(source, candidate)
    if kind == "web":
        return _web_source_evidence(source, candidate)
    if kind == "project":
        return _project_source_evidence(source, candidate)
    if kind == "world":
        return _world_source_evidence(source, candidate)
    return None


def default_research_reader(
    database: FoundryDatabase,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Collect several read-only source receipts for dossier synthesis."""
    candidate_id = str(candidate.get("candidate_id") or "")
    sources = _research_sources(database, candidate_id)
    if not sources:
        return {
            "status": "UNKNOWN",
            "source_evidence": [],
            "unresolved_unknowns": [{
                "unknown_id": "source-lineage-unavailable",
                "question": "Which bounded source can substantiate this candidate?",
                "why_it_matters": "A dossier cannot infer evidence without provenance.",
                "expected_information_value": 1.0,
            }],
            "budget_used": 0.0,
        }
    evidence_items = [
        item for item in (
            _source_evidence(source, candidate) for source in sources[:4]
        ) if item is not None
    ]
    evidence_types = {
        kind for item in evidence_items for kind in item.get("evidence_types", ())
    }
    independent = {item.get("independent_source") for item in evidence_items}
    unknowns: list[dict[str, Any]] = []
    if "EXPECTED_BENEFIT" not in evidence_types:
        unknowns.append({
            "unknown_id": "expected-benefit",
            "question": "What measurable owner or project benefit would this candidate deliver?",
            "why_it_matters": "Expected benefit must be evidence-backed before scoring.",
            "expected_information_value": 0.85,
        })
    if not {"EFFORT", "DEPENDENCY"}.intersection(evidence_types):
        unknowns.append({
            "unknown_id": "effort-dependency",
            "question": "What implementation effort and dependency changes would this candidate require?",
            "why_it_matters": "Hidden migration or dependency work can reverse the decision.",
            "expected_information_value": 0.95,
        })
    if len(independent - {None}) < 2:
        unknowns.append({
            "unknown_id": "independent-corroboration",
            "question": "Can an independent source corroborate the candidate premise?",
            "why_it_matters": "One-source evidence is too fragile for a durable dossier.",
            "expected_information_value": 0.80,
        })
    return {
        "status": "COMPLETED" if evidence_items else "UNKNOWN",
        "source_evidence": evidence_items,
        "unresolved_unknowns": unknowns,
        "source_ref": str(sources[0].get("source_ref") or ""),
        "budget_used": 1.0 if evidence_items else 0.0,
    }


def _default_follow_up_reader(
    unknown: dict[str, Any],
    candidate: dict[str, Any],
    _research_question: str,
    _policy: ResearchDossierPolicy,
) -> dict[str, Any]:
    """Target a high-value unknown with bounded current-source inspection."""
    unknown_id = str(unknown.get("unknown_id") or "")
    target = f"{candidate.get('target_project', '')} {candidate.get('objective', '')}".lower()
    if unknown_id != "effort-dependency" or "jarvis" not in target:
        return {
            "status": "UNKNOWN",
            "resolved": False,
            "reason": "no configured bounded follow-up source for this unknown",
            "evidence": [],
        }
    root = Path(__file__).resolve().parents[2]
    manifest = root / "pyproject.toml"
    launcher = root / "src" / "jarvis_foundry" / "deployment" / "release.py"
    if not manifest.is_file() or not launcher.is_file():
        return {
            "status": "UNKNOWN", "resolved": False,
            "reason": "JARVIS dependency/launcher sources unavailable", "evidence": [],
        }
    manifest_text = manifest.read_text(encoding="utf-8")[:64_000]
    launcher_text = launcher.read_text(encoding="utf-8")[:128_000]
    digest = hashlib.sha256(
        manifest_text.encode("utf-8") + b"\x00" + launcher_text.encode("utf-8")
    ).hexdigest()
    python_314 = "3.14" in str(candidate.get("objective") or "")
    launcher_313 = "py -3.13" in launcher_text
    contradicts = python_314 and launcher_313
    statement = (
        "JARVIS declares Python >=3.13, but its Windows release launcher is "
        "explicitly pinned to py -3.13; a Python 3.14 adoption candidate "
        "therefore requires launcher, dependency, and regression proof."
        if contradicts else
        "JARVIS dependency metadata and its release launcher were inspected; "
        "implementation effort remains tied to the current Python runtime contract."
    )
    now = datetime.now(timezone.utc)
    return {
        "status": "COMPLETED",
        "resolved": True,
        "reason": "bounded local dependency and launcher inspection",
        "evidence": [{
            "source_kind": "local",
            "source_ref": f"{root}#pyproject.toml+src/jarvis_foundry/deployment/release.py",
            "source_title": "JARVIS runtime and release contract",
            "statement": statement,
            "stance": "CONTRADICTING" if contradicts else "CONTEXT",
            "claim_key": "candidate_viability",
            "confidence": 0.96,
            "content_hash": digest,
            "retrieved_at": now,
            "fresh_until": now + timedelta(hours=24),
            "evidence_types": ["EFFORT", "DEPENDENCY", "PROJECT_CONTEXT"],
            "outcome": "COMPLETED",
            "independent_source": f"local:{root}",
        }],
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
    now: datetime | str | None = None,
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
        def initial_reader(
            _identity: dict[str, Any],
            _question: str,
            _policy: ResearchDossierPolicy,
        ) -> Mapping[str, Any]:
            if _stop_reason(database, stop_path):
                return {
                    "status": "BLOCKED", "source_evidence": [],
                    "unresolved_unknowns": [{
                        "unknown_id": "owner-stop",
                        "question": "Can research resume after owner STOP?",
                        "why_it_matters": "STOP dominates every research read.",
                        "expected_information_value": 1.0,
                    }],
                    "budget_used": 0.0,
                }
            return research_reader(database, row)

        completed = research_candidate_dossier(
            database,
            candidate_id=str(row["candidate_id"]),
            cycle_id=cycle_id,
            initial_reader=initial_reader,
            follow_up_reader=(
                _default_follow_up_reader
                if research_reader is default_research_reader else None
            ),
            policy=ResearchDossierPolicy(),
            reserved_budget=planned,
            per_candidate_budget=bounds.research_budget_per_candidate,
            cycle_budget=bounds.research_budget_per_cycle,
            now=now,
        )
        if completed.get("status") in {
            "COMPLETED", "BLOCKED", "FAILED", "UNKNOWN", "REUSED",
        }:
            results.append(completed)
            used = float(completed.get("budget_used") or 0.0)
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
    source_readers: Mapping[str, SourceReader] | None = None,
    research_wake_consumed: int = 0,
) -> dict[str, Any]:
    """Run one replay-safe A2 pass and return WAIT/IDLE, never work.

    ``research_wake_consumed`` counts the research-completion wakes the calling
    Executive cycle released.  Durable research findings are a meaningful change
    for the Executive even when no observed source moved, so a non-zero count
    suppresses the zero-change short circuit and this pass reaches research
    reuse and a fresh A2 evaluation instead of returning WAIT immediately.
    """
    if not cycle_id.strip():
        raise ValueError("cycle_id is required")
    policy = bounds or InitiativeA2Bounds()
    sources = _with_default_vault_source(
        database, sources, source_limit=policy.source_limit,
    )
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
            readers=source_readers,
        )
        source_digest = dict(observed.get("source_digest") or {})
        fabric_catalog_watermark = _fabric_catalog_watermark(database)
        fabric_catalog_changed = (
            _last_fabric_catalog_watermark(database)
            != fabric_catalog_watermark
        )
        source_digest["fabric_catalog_watermark"] = fabric_catalog_watermark
        _append_stage(database, cycle_id, "OBSERVATIONS_RECORDED")
        if fault_hook:
            fault_hook("after_observations")
        stopped = _stop_reason(database, stop_path)
        if stopped:
            return _complete_stopped(database, cycle_id, stopped, observed, started, start_sequence)

        expiry_cutoff = _time_text(_current_time(now))
        with database.connect(readonly=True) as connection:
            expired_due = connection.execute(
                "SELECT 1 FROM candidates WHERE source = 'initiative_observation' "
                "AND status IN ('NEW', 'SCORED', 'ELIGIBLE', 'DEFERRED') "
                "AND expires_at IS NOT NULL AND expires_at <= ? LIMIT 1",
                (expiry_cutoff,),
            ).fetchone() is not None
        if (not expired_due
                and int(observed.get("candidates_created") or 0) == 0
                and int(observed.get("candidates_refreshed") or 0) == 0
                and int(observed.get("meaningful_changes") or 0) == 0
                and int(research_wake_consumed or 0) == 0
                and not fabric_catalog_changed):
            source_rows = list(observed.get("sources") or ())
            status = (
                "IDLE" if source_rows
                and all(row.get("status") == "IDLE" for row in source_rows)
                else "WAIT"
            )
            zero = _event_delta_counts(database, start_sequence)
            metrics = {
                "source_classes_attempted": sorted({
                    str(row.get("source_kind")) for row in source_rows
                }),
                "source_classes_ready": sorted({
                    str(row.get("source_kind")) for row in source_rows
                    if row.get("status") == "READY"
                }),
                "observations_recorded": int(
                    observed.get("observations_recorded") or 0),
                "raw_candidates_generated": int(
                    observed.get("raw_candidates") or 0,
                ),
                "unique_candidates": 0,
                "candidate_duplicates_suppressed": int(
                    observed.get("duplicates_suppressed") or 0,
                ),
                "candidates_refreshed": 0,
                "meaningful_source_changes": 0,
                "fabric_catalog_changed": fabric_catalog_changed,
                "candidates_researched": 0,
                "candidates_rejected": 0,
                "candidates_deferred": 0,
                "candidates_eligible": 0,
                "cycles_completed": 1,
                "cycle_duration_ms": round(
                    (time.perf_counter() - started) * 1000, 3),
                "research_calls": 0,
                "research_budget_used": 0.0,
                **zero,
                "duplicate_candidate_records": 0,
                "unbounded_loop_detected": False,
            }
            database.append(Event.create(
                "initiative.cycle_completed",
                {
                    "cycle_id": cycle_id,
                    "status": status,
                    "completed_at": isoformat(),
                    "budgets": {"research_used": 0.0},
                    "metrics": metrics,
                    "reason": "no meaningful source change",
                    "source_digest": source_digest,
                    "next_due_at": min((
                        row.get("next_due_at") for row in source_rows
                        if row.get("next_due_at")
                    ), default=None),
                },
                source_type="supervisor",
                source_id="v11-continuous-observation",
                idempotency_key=f"initiative.cycle_completed:{cycle_id}",
            ))
            return {
                "cycle_id": cycle_id, "outcome": status,
                "reason": "no meaningful source change", "metrics": metrics,
                "source_digest": source_digest,
            }

        _append_stage(database, cycle_id, "CANDIDATES_READY")
        if fault_hook:
            fault_hook("after_candidate_generation")
        rows = _candidate_rows(
            database,
            policy.candidate_limit,
            reconsider_dispositions=(
                int(research_wake_consumed or 0) > 0
                or fabric_catalog_changed
            ),
        )
        research = _run_research(
            database,
            cycle_id=cycle_id,
            rows=rows,
            bounds=policy,
            research_reader=research_reader,
            stop_path=stop_path,
            resource_id=resource_id,
            now=now,
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
            reconsider_dispositions=(
                int(research_wake_consumed or 0) > 0
                or fabric_catalog_changed
            ),
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
        "candidates_refreshed": int(observed.get("candidates_refreshed") or 0),
        "meaningful_source_changes": int(observed.get("meaningful_changes") or 0),
        "fabric_catalog_changed": fabric_catalog_changed,
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
            "source_digest": source_digest,
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
        "source_digest": source_digest,
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
            "source_digest": observed.get("source_digest", {}),
        },
        source_type="supervisor",
        source_id="v11-a2-initiative",
        idempotency_key=f"initiative.cycle_completed:{cycle_id}",
    ))
    return {
        "cycle_id": cycle_id, "outcome": "STOPPED", "reason": reason,
        "metrics": metrics, "source_digest": observed.get("source_digest", {}),
    }


_OBSERVATION_WAKE_SUBJECT = "system:continuous-observation"


def _interrupted_continuous_cycle(database: FoundryDatabase) -> str | None:
    """Return the oldest incomplete due-cycle identity, if one exists."""
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT cycle_id FROM initiative_cycles "
            "WHERE cycle_id LIKE 'continuous-observation-%' "
            "AND stage != 'COMPLETED' "
            "AND status IN ('RUNNING', 'INTERRUPTED') "
            "ORDER BY started_at, rowid LIMIT 1"
        ).fetchone()
    return str(row[0]) if row is not None else None


def _configured_source_specs(database: FoundryDatabase) -> tuple[SourceSpec, ...]:
    return tuple(
        SourceSpec(
            source_id=str(row["source_id"]),
            source_kind=str(row["source_kind"]),
            source_identity=str(row["source_ref"]),
            poll_interval_s=int(row["poll_interval_s"]),
            config=dict(row.get("config") or {}),
            enabled=True,
        )
        for row in configured_monitors(database, enabled_only=True)
    )


def _with_default_vault_source(
    database: FoundryDatabase,
    sources: Sequence[SourceSpec],
    *,
    source_limit: int,
) -> tuple[SourceSpec, ...]:
    """Keep the already-indexed owner vault on the canonical A2 watch path."""
    resolved = tuple(sources)
    if any(spec.source_kind == "vault" for spec in resolved):
        return resolved
    if len(resolved) >= source_limit:
        return resolved
    from .vault_indexer import configured_vault_root

    root = configured_vault_root(database)
    if root is None:
        return resolved
    return resolved + (SourceSpec(
        source_id="owner-vault",
        source_kind="vault",
        source_identity=str(root),
        poll_interval_s=300,
        config={},
        enabled=True,
    ),)


def _current_time(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _time_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_observation_wake(
    database: FoundryDatabase,
    *,
    not_before: str | None,
) -> dict[str, Any] | None:
    """Coalesce the subsystem onto one existing Executive time wake."""
    from .executive.continuous import (
        WakeCondition,
        clear_wake,
        create_wake,
        rebuild_wake_state,
    )

    wakes = rebuild_wake_state(database)
    with database.connect(readonly=True) as connection:
        schedule_rows = [dict(row) for row in connection.execute(
            "SELECT source_id, next_due_at, backoff_until, last_attempt_at "
            "FROM monitors WHERE source_id IS NOT NULL AND enabled = 1 "
            "ORDER BY source_id"
        ).fetchall()]
    base_wake_id = (
        "wake:system:continuous-observation:until:"
        + _hash({"not_before": not_before, "schedule": schedule_rows})[:16]
    )
    active = [
        wake for wake in wakes.values()
        if wake.get("objective_id") == _OBSERVATION_WAKE_SUBJECT
        and wake.get("status") == "ACTIVE"
    ]
    matching = next((wake for wake in active
                     if wake.get("wake_id", "").startswith(base_wake_id)
                     and wake.get("not_before") == not_before), None)
    if matching is not None:
        return matching
    for wake in active:
        clear_wake(
            database, str(wake["wake_id"]), controller="supervisor",
            reason="continuous observation due time replaced",
        )
    if not not_before:
        return None
    prior = sum(str(wake_id).startswith(base_wake_id) for wake_id in wakes)
    wake_id = base_wake_id if prior == 0 else f"{base_wake_id}:retry:{prior}"
    wake_condition = WakeCondition(
        wake_id=wake_id,
        objective_id=_OBSERVATION_WAKE_SUBJECT,
        kind="WAIT_UNTIL_TIME",
        reason="next durable watch source is due",
        created_at=isoformat(),
        not_before=not_before,
    )
    create_wake(database, wake_condition, controller="supervisor")
    return wake_condition.to_payload()


def run_continuous_observation_cycle(
    database: FoundryDatabase,
    *,
    sources: Sequence[SourceSpec] = (),
    bounds: InitiativeA2Bounds | None = None,
    resource_id: str = "v11-continuous-observation",
    capability: str = "cap.file.read",
    stop_path: str | Path | None = None,
    research_reader: ResearchReader = default_research_reader,
    source_readers: Mapping[str, SourceReader] | None = None,
    fault_hook: FaultHook | None = None,
    now: datetime | str | None = None,
    research_wake_consumed: int = 0,
    research_trigger_id: str = "",
) -> dict[str, Any]:
    """Dispatch at most one due observation pass from one Executive wake.

    Runtime configuration is projected into the existing monitor table once.
    Later invocations read schedule/readiness/backoff from SQLite, so restart
    does not reset cadence. Calls before the durable due time append nothing.

    ``research_wake_consumed`` is the count of research-completion wakes the
    current Continuous Executive cycle consumed.  Newly durable research
    findings are a meaningful change for the Executive even when no observed
    source moved, so that count suppresses the zero-change short circuit below
    and the cycle proceeds to research reuse and a fresh A2 evaluation.
    """
    policy = bounds or InitiativeA2Bounds()
    current = _current_time(now)
    current_text = _time_text(current)
    stopped = _stop_reason(database, stop_path)
    if stopped:
        return {
            "outcome": "STOPPED", "reason": stopped,
            "cycles_dispatched": 0, "research_dispatches": 0,
            "next_due_at": next_monitor_due_at(database),
        }

    sources = tuple(sources) or _configured_source_specs(database)
    sources = _with_default_vault_source(
        database, sources, source_limit=policy.source_limit,
    )
    if len(sources) > policy.source_limit:
        raise ValueError("configured source count exceeds observation bound")

    # A non-empty source list is the complete desired configuration. Empty is
    # a restart path: continue from durable configuration without requiring
    # the YAML to be reparsed by this function.
    if sources:
        desired_ids = {spec.source_id for spec in sources}
        for spec in sources:
            configure_monitor(
                database,
                source_id=spec.source_id,
                source_kind=spec.source_kind,
                source_identity=spec.source_identity,
                poll_interval_s=spec.poll_interval_s,
                config=spec.config,
                enabled=spec.enabled,
                now=current,
            )
        from .discovery_monitors import set_monitor_enabled
        for row in configured_monitors(database, enabled_only=True):
            if str(row.get("source_id")) not in desired_ids:
                set_monitor_enabled(
                    database, str(row["source_id"]), False,
                    reason="removed from continuous observation configuration",
                    now=current,
                )

    # A hard stop after candidate generation has already advanced every
    # monitor clock, but the A2 decision is still incomplete. Resume that
    # durable cycle before consulting the next-due schedule; otherwise the
    # selected candidate can be stranded until an unrelated later poll.
    interrupted_cycle = _interrupted_continuous_cycle(database)
    if interrupted_cycle:
        resume_sources = tuple(sources) or _configured_source_specs(database)
        report = run_initiative_a2_cycle(
            database,
            cycle_id=interrupted_cycle,
            sources=resume_sources,
            resource_id=resource_id,
            capability=capability,
            bounds=policy,
            stop_path=stop_path,
            research_reader=research_reader,
            source_readers=source_readers,
            fault_hook=fault_hook,
            now=current,
            research_wake_consumed=int(research_wake_consumed or 0),
        )
        next_due = next_monitor_due_at(database)
        next_wake = None
        if report.get("outcome") != "STOPPED":
            next_wake = _ensure_observation_wake(database, not_before=next_due)
        metrics = dict(report.get("metrics") or {})
        return {
            **report,
            "cycle_id": interrupted_cycle,
            "cycles_dispatched": 1,
            "research_dispatches": int(metrics.get("research_calls") or 0),
            "next_due_at": next_due,
            "wake_id": next_wake.get("wake_id") if next_wake else None,
            "dispatched_at": current_text,
            "resumed_interrupted_cycle": True,
        }

    due = due_monitors(database, now=current, limit=policy.source_limit)
    earliest = next_monitor_due_at(database)
    wake = _ensure_observation_wake(database, not_before=earliest)
    fabric_catalog_watermark = _fabric_catalog_watermark(database)
    fabric_catalog_changed = (
        _last_fabric_catalog_watermark(database)
        != fabric_catalog_watermark
    )
    if not due and (
        int(research_wake_consumed or 0) > 0 or fabric_catalog_changed
    ):
        # Durable research completion and canonical Fabric state changes are
        # independent reevaluation triggers. Neither should wait for an
        # unrelated project edit or poll interval; both re-enter normal A2.
        specs = tuple(sources) or _configured_source_specs(database)
        if not specs:
            return {
                "outcome": "WAIT",
                "reason": "reevaluation requested but no observation source is configured",
                "cycles_dispatched": 0,
                "research_dispatches": 0,
                "next_due_at": earliest,
                "wake_id": wake.get("wake_id") if wake else None,
            }
        trigger_material = {
            "research_trigger_id": str(research_trigger_id or ""),
            "fabric_catalog_watermark": (
                fabric_catalog_watermark if fabric_catalog_changed else None
            ),
            "sources": [spec.source_id for spec in specs],
        }
        trigger_kind = (
            "research" if int(research_wake_consumed or 0) > 0 else "fabric"
        )
        cycle_id = f"{trigger_kind}-reevaluation-" + _hash(trigger_material)[:20]
        report = run_initiative_a2_cycle(
            database,
            cycle_id=cycle_id,
            sources=specs,
            resource_id=resource_id,
            capability=capability,
            bounds=policy,
            stop_path=stop_path,
            research_reader=research_reader,
            source_readers=source_readers,
            fault_hook=fault_hook,
            now=current,
            research_wake_consumed=int(research_wake_consumed or 0),
        )
        metrics = dict(report.get("metrics") or {})
        return {
            **report,
            "cycle_id": cycle_id,
            "cycles_dispatched": 1,
            "research_dispatches": int(metrics.get("research_calls") or 0),
            "next_due_at": next_monitor_due_at(database),
            "wake_id": wake.get("wake_id") if wake else None,
            "dispatched_at": current_text,
            "research_triggered_reevaluation": int(research_wake_consumed or 0) > 0,
            "fabric_triggered_reevaluation": fabric_catalog_changed,
        }
    if not due:
        return {
            "outcome": "WAIT",
            "reason": ("no watch source is due" if earliest
                       else "no enabled watch source is configured"),
            "cycles_dispatched": 0,
            "research_dispatches": 0,
            "next_due_at": earliest,
            "wake_id": wake.get("wake_id") if wake else None,
        }

    # The due schedule itself is the replay identity. A crash before source
    # attempt commit sees the same monitor due time and therefore the same
    # wake/cycle id on restart.
    from .executive.continuous import is_wake_due, trigger_wake
    if wake is None or not is_wake_due(database, wake, now=current):
        return {
            "outcome": "WAIT", "reason": "durable wake is not due",
            "cycles_dispatched": 0, "research_dispatches": 0,
            "next_due_at": earliest,
            "wake_id": wake.get("wake_id") if wake else None,
        }
    trigger_wake(
        database, str(wake["wake_id"]), controller="supervisor",
        reason="continuous observation source due",
    )
    specs = tuple(SourceSpec(
        source_id=str(row["source_id"]),
        source_kind=str(row["source_kind"]),
        source_identity=str(row["source_ref"]),
        poll_interval_s=int(row["poll_interval_s"]),
        config=dict(row.get("config") or {}),
        enabled=True,
    ) for row in due)
    cycle_material = {
        "wake_id": wake["wake_id"],
        "sources": [
            {"source_id": row["source_id"],
             "due": row.get("backoff_until") or row.get("next_due_at")}
            for row in due
        ],
    }
    cycle_id = "continuous-observation-" + _hash(cycle_material)[:20]
    report = run_initiative_a2_cycle(
        database,
        cycle_id=cycle_id,
        sources=specs,
        resource_id=resource_id,
        capability=capability,
        bounds=policy,
        stop_path=stop_path,
        research_reader=research_reader,
        source_readers=source_readers,
        fault_hook=fault_hook,
        now=current,
        research_wake_consumed=int(research_wake_consumed or 0),
    )
    next_due = next_monitor_due_at(database)
    next_wake = None
    if report.get("outcome") != "STOPPED":
        next_wake = _ensure_observation_wake(database, not_before=next_due)
    metrics = dict(report.get("metrics") or {})
    return {
        **report,
        "cycle_id": cycle_id,
        "cycles_dispatched": 1,
        "research_dispatches": int(metrics.get("research_calls") or 0),
        "next_due_at": next_due,
        "wake_id": next_wake.get("wake_id") if next_wake else None,
        "dispatched_at": current_text,
    }


__all__ = [
    "InitiativeA2Bounds",
    "default_research_reader",
    "run_continuous_observation_cycle",
    "run_initiative_a2_cycle",
]
