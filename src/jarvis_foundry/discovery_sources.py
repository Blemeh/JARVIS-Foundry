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
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

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

    def __post_init__(self) -> None:
        if self.source_kind in EXTERNAL_SOURCE_KINDS and self.authority != "DATA_ONLY":
            raise ValueError("external observations must have DATA_ONLY authority")
        digest = self.content_hash
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("content_hash must be sha256:<64 lowercase hex>")

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
    """Read repository metadata, open issues, and recent CI with fixed bounds."""
    repo = str(spec.config.get("repo") or spec.source_identity).strip()
    if not _REPO.fullmatch(repo):
        return SourceReadResult("BLOCKED", reason="invalid configured GitHub repository")
    limit = max(1, min(int(spec.config.get("max_items", 10)), 20))
    reader = connector or GitHubResearchConnector()
    requests = (
        ("github.repo.read", f"/repos/{repo}"),
        ("github.issue.read", f"/repos/{repo}/issues?state=open&per_page={limit}"),
        ("github.check.read", f"/repos/{repo}/actions/runs?per_page={limit}"),
    )
    documents: list[tuple[str, str, Any]] = []
    failures: list[str] = []
    for action, endpoint in requests:
        if stop_check and stop_check():
            return SourceReadResult("BLOCKED", reason="stop requested")
        attempt, document = reader.read_api(action, endpoint)
        if attempt.outcome != "COMPLETED" or document is None:
            failures.append(str(attempt.error_code or attempt.outcome))
            continue
        documents.append((action, endpoint, document))
    if not documents:
        return SourceReadResult(
            "UNKNOWN", reason="GitHub reads unavailable: " + ",".join(failures[:3]),
        )

    observations: list[SourceObservation] = []
    for action, endpoint, document in documents:
        if action == "github.issue.read":
            records = document if isinstance(document, list) else []
            for item in records[:limit]:
                if not isinstance(item, dict) or "pull_request" in item:
                    continue
                number = item.get("number")
                title = _bounded_text(item.get("title"), 240)
                if not number or not title:
                    continue
                safe = {
                    "kind": "issue", "repo": repo, "number": number,
                    "title": title, "state": _bounded_text(item.get("state"), 20),
                    "updated_at": _bounded_text(item.get("updated_at"), 40),
                    "url": _bounded_text(item.get("html_url"), 500),
                }
                digest = content_hash(safe)
                observations.append(SourceObservation(
                    "github", f"github:{repo}:issue:{number}", safe["url"] or endpoint,
                    safe, digest, proposed_outcome=f"Evaluate and resolve GitHub issue #{number}: {title}",
                    target_project=repo, alias_key=f"github:{repo}:issue:{number}",
                    candidate_type="github_issue", confidence=0.8,
                ))
        elif action == "github.check.read":
            records = document.get("workflow_runs", []) if isinstance(document, dict) else []
            for item in records[:limit]:
                if not isinstance(item, dict) or item.get("conclusion") not in {"failure", "cancelled", "timed_out"}:
                    continue
                run_id = item.get("id")
                name = _bounded_text(item.get("name"), 160) or "workflow"
                safe = {
                    "kind": "workflow_run", "repo": repo, "run_id": run_id,
                    "name": name, "conclusion": item.get("conclusion"),
                    "head_sha": _bounded_text(item.get("head_sha"), 64),
                    "created_at": _bounded_text(item.get("created_at"), 40),
                    "updated_at": _bounded_text(item.get("updated_at"), 40),
                    "url": _bounded_text(item.get("html_url"), 500),
                }
                digest = content_hash(safe)
                observations.append(SourceObservation(
                    "github", f"github:{repo}:workflow:{run_id}", safe["url"] or endpoint,
                    safe, digest,
                    proposed_outcome=f"Investigate failed GitHub workflow {name}",
                    target_project=repo, alias_key=f"github:{repo}:workflow:{name.lower()}",
                    candidate_type="github_workflow_failure", confidence=0.75,
                    expires_at=_historical_expiry(
                        item.get("updated_at") or item.get("created_at")),
                ))
        else:
            safe = {
                "kind": "repository", "repo": repo,
                "default_branch": _bounded_text(document.get("default_branch") if isinstance(document, dict) else "", 100),
                "updated_at": _bounded_text(document.get("updated_at") if isinstance(document, dict) else "", 40),
                "open_issues_count": document.get("open_issues_count") if isinstance(document, dict) else None,
            }
            observations.append(SourceObservation(
                "github", f"github:{repo}:repository", f"https://github.com/{repo}",
                safe, content_hash(safe),
            ))
    return SourceReadResult(
        "READY", tuple(observations[: limit * 2 + 1]),
        reason=("partial GitHub read failure" if failures else ""),
    )


