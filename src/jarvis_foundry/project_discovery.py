"""Project discovery: real repositories to evidence-backed work candidates.

This module is the PRODUCT path the lane activates:

    GitHub/docs reads (connectors.github_research)
      -> repository inventory
      -> change observations
      -> typed opportunities with duplicate keys
      -> duplicate / already-solved rejection
      -> EXISTING candidate generator + scorer (discovery_candidates)
      -> EXISTING V7A portfolio items (evolution.executive_portfolio)

It introduces no second scorer, store, or engine: candidate admission,
scoring, and portfolio decisions remain exactly the canonical machinery.
Honest outcomes are first-class: a repository can conclude
"no current work is warranted" (WAIT), and every rejection carries its
evidence.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .database import FoundryDatabase

OBSERVATION_TYPES = frozenset({
    "DEFAULT_BRANCH_DRIFT", "CI_FAILED", "NO_CI_ON_DEFAULT",
    "NEW_ISSUE", "NEW_PR", "NEW_RELEASE", "STALE_REPO",
})

OPPORTUNITY_TYPES = frozenset({
    "BUG", "MISSING_TEST", "MISSING_FEATURE", "UX_FRICTION",
    "PERFORMANCE", "DEPENDENCY_UPDATE", "DOC_DRIFT", "RELEASE_GAP",
    "CI_FAILURE", "REFACTOR_WITH_MEASURABLE_VALUE",
    "UNKNOWN_REQUIRING_RESEARCH", "MAINTENANCE",
    "NEW_PROJECT_OPPORTUNITY",
})

# Portfolio classes are the EXISTING canonical set.
_PORTFOLIO_CLASS = {
    "BUG": "REPAIR",
    "CI_FAILURE": "REPAIR",
    "MISSING_TEST": "MAINTENANCE",
    "MAINTENANCE": "MAINTENANCE",
    "DOC_DRIFT": "MAINTENANCE",
    "RELEASE_GAP": "MAINTENANCE",
    "PERFORMANCE": "IMPROVEMENT",
    "DEPENDENCY_UPDATE": "IMPROVEMENT",
    "REFACTOR_WITH_MEASURABLE_VALUE": "IMPROVEMENT",
    "UX_FRICTION": "IMPROVEMENT",
    "MISSING_FEATURE": "CANDIDATE",
    "NEW_PROJECT_OPPORTUNITY": "CANDIDATE",
    "UNKNOWN_REQUIRING_RESEARCH": "RESEARCH",
}

# NOTE: the selection bar is NOT duplicated here. The authoritative
# minimum candidate score lives in the canonical discovery loop policy
# (`v3_loop.DEFAULT_MIN_SCORE`) and is consumed at decision time so a
# canonical policy change flows through automatically.

_STALE_DAYS = 45


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RepositoryRecord:
    """Exact remote identity for one discovered repository."""

    full_name: str
    visibility: str
    default_branch: str
    head_sha: str | None
    pushed_at: str | None
    language: str | None
    issues_enabled: bool
    open_issues_count: int
    ci_runs: tuple[dict[str, Any], ...] = ()
    releases: tuple[dict[str, Any], ...] = ()
    local_checkout_path: str | None = None
    dirty_local: bool | None = None
    resource_id: str | None = None

    @property
    def resource_key(self) -> str:
        return f"github:{self.full_name}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "resource_key": self.resource_key,
            "provider": "github",
            "full_name": self.full_name,
            "visibility": self.visibility,
            "default_branch": self.default_branch,
            "head_sha": self.head_sha,
            "pushed_at": self.pushed_at,
            "language": self.language,
            "issues_enabled": self.issues_enabled,
            "open_issues_count": self.open_issues_count,
            "ci_recent": list(self.ci_runs)[:4],
            "releases_recent": list(self.releases)[:3],
            "local_checkout_path": self.local_checkout_path,
            "dirty_local": self.dirty_local,
            "resource_id": self.resource_id,
        }


@dataclass(frozen=True, slots=True)
class Observation:
    observation_type: str
    repository: str
    detail: str
    observed_at: str
    evidence_ref: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "observation_type": self.observation_type,
            "repository": self.repository,
            "detail": self.detail,
            "observed_at": self.observed_at,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class Opportunity:
    """One evidence-backed software opportunity (G6 contract)."""

    kind: str
    repository: str
    problem_statement: str
    evidence_refs: tuple[str, ...]
    expected_value: float
    confidence: float
    effort: float
    risk: float
    reversibility: float
    urgency: float
    information_gain: float
    why_now: str
    duplicate_key: str
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in OPPORTUNITY_TYPES:
            raise ValueError(f"unsupported opportunity kind: {self.kind}")

    def objective(self) -> str:
        return f"{self.kind}: {self.problem_statement.strip()}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "repository": self.repository,
            "problem_statement": self.problem_statement,
            "evidence_refs": list(self.evidence_refs),
            "expected_value": self.expected_value,
            "confidence": self.confidence,
            "effort": self.effort,
            "risk": self.risk,
            "reversibility": self.reversibility,
            "urgency": self.urgency,
            "information_gain": self.information_gain,
            "why_now": self.why_now,
            "duplicate_key": self.duplicate_key,
            "dependencies": list(self.dependencies),
        }

    @staticmethod
    def make_duplicate_key(repository: str, kind: str, subject: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:60]
        digest = hashlib.sha256(
            f"{repository}|{kind}|{slug}".encode("utf-8")).hexdigest()[:12]
        return f"dupkey-{repository}-{kind}-{digest}"


@dataclass(frozen=True, slots=True)
class Rejection:
    duplicate_key: str
    repository: str
    reason: str
    evidence_refs: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {"duplicate_key": self.duplicate_key,
                "repository": self.repository, "reason": self.reason,
                "evidence_refs": list(self.evidence_refs)}


def observe_repository(
    record: RepositoryRecord,
    *,
    previous_head_sha: str | None = None,
    previous_pushed_at: str | None = None,
    now: datetime | None = None,
) -> list[Observation]:
    """Bounded change observations for ONE repository (G5)."""
    now = now or datetime.now(timezone.utc)
    observed = _now_iso()
    ref = f"github:{record.full_name}@{record.default_branch}"
    observations: list[Observation] = []
    if previous_head_sha and record.head_sha \
            and previous_head_sha != record.head_sha:
        observations.append(Observation(
            "DEFAULT_BRANCH_DRIFT", record.full_name,
            f"default branch advanced {previous_head_sha[:10]} -> "
            f"{record.head_sha[:10]}", observed, ref))
    failed = [run for run in record.ci_runs
              if run.get("conclusion") == "failure"]
    if failed:
        latest = failed[0]
        observations.append(Observation(
            "CI_FAILED", record.full_name,
            f"workflow {latest.get('name')!r} concluded failure at "
            f"{latest.get('created_at')}", observed, ref))
        recurring = len(failed) > 1
        if recurring:
            observations.append(Observation(
                "CI_FAILED", record.full_name,
                f"{len(failed)} recent failing runs — recurring failure "
                "pattern", observed, ref))
    elif not record.ci_runs:
        observations.append(Observation(
            "NO_CI_ON_DEFAULT", record.full_name,
            "no recent CI runs observable on the default branch",
            observed, ref))
    if record.open_issues_count > 0:
        observations.append(Observation(
            "NEW_ISSUE", record.full_name,
            f"{record.open_issues_count} open issue(s)/PR(s) present",
            observed, ref))
    if record.releases:
        observations.append(Observation(
            "NEW_RELEASE", record.full_name,
            f"latest release {record.releases[0].get('tag_name')!r} at "
            f"{record.releases[0].get('published_at')}", observed, ref))
    pushed = _parse_ts(record.pushed_at)
    if pushed is not None:
        age_days = (now - pushed).total_seconds() / 86400.0
        if age_days >= _STALE_DAYS:
            observations.append(Observation(
                "STALE_REPO", record.full_name,
                f"no push activity for {int(age_days)} days",
                observed, ref))
    return observations


def detect_opportunities(
    observations: list[Observation],
    record: RepositoryRecord,
) -> list[Opportunity]:
    """Typed, evidence-backed opportunities from observations (G6).

    Deliberately conservative: only patterns with concrete evidence
    become candidates. Vague "improve quality" findings never do.
    """
    opportunities: list[Opportunity] = []
    ci_failures = [o for o in observations
                   if o.observation_type == "CI_FAILED"]
    seen_ci = False
    for obs in observations:
        if obs.observation_type == "CI_FAILED" and not seen_ci:
            seen_ci = True
            recurring = len(ci_failures) > 1
            detail_suffix = (
                "; unverified changes risk regressions reaching main "
                "while pushes stay blocked"
                if recurring else
                "; changes currently land without CI verification")
            opportunities.append(Opportunity(
                kind="CI_FAILURE",
                repository=obs.repository,
                problem_statement=(
                    f"fix failing CI on {obs.repository}: {obs.detail}"
                    f"{detail_suffix}"),
                evidence_refs=(obs.evidence_ref,),
                expected_value=0.7 if recurring else 0.5,
                confidence=0.85 if recurring else 0.7,
                effort=0.35,
                risk=0.2,
                reversibility=0.9,
                urgency=0.6 if recurring else 0.4,
                information_gain=0.4,
                why_now=("every push stays red while failures recur"
                         if recurring else
                         "default branch confidence is currently broken"),
                duplicate_key=Opportunity.make_duplicate_key(
                    obs.repository, "CI_FAILURE", "ci-red"),
            ))
        if obs.observation_type == "NO_CI_ON_DEFAULT":
            opportunities.append(Opportunity(
                kind="MISSING_TEST",
                repository=obs.repository,
                problem_statement=(
                    f"{obs.repository}: default branch has no observable "
                    "CI coverage; regressions reach main unnoticed"),
                evidence_refs=(obs.evidence_ref,),
                expected_value=0.5,
                confidence=0.75,
                effort=0.3,
                risk=0.15,
                reversibility=0.95,
                urgency=0.3,
                information_gain=0.5,
                why_now="changes merge with zero automated verification",
                duplicate_key=Opportunity.make_duplicate_key(
                    obs.repository, "MISSING_TEST", "no-ci-on-default"),
            ))
        if obs.observation_type == "STALE_REPO":
            opportunities.append(Opportunity(
                kind="MAINTENANCE",
                repository=obs.repository,
                problem_statement=(
                    f"{obs.repository}: {obs.detail}; decide resume, "
                    "archive, or deliberate keep"),
                evidence_refs=(obs.evidence_ref,),
                expected_value=0.25,
                confidence=0.8,
                effort=0.1,
                risk=0.05,
                reversibility=1.0,
                urgency=0.1,
                information_gain=0.2,
                why_now="stale estate misleads discovery and wastes reads",
                duplicate_key=Opportunity.make_duplicate_key(
                    obs.repository, "MAINTENANCE", "stale-repo"),
            ))
    return opportunities


def reject_duplicates(
    db: FoundryDatabase,
    mission_id: str,
    opportunities: list[Opportunity],
    *,
    open_issue_titles_by_repo: dict[str, list[str]] | None = None,
    already_solved_on_default: Callable[[Opportunity], bool] | None = None,
) -> tuple[list[Opportunity], list[Rejection]]:
    """G7: reject duplicates against existing work AND each other.

    Layers:
    1. canonicalized-objective match against existing candidates/missions
       (``covered_objective``);
    2. an open GitHub issue/PR with materially the same subject;
    3. the fix already being present on the default branch (caller-
       supplied check keeps this bounded);
    4. intra-batch duplicate keys.
    """
    from .discovery_candidates import covered_objective

    accepted: list[Opportunity] = []
    rejections: list[Rejection] = []
    seen_keys: set[str] = set()
    issues = open_issue_titles_by_repo or {}
    for opp in opportunities:
        key = opp.duplicate_key
        if key in seen_keys:
            rejections.append(Rejection(
                key, opp.repository, "duplicate within discovery batch",
                opp.evidence_refs))
            continue
        covering = covered_objective(db, opp.objective())
        if covering is not None:
            rejections.append(Rejection(
                key, opp.repository,
                f"already covered by existing candidate/mission {covering}",
                opp.evidence_refs))
            continue
        titles = [t.lower() for t in issues.get(opp.repository, [])]
        subject_words = {
            word for word in re.findall(r"[a-z0-9]+", opp.problem_statement.lower())
            if len(word) > 4}
        overlap_issue = next(
            (title for title in titles
             if len(subject_words & set(title.split())) >= max(
                 2, int(len(subject_words) * 0.4))),
            None)
        if overlap_issue is not None:
            rejections.append(Rejection(
                key, opp.repository,
                f"open GitHub issue likely tracks this: '{overlap_issue}'",
                opp.evidence_refs))
            continue
        if already_solved_on_default is not None \
                and already_solved_on_default(opp):
            rejections.append(Rejection(
                key, opp.repository,
                "fix already present on the default branch",
                opp.evidence_refs))
            continue
        seen_keys.add(key)
        accepted.append(opp)
    return accepted, rejections


def canonical_min_candidate_score() -> float:
    """The authoritative selection bar, read from canonical policy.

    Single source of truth: ``v3_loop.DEFAULT_MIN_SCORE``. This module
    never keeps its own copy, so a canonical policy change applies here
    without touching discovery code.
    """
    from .v3_loop import DEFAULT_MIN_SCORE
    return float(DEFAULT_MIN_SCORE)


def feed_portfolio(
    db: FoundryDatabase,
    mission_id: str,
    opportunities: list[Opportunity],
) -> dict[str, Any]:
    """G8: feed accepted opportunities into the EXISTING pipeline.

    Candidates are generated through ``generate_candidate`` (dedupe),
    scored through ``score_candidate`` (canonical calibrated truth), and
    registered as V7A work items via ``register_work_item``. The
    SELECT/WAIT decision reads the CANONICAL selection bar at decision
    time (``canonical_min_candidate_score``); this module computes NO
    second final score and owns no threshold.
    """
    from .discovery_candidates import (
        generate_candidate,
        score_candidate,
    )
    from .supervisor.authority import SUPERVISOR_CONTROLLER
    from .evolution.executive_portfolio import register_work_item

    min_score = canonical_min_candidate_score()
    fed: list[dict[str, Any]] = []
    for opp in opportunities:
        result = generate_candidate(
            db, mission_id=mission_id, source="repo",
            objective=opp.objective(),
            evidence=[{"ref": ref, "type": "github_research"}
                      for ref in opp.evidence_refs],
            typed={
                "candidate_type": opp.kind,
                "fingerprint": opp.duplicate_key,
                "provenance_hash": hashlib.sha256(
                    opp.objective().encode("utf-8")).hexdigest()[:16],
                "expected_outcome": opp.why_now,
            })
        if not result["created"]:
            fed.append({"duplicate_key": opp.duplicate_key,
                        "candidate_id": result["candidate_id"],
                        "duplicate_of": result["duplicate_of"]})
            continue
        candidate_id = result["candidate_id"]
        score = score_candidate(db, candidate_id)
        item_id = f"pw-{opp.duplicate_key}"
        try:
            register_work_item(
                db,
                item_id=item_id,
                kind=_PORTFOLIO_CLASS[opp.kind],
                objective=opp.objective(),
                controller=SUPERVISOR_CONTROLLER,
                candidate_id=candidate_id,
                expected_value=opp.expected_value,
                urgency=opp.urgency,
                information_gain=opp.information_gain,
                confidence=opp.confidence,
                effort=opp.effort,
                risk=opp.risk,
                reversibility=opp.reversibility,
                dependencies=list(opp.dependencies),
                evidence_refs=list(opp.evidence_refs),
                provenance={
                    "source_lane": "live-github-research-activation",
                    "repository": opp.repository,
                    "duplicate_key": opp.duplicate_key,
                    "why_now": opp.why_now,
                },
                metadata={"repository": opp.repository},
            )
        except Exception as error:  # noqa: BLE001 — recorded honestly below
            fed.append({"duplicate_key": opp.duplicate_key,
                        "candidate_id": candidate_id,
                        "score": score,
                        "portfolio_error": str(error)})
            continue
        fed.append({"duplicate_key": opp.duplicate_key,
                    "candidate_id": candidate_id,
                    "score": score,
                    "portfolio_item_id": item_id,
                    "portfolio_class": _PORTFOLIO_CLASS[opp.kind]})
    passing = [entry for entry in fed
               if entry.get("score") is not None
               and entry["score"] >= min_score]
    waiting = bool(fed) and not passing
    return {
        "mission_id": mission_id,
        "fed": fed,
        "min_score_source": "v3_loop.DEFAULT_MIN_SCORE",
        "min_score_applied": min_score,
        "passing_min_score": passing,
        "decision": "SELECT_CANDIDATES" if passing else "WAIT",
        "wait_reason": (
            "no opportunity reached the canonical selection bar"
            if waiting else None) if fed else
        "no accepted opportunities to feed",
        "generated_at": _now_iso(),
    }


__all__ = [
    "Opportunity",
    "Observation",
    "Rejection",
    "RepositoryRecord",
    "canonical_min_candidate_score",
    "detect_opportunities",
    "feed_portfolio",
    "observe_repository",
    "reject_duplicates",
]
