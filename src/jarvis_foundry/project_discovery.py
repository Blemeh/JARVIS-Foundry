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
# WP3 (post-4 integration): NEW_PROJECT_OPPORTUNITY maps to the canonical
# NEW_PROJECT work class (Lane 3). Lane 1's older CANDIDATE generic is
# superseded; V7A remains the single selector.
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
    "NEW_PROJECT_OPPORTUNITY": "NEW_PROJECT",
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
                    f"{obs.repository}: no push activity beyond the stale "
                    f"threshold ({_STALE_DAYS}+ days); decide resume, "
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
    *,
    resource_id: str | None = None,
    executive_objective_id: str | None = None,
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
                resource_id=resource_id,
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
                    "opportunity_kind": opp.kind,
                    "problem_statement": opp.problem_statement,
                    "duplicate_key": opp.duplicate_key,
                    "why_now": opp.why_now,
                },
                metadata={"repository": opp.repository,
                          **({"executive_objective_id": executive_objective_id}
                             if executive_objective_id else {})},
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
    "discover_portfolio_opportunities",
    "eligible_discovery_repositories",
    "feed_portfolio",
    "observe_repository",
    "reject_duplicates",
]


# ---------------------------------------------------------------------------
# Autonomous portfolio-discovery bridge.
#
# FIRST-BROKEN-EDGE REPAIR (2026-09-29): the observe -> detect -> reject ->
# feed chain above existed and was tested but had ZERO production callers,
# so a valid broad owner goal parked at BLOCKED / WAIT forever with no
# concrete candidates. This bridge is the single production caller. It runs
# bounded read-only discovery over eligible registered projects when the
# portfolio is starved, then feeds the EXISTING candidate / portfolio /
# selection machinery. It introduces no second scorer, store, planner,
# discovery authority, or mission system.
#
# Fail-closed rules (no fabrication, ever):
#   - GitHub reads unavailable -> repo skipped with reason, no record built.
#   - CI state unreadable -> repo skipped (a NO_CI claim needs proof).
#   - slug unparseable / resource unbound / checkout missing -> skipped.
#   - broad goals are NEVER stamped executable; only concrete fed items
#     compete in selection, scored by the existing value/readiness/risk law.
# ---------------------------------------------------------------------------

_DISCOVERY_BRIDGE_LANE = "portfolio-discovery-v1"
_DISCOVERY_BRIDGE_SOURCE = "portfolio-discovery-bridge"
_DISCOVERY_INTERVAL_S = 6 * 3600
_DISCOVERY_MAX_REPOS = 5


def _norm_profile_path(path: str) -> str:
    """Normalize a profile-bound path for comparison across separators."""
    return str(path).replace("\\", "/").lower()


def _bridge_slug_from_url(url: str) -> str | None:
    """Parse one remote URL to ``owner/repo`` via the canonical normalizer."""
    from .software_intelligence.project_identity import normalize_remote
    try:
        slug = normalize_remote(url)
    except Exception:  # noqa: BLE001 — unparseable remote, fail closed
        return None
    if "/" not in slug:
        return None
    return slug


def eligible_discovery_repositories(db: FoundryDatabase) -> list[dict[str, Any]]:
    """Return registered ACTIVE projects eligible for autonomous discovery.

    Eligibility uses EXISTING authorities only: the resource registry (a
    checkout with a GitHub remote) intersected with the project-profile
    bindings (the owner's registered active projects). Test fixtures and
    probe worktrees are not profile-bound, so they are excluded by
    construction — no hard-coded exclusion list.
    """
    import json
    from pathlib import Path

    bindings: dict[str, str] = {}
    try:
        raw = json.loads(
            (Path(db.path).parent / "project_profiles" / "bindings.json")
            .read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            bindings = {_norm_profile_path(str(key)): str(value)
                        for key, value in raw.items()}
    except (OSError, ValueError):
        bindings = {}
    eligible: list[dict[str, Any]] = []
    try:
        with db.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT resource_id, resolved_root, root_path, baseline_json"
                " FROM resources").fetchall()
    except Exception:  # noqa: BLE001 — registry unreadable, nothing eligible
        return []
    for row in rows:
        mapping = dict(row) if not isinstance(row, dict) else row
        resource_id = str(mapping.get("resource_id") or "")
        root = str(mapping.get("resolved_root") or mapping.get("root_path")
                   or "")
        if not resource_id or not root:
            continue
        if _norm_profile_path(root) not in bindings:
            continue
        baseline: dict[str, Any] = {}
        try:
            baseline = json.loads(str(mapping.get("baseline_json") or "{}"))
        except ValueError:
            continue
        remotes = baseline.get("remotes") or []
        if not remotes and isinstance(baseline.get("git"), dict):
            # software-intelligence registrations overlay the git baseline
            # under ``git``; WP0 discovery records keep it top-level.
            remotes = baseline["git"].get("remotes") or []
        slug: str | None = None
        if isinstance(remotes, list):
            for entry in remotes:
                text = str(entry)
                if "github.com" not in text.lower():
                    continue
                url_token: str | None = None
                for token in text.split():
                    if "://" in token:
                        url_token = token
                        break
                slug = _bridge_slug_from_url(url_token or text)
                if slug:
                    break
        if not slug:
            continue
        if not Path(root).is_dir():
            continue
        eligible.append({
            "resource_id": resource_id,
            "slug": slug,
            "root": root,
            "profile_id": bindings[_norm_profile_path(root)],
        })
    eligible.sort(key=lambda item: item["resource_id"])
    return eligible


