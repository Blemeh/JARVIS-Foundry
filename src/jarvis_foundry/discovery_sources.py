"""Bounded V11-A2 observation adapters.

The adapters in this module only read configured sources and turn their
receipts into World Model observations and discovery candidates.  External
content is always DATA_ONLY.  No function here can create an objective,
mission, effect, worktree, or provider write.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tomllib
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote, urlsplit

from .connectors.github_research import GitHubResearchConnector
from .connectors.web_research import PlaywrightBrowserConnector, TrafilaturaExtractor
from .control.broker import emergency_stop_reason
from .control.redaction import redact_text
from .database import FoundryDatabase
from .discovery_candidates import ingest_observed_candidate
from .discovery_monitors import ensure_monitor, monitor_due, record_monitor_attempt
from .events import Event, canonical_json
from .world_model.observations import WorldObservationStore
from .world_model.signals import WorldSignalStore


SOURCE_STATUSES = frozenset({"READY", "BLOCKED", "UNKNOWN", "IDLE"})
EXTERNAL_SOURCE_KINDS = frozenset({"github", "web", "project"})
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TEXT_EXTENSIONS = frozenset({
    ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".toml",
    ".yaml", ".yml", ".json", ".ini", ".cfg", ".ps1", ".sh",
})
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build",
})
_DEPENDENCY_FILES = frozenset({
    "Cargo.lock", "Cargo.toml", "Gemfile", "Gemfile.lock", "go.mod", "go.sum",
    "package-lock.json", "package.json", "pnpm-lock.yaml", "poetry.lock",
    "pyproject.toml", "requirements.txt", "uv.lock", "yarn.lock",
})
_IMPORTANT_PROJECT_FILES = (
    "pyproject.toml", "package.json", "uv.lock", "package-lock.json",
    "requirements.txt", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
    "Dockerfile", "docker-compose.yml", "compose.yml", ".github/workflows",
)
_TRUSTED_WORLD_AUTHORITIES = frozenset({
    "OWNER_STATEMENT", "VERIFIED_EVIDENCE", "VERIFIER_RESULT",
    "SYSTEM_OBSERVATION", "REPOSITORY_AUTHORITY",
})


def _utc(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def content_hash(value: Any) -> str:
    """Hash a bounded canonical value without preserving unstable formatting."""
    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def observation_identity(source_kind: str, source_identity: str, digest: str) -> str:
    raw = f"{source_kind}|{source_identity}|{digest}"
    return "wo-a2-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    source_kind: str
    source_identity: str
    poll_interval_s: int = 300
    config: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.source_identity.strip():
            raise ValueError("source_id and source_identity are required")
        if self.source_kind not in {"github", "web", "project", "world", "gmail"}:
            raise ValueError(f"unsupported source_kind {self.source_kind!r}")
        if not 1 <= int(self.poll_interval_s) <= 86_400:
            raise ValueError("poll_interval_s must be between 1 and 86400")


@dataclass(frozen=True, slots=True)
class SourceObservation:
    source_kind: str
    source_identity: str
    source_ref: str
    payload: Mapping[str, Any]
    content_hash: str
    authority: str = "DATA_ONLY"
    freshness: str = "CURRENT"
    expires_at: str | None = None
    proposed_outcome: str | None = None
    target_project: str = ""
    alias_key: str | None = None
    candidate_type: str | None = None
    confidence: float | None = None
    candidate_on: str = "always"
    raw_content_hash: str | None = None
    opportunity_key: str | None = None
    significance_hint: str = ""

    def __post_init__(self) -> None:
        if self.source_kind in EXTERNAL_SOURCE_KINDS and self.authority != "DATA_ONLY":
            raise ValueError("external observations must have DATA_ONLY authority")
        digest = self.content_hash
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("content_hash must be sha256:<64 lowercase hex>")
        if self.raw_content_hash is not None and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.raw_content_hash,
        ):
            raise ValueError("raw_content_hash must be sha256:<64 lowercase hex>")
        if self.candidate_on not in {"always", "change_only", "never"}:
            raise ValueError("candidate_on must be always, change_only, or never")

    @property
    def observation_id(self) -> str:
        return observation_identity(
            self.source_kind, self.source_identity, self.content_hash,
        )


@dataclass(frozen=True, slots=True)
class SourceReadResult:
    status: str
    observations: tuple[SourceObservation, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in SOURCE_STATUSES - {"IDLE"}:
            raise ValueError(f"invalid read status {self.status!r}")


def _bounded_text(value: Any, limit: int = 300) -> str:
    return str(value or "").replace("\x00", " ").strip()[:limit]


def _raw_hash(value: bytes | str) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8", errors="replace")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _semantic_text(value: Any, limit: int = 16_000) -> str:
    """Normalize prose/code layout so formatting-only edits do not become work."""
    text = str(value or "").replace("\x00", " ")[:limit]
    return " ".join(text.split())


def _semantic_file(path: Path, raw: bytes) -> Any:
    """Return a bounded semantic form for common dependency/config formats."""
    text = raw.decode("utf-8", errors="replace")
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            return json.loads(text)
        if suffix == ".toml":
            return tomllib.loads(text)
    except (ValueError, tomllib.TOMLDecodeError):
        pass
    if suffix in {".yaml", ".yml"}:
        # Do not require a second parser at runtime merely to suppress comments.
        meaningful = [line.split(" #", 1)[0].strip() for line in text.splitlines()]
        return [line for line in meaningful if line and not line.startswith("#")]
    return _semantic_text(text)


def _changed_fields(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    ignored = {
        "raw_content_sha256", "updated_at", "observed_at", "scan_stats",
        "observation_intelligence",
    }
    return sorted(
        key for key in set(before) | set(after)
        if key not in ignored and before.get(key) != after.get(key)
    )[:30]


def _change_type(before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> str:
    if before is None:
        return "NEW"
    old_state = str(before.get("state") or before.get("status") or "").upper()
    new_state = str(after.get("state") or after.get("status") or "").upper()
    if old_state != new_state and new_state in {"CLOSED", "RESOLVED", "CLEAN", "COMPLETED"}:
        return "RESOLVED"
    if old_state != new_state:
        return "STATE_CHANGED"
    return "CONTENT_CHANGED"


def _historical_expiry(value: Any, hours: float = 168.0) -> str | None:
    try:
        stamp = _utc(str(value))
    except (TypeError, ValueError):
        return None
    return _iso(stamp + timedelta(hours=hours))


def read_github_source(
    spec: SourceSpec,
    *,
    connector: GitHubResearchConnector | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> SourceReadResult:
    """Read bounded project state from one configured repository.

    GitHub timestamps and API envelope changes are retained as raw evidence but
    excluded from semantic hashes.  Issue text/state, PR state, branch heads,
    releases/tags, workflow failures, and important tree entries are the
    meaningful surfaces.
    """
    repo = str(spec.config.get("repo") or spec.source_identity).strip()
    if not _REPO.fullmatch(repo):
        return SourceReadResult("BLOCKED", reason="invalid configured GitHub repository")
    limit = max(1, min(int(spec.config.get("max_items", 10)), 20))
    recent_days = max(1, min(int(spec.config.get("recent_days", 30)), 365))
    reader = connector or GitHubResearchConnector()
    failures: list[str] = []

    def fetch(action: str, endpoint: str) -> Any:
        if stop_check and stop_check():
            raise InterruptedError("stop requested")
        attempt, document = reader.read_api(action, endpoint)
        if attempt.outcome != "COMPLETED" or document is None:
            failures.append(str(attempt.error_code or attempt.outcome))
            return None
        return document

    try:
        repository = fetch("github.repo.read", f"/repos/{repo}")
    except InterruptedError:
        return SourceReadResult("BLOCKED", reason="stop requested")
    if not isinstance(repository, dict):
        return SourceReadResult(
            "UNKNOWN", reason="GitHub reads unavailable: " + ",".join(failures[:3]),
        )
    default_branch = _bounded_text(repository.get("default_branch"), 100) or "main"
    requests = (
        ("commits", "github.commit.read", f"/repos/{repo}/commits?sha={quote(default_branch)}&per_page={limit}"),
        ("issues", "github.issue.read", f"/repos/{repo}/issues?state=all&sort=updated&direction=desc&per_page={limit}"),
        ("pulls", "github.pull_request.read", f"/repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page={limit}"),
        ("runs", "github.check.read", f"/repos/{repo}/actions/runs?per_page={limit}"),
        ("releases", "github.release.read", f"/repos/{repo}/releases?per_page={limit}"),
        ("tags", "github.release.read", f"/repos/{repo}/tags?per_page={limit}"),
        # A recursive CPython-sized tree can exceed the connector's bounded
        # response limit and become malformed.  The root tree is bounded; a
        # configured important directory's tree SHA still changes with its
        # descendants and therefore remains a useful content-state sensor.
        ("tree", "github.repo.read", f"/repos/{repo}/git/trees/{quote(default_branch)}"),
    )
    documents: dict[str, Any] = {}
    try:
        for name, action, endpoint in requests:
            documents[name] = fetch(action, endpoint)
    except InterruptedError:
        return SourceReadResult("BLOCKED", reason="stop requested")
    observations: list[SourceObservation] = []

    repo_safe = {
        "kind": "repository", "repo": repo, "default_branch": default_branch,
        "archived": bool(repository.get("archived")),
        "visibility": _bounded_text(repository.get("visibility"), 30),
        "open_issues_count": repository.get("open_issues_count"),
        "updated_at": _bounded_text(repository.get("updated_at"), 40),
    }
    repo_semantic = {key: value for key, value in repo_safe.items() if key != "updated_at"}
    observations.append(SourceObservation(
        "github", f"github:{repo}:repository", f"https://github.com/{repo}",
        repo_safe, content_hash(repo_semantic), candidate_on="never",
        raw_content_hash=content_hash(repo_safe),
        opportunity_key=f"project:{repo}", significance_hint="repository metadata changed",
    ))

    commits = documents.get("commits") if isinstance(documents.get("commits"), list) else []
    if commits and isinstance(commits[0], dict) and commits[0].get("sha"):
        head_item = commits[0]
        head_commit = head_item.get("commit") if isinstance(head_item.get("commit"), dict) else {}
        branch_safe = {
            "kind": "default_branch_state", "repo": repo,
            "branch": default_branch, "head_sha": _bounded_text(head_item.get("sha"), 64),
            "head_message": _bounded_text(head_commit.get("message"), 500),
            "committed_at": _bounded_text(
                (head_commit.get("committer") or {}).get("date")
                if isinstance(head_commit.get("committer"), dict) else "", 40,
            ),
        }
        observations.append(SourceObservation(
            "github", f"github:{repo}:default-branch", f"https://github.com/{repo}/tree/{default_branch}",
            branch_safe, content_hash(branch_safe), candidate_on="never",
            raw_content_hash=content_hash(head_item), opportunity_key=f"project:{repo}",
            significance_hint="default branch or HEAD moved",
        ))
    for index, item in enumerate(commits[:limit]):
        if not isinstance(item, dict) or not item.get("sha"):
            continue
        commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
        stamp = ((commit.get("committer") or {}).get("date")
                 if isinstance(commit.get("committer"), dict) else None)
        safe = {
            "kind": "commit", "repo": repo, "sha": _bounded_text(item.get("sha"), 64),
            "default_branch": default_branch,
            "message": _bounded_text(commit.get("message"), 500),
            "committed_at": _bounded_text(stamp, 40),
            "url": _bounded_text(item.get("html_url"), 500),
            "is_default_branch_head": index == 0,
        }
        observations.append(SourceObservation(
            "github", f"github:{repo}:commit:{safe['sha']}", safe["url"] or f"https://github.com/{repo}/commit/{safe['sha']}",
            safe, content_hash(safe), candidate_on="never",
            raw_content_hash=content_hash(item), opportunity_key=f"project:{repo}",
            significance_hint=("default branch head moved" if index == 0 else "recent commit observed"),
        ))

    issues = documents.get("issues") if isinstance(documents.get("issues"), list) else []
    open_problem_groups: dict[str, list[int]] = {}
    for item in issues[:limit]:
        if not isinstance(item, dict) or "pull_request" in item:
            continue
        number, title = item.get("number"), _bounded_text(item.get("title"), 240)
        if not number or not title:
            continue
        labels = sorted(_bounded_text(value.get("name"), 80) for value in item.get("labels", [])
                        if isinstance(value, dict) and value.get("name"))[:12]
        state = _bounded_text(item.get("state"), 20).lower()
        safe = {
            "kind": "issue", "repo": repo, "number": number, "title": title,
            "body": _semantic_text(item.get("body"), 2_000), "state": state,
            "state_reason": _bounded_text(item.get("state_reason"), 40),
            "labels": labels, "milestone": _bounded_text(
                (item.get("milestone") or {}).get("title") if isinstance(item.get("milestone"), dict) else "", 120),
            "updated_at": _bounded_text(item.get("updated_at"), 40),
            "url": _bounded_text(item.get("html_url"), 500),
        }
        semantic = {key: value for key, value in safe.items() if key != "updated_at"}
        opportunity = f"github:{repo}:issue:{number}"
        observations.append(SourceObservation(
            "github", opportunity, safe["url"] or f"https://github.com/{repo}/issues/{number}",
            safe, content_hash(semantic),
            proposed_outcome=(f"Evaluate and resolve GitHub issue #{number}: {title}" if state == "open" else None),
            target_project=repo, alias_key=opportunity, candidate_type="github_issue",
            confidence=0.8, candidate_on="always" if state == "open" else "never",
            raw_content_hash=content_hash(safe), opportunity_key=opportunity,
            significance_hint="issue content or lifecycle changed",
        ))
        if state == "open":
            stem = " ".join(re.findall(r"[a-z0-9]+", title.lower())[:4])
            group = ",".join(labels[:2]) or stem
            open_problem_groups.setdefault(group, []).append(int(number))

    pulls = documents.get("pulls") if isinstance(documents.get("pulls"), list) else []
    for item in pulls[:limit]:
        if not isinstance(item, dict) or not item.get("number"):
            continue
        number = int(item["number"])
        safe = {
            "kind": "pull_request", "repo": repo, "number": number,
            "title": _bounded_text(item.get("title"), 240),
            "state": _bounded_text(item.get("state"), 20).lower(),
            "draft": bool(item.get("draft")), "merged_at": _bounded_text(item.get("merged_at"), 40),
            "head_sha": _bounded_text((item.get("head") or {}).get("sha") if isinstance(item.get("head"), dict) else "", 64),
            "base_sha": _bounded_text((item.get("base") or {}).get("sha") if isinstance(item.get("base"), dict) else "", 64),
            "updated_at": _bounded_text(item.get("updated_at"), 40),
            "url": _bounded_text(item.get("html_url"), 500),
        }
        semantic = {key: value for key, value in safe.items() if key != "updated_at"}
        opportunity = f"github:{repo}:pr:{number}"
        outcome = (f"Review pull request #{number} state change: {safe['title']}"
                   if spec.config.get("candidate_on_pr_changes") else None)
        observations.append(SourceObservation(
            "github", opportunity, safe["url"] or f"https://github.com/{repo}/pull/{number}",
            safe, content_hash(semantic), proposed_outcome=outcome,
            target_project=repo, alias_key=opportunity, candidate_type="github_pull_request",
            confidence=0.7, candidate_on="change_only" if outcome else "never",
            raw_content_hash=content_hash(safe), opportunity_key=opportunity,
            significance_hint="pull request lifecycle changed",
        ))

    releases = documents.get("releases") if isinstance(documents.get("releases"), list) else []
    now = _utc(spec.config.get("now")) if spec.config.get("now") else datetime.now(timezone.utc)
    for item in releases[:limit]:
        if not isinstance(item, dict) or not item.get("tag_name"):
            continue
        tag = _bounded_text(item.get("tag_name"), 120)
        published = _bounded_text(item.get("published_at"), 40)
        safe = {
            "kind": "release", "repo": repo, "tag": tag,
            "name": _bounded_text(item.get("name"), 240),
            "body": _semantic_text(item.get("body"), 3_000),
            "draft": bool(item.get("draft")), "prerelease": bool(item.get("prerelease")),
            "published_at": published, "url": _bounded_text(item.get("html_url"), 500),
        }
        recent = False
        try:
            recent = _utc(published) >= now - timedelta(days=recent_days)
        except (TypeError, ValueError):
            pass
        affects_runtime = bool(spec.config.get("affects_current_runtime"))
        opportunity = str(spec.config.get("opportunity_key") or f"github:{repo}:release")
        observations.append(SourceObservation(
            "github", f"github:{repo}:release:{tag}", safe["url"] or f"https://github.com/{repo}/releases/tag/{tag}",
            safe, content_hash(safe),
            proposed_outcome=(f"Review {repo} release {tag} for current runtime compatibility"
                              if affects_runtime and recent and not safe["draft"] else None),
            target_project=str(spec.config.get("target_project") or repo), alias_key=opportunity,
            candidate_type="github_release", confidence=0.85,
            candidate_on="always" if affects_runtime and recent else "never",
            raw_content_hash=content_hash(item), opportunity_key=opportunity,
            significance_hint="release or release notes changed",
        ))

    tags = documents.get("tags") if isinstance(documents.get("tags"), list) else []
    tag_state = [{"name": _bounded_text(item.get("name"), 120),
                  "sha": _bounded_text((item.get("commit") or {}).get("sha")
                                        if isinstance(item.get("commit"), dict) else "", 64)}
                 for item in tags[:limit] if isinstance(item, dict) and item.get("name")]
    if tag_state:
        tag_opportunity = str(
            spec.config.get("opportunity_key") or f"github:{repo}:release"
        )
        latest_tag = tag_state[0]["name"]
        tag_affects_runtime = bool(spec.config.get("affects_current_runtime"))
        observations.append(SourceObservation(
            "github", f"github:{repo}:tags", f"https://github.com/{repo}/tags",
            {"kind": "tags", "repo": repo, "tags": tag_state}, content_hash(tag_state),
            proposed_outcome=(f"Review {repo} tag {latest_tag} for current runtime compatibility"
                              if tag_affects_runtime else None),
            target_project=str(spec.config.get("target_project") or repo),
            alias_key=tag_opportunity, candidate_type="github_release_tag",
            confidence=0.8, candidate_on="always" if tag_affects_runtime else "never",
            raw_content_hash=content_hash(tags[:limit]),
            opportunity_key=tag_opportunity, significance_hint="repository tags changed",
        ))

    runs = ((documents.get("runs") or {}).get("workflow_runs", [])
            if isinstance(documents.get("runs"), dict) else [])
    failures_by_name: dict[str, list[Any]] = {}
    for item in runs[:limit]:
        if not isinstance(item, dict) or item.get("conclusion") not in {"failure", "cancelled", "timed_out"}:
            continue
        run_id, name = item.get("id"), _bounded_text(item.get("name"), 160) or "workflow"
        safe = {
            "kind": "workflow_run", "repo": repo, "run_id": run_id, "name": name,
            "conclusion": item.get("conclusion"), "head_sha": _bounded_text(item.get("head_sha"), 64),
            "created_at": _bounded_text(item.get("created_at"), 40),
            "url": _bounded_text(item.get("html_url"), 500),
        }
        opportunity = f"github:{repo}:workflow:{name.lower()}"
        observations.append(SourceObservation(
            "github", f"github:{repo}:workflow:{run_id}", safe["url"] or f"https://github.com/{repo}/actions",
            safe, content_hash(safe), proposed_outcome=f"Investigate failed GitHub workflow {name}",
            target_project=repo, alias_key=opportunity, candidate_type="github_workflow_failure",
            confidence=0.75, raw_content_hash=content_hash(item), opportunity_key=opportunity,
            significance_hint="failed workflow observed",
            expires_at=_historical_expiry(item.get("updated_at") or item.get("created_at")),
        ))
        failures_by_name.setdefault(name.lower(), []).append(run_id)

    recurring = [
        {"problem_key": key, "issue_numbers": values}
        for key, values in open_problem_groups.items() if key and len(values) >= 2
    ] + [
        {"problem_key": f"workflow:{name}", "run_ids": values}
        for name, values in failures_by_name.items() if len(values) >= 2
    ]
    for problem in recurring[:limit]:
        key = _bounded_text(problem["problem_key"], 160)
        opportunity = f"github:{repo}:recurring:{content_hash(key)[7:23]}"
        safe = {"kind": "recurring_problem", "repo": repo, **problem}
        observations.append(SourceObservation(
            "github", opportunity, f"https://github.com/{repo}", safe, content_hash(safe),
            proposed_outcome=f"Investigate recurring project problem in {repo}: {key}",
            target_project=repo, alias_key=opportunity, candidate_type="github_recurring_problem",
            confidence=0.8, opportunity_key=opportunity,
            significance_hint="recurring open project problem observed",
        ))

    tree = documents.get("tree") if isinstance(documents.get("tree"), dict) else {}
    important = tuple(str(value).replace("\\", "/").strip("/") for value in
                      (spec.config.get("important_paths") or _IMPORTANT_PROJECT_FILES))[:20]
    for entry in (tree.get("tree") or [])[:5000]:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        if not path or not any(path == wanted or path.startswith(wanted.rstrip("/") + "/")
                               for wanted in important):
            continue
        safe = {
            "kind": "dependency_or_config", "repo": repo, "path": path,
            "object_sha": _bounded_text(entry.get("sha"), 64),
            "object_type": _bounded_text(entry.get("type"), 20),
        }
        opportunity = f"project:{repo}:dependency-config:{path.lower()}"
        observations.append(SourceObservation(
            "github", f"github:{repo}:tree:{path}", f"https://github.com/{repo}/blob/{default_branch}/{path}",
            safe, content_hash(safe),
            proposed_outcome=f"Review dependency or configuration change in {repo}: {path}",
            target_project=repo, alias_key=opportunity, candidate_type="github_dependency_change",
            confidence=0.75, candidate_on="change_only", opportunity_key=opportunity,
            significance_hint="dependency or configuration object changed",
        ))
    return SourceReadResult(
        "READY", tuple(observations[: max(20, limit * 8)]),
        reason=("partial GitHub read failure" if failures else ""),
    )


def read_web_source(
    spec: SourceSpec,
    *,
    browser: PlaywrightBrowserConnector | None = None,
    extractor: TrafilaturaExtractor | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> SourceReadResult:
    """Read configured pages and fingerprint meaningful extracted content."""
    urls = tuple(spec.config.get("urls") or ())
    if not urls:
        return SourceReadResult("BLOCKED", reason="web watchlist has no configured URLs")
    if len(urls) > 8:
        return SourceReadResult("BLOCKED", reason="web watchlist exceeds 8 URL bound")
    configured_hosts = tuple(spec.config.get("allowed_hosts") or ())
    allowed_hosts = configured_hosts or tuple(sorted({
        str(urlsplit(str(value)).hostname or "").lower()
        for value in urls if urlsplit(str(value)).hostname
    }))
    configured_outcomes = dict(spec.config.get("proposed_outcomes") or {})
    configured_aliases = dict(spec.config.get("alias_keys") or {})
    target_project = str(spec.config.get("target_project") or "")
    web = browser or PlaywrightBrowserConnector(allowed_hosts=allowed_hosts)
    parser = extractor or TrafilaturaExtractor()
    observations: list[SourceObservation] = []
    failures: list[str] = []
    for url_value in urls:
        url = str(url_value)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or (
            allowed_hosts and parsed.hostname.lower() not in {h.lower() for h in allowed_hosts}
        ):
            failures.append("invalid_or_unapproved_url")
            continue
        if stop_check and stop_check():
            return SourceReadResult("BLOCKED", tuple(observations), "stop requested")
        capture = web.read(url, stop_check=stop_check, allowed_hosts=allowed_hosts)
        extraction = parser.extract(capture, stop_check=stop_check)
        if not extraction.success or extraction.page is None:
            failures.append(str(extraction.attempt.error_code or extraction.attempt.outcome))
            continue
        meaningful_text = _semantic_text(extraction.text, 12_000)
        lines = [line.strip() for line in str(extraction.text or "").splitlines()
                 if line.strip()]
        sections: list[dict[str, Any]] = []
        heading = "page"
        body: list[str] = []

        def finish_section() -> None:
            nonlocal body
            text = _semantic_text("\n".join(body), 3_000)
            if text:
                sections.append({
                    "key": content_hash({"heading": heading.lower()})[7:23],
                    "title": _bounded_text(heading.lstrip("# "), 160) or "page",
                    "text_sha256": content_hash(text),
                    "excerpt": _bounded_text(text, 300),
                })
            body = []

        for line in lines[:300]:
            looks_heading = line.startswith("#") or (
                len(line) <= 100 and line.endswith(":") and len(body) >= 1
            )
            if looks_heading:
                finish_section()
                heading = line
            else:
                body.append(line)
            if len(sections) >= 20:
                break
        finish_section()
        versions = sorted(set(re.findall(
            r"(?<![\w.])v?\d+\.\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?",
            meaningful_text,
        )))[:30]
        dates = sorted(set(re.findall(
            r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|"
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
            r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+20\d{2})\b",
            meaningful_text, flags=re.IGNORECASE,
        )))[:30]
        safe = {
            "kind": "web_watch", "url": extraction.page.final_url,
            "title": _bounded_text(extraction.metadata.get("title"), 240),
            "meaningful_text": meaningful_text,
            "safe_excerpt": _bounded_text(meaningful_text, 500),
            "sections": sections, "versions": versions, "dates": dates,
            "raw_content_sha256": "sha256:" + str(extraction.page.source_sha256).removeprefix("sha256:"),
            "authority": "DATA_ONLY",
        }
        semantic = {
            "kind": safe["kind"], "url": safe["url"], "title": safe["title"],
            "meaningful_text": meaningful_text, "sections": sections,
            "versions": versions, "dates": dates,
        }
        digest = content_hash(semantic)
        configured = _bounded_text(configured_outcomes.get(url), 300) or None
        opportunity = str(configured_aliases.get(url) or f"web:{url}")
        observations.append(SourceObservation(
            "web", f"web:{url}", extraction.page.final_url, safe, digest,
            proposed_outcome=configured, target_project=target_project,
            alias_key=opportunity,
            candidate_type="web_watch", confidence=0.6 if configured else None,
            candidate_on=str(spec.config.get("candidate_on", "change_only")),
            raw_content_hash=safe["raw_content_sha256"], opportunity_key=opportunity,
            significance_hint="title, version, date, or meaningful section changed",
        ))
    status = "READY" if observations else "UNKNOWN"
    return SourceReadResult(status, tuple(observations), ",".join(failures[:3]))


def _git_output(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True,
            check=False, timeout=15, text=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace").rstrip("\r\n\x00")


def _git_drift(root: Path, limit: int) -> dict[str, Any] | None:
    raw_status = _git_output(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=no",
    )
    if raw_status is None:
        return None
    changed = [item[:500] for item in raw_status.split("\x00") if item][:limit]
    branch = _git_output(root, "branch", "--show-current") or "DETACHED"
    head = _git_output(root, "rev-parse", "HEAD") or ""
    last_commit_at = _git_output(root, "log", "-1", "--format=%cI") or ""
    last_subject = _bounded_text(_git_output(root, "log", "-1", "--format=%s"), 300)
    file_states: list[dict[str, Any]] = []
    resolved_root = root.resolve()
    for record in changed:
        relative = record[3:].split(" -> ")[-1].strip()
        path = (root / relative).resolve()
        try:
            path.relative_to(resolved_root)
            if path.is_file() and not path.is_symlink():
                raw = path.read_bytes()[:64_000]
                file_states.append({
                    "path": relative.replace("\\", "/"),
                    "size": path.stat().st_size,
                    "head_sha256": hashlib.sha256(raw).hexdigest(),
                })
        except (OSError, ValueError):
            continue
    return {
        "branch": branch, "head": head, "dirty": bool(changed),
        "state": "DIRTY" if changed else "CLEAN", "changed": sorted(changed),
        "changed_file_states": file_states, "last_commit_at": last_commit_at,
        "last_commit_subject": last_subject,
    }


def read_project_source(
    spec: SourceSpec, *, stop_check: Callable[[], bool] | None = None,
) -> SourceReadResult:
    """Observe one explicit owner project root with strict traversal bounds."""
    root = Path(str(spec.config.get("root") or spec.source_identity)).expanduser()
    if (not root.is_absolute() or not root.is_dir()
            or root.resolve() == Path(root.anchor).resolve()):
        return SourceReadResult("BLOCKED", reason="configured project root is unavailable")
    max_files = max(1, min(int(spec.config.get("max_files", 100)), 500))
    max_dirs = max(1, min(int(spec.config.get("max_dirs", 40)), 100))
    max_bytes = max(1024, min(int(spec.config.get("max_bytes", 256_000)), 1_000_000))
    max_depth = max(0, min(int(spec.config.get("max_depth", 5)), 10))
    project_id = str(spec.config.get("project_id") or spec.source_id)
    observations: list[SourceObservation] = []
    git_state = _git_drift(root, min(max_files, 100))
    if git_state is not None:
        safe = {"kind": "git_project_state", "project_id": project_id, **git_state}
        semantic = {
            "kind": safe["kind"], "project_id": project_id,
            "branch": safe["branch"], "head": safe["head"], "dirty": safe["dirty"],
            "changed": safe["changed"], "changed_file_states": safe["changed_file_states"],
        }
        opportunity = f"project:{project_id}:repository-state"
        observations.append(SourceObservation(
            "project", f"project:{project_id}:git-state", str(root), safe,
            content_hash(semantic),
            proposed_outcome=(f"Review meaningful working-tree drift in {project_id}"
                              if safe["dirty"] else None),
            target_project=project_id, alias_key=opportunity,
            candidate_type="project_drift", confidence=0.7,
            candidate_on="always" if safe["dirty"] else "never",
            raw_content_hash=content_hash(safe), opportunity_key=opportunity,
            significance_hint="Git branch, HEAD, or dirty state changed",
        ))
        stale_days = max(1, min(int(spec.config.get("stale_after_days", 30)), 3650))
        if git_state.get("last_commit_at"):
            try:
                last_commit = _utc(git_state["last_commit_at"])
                current_time = (
                    _utc(spec.config.get("now"))
                    if spec.config.get("now") else datetime.now(timezone.utc)
                )
                inactive_days = max(0, int((current_time - last_commit).total_seconds() // 86400))
                stale = inactive_days >= stale_days
                stale_safe = {
                    "kind": "repository_inactivity", "project_id": project_id,
                    "state": "STALE" if stale else "ACTIVE",
                    "last_commit_at": git_state["last_commit_at"],
                    "inactive_days": inactive_days, "threshold_days": stale_days,
                }
                stale_semantic = {key: value for key, value in stale_safe.items()
                                  if key != "inactive_days"}
                stale_opportunity = f"project:{project_id}:repository-inactivity"
                observations.append(SourceObservation(
                    "project", stale_opportunity, str(root), stale_safe,
                    content_hash(stale_semantic),
                    proposed_outcome=(f"Review inactive project {project_id}" if stale else None),
                    target_project=project_id, alias_key=stale_opportunity,
                    candidate_type="project_staleness", confidence=0.7,
                    candidate_on="always" if stale else "never",
                    raw_content_hash=content_hash(stale_safe), opportunity_key=stale_opportunity,
                    significance_hint="repository entered or left inactivity threshold",
                ))
            except (TypeError, ValueError):
                pass

    configured_important = tuple(spec.config.get("important_files") or ())
    important_files = tuple(dict.fromkeys((*configured_important, *_IMPORTANT_PROJECT_FILES)))[:40]
    for relative_value in important_files:
        relative = str(relative_value).replace("\\", "/").strip("/")
        if not relative or relative.endswith("/workflows"):
            continue
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if not path.is_file() or path.is_symlink():
            continue
        try:
            raw = path.read_bytes()[: min(128_000, max_bytes)]
        except OSError:
            continue
        semantic_value = _semantic_file(path, raw)
        kind = "dependency_file" if path.name in _DEPENDENCY_FILES else "important_file"
        safe = {
            "kind": kind, "project_id": project_id, "path": relative,
            "semantic_sha256": content_hash(semantic_value),
            "raw_content_sha256": _raw_hash(raw), "size": path.stat().st_size,
        }
        opportunity = f"project:{project_id}:dependency-config:{relative.lower()}"
        observations.append(SourceObservation(
            "project", f"project:{project_id}:file:{relative}", str(path), safe,
            content_hash({"kind": kind, "project_id": project_id, "path": relative,
                          "semantic_sha256": safe["semantic_sha256"]}),
            proposed_outcome=f"Review meaningful {kind.replace('_', ' ')} change in {relative}",
            target_project=project_id, alias_key=opportunity,
            candidate_type="project_dependency_change" if kind == "dependency_file" else "project_important_file_change",
            confidence=0.75, candidate_on="change_only",
            raw_content_hash=safe["raw_content_sha256"], opportunity_key=opportunity,
            significance_hint="important project file changed semantically",
        ))

    generated = spec.config.get("generated_artifacts") or {}
    if isinstance(generated, Mapping):
        for artifact_value, source_values in list(generated.items())[:20]:
            artifact_rel = str(artifact_value).replace("\\", "/").strip("/")
            artifact = (root / artifact_rel).resolve()
            try:
                artifact.relative_to(root.resolve())
            except ValueError:
                continue
            sources_for_artifact = [
                (root / str(value).replace("\\", "/").strip("/")).resolve()
                for value in (source_values if isinstance(source_values, (list, tuple)) else [source_values])
            ][:20]
            source_times = [path.stat().st_mtime_ns for path in sources_for_artifact
                            if path.is_file() and not path.is_symlink()]
            missing = not artifact.is_file()
            artifact_time = artifact.stat().st_mtime_ns if not missing else 0
            drift = missing or bool(source_times and max(source_times) > artifact_time)
            safe = {
                "kind": "generated_artifact", "project_id": project_id,
                "path": artifact_rel, "state": "DRIFTED" if drift else "CURRENT",
                "missing": missing,
                "source_paths": [str(path.relative_to(root.resolve())).replace("\\", "/")
                                 for path in sources_for_artifact if path.is_file()],
            }
            opportunity = f"project:{project_id}:generated:{artifact_rel.lower()}"
            observations.append(SourceObservation(
                "project", opportunity, str(artifact), safe, content_hash(safe),
                proposed_outcome=(f"Regenerate drifted artifact {artifact_rel} in {project_id}" if drift else None),
                target_project=project_id, alias_key=opportunity,
                candidate_type="generated_artifact_drift", confidence=0.8,
                candidate_on="always" if drift else "never", opportunity_key=opportunity,
                significance_hint="generated artifact freshness changed",
            ))

    indicator_paths = tuple(spec.config.get("indicator_files") or (
        ".pytest_cache/v/cache/lastfailed",
    ))[:20]
    for relative_value in indicator_paths:
        relative = str(relative_value).replace("\\", "/").strip("/")
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if not path.is_file() or path.is_symlink():
            continue
        try:
            raw = path.read_bytes()[:64_000]
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        failures = len(parsed) if isinstance(parsed, (dict, list)) else int(bool(parsed))
        safe = {
            "kind": "project_indicator", "project_id": project_id,
            "path": relative, "state": "FAILING" if failures else "CLEAR",
            "failure_count": failures,
        }
        opportunity = f"project:{project_id}:indicator:{relative.lower()}"
        observations.append(SourceObservation(
            "project", opportunity, str(path), safe, content_hash(safe),
            proposed_outcome=(f"Investigate {failures} locally recorded failing checks in {project_id}" if failures else None),
            target_project=project_id, alias_key=opportunity,
            candidate_type="project_failure_indicator", confidence=0.85,
            candidate_on="always" if failures else "never",
            raw_content_hash=_raw_hash(raw), opportunity_key=opportunity,
            significance_hint="existing local project health indicator changed",
        ))

    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    files_seen = dirs_seen = bytes_seen = 0
    while queue and files_seen < max_files and dirs_seen < max_dirs and bytes_seen < max_bytes:
        if stop_check and stop_check():
            return SourceReadResult("BLOCKED", tuple(observations), "stop requested")
        directory, depth = queue.popleft()
        dirs_seen += 1
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if depth < max_depth and entry.name not in _SKIP_DIRS:
                    queue.append((Path(entry.path), depth + 1))
                continue
            path = Path(entry.path)
            if entry.is_symlink() or path.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            files_seen += 1
            try:
                remaining = min(64_000, max_bytes - bytes_seen)
                raw = path.read_bytes()[:remaining]
            except OSError:
                continue
            bytes_seen += len(raw)
            text = raw.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                match = re.search(r"\b(TODO|FIXME)\b[:\s-]*(.{1,240})", line, re.IGNORECASE)
                if not match:
                    continue
                note = _bounded_text(redact_text(match.group(2)), 240)
                if not note:
                    continue
                relative = path.relative_to(root).as_posix()
                normalized_note = " ".join(note.lower().split())
                alias = content_hash({"project": project_id, "path": relative, "note": normalized_note})[7:31]
                safe = {
                    "kind": "tracked_work_marker", "project_id": project_id,
                    "path": relative, "line": line_number, "marker": match.group(1).upper(),
                    "text": note,
                }
                semantic = {key: value for key, value in safe.items() if key != "line"}
                opportunity = f"project:{project_id}:todo:{alias}"
                observations.append(SourceObservation(
                    "project", opportunity, f"{root}#{relative}:{line_number}", safe,
                    content_hash(semantic),
                    proposed_outcome=f"Resolve tracked {match.group(1).upper()} in {relative}: {note}",
                    target_project=project_id, alias_key=opportunity,
                    candidate_type="tracked_work", confidence=0.65,
                    raw_content_hash=content_hash(safe), opportunity_key=opportunity,
                    significance_hint="tracked TODO or FIXME meaning changed",
                ))
                if len(observations) >= max_files:
                    break
            if files_seen >= max_files or len(observations) >= max_files:
                break
    summary = {
        "kind": "project_scan", "project_id": project_id,
        "files_seen": files_seen, "dirs_seen": dirs_seen, "bytes_seen": bytes_seen,
    }
    observations.insert(0, SourceObservation(
        "project", f"project:{project_id}:scan", str(root), summary, content_hash(summary),
    ))
    return SourceReadResult("READY", tuple(observations[:max_files]))


def read_world_source(
    spec: SourceSpec, *, store: WorldSignalStore,
    stop_check: Callable[[], bool] | None = None,
) -> SourceReadResult:
    limit = max(1, min(int(spec.config.get("max_signals", 50)), 100))
    observations: list[SourceObservation] = []
    with store.db.connect(readonly=True) as connection:
        signal_rows = [dict(row) for row in connection.execute(
            "SELECT * FROM world_signals WHERE state IN "
            "('OPEN','MONITORING','ACKNOWLEDGED') "
            "ORDER BY urgency DESC, importance DESC LIMIT ?", (limit,),
        ).fetchall()]
    signals: list[dict[str, Any]] = []
    for row in signal_rows:
        for field_name in ("affected_entities", "evidence", "provenance"):
            raw = row.pop(f"{field_name}_json", None)
            try:
                row[field_name] = json.loads(raw) if raw else (
                    {} if field_name == "provenance" else []
                )
            except (TypeError, ValueError):
                row[field_name] = {} if field_name == "provenance" else []
        signals.append(row)
    for signal in signals:
        if stop_check and stop_check():
            return SourceReadResult("BLOCKED", tuple(observations), "stop requested")
        provenance = dict(signal.get("provenance") or {})
        evidence = [str(item)[:500] for item in (signal.get("evidence") or ())[:20]]
        verification = str(provenance.get("verification") or "").upper()
        verified = provenance.get("verified") is True or verification in {
            "VERIFIED", "MATCH", "PASS",
        }
        trusted_authority = str(provenance.get("authority") or "") in {
            "SYSTEM_OBSERVATION", "OWNER_AUTHORITY", "REPOSITORY_AUTHORITY",
        }
        candidate_authority = verified and trusted_authority and bool(evidence)
        safe = {
            key: signal.get(key) for key in (
                "signal_id", "signal_kind", "state", "salience", "title", "summary",
                "affected_entities", "confidence", "importance", "urgency", "dedupe_key",
                "stale_after", "due_at", "recommended_action",
            )
        }
        safe["evidence_refs"] = evidence
        safe["provenance_hash"] = content_hash(provenance)
        safe["verified_for_candidate"] = candidate_authority
        digest = content_hash(safe)
        action = _bounded_text(signal.get("recommended_action"), 300) or None
        # BLOCKER and owner-attention signals are observations, never autonomous work.
        eligible_kind = signal.get("signal_kind") in {
            "OPPORTUNITY", "FOLLOW_UP", "MISSING_NEXT_STEP", "PROJECT_DRIFT",
            "ABANDONED_WORK", "RECURRING_PATTERN",
        }
        observations.append(SourceObservation(
            "world", f"world-signal:{signal.get('signal_id')}",
            f"world-signal:{signal.get('signal_id')}", safe, digest,
            authority=("SYSTEM_OBSERVATION" if candidate_authority else "DATA_ONLY"),
            proposed_outcome=action if eligible_kind and candidate_authority else None,
            target_project=_bounded_text((signal.get("affected_entities") or [""])[0], 120),
            alias_key=f"world-signal:{signal.get('dedupe_key') or signal.get('signal_id')}",
            candidate_type="world_signal", confidence=signal.get("confidence"),
            expires_at=signal.get("stale_after"),
            candidate_on="always" if eligible_kind and candidate_authority else "never",
            raw_content_hash=content_hash(signal),
            opportunity_key=f"world:{signal.get('dedupe_key') or signal.get('signal_id')}",
            significance_hint="verified WorldModel signal changed",
        ))

    # Existing verified WorldModel facts are also sensors.  These are bounded
    # read projections; this function never creates or mutates WorldModel facts.
    fact_limit = max(1, min(int(spec.config.get("max_facts", limit)), 100))
    now = _iso(_utc(spec.config.get("now"))) if spec.config.get("now") else _iso(_utc())
    with store.db.connect(readonly=True) as connection:
        entities = [dict(row) for row in connection.execute(
            "SELECT * FROM world_entities WHERE authority IN "
            "('OWNER_STATEMENT','VERIFIED_EVIDENCE','VERIFIER_RESULT','SYSTEM_OBSERVATION') "
            "ORDER BY updated_at DESC LIMIT ?", (fact_limit,),
        ).fetchall()]
        contradictions = [dict(row) for row in connection.execute(
            "SELECT * FROM world_relationships WHERE relationship_type = 'CONTRADICTS' "
            "ORDER BY updated_at DESC LIMIT ?", (fact_limit,),
        ).fetchall()]
        gaps = [dict(row) for row in connection.execute(
            "SELECT * FROM world_knowledge_gaps WHERE resolved = 0 "
            "ORDER BY blocking DESC, updated_at DESC LIMIT ?", (fact_limit,),
        ).fetchall()]
        patterns = [dict(row) for row in connection.execute(
            "SELECT * FROM world_patterns WHERE occurrences >= ? "
            "ORDER BY occurrences DESC, last_seen DESC LIMIT ?",
            (max(2, int(spec.config.get("failure_occurrences", 2))), fact_limit),
        ).fetchall()]

    for entity in entities:
        try:
            attributes = json.loads(entity.get("attributes_json") or "{}")
            provenance = json.loads(entity.get("provenance_json") or "{}")
        except (TypeError, ValueError):
            attributes, provenance = {}, {}
        authority = str(entity.get("authority") or "")
        verified = authority in _TRUSTED_WORLD_AUTHORITIES
        stale = str(entity.get("state") or "") == "STALE" or bool(
            entity.get("stale_after") and str(entity["stale_after"]) <= now
        )
        is_project = entity.get("entity_type") == "PROJECT"
        if not stale and not is_project:
            continue
        kind = "stale_assumption" if stale else "project_state"
        safe = {
            "kind": kind, "entity_id": entity.get("entity_id"),
            "entity_type": entity.get("entity_type"),
            "name": _bounded_text(entity.get("display_name"), 240),
            "state": entity.get("state"), "version": entity.get("version"),
            "attributes": attributes, "stale_after": entity.get("stale_after"),
            "authority": authority, "verified": verified,
            "provenance_hash": content_hash(provenance),
        }
        opportunity = f"world:entity:{entity.get('entity_id')}"
        observations.append(SourceObservation(
            "world", opportunity, opportunity, safe, content_hash(safe),
            authority="SYSTEM_OBSERVATION" if verified else "DATA_ONLY",
            proposed_outcome=(f"Re-verify stale WorldModel assumption: {safe['name']}"
                              if stale and verified else None),
            target_project=safe["name"] if is_project else "",
            alias_key=opportunity, candidate_type=("world_stale_assumption" if stale else "world_project_state"),
            confidence=float(entity.get("confidence") or 0.5),
            candidate_on="always" if stale and verified else "never",
            raw_content_hash=content_hash(entity), opportunity_key=opportunity,
            significance_hint=("verified assumption became stale" if stale else "verified project state changed"),
        ))

    for relation in contradictions:
        try:
            provenance = json.loads(relation.get("provenance_json") or "{}")
        except (TypeError, ValueError):
            provenance = {}
        authority = str(provenance.get("authority") or "")
        verified = provenance.get("verified") is True or authority in _TRUSTED_WORLD_AUTHORITIES
        safe = {
            "kind": "contradiction", "relationship_id": relation.get("relationship_id"),
            "source_entity": relation.get("source_id"), "target_entity": relation.get("target_id"),
            "confidence": relation.get("confidence"), "verified": verified,
            "provenance_hash": content_hash(provenance),
        }
        opportunity = f"world:contradiction:{relation.get('relationship_id')}"
        observations.append(SourceObservation(
            "world", opportunity, opportunity, safe, content_hash(safe),
            authority="SYSTEM_OBSERVATION" if verified else "DATA_ONLY",
            candidate_on="never", raw_content_hash=content_hash(relation),
            opportunity_key=opportunity,
            significance_hint="verified WorldModel facts contradict",
        ))

    for gap in gaps:
        gap_kind = str(gap.get("gap_kind") or "")
        capability_gap = gap_kind == "UNKNOWN_CAPABILITY"
        safe = {
            "kind": "unresolved_unknown", "gap_id": gap.get("gap_id"),
            "gap_kind": gap_kind,
            "gap_domain": "FABRIC_CAPABILITY" if capability_gap else "EPISTEMIC",
            "epistemic_gap": not capability_gap,
            "description": _bounded_text(gap.get("description"), 500),
            "blocking": bool(gap.get("blocking")),
            "subject_type": gap.get("subject_type"), "subject_id": gap.get("subject_id"),
        }
        opportunity = f"world:gap:{gap.get('gap_id')}"
        observations.append(SourceObservation(
            "world", opportunity, opportunity, safe, content_hash(safe),
            authority="SYSTEM_OBSERVATION", candidate_on="never",
            raw_content_hash=content_hash(gap), opportunity_key=opportunity,
            significance_hint=("Fabric capability gap remains distinct from epistemic unknown"
                               if capability_gap else "important epistemic unknown remains unresolved"),
        ))

    for pattern in patterns:
        pattern_kind = str(pattern.get("pattern_kind") or "")
        if "FAIL" not in pattern_kind.upper():
            continue
        try:
            evidence = [str(value)[:500] for value in json.loads(
                pattern.get("evidence_json") or "[]")[:20]]
        except (TypeError, ValueError):
            evidence = []
        safe = {
            "kind": "repeated_failure", "pattern_id": pattern.get("pattern_id"),
            "pattern_kind": pattern_kind,
            "description": _bounded_text(pattern.get("description"), 500),
            "occurrences": int(pattern.get("occurrences") or 0),
            "first_seen": pattern.get("first_seen"), "last_seen": pattern.get("last_seen"),
            "evidence_refs": evidence, "confidence": pattern.get("confidence"),
        }
        opportunity = f"world:pattern:{pattern.get('pattern_id')}"
        candidate_authority = bool(evidence) and float(pattern.get("confidence") or 0.0) >= 0.5
        observations.append(SourceObservation(
            "world", opportunity, opportunity, safe, content_hash(safe),
            authority="SYSTEM_OBSERVATION" if candidate_authority else "DATA_ONLY",
            proposed_outcome=(f"Investigate repeated failure: {safe['description']}"
                              if candidate_authority else None),
            alias_key=opportunity, candidate_type="world_repeated_failure",
            confidence=float(pattern.get("confidence") or 0.5),
            candidate_on="always" if candidate_authority else "never",
            raw_content_hash=content_hash(pattern), opportunity_key=opportunity,
            significance_hint="verified failure pattern repeated",
        ))
    return SourceReadResult("READY", tuple(observations[: limit + fact_limit * 4]))


def read_gmail_source(spec: SourceSpec) -> SourceReadResult:
    del spec
    return SourceReadResult("BLOCKED", reason="Gmail observation is deferred; no configured read capability")


def _existing_source_payload(db: FoundryDatabase, key: str) -> dict[str, Any] | None:
    with db.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE idempotency_key = ? LIMIT 1", (key,),
        ).fetchone()
    return json.loads(row[0]) if row else None


def _previous_source_observation(
    db: FoundryDatabase, item: SourceObservation,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    with db.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT payload_json, provenance_json FROM world_observations "
            "WHERE source_kind = ? ORDER BY observed_at DESC LIMIT 500",
            (item.source_kind,),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row[0] or "{}")
            provenance = json.loads(row[1] or "{}")
        except (TypeError, ValueError):
            continue
        if provenance.get("source_identity") == item.source_identity:
            return payload, provenance
    return None


def _prepare_observation(
    db: FoundryDatabase, item: SourceObservation,
) -> tuple[SourceObservation | None, dict[str, Any]]:
    previous = _previous_source_observation(db, item)
    previous_payload = previous[0] if previous else None
    previous_provenance = previous[1] if previous else {}
    previous_intelligence = (
        previous_payload.get("observation_intelligence", {})
        if isinstance(previous_payload, dict) else {}
    )
    previous_semantic = str(
        previous_intelligence.get("semantic_content_hash")
        or previous_provenance.get("semantic_content_hash")
        or previous_provenance.get("content_hash") or ""
    )
    previous_raw = str(
        previous_intelligence.get("raw_content_hash")
        or previous_provenance.get("raw_content_hash") or ""
    )
    if previous and previous_semantic == item.content_hash:
        noise = bool(item.raw_content_hash and previous_raw
                     and item.raw_content_hash != previous_raw)
        return None, {
            "source_identity": item.source_identity,
            "semantic_change": False, "candidate_worthy": False,
            "noise_suppressed": noise,
            "reason": "formatting_or_metadata_only_change" if noise else "unchanged_replay",
            "opportunity_key": item.opportunity_key,
        }

    before = previous_payload if isinstance(previous_payload, dict) else None
    changed = _changed_fields(before or {}, item.payload) if before is not None else sorted(
        key for key in item.payload if key not in {"raw_content_sha256", "updated_at"}
    )[:30]
    change_type = _change_type(before, item.payload)
    candidate_worthy = bool(item.proposed_outcome) and (
        item.candidate_on == "always"
        or (item.candidate_on == "change_only" and previous is not None)
    )
    record_hash = item.content_hash if previous is None else content_hash({
        "before": previous_semantic, "after": item.content_hash,
    })
    before_values = {key: before.get(key) for key in changed} if before else {}
    after_values = {key: item.payload.get(key) for key in changed}
    intelligence = {
        "stable_identity": item.source_identity,
        "change_type": change_type,
        "changed_fields": changed,
        "before": before_values, "after": after_values,
        "semantic_content_hash": item.content_hash,
        "raw_content_hash": item.raw_content_hash,
        "significance": {
            "meaningful": True, "candidate_worthy": candidate_worthy,
            "reason": item.significance_hint or "source semantic state changed",
            "filter_owner": "SOURCE_ADAPTER",
            "judgment_owner": "A2_SCORING",
        },
        "opportunity_key": item.opportunity_key,
    }
    prepared = replace(
        item, payload={**dict(item.payload), "observation_intelligence": intelligence},
        content_hash=record_hash,
    )
    return prepared, {
        "observation_id": prepared.observation_id,
        "source_identity": item.source_identity,
        "source_kind": item.source_kind,
        "change_type": change_type, "changed_fields": changed,
        "candidate_worthy": candidate_worthy,
        "noise_suppressed": False,
        "reason": intelligence["significance"]["reason"],
        "opportunity_key": item.opportunity_key,
    }


def _record_observation(
    db: FoundryDatabase,
    item: SourceObservation,
    observed_at: str,
    *,
    expires_at: str,
) -> bool:
    store = WorldObservationStore(db)
    if store.get(item.observation_id) is not None:
        return False
    store.record(
        item.source_kind, item.source_ref, dict(item.payload),
        observation_id=item.observation_id, observed_at=observed_at,
        provenance={
            "authority": item.authority, "content_hash": item.content_hash,
            "semantic_content_hash": (
                dict(item.payload).get("observation_intelligence", {}).get(
                    "semantic_content_hash", item.content_hash)
            ),
            "raw_content_hash": item.raw_content_hash,
            "freshness": item.freshness,
            "expires_at": item.expires_at or expires_at,
            "ref": item.source_ref, "source_identity": item.source_identity,
        },
        source_id="v11-a2-observation",
        idempotency_key=f"world.observation_recorded:{item.observation_id}",
    )
    return True


def run_source_observation_cycle(
    db: FoundryDatabase,
    cycle_id: str,
    sources: Sequence[SourceSpec],
    *,
    mission_id: str = "v11-a2-candidate-holding",
    candidate_limit: int = 25,
    candidate_ttl_hours: float = 168,
    now: datetime | str | None = None,
    stop_path: str | Path | None = None,
    readers: Mapping[str, Callable[[SourceSpec, Callable[[], bool]], SourceReadResult]] | None = None,
) -> dict[str, Any]:
    """Run due sources independently and stop at durable candidate truth."""
    current = _utc(now)
    observed_at = _iso(current)
    stop_check = lambda: emergency_stop_reason(db, stop_path) is not None
    if stop_check():
        return {"status": "STOPPED", "reason": emergency_stop_reason(db, stop_path), "sources": []}
    candidate_limit = max(0, min(int(candidate_limit), 100))
    candidate_ttl_hours = float(candidate_ttl_hours)
    if not math.isfinite(candidate_ttl_hours) or not 0 < candidate_ttl_hours <= 2160:
        raise ValueError("candidate_ttl_hours must be finite and between 0 and 2160")
    default_expiry = _iso(current + timedelta(hours=candidate_ttl_hours))
    remaining = candidate_limit
    source_results: list[dict[str, Any]] = []
    reader_map = dict(readers or {})

    for spec in sorted(sources, key=lambda value: value.source_id):
        if stop_check():
            break
        source_run_id = "isr-" + hashlib.sha256(
            f"{cycle_id}|{spec.source_id}".encode("utf-8")
        ).hexdigest()[:24]
        event_key = f"initiative.source_recorded:{cycle_id}:{spec.source_id}"
        existing = _existing_source_payload(db, event_key)
        if existing is not None:
            source_results.append(existing)
            continue
        monitor_id = ensure_monitor(
            db, mission_id, spec.source_kind, spec.source_identity, spec.poll_interval_s,
        )
        due = monitor_due(db, monitor_id, now=current)
        if not due["due"]:
            payload = {
                "cycle_id": cycle_id, "source_id": source_run_id,
                "source_kind": spec.source_kind, "source_identity": spec.source_identity,
                "status": "IDLE", "observations_recorded": 0,
                "candidates_recorded": 0,
                "next_due_at": due["next_due_at"],
            }
            db.append(Event.create(
                "initiative.source_recorded", payload,
                source_type="supervisor", source_id="v11-a2-observation",
                idempotency_key=event_key,
            ))
            source_results.append(payload)
            continue
        monitor_row = db.row("monitors", "monitor_id", monitor_id) or {}

        try:
            if spec.source_kind in reader_map:
                result = reader_map[spec.source_kind](spec, stop_check)
            elif spec.source_kind == "github":
                result = read_github_source(spec, stop_check=stop_check)
            elif spec.source_kind == "web":
                result = read_web_source(spec, stop_check=stop_check)
            elif spec.source_kind == "project":
                result = read_project_source(spec, stop_check=stop_check)
            elif spec.source_kind == "world":
                result = read_world_source(spec, store=WorldSignalStore(db), stop_check=stop_check)
            else:
                result = read_gmail_source(spec)
        except Exception as error:  # isolate one real-source failure
            result = SourceReadResult("UNKNOWN", reason=f"{type(error).__name__}: {_bounded_text(error, 240)}")

        recorded = created = raw_candidates = duplicates = refreshed = 0
        noise_suppressed = unchanged_items = 0
        meaningful_signals: list[dict[str, Any]] = []
        hashes: list[str] = []
        for item in result.observations[:100]:
            if stop_check():
                break
            hashes.append(item.content_hash)
            prepared, assessment = _prepare_observation(db, item)
            if prepared is None:
                unchanged_items += 1
                noise_suppressed += int(bool(assessment.get("noise_suppressed")))
                continue
            observation_recorded = _record_observation(
                db, prepared, observed_at, expires_at=default_expiry,
            )
            recorded += int(observation_recorded)
            if observation_recorded:
                meaningful_signals.append(assessment)
            # Stable observation identity is the change gate. An unchanged
            # source never relinks, refreshes, or regenerates candidate work.
            if (observation_recorded and item.proposed_outcome
                    and assessment.get("candidate_worthy") and remaining > 0):
                raw_candidates += 1
                ingested = ingest_observed_candidate(
                    db, mission_id, observation_id=prepared.observation_id,
                    source_kind=item.source_kind, source_identity=item.source_identity,
                    content_hash=prepared.content_hash, objective=item.proposed_outcome,
                    source_ref=item.source_ref, observed_at=observed_at,
                    authority=item.authority,
                    target_project=item.target_project,
                    alias_key=item.alias_key or item.opportunity_key,
                    candidate_type=item.candidate_type, confidence=item.confidence,
                    expires_at=item.expires_at or default_expiry,
                    trusted_requirements={
                        "required_capability_ids": list(
                            spec.config.get("required_capabilities") or ()),
                        "optional_capability_ids": list(
                            spec.config.get("optional_capabilities") or ()),
                        "required_actions": list(
                            spec.config.get("required_actions") or ()),
                        "optional_actions": list(
                            spec.config.get("optional_actions") or ()),
                        "action_metadata": list(
                            spec.config.get("action_metadata") or ()),
                        "proposed_target": {
                            "kind": item.source_kind,
                            "target_project": item.target_project or None,
                            "resource_id": spec.config.get("resource_id"),
                            "operation": spec.config.get("proposed_operation"),
                        },
                        "project_profile": dict(
                            spec.config.get("project_profile") or {}),
                        "source": f"SourceSpec.config:{spec.source_id}",
                    },
                    evidence=[{
                        "ref": item.source_ref,
                        "authority": item.authority,
                        "opportunity_key": item.opportunity_key,
                        "change_type": assessment.get("change_type"),
                        "changed_fields": assessment.get("changed_fields", []),
                        "required_capabilities": list(
                            spec.config.get("required_capabilities") or ()),
                    }],
                )
                created += int(bool(ingested["created"]))
                duplicates += int(not bool(ingested["created"]))
                refreshed += int(bool(ingested.get("refreshed")))
                remaining -= int(bool(ingested["created"]))
        aggregate_hash = content_hash(sorted(hashes))
        failure = result.status in {"BLOCKED", "UNKNOWN"}
        prior_failures = int(monitor_row.get("consecutive_failures") or 0)
        consecutive_failures = prior_failures + 1 if failure else 0
        base_backoff = max(
            int(spec.poll_interval_s),
            max(1, int(spec.config.get("failure_backoff_s", spec.poll_interval_s))),
        )
        max_backoff = max(
            base_backoff,
            min(int(spec.config.get("max_backoff_s", 86_400)), 604_800),
        )
        delay_s = (
            min(max_backoff, base_backoff * (2 ** min(consecutive_failures - 1, 10)))
            if failure else int(spec.poll_interval_s)
        )
        next_due_at = _iso(current + timedelta(seconds=delay_s))
        prior_hash = monitor_row.get("last_content_hash")
        meaningful_change = result.status == "READY" and (
            bool(recorded) or aggregate_hash != prior_hash
        )
        payload = {
            "cycle_id": cycle_id, "source_id": source_run_id,
            "source_kind": spec.source_kind, "source_identity": spec.source_identity,
            "status": result.status, "observations_recorded": recorded,
            "candidates_recorded": created,
            "raw_candidates": raw_candidates,
            "duplicates_suppressed": duplicates,
            "candidates_refreshed": refreshed,
            "content_hash": aggregate_hash,
            "meaningful_change": meaningful_change,
            "unchanged": result.status == "READY" and not meaningful_change,
            "meaningful_observations": meaningful_signals[:30],
            "candidate_worthy_signals": [
                item for item in meaningful_signals if item.get("candidate_worthy")
            ][:30],
            "unchanged_items": unchanged_items,
            "noise_suppressed": noise_suppressed,
            "consecutive_failures": consecutive_failures,
            "backoff_seconds": delay_s if failure else 0,
            "reason": result.reason or None, "next_due_at": next_due_at,
        }
        source_event = Event.create(
            "initiative.source_recorded", payload,
            source_type="supervisor", source_id="v11-a2-observation",
            idempotency_key=event_key,
        )
        # The monitor clock and source result are one commit. A crash before
        # this transaction leaves the source due; restart replays the already
        # durable observations/candidates and finishes the attempt truthfully.
        record_monitor_attempt(
            db, monitor_id, poll_key=cycle_id,
            items_seen=len(result.observations), candidates_found=created,
            additional_events=(source_event,),
            status=result.status, attempted_at=observed_at,
            observation_at=observed_at if result.observations else None,
            content_hash=aggregate_hash,
            meaningful_change=meaningful_change,
            next_due_at=next_due_at,
            consecutive_failures=consecutive_failures,
            backoff_until=next_due_at if failure else None,
            reason=result.reason,
        )
        source_results.append(payload)
    digest_sources = [{
        "source_kind": item.get("source_kind"),
        "source_identity": item.get("source_identity"),
        "status": item.get("status"),
        "observations_recorded": int(item.get("observations_recorded") or 0),
        "candidate_worthy": len(item.get("candidate_worthy_signals") or ()),
        "noise_suppressed": int(item.get("noise_suppressed") or 0),
        "reason": item.get("reason"),
    } for item in source_results]
    digest = {
        "schema": "jarvis.source-digest.v1", "cycle_id": cycle_id,
        "sources_checked": digest_sources,
        "meaningful_changes": [signal for item in source_results
                               for signal in (item.get("meaningful_observations") or ())],
        "unchanged_sources": [item["source_identity"] for item in digest_sources
                              if item["status"] in {"READY", "IDLE"}
                              and not item["observations_recorded"]],
        "blocked_sources": [{"source_identity": item["source_identity"],
                             "status": item["status"], "reason": item.get("reason")}
                            for item in digest_sources if item["status"] in {"BLOCKED", "UNKNOWN"}],
        "candidate_worthy_signals": [signal for item in source_results
                                     for signal in (item.get("candidate_worthy_signals") or ())],
        "noise_suppressed": sum(int(item.get("noise_suppressed") or 0)
                                for item in source_results),
    }
    return {
        "status": "COMPLETED" if len(source_results) == len(sources) else "STOPPED",
        "sources": source_results,
        "source_digest": digest,
        "observations_recorded": sum(int(item.get("observations_recorded", 0)) for item in source_results),
        "candidates_created": candidate_limit - remaining,
        "raw_candidates": sum(int(item.get("raw_candidates", 0)) for item in source_results),
        "duplicates_suppressed": sum(int(item.get("duplicates_suppressed", 0)) for item in source_results),
        "candidates_refreshed": sum(int(item.get("candidates_refreshed", 0)) for item in source_results),
        "meaningful_changes": sum(int(bool(item.get("meaningful_change"))) for item in source_results),
        "noise_suppressed": digest["noise_suppressed"],
    }