def read_web_source(
    spec: SourceSpec,
    *,
    browser: PlaywrightBrowserConnector | None = None,
    extractor: TrafilaturaExtractor | None = None,
    stop_check: Callable[[], bool] | None = None,
) -> SourceReadResult:
    """Read only explicitly configured URLs; page prose cannot set authority."""
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
        safe = {
            "kind": "web_watch", "url": extraction.page.final_url,
            "title": _bounded_text(extraction.metadata.get("title"), 240),
            "safe_excerpt": _bounded_text(extraction.text, 500),
            "source_sha256": extraction.page.source_sha256,
            "safe_text_sha256": extraction.safe_text_sha256,
            "authority": "DATA_ONLY",
        }
        digest = content_hash(safe)
        configured = _bounded_text(configured_outcomes.get(url), 300) or None
        observations.append(SourceObservation(
            "web", f"web:{url}", extraction.page.final_url, safe, digest,
            proposed_outcome=configured, target_project=target_project,
            alias_key=str(configured_aliases.get(url) or "") or None,
            candidate_type="web_watch", confidence=0.6 if configured else None,
        ))
    status = "READY" if observations else "UNKNOWN"
    return SourceReadResult(status, tuple(observations), ",".join(failures[:3]))


def _git_drift(root: Path, limit: int) -> tuple[list[str], str | None]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=no"],
            capture_output=True, check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], None
    if completed.returncode != 0:
        return [], None
    changed = [item.decode("utf-8", errors="replace")[:500]
               for item in completed.stdout.split(b"\x00") if item][:limit]
    if not changed:
        return [], None
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
    return changed, content_hash({
        "status": sorted(changed), "file_states": file_states,
    })


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
    changed, drift_hash = _git_drift(root, min(max_files, 100))
    if changed:
        safe = {
            "kind": "working_tree_drift", "project_id": project_id,
            "changed": sorted(changed), "state_hash": drift_hash,
        }
        observations.append(SourceObservation(
            "project", f"project:{project_id}:git-drift", str(root), safe,
            content_hash(safe), proposed_outcome=f"Review meaningful working-tree drift in {project_id}",
            target_project=project_id, alias_key=f"project:{project_id}:working-tree-drift",
            candidate_type="project_drift", confidence=0.7,
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
                observations.append(SourceObservation(
                    "project", f"project:{project_id}:todo:{relative}:{line_number}",
                    f"{root}#{relative}:{line_number}", safe, content_hash(safe),
                    proposed_outcome=f"Resolve tracked {match.group(1).upper()} in {relative}: {note}",
                    target_project=project_id, alias_key=f"project:{project_id}:todo:{alias}",
                    candidate_type="tracked_work", confidence=0.65,
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
    for signal in store.active(limit=limit):
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
        ))
    return SourceReadResult("READY", tuple(observations))


def read_gmail_source(spec: SourceSpec) -> SourceReadResult:
    del spec
    return SourceReadResult("BLOCKED", reason="Gmail observation is deferred; no configured read capability")


def _existing_source_payload(db: FoundryDatabase, key: str) -> dict[str, Any] | None:
    with db.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE idempotency_key = ? LIMIT 1", (key,),
        ).fetchone()
    return json.loads(row[0]) if row else None


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

        recorded = created = raw_candidates = duplicates = 0
        hashes: list[str] = []
        for item in result.observations[:100]:
            if stop_check():
                break
            hashes.append(item.content_hash)
            recorded += int(_record_observation(
                db, item, observed_at, expires_at=default_expiry,
            ))
            if item.proposed_outcome and remaining > 0:
                raw_candidates += 1
                ingested = ingest_observed_candidate(
                    db, mission_id, observation_id=item.observation_id,
                    source_kind=item.source_kind, source_identity=item.source_identity,
                    content_hash=item.content_hash, objective=item.proposed_outcome,
                    source_ref=item.source_ref, observed_at=observed_at,
                    authority=item.authority,
                    target_project=item.target_project, alias_key=item.alias_key,
                    candidate_type=item.candidate_type, confidence=item.confidence,
                    expires_at=item.expires_at or default_expiry,
                    evidence=[{
                        "ref": item.source_ref,
                        "authority": item.authority,
                        "required_capabilities": list(
                            spec.config.get("required_capabilities") or ()),
                    }],
                )
                created += int(bool(ingested["created"]))
                duplicates += int(not bool(ingested["created"]))
                remaining -= int(bool(ingested["created"]))
        next_due_at = _iso(current + timedelta(seconds=spec.poll_interval_s))
        payload = {
            "cycle_id": cycle_id, "source_id": source_run_id,
            "source_kind": spec.source_kind, "source_identity": spec.source_identity,
            "status": result.status, "observations_recorded": recorded,
            "candidates_recorded": created,
            "raw_candidates": raw_candidates,
            "duplicates_suppressed": duplicates,
            "content_hash": content_hash(sorted(hashes)) if hashes else None,
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
        )
        source_results.append(payload)
    return {
        "status": "COMPLETED" if len(source_results) == len(sources) else "STOPPED",
        "sources": source_results,
        "observations_recorded": sum(int(item.get("observations_recorded", 0)) for item in source_results),
        "candidates_created": candidate_limit - remaining,
        "raw_candidates": sum(int(item.get("raw_candidates", 0)) for item in source_results),
        "duplicates_suppressed": sum(int(item.get("duplicates_suppressed", 0)) for item in source_results),
    }