def _last_bridge_record(
    db: FoundryDatabase, slug: str,
) -> dict[str, Any] | None:
    """Return the latest durable discovery run record for one repository."""
    lowered = slug.lower()
    try:
        recent = db.events(
            event_types=("portfolio.discovery_completed",),
            newest_first=True, limit=25)
    except TypeError:
        recent = list(reversed(db.events()))[:500]
    for event in recent:
        payload = event.payload or {}
        if str(payload.get("repository") or "").lower() != lowered:
            continue
        return dict(payload)
    return None


def _bridge_cheap_fingerprint(
    connector: Any, slug: str,
) -> dict[str, Any] | None:
    """One bounded repo-endpoint read: new pushes/issues without a full scan."""
    try:
        attempt, document = connector.read_api(
            "github.repo.read", f"/repos/{slug}")
    except Exception:  # noqa: BLE001 — transport failure, fail closed
        return None
    if getattr(attempt, "outcome", "") != "COMPLETED" \
            or not isinstance(document, dict):
        return None
    fingerprint = "|".join((
        str(document.get("pushed_at") or ""),
        str(document.get("updated_at") or ""),
        str(document.get("open_issues_count") or ""),
        str(document.get("default_branch") or ""),
        str(document.get("archived") or ""),
    ))
    return {
        "fingerprint": hashlib.sha256(fingerprint.encode("utf-8"))
        .hexdigest()[:24],
        "pushed_at": document.get("pushed_at"),
        "open_issues_count": document.get("open_issues_count"),
        "default_branch": document.get("default_branch") or "main",
        "archived": bool(document.get("archived")),
    }


def _bridge_ci_runs(connector: Any, slug: str) -> tuple[list[dict[str, Any]], bool]:
    """Bounded workflow-run read: green runs are invisible to the source pass.

    Returns (runs, readable). ``readable=False`` means CI state is unknown
    and the caller must skip the repository rather than claim NO_CI.
    """
    try:
        attempt, document = connector.read_api(
            "github.check.read", f"/repos/{slug}/actions/runs?per_page=5")
    except Exception:  # noqa: BLE001 — transport failure, fail closed
        return [], False
    if getattr(attempt, "outcome", "") != "COMPLETED" \
            or not isinstance(document, dict):
        return [], False
    # total_count distinguishes "no runs exist" (genuine NO_CI evidence)
    # from a truncated/malformed page (unknown — fail closed). The key must
    # be present and coercible; absence is not evidence of absence.
    if "total_count" not in document:
        return [], False
    try:
        total = int(document.get("total_count") or 0)
        if document.get("total_count") is None:
            return [], False
    except (TypeError, ValueError):
        return [], False
    runs: list[dict[str, Any]] = []
    items = document.get("workflow_runs")
    if not isinstance(items, list):
        return [], False
    if total > 0 and not items:
        return [], False
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        runs.append({
            "name": str(item.get("name") or "workflow")[:160],
            "status": str(item.get("status") or "")[:40],
            "conclusion": str(item.get("conclusion") or "")[:40],
            "created_at": str(item.get("created_at")
                              or item.get("updated_at") or "")[:40],
        })
    return runs, True


def _workflows_on_default(root: str, default_branch: str) -> bool | None:
    """Read-only local check: does the default branch define workflows?

    Uses the locally-tracked remote ref (kept current by production refresh);
    ``None`` means unknown and must not assert either direction.
    """
    import subprocess
    for ref in (f"origin/{default_branch}", default_branch):
        try:
            result = subprocess.run(
                ["git", "ls-tree", "-r", ref, "--name-only", "--",
                 ".github/workflows/"],
                capture_output=True, timeout=30, check=False, cwd=root,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            continue
        names = [line.strip() for line in
                 result.stdout.decode("utf-8", errors="replace").splitlines()
                 if line.strip()]
        return any(name.endswith((".yml", ".yaml")) for name in names)
    return None


def _bridge_already_solved(
    opportunity: Opportunity,
    *,
    root: str,
    default_branch: str,
    ci_runs: list[dict[str, Any]],
) -> bool | None:
    """Local solved-check for one opportunity; ``None`` means unknown.

    A solved opportunity must not become a candidate (it would be busywork);
    an unknown one stays eligible with its stated confidence.
    """
    if opportunity.kind == "MISSING_TEST":
        solved = _workflows_on_default(root, default_branch)
        return solved
    if opportunity.kind == "CI_FAILURE":
        if not ci_runs:
            return None
        latest = ci_runs[0].get("conclusion") or ""
        if latest in ("success", "skipped", "neutral"):
            return True
        if latest in ("failure", "cancelled", "timed_out", "action_required"):
            return False
        return None
    return None


def _record_from_observations(
    slug: str,
    result: Any,
    ci_runs: list[dict[str, Any]],
    *,
    resource_id: str,
    root: str,
) -> tuple[RepositoryRecord, list[str]]:
    """Map a GitHub source read onto the canonical repository record."""
    observations = list(getattr(result, "observations", None) or ())
    by_identity: dict[str, Any] = {}
    for obs in observations:
        by_identity[str(getattr(obs, "source_identity", ""))] = obs
    repo_payload: dict[str, Any] = {}
    branch_payload: dict[str, Any] = {}
    open_titles: list[str] = []
    releases: list[dict[str, Any]] = []
    repo_obs = by_identity.get(f"github:{slug}:repository")
    if repo_obs is not None:
        repo_payload = dict(getattr(repo_obs, "payload", None) or {})
    branch_obs = by_identity.get(f"github:{slug}:default-branch")
    if branch_obs is not None:
        branch_payload = dict(getattr(branch_obs, "payload", None) or {})
    for obs in observations:
        payload = dict(getattr(obs, "payload", None) or {})
        if payload.get("kind") == "issue" \
                and str(payload.get("state") or "").lower() == "open":
            title = str(payload.get("title") or "").strip()
            if title:
                open_titles.append(title)
        if payload.get("kind") == "release" and not payload.get("draft"):
            tag = str(payload.get("tag") or "").strip()
            if tag:
                releases.append({"tag_name": tag,
                                 "published_at": payload.get("published_at")})
    default_branch = str(repo_payload.get("default_branch")
                         or branch_payload.get("branch") or "main")
    pushed_at = repo_payload.get("pushed_at") \
        or repo_payload.get("updated_at")
    try:
        open_count = int(repo_payload.get("open_issues_count") or 0)
    except (TypeError, ValueError):
        open_count = 0
    return RepositoryRecord(
        full_name=slug,
        visibility=str(repo_payload.get("visibility") or "UNKNOWN"),
        default_branch=default_branch,
        head_sha=branch_payload.get("head_sha"),
        pushed_at=pushed_at,
        language=None,
        issues_enabled=True,
        open_issues_count=open_count,
        ci_runs=tuple(ci_runs),
        releases=tuple(releases[:3]),
        local_checkout_path=root,
        resource_id=resource_id,
    ), open_titles


def discover_portfolio_opportunities(
    db: FoundryDatabase,
    *,
    objective_id: str,
    source_id: str = _DISCOVERY_BRIDGE_SOURCE,
    reader: Any | None = None,
    connector: Any | None = None,
    now: datetime | None = None,
    max_repos: int = _DISCOVERY_MAX_REPOS,
    executive_objective_id: str | None = None,
) -> dict[str, Any]:
    """Bounded read-only discovery over eligible projects; feed what is real.

    ``reader``/``connector`` are test seams (the production defaults are the
    existing GitHub source pass and research connector). Every repository
    concludes fed, rejected-with-evidence, or skipped-with-reason — never
    fabricated. Runs are durable (``portfolio.discovery_completed``) and
    idempotent (same evidence fingerprint reuses the same record).
    ``executive_objective_id`` binds fed items to one executive objective
    so the canonical objective-scoped selection can consume them; without
    it items compete only in the global pool.
    """
    from .events import Event

    current = now or datetime.now(timezone.utc)
    day = current.strftime("%Y-%m-%d")
    if reader is None:
        from .discovery_sources import read_github_source as reader
    report: dict[str, Any] = {
        "objective_id": objective_id,
        "lane": _DISCOVERY_BRIDGE_LANE,
        "repos": [],
        "generated_at": current.isoformat(),
    }
    for entry in eligible_discovery_repositories(db)[:max(1, max_repos)]:
        slug = entry["slug"]
        repo_report: dict[str, Any] = {
            "repository": slug, "resource_id": entry["resource_id"],
            "outcome": "skipped", "reason": None,
        }
        report["repos"].append(repo_report)
        if connector is None:
            from .connectors.github_research import (
                GitHubResearchConnector as fresh_connector)
            active_connector: Any = fresh_connector()
        else:
            active_connector = connector
        cheap = _bridge_cheap_fingerprint(active_connector, slug)
        if cheap is None:
            repo_report["reason"] = "github reads unavailable"
            continue
        if cheap["archived"]:
            repo_report["reason"] = "repository archived"
            continue
        last = _last_bridge_record(db, slug)
        if last is not None and last.get("cheap_fp") == cheap["fingerprint"]:
            try:
                last_ts = datetime.fromisoformat(str(last.get("ran_at")))
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                fresh = (current - last_ts).total_seconds()
            except (TypeError, ValueError):
                fresh = _DISCOVERY_INTERVAL_S
            if fresh < _DISCOVERY_INTERVAL_S:
                repo_report["reason"] = "no new evidence since last run"
                continue
        runs, readable = _bridge_ci_runs(active_connector, slug)
        if not readable:
            repo_report["reason"] = "CI state unreadable; NO_CI unprovable"
            continue
        try:
            from .discovery_sources import SourceSpec
            read = reader(SourceSpec(
                source_id=f"{_DISCOVERY_BRIDGE_SOURCE}:{slug}",
                source_kind="github", source_identity=slug,
                config={"repo": slug, "target_project": slug,
                        "max_items": 10, "recent_days": 30}))
        except Exception as error:  # noqa: BLE001 — spec/read failure
            repo_report["reason"] = f"source read failed: {error}"[:200]
            continue
        if getattr(read, "status", "") != "READY":
            repo_report["reason"] = (
                f"source read {getattr(read, 'status', '?')}: "
                f"{getattr(read, 'reason', '')}"[:200])
            continue
        record, open_titles = _record_from_observations(
            slug, read, runs, resource_id=entry["resource_id"],
            root=entry["root"])
        observations = observe_repository(
            record,
            previous_head_sha=(last or {}).get("head_sha"),
            previous_pushed_at=(last or {}).get("pushed_at"),
            now=current)
        opportunities = detect_opportunities(observations, record)
        mission_id = f"portfolio-discovery:{slug}:{day}"
        accepted, rejected = reject_duplicates(
            db, mission_id, opportunities,
            open_issue_titles_by_repo={slug: open_titles},
            already_solved_on_default=lambda opp: bool(
                _bridge_already_solved(
                    opp, root=entry["root"],
                    default_branch=record.default_branch, ci_runs=runs)),
        )
        rejected_report = [
            {"duplicate_key": rej.duplicate_key, "reason": rej.reason}
            for rej in rejected]
        fed: list[dict[str, Any]] = []
        feed_error: str | None = None
        if accepted:
            try:
                feed = feed_portfolio(
                    db, mission_id, accepted,
                    resource_id=entry["resource_id"],
                    executive_objective_id=executive_objective_id)
                fed = feed["fed"]
            except Exception as error:  # noqa: BLE001 — recorded, not raised
                feed_error = str(error)[:200]
        evidence_hash = hashlib.sha256(
            f"{slug}|{cheap['fingerprint']}|"
            f"{record.head_sha}|{len(accepted)}|{len(rejected)}"
            .encode("utf-8")).hexdigest()[:24]
        try:
            db.append(Event.create(
                "portfolio.discovery_completed",
                {"repository": slug, "resource_id": entry["resource_id"],
                 "objective_id": objective_id,
                 "lane": _DISCOVERY_BRIDGE_LANE,
                 "cheap_fp": cheap["fingerprint"],
                 "evidence_hash": evidence_hash,
                 "head_sha": record.head_sha,
                 "pushed_at": record.pushed_at,
                 "opportunities": len(opportunities),
                 "accepted": len(accepted),
                 "rejected": rejected_report,
                 "fed": fed,
                 "feed_error": feed_error,
                 "ran_at": current.isoformat()},
                source_type="supervisor", source_id=source_id,
                idempotency_key=f"portfolio.discovery:{slug}:{evidence_hash}",
            ))
        except Exception:  # noqa: BLE001 — same-day rerun dedupes honestly
            pass
        repo_report.update({
            "outcome": "completed",
            "opportunities": len(opportunities),
            "accepted": [opp.duplicate_key for opp in accepted],
            "rejected": rejected_report,
            "fed": fed,
            "feed_error": feed_error,
        })
    return report
