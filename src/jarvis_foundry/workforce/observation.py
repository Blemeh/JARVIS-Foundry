"""Read-only workforce observation with semantic event coalescing."""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from ..events import Event, EventConflict, canonical_json
from .contracts import WorkPackage, WorkerObservation, WorkerSession
from .state import rebuild_workforce_state

_RELEVANT_EVENT_TYPES = (
    "agent_run.completed",
    "agent_run.failed",
    "agent_run.heartbeat",
    "agent_run.started",
    "mission.blocked",
    "provider.dispatch_attempted",
    "provider.dispatch_failed",
    "provider.dispatch_succeeded",
    "provider.failed",
    "provider.health_recorded",
    "provider.health_sample",
    "provider.outage",
    "provider.route_decided",
    "provider.unavailable",
    "verification.accepted",
    "verification.blocked",
    "verification.failed",
    "verification.not_tested",
    "verification.passed",
    "worker.crashed",
    "worker.route_decided",
    "worker.run_recorded",
    "workforce.observation_recorded",
    "workforce.package_state_changed",
)
_OUTAGE_FAILURES = frozenset({
    "DEGRADED",
    "HEALTH_UNAVAILABLE",
    "OUTAGE",
    "PROVIDER_OUTAGE",
    "PROVIDER_UNKNOWN",
    "PROVIDER_UNAVAILABLE",
    "RATE_LIMITED",
})
_EXTERNAL_FAILURES = frozenset({
    "AUTH_UNKNOWN",
    "AUTH_UNAVAILABLE",
    "BLOCKED_POLICY",
    "BLOCKED_EXTERNAL_DEPENDENCY",
    "CREDENTIAL_MISSING",
    "EXTERNAL_DEPENDENCY",
    "OWNER_RESTRICTED",
    "OWNER_APPROVAL_REQUIRED",
    "PROVIDER_POLICY_BLOCKED",
})


@dataclass(frozen=True, slots=True)
class WorkforceObservationSnapshot:
    """Stable, evidence-derived state for one durable worker session."""

    package_id: str
    session_id: str
    kind: str
    evidence: tuple[str, ...]
    semantic_state: dict[str, Any]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class WorkforceObservationReport:
    scanned: int
    recorded: tuple[WorkerObservation, ...]
    suppressed: int
    snapshots: tuple[WorkforceObservationSnapshot, ...]


class WorkforceObserver:
    """Observe workforce sessions without changing their execution state."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        source_id: str = "workforce-observer",
        stall_after_seconds: int = 300,
    ) -> None:
        self.database = database
        self.source_id = source_id
        self.stall_after_seconds = max(1, int(stall_after_seconds))

    def observe(
        self,
        *,
        package_id: str | None = None,
        now: datetime | str | None = None,
    ) -> WorkforceObservationReport:
        observed_at = _as_utc(now)
        with self.database.read_snapshot():
            state = rebuild_workforce_state(self.database)
            events = self.database.events(event_types=_RELEVANT_EVENT_TYPES)
        sessions = sorted(
            (
                session
                for session in state.sessions.values()
                if package_id is None or session.package_id == package_id
            ),
            key=lambda item: (item.package_id, item.session_id),
        )
        snapshots = tuple(
            self._snapshot(
                state.package(session.package_id).package,
                state.package(session.package_id).status,
                session,
                events,
                observed_at,
            )
            for session in sessions
        )
        recorded: list[WorkerObservation] = []
        suppressed = 0
        for snapshot in snapshots:
            prior = self._prior_observation(snapshot.session_id, events)
            if (
                prior is not None
                and self._observation_fingerprint(prior) == snapshot.fingerprint
            ):
                suppressed += 1
                continue
            predecessor = prior.event_id if prior is not None else "origin"
            transition_id = hashlib.sha256(
                f"{predecessor}:{snapshot.fingerprint}".encode("utf-8")
            ).hexdigest()
            observation = WorkerObservation(
                observation_id=(
                    f"observation:{snapshot.package_id}:{snapshot.session_id}:"
                    f"{transition_id[:20]}"
                ),
                package_id=snapshot.package_id,
                session_id=snapshot.session_id,
                kind=snapshot.kind,
                evidence=snapshot.evidence,
                progress_fingerprint=snapshot.fingerprint,
                observed_at=isoformat(observed_at),
            )
            payload = {
                **observation.to_payload(),
                "semantic_fingerprint": snapshot.fingerprint,
                "semantic_state": snapshot.semantic_state,
            }
            event = Event.create(
                "workforce.observation_recorded",
                payload,
                source_type="supervisor",
                source_id=self.source_id,
                correlation_id=snapshot.package_id,
                idempotency_key=(
                    "workforce.observation_recorded:transition:"
                    f"{snapshot.package_id}:{snapshot.session_id}:{transition_id}"
                ),
                occurred_at=isoformat(observed_at),
            )
            try:
                _sequence, inserted = self.database.append(event)
            except EventConflict:
                inserted = False
            if inserted:
                recorded.append(observation)
            else:
                suppressed += 1
        return WorkforceObservationReport(
            scanned=len(snapshots),
            recorded=tuple(recorded),
            suppressed=suppressed,
            snapshots=snapshots,
        )

    def _snapshot(
        self,
        package: WorkPackage,
        package_status: str,
        session: WorkerSession,
        events: Iterable[Event],
        now: datetime,
    ) -> WorkforceObservationSnapshot:
        related = tuple(
            event for event in events
            if _related(event, package, session)
        )
        worktree = _inspect_worktree(package)
        run = _run_state(session.run_id, related)
        provider = _provider_state(session, related, run)
        verification = _verification_state(
            session.run_id,
            package.mission_id,
            getattr(package, "step_id", None),
            related,
        )
        activity_state = _activity_state(
            session,
            run,
            now=now,
            stall_after_seconds=self.stall_after_seconds,
        )
        flags = _evidence_flags(
            package,
            session,
            related,
            worktree,
            run,
            provider,
            verification,
        )
        kind, evidence = _classify(
            package,
            session,
            run=run,
            provider=provider,
            verification=verification,
            flags=flags,
            worktree=worktree,
            activity_state=activity_state,
        )
        semantic_state = {
            "package_status": package_status,
            "session_status": session.status,
            "session_progress_fingerprint": session.progress_fingerprint,
            "run_status": run["status"],
            "run_failure_class": run["failure_class"],
            "run_failure_count": run["failure_count"],
            "result_fingerprint": _result_fingerprint(session, run),
            "provider": provider,
            "verification": verification,
            "activity_state": activity_state,
            "worktree": worktree,
            "flags": flags,
        }
        fingerprint = hashlib.sha256(
            canonical_json({
                "package_id": package.package_id,
                "session_id": session.session_id,
                "kind": kind,
                "evidence": list(evidence),
                "semantic_state": semantic_state,
            }).encode("utf-8")
        ).hexdigest()
        return WorkforceObservationSnapshot(
            package_id=package.package_id,
            session_id=session.session_id,
            kind=kind,
            evidence=evidence,
            semantic_state=semantic_state,
            fingerprint=fingerprint,
        )

    @staticmethod
    def _prior_observation(
        session_id: str,
        events: Iterable[Event],
    ) -> Event | None:
        for event in reversed(tuple(events)):
            if event.event_type != "workforce.observation_recorded":
                continue
            if str(event.payload.get("session_id") or "") != session_id:
                continue
            return event
        return None

    @staticmethod
    def _observation_fingerprint(event: Event) -> str | None:
        return str(
            event.payload.get("semantic_fingerprint")
            or event.payload.get("progress_fingerprint")
            or ""
        ) or None


def _as_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, str):
        return parse(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _related(
    event: Event,
    package: WorkPackage,
    session: WorkerSession,
) -> bool:
    payload = event.payload
    observed_run = str(payload.get("run_id") or "")
    if observed_run:
        return observed_run == session.run_id
    builder_run = str(payload.get("builder_run_id") or "")
    if builder_run:
        return builder_run == session.run_id
    observed_session = str(payload.get("session_id") or "")
    if observed_session:
        return observed_session == session.session_id
    if (
        event.event_type.startswith("workforce.package_")
        and str(payload.get("package_id") or "") == package.package_id
    ):
        return True
    if event.event_type.startswith("provider.") and not payload.get("run_id"):
        return _provider_identity(payload) == session.provider
    if not package.mission_id:
        return False
    if str(payload.get("mission_id") or "") != package.mission_id:
        return False
    expected_step = str(getattr(package, "step_id", None) or "")
    observed_step = str(payload.get("step_id") or "")
    return not expected_step or not observed_step or observed_step == expected_step


def _run_state(run_id: str, events: Iterable[Event]) -> dict[str, Any]:
    status = "UNKNOWN"
    failure_class = ""
    failure_count = 0
    last_activity_at: str | None = None
    result: dict[str, Any] = {}
    for event in events:
        if str(event.payload.get("run_id") or "") != run_id:
            continue
        if event.event_type == "agent_run.started":
            if status != "COMPLETED":
                status = "RUNNING"
                failure_class = ""
            last_activity_at = event.occurred_at
        elif event.event_type == "agent_run.heartbeat":
            last_activity_at = event.occurred_at
        elif event.event_type == "agent_run.completed":
            if status != "FAILED":
                status = "COMPLETED"
                failure_class = ""
                result = dict(event.payload.get("result") or {})
        elif event.event_type == "agent_run.failed":
            failure_count += 1
            if status != "COMPLETED":
                status = "FAILED"
                failure_class = _normalize_failure(
                    event.payload.get("failure_class")
                )
    return {
        "status": status,
        "failure_class": failure_class,
        "failure_count": failure_count,
        "last_activity_at": last_activity_at,
        "result": result,
    }


def _provider_state(
    session: WorkerSession,
    events: Iterable[Event],
    run: Mapping[str, Any],
) -> dict[str, Any]:
    status = "UNKNOWN"
    active_failure = ""
    failure_count = 0
    failures: set[str] = set()
    selected_provider = session.provider
    selected_model = session.model
    for event in events:
        payload = event.payload
        if not event.event_type.startswith("provider."):
            continue
        if event.event_type == "provider.route_decided":
            status = str(payload.get("status") or "UNKNOWN").upper()
            selected = payload.get("selected")
            if isinstance(selected, Mapping):
                selected_provider = str(
                    selected.get("provider") or selected_provider
                )
                selected_model = str(selected.get("model") or selected_model)
            route_failure = _normalize_failure(payload.get("failure_class"))
            if route_failure:
                active_failure = route_failure
                failures.add(route_failure)
            elif status in {"FALLBACK", "SELECTED"}:
                active_failure = ""
            for item in payload.get("rejected") or ():
                if not isinstance(item, Mapping):
                    continue
                rejected = _normalize_failure(item.get("failure_class"))
                if rejected:
                    failures.add(rejected)
        elif event.event_type == "provider.dispatch_attempted":
            status = "ATTEMPTING"
        elif event.event_type == "provider.dispatch_failed":
            status = "FAILED"
            active_failure = _normalize_failure(payload.get("failure_class"))
            if active_failure:
                failures.add(active_failure)
            failure_count += 1
        elif event.event_type == "provider.dispatch_succeeded":
            status = "READY"
            active_failure = ""
        elif event.event_type in {
            "provider.failed",
            "provider.outage",
            "provider.unavailable",
        }:
            status = "FAILED"
            active_failure = _normalize_failure(
                payload.get("failure_class") or event.event_type.rsplit(".", 1)[-1]
            )
            failures.add(active_failure)
            failure_count += 1
        elif event.event_type in {
            "provider.health_recorded",
            "provider.health_sample",
        }:
            health = str(
                payload.get("health")
                or payload.get("status")
                or payload.get("readiness")
                or "UNKNOWN"
            ).upper()
            status = health
            if health in {"AVAILABLE", "HEALTHY", "READY"}:
                active_failure = ""
    result_error = _normalize_failure(
        (run.get("result") or {}).get("error_class")
    )
    if result_error:
        failures.add(result_error)
        if not active_failure:
            active_failure = result_error
    return {
        "status": status,
        "active_failure": active_failure,
        "failure_count": failure_count,
        "failure_classes": sorted(failures),
        "selected_provider": selected_provider,
        "selected_model": selected_model,
    }


def _provider_identity(payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("provider")
        or payload.get("provider_id")
        or payload.get("provider_name")
        or payload.get("connector_id")
        or ""
    )


def _normalize_failure(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _verification_state(
    run_id: str,
    mission_id: str | None,
    step_id: str | None,
    events: Iterable[Event],
) -> str:
    state = "NONE"
    for event in events:
        if not event.event_type.startswith("verification."):
            continue
        payload = event.payload
        if (
            str(payload.get("builder_run_id") or "") != run_id
            and str(payload.get("run_id") or "") != run_id
            and str(payload.get("mission_id") or "") != (mission_id or "")
        ):
            continue
        if (
            step_id
            and payload.get("step_id")
            and str(payload.get("step_id")) != step_id
        ):
            continue
        if event.event_type == "verification.passed":
            state = "PASS"
        elif event.event_type == "verification.accepted":
            state = "ACCEPTED"
        elif event.event_type == "verification.failed":
            state = "FAIL"
        elif event.event_type == "verification.blocked":
            state = "BLOCKED"
        elif event.event_type == "verification.not_tested":
            state = "NOT_TESTED"
    return state


def _evidence_flags(
    package: WorkPackage,
    session: WorkerSession,
    events: Iterable[Event],
    worktree: Mapping[str, Any],
    run: Mapping[str, Any],
    provider: Mapping[str, Any],
    verification: str,
) -> dict[str, Any]:
    result = {
        **dict(session.result or {}),
        **dict(run.get("result") or {}),
    }
    mappings = tuple(
        item
        for value in (result, *(event.payload for event in events))
        for item in _walk_mappings(value)
    )
    goal_drift = any(
        _mapping_truthy(item, {
            "goal_drift",
            "goal_drift_detected",
            "drifted_from_goal",
            "objective_mismatch",
        })
        or item.get("goal_alignment") is False
        or item.get("objective_alignment") is False
        for item in mappings
    )
    test_skipped = any(_tests_skipped(item) for item in mappings)
    external_blocker = any(
        _mapping_truthy(item, {
            "blocked_by_external",
            "external_blocker",
            "owner_approval_required",
        })
        for item in mappings
    )
    negative_evidence = any(_negative_evidence(item) for item in mappings)
    missing_artifacts = _missing_expected_artifacts(package, worktree)
    completed = (
        session.status == "COMPLETED"
        or str(run.get("status")) == "COMPLETED"
    )
    terminal_failure = (
        session.status in {"BLOCKED", "FAILED", "TERMINATED"}
        or str(run.get("status")) == "FAILED"
    )
    crash_partial = any(_crash_has_partial_state(event) for event in events)
    failure_count = (
        int(run.get("failure_count") or 0)
        + int(provider.get("failure_count") or 0)
        + sum(event.event_type == "worker.crashed" for event in events)
        + sum(event.event_type == "verification.failed" for event in events)
    )
    result_verdict = str(result.get("verdict") or "").upper()
    result_status = str(result.get("status") or "").upper()
    return {
        "analysis_only": bool(
            _as_int(result.get("analysis_rounds")) >= 3
            and not worktree.get("changed_files")
        ),
        "external_blocker": bool(
            external_blocker
            or provider.get("active_failure") in _EXTERNAL_FAILURES
        ),
        "failure_count": failure_count,
        "false_completion": bool(
            completed
            and (
                verification in {"BLOCKED", "FAIL", "NOT_TESTED"}
                or result_verdict in {"BLOCKED", "FAIL", "NOT_TESTED"}
                or result_status in {"BLOCKED", "FAILED"}
                or negative_evidence
                or missing_artifacts
            )
        ),
        "goal_drift": goal_drift,
        "missing_expected_artifacts": list(missing_artifacts),
        "mandatory_tests_skipped": bool(package.required_tests and test_skipped),
        "partial_mutation": bool(
            crash_partial
            or (
                terminal_failure
                and bool(worktree.get("changed_files"))
            )
        ),
        "repeated_failure": bool(
            failure_count >= 2
            and not (
                str(run.get("status")) == "COMPLETED"
                and verification in {"ACCEPTED", "PASS"}
            )
        ),
    }


def _result_fingerprint(
    session: WorkerSession,
    run: Mapping[str, Any],
) -> str:
    merged = {
        **dict(session.result or {}),
        **dict(run.get("result") or {}),
    }
    semantic_keys = (
        "analysis_rounds",
        "artifacts",
        "blockers",
        "claimed_outcome",
        "error_class",
        "evidence",
        "external_blocker",
        "files_changed",
        "goal_alignment",
        "goal_drift",
        "objective_alignment",
        "required_tests_skipped",
        "status",
        "test_results",
        "tests_run",
        "tests_skipped",
        "verdict",
    )
    semantic = {
        key: merged[key]
        for key in semantic_keys
        if key in merged
    }
    return hashlib.sha256(
        canonical_json(semantic).encode("utf-8")
    ).hexdigest()


def _crash_has_partial_state(event: Event) -> bool:
    if event.event_type != "worker.crashed":
        return False
    partial = event.payload.get("partial_state")
    return isinstance(partial, Mapping) and bool(partial.get("changed_files"))


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_mappings(child)


def _mapping_truthy(
    value: Mapping[str, Any],
    keys: set[str],
) -> bool:
    return any(bool(value.get(key)) for key in keys)


def _tests_skipped(value: Mapping[str, Any]) -> bool:
    if _mapping_truthy(
        value,
        {"mandatory_tests_skipped", "required_tests_skipped", "tests_skipped"},
    ):
        return True
    if value.get("tests_run") is False:
        return True
    status = str(
        value.get("test_status")
        or value.get("conclusion")
        or value.get("outcome")
        or value.get("verdict")
        or ""
    ).upper()
    return status in {"NOT_RUN", "NOT_TESTED", "SKIP", "SKIPPED"}


def _negative_evidence(value: Mapping[str, Any]) -> bool:
    if value.get("agree") is False:
        return True
    for key in (
        "artifact_hash_mismatches",
        "containment_violations",
        "mismatches",
        "missing_artifacts",
        "unrecorded_mutations",
    ):
        if value.get(key):
            return True
    return False


def _missing_expected_artifacts(
    package: WorkPackage,
    worktree: Mapping[str, Any],
) -> tuple[str, ...]:
    root_value = worktree.get("worktree_root")
    if not root_value:
        return ()
    root = Path(str(root_value))
    missing: list[str] = []
    for raw in package.expected_artifacts:
        artifact = Path(raw).expanduser()
        target = (
            artifact.resolve(strict=False)
            if artifact.is_absolute()
            else (root / artifact).resolve(strict=False)
        )
        if not target.exists():
            missing.append(str(raw))
    return tuple(sorted(missing))


def _activity_state(
    session: WorkerSession,
    run: Mapping[str, Any],
    *,
    now: datetime,
    stall_after_seconds: int,
) -> str:
    if (
        session.status in {"BLOCKED", "COMPLETED", "FAILED", "STOPPED", "TERMINATED"}
        or run.get("status") in {"COMPLETED", "FAILED"}
    ):
        return "TERMINAL"
    if session.status == "STALLED":
        return "STALE"
    candidates = (
        session.heartbeat_at,
        run.get("last_activity_at"),
        session.started_at,
    )
    parsed = [
        timestamp
        for value in candidates
        if (timestamp := _parse_time(value)) is not None
    ]
    if not parsed:
        return "UNKNOWN"
    latest = max(parsed)
    return (
        "STALE"
        if now - latest > timedelta(seconds=stall_after_seconds)
        else "FRESH"
    )


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse(str(value))
    except (TypeError, ValueError):
        return None


def _classify(
    package: WorkPackage,
    session: WorkerSession,
    *,
    run: Mapping[str, Any],
    provider: Mapping[str, Any],
    verification: str,
    flags: Mapping[str, Any],
    worktree: Mapping[str, Any],
    activity_state: str,
) -> tuple[str, tuple[str, ...]]:
    if worktree["repository_mismatch"]:
        return "WRONG_REPOSITORY", (
            "declared package path or source commit does not match the observed repository",
        )
    if worktree["branch_mismatch"]:
        return "WRONG_BRANCH", (
            f"observed branch {worktree['branch']} does not preserve "
            f"{package.branch_name} from source {package.source_sha}",
        )
    if worktree["scope_violations"]:
        return "SCOPE_ESCAPE", tuple(worktree["scope_violations"])
    if flags["goal_drift"]:
        return "GOAL_DRIFT", (
            "durable structured evidence reports a goal-alignment drift signal",
        )
    if flags["partial_mutation"]:
        return "PARTIAL_MUTATION", (
            "terminal worker failure left observable worktree mutations",
        )
    if flags["mandatory_tests_skipped"]:
        return "MANDATORY_TESTS_SKIPPED", (
            "required tests are explicitly marked skipped in durable worker evidence",
        )
    if flags["false_completion"]:
        reasons = ["worker completion is contradicted by durable evidence"]
        if flags["missing_expected_artifacts"]:
            reasons.append(
                "missing expected artifacts: "
                + ", ".join(flags["missing_expected_artifacts"])
            )
        return "FALSE_COMPLETION", tuple(reasons)
    if (
        provider.get("active_failure") in _OUTAGE_FAILURES
        or run.get("failure_class") in _OUTAGE_FAILURES
    ):
        return "PROVIDER_OUTAGE", (
            "provider evidence reports "
            f"{provider.get('active_failure') or run.get('failure_class')}",
        )
    if flags["external_blocker"]:
        return "EXTERNAL_BLOCKER", (
            "durable structured evidence records an external dependency blocker",
        )
    if flags["repeated_failure"]:
        return "REPEATED_FAILURE", (
            f"{flags['failure_count']} durable failure signals remain unresolved",
        )
    if (
        session.status in {"BLOCKED", "FAILED", "TERMINATED"}
        or run["status"] == "FAILED"
    ):
        return "SESSION_DEAD", (
            "worker session ended without a successful terminal result",
        )
    if flags["analysis_only"]:
        return "ENDLESS_ANALYSIS", (
            "worker reports repeated analysis without an observable worktree delta",
        )
    if activity_state == "STALE":
        return "NO_PROGRESS_STALL", (
            "durable worker activity is stale beyond the configured bound",
        )
    return "HEALTHY_PROGRESS", (
        f"healthy session at {str(worktree.get('head') or 'unknown')[:12]} "
        f"with {len(worktree.get('changed_files') or ())} changed paths",
    )


def _inspect_worktree(package: WorkPackage) -> dict[str, Any]:
    expected = Path(package.worktree_path).expanduser().resolve(strict=False)
    result: dict[str, Any] = {
        "branch": None,
        "branch_mismatch": False,
        "change_fingerprint": None,
        "changed_files": [],
        "head": None,
        "repository_mismatch": False,
        "scope_violations": [],
        "source_is_ancestor": None,
        "worktree_root": None,
    }
    try:
        root = Path(_git(expected, "rev-parse", "--show-toplevel")).resolve()
        branch = _git(root, "branch", "--show-current") or "DETACHED"
        head = _git(root, "rev-parse", "HEAD")
        source_present = _git_check(
            root,
            "cat-file",
            "-e",
            f"{package.source_sha}^{{commit}}",
        )
        source_is_ancestor = (
            _git_check(
                root,
                "merge-base",
                "--is-ancestor",
                package.source_sha,
                head,
            )
            if source_present
            else False
        )
        changed_entries = _changed_entries(root)
        changed = tuple(
            sorted({entry["path"] for entry in changed_entries})
        )
    except (OSError, RuntimeError):
        result["repository_mismatch"] = True
        return result
    result["worktree_root"] = str(root)
    result["branch"] = branch
    result["head"] = head
    result["changed_files"] = list(changed)
    result["change_fingerprint"] = _change_fingerprint(root, changed_entries)
    result["source_is_ancestor"] = source_is_ancestor
    result["repository_mismatch"] = root != expected or not source_present
    result["branch_mismatch"] = (
        branch != package.branch_name or source_is_ancestor is not True
    )
    result["scope_violations"] = _scope_violations(package, root, changed)
    return result


def _git(root: Path, *args: str) -> str:
    return _git_result(root, *args).stdout.strip()


def _git_raw(root: Path, *args: str) -> str:
    return _git_result(root, *args).stdout


def _git_check(root: Path, *args: str) -> bool:
    from ..platform.windows_subprocess import headless_run_kwargs

    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        **headless_run_kwargs(),
    )
    if result.returncode in {0, 1}:
        return result.returncode == 0
    raise RuntimeError(result.stderr.strip() or "Git observation failed")


def _git_result(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    from ..platform.windows_subprocess import headless_run_kwargs

    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        **headless_run_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Git observation failed")
    return result


def _changed_entries(root: Path) -> tuple[dict[str, str], ...]:
    fields = _git_raw(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).split("\0")
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record or len(record) < 4:
            continue
        status = record[:2]
        path = record[3:].replace("\\", "/")
        if path:
            entries.append({"path": path, "status": status})
        if ("R" in status or "C" in status) and index < len(fields):
            source = fields[index].replace("\\", "/")
            index += 1
            if source:
                entries.append({
                    "path": source,
                    "status": f"{status}:source",
                })
    return tuple(sorted(
        entries,
        key=lambda item: (item["path"], item["status"]),
    ))


def _change_fingerprint(
    root: Path,
    entries: Iterable[Mapping[str, str]],
) -> str:
    observed: list[dict[str, str]] = []
    for entry in entries:
        relative = entry["path"]
        target = root / relative
        if target.exists() or target.is_symlink():
            try:
                digest = _git(root, "hash-object", "--no-filters", "--", relative)
            except RuntimeError:
                digest = "UNREADABLE"
        else:
            digest = "MISSING"
        observed.append({
            "path": relative,
            "status": entry["status"],
            "content": digest,
        })
    return hashlib.sha256(
        canonical_json(observed).encode("utf-8")
    ).hexdigest()


def _scope_violations(
    package: WorkPackage,
    root: Path,
    changed_files: Iterable[str],
) -> list[str]:
    allowed = _roots(root, package.allowed_paths)
    forbidden = _roots(root, package.forbidden_paths)
    violations: list[str] = []
    for relative in changed_files:
        path = (root / relative).resolve(strict=False)
        if not any(_within(path, candidate) for candidate in allowed):
            violations.append(f"{relative} escapes declared mutable scope")
        elif any(_within(path, candidate) for candidate in forbidden):
            violations.append(f"{relative} touches a forbidden package path")
    return violations


def _roots(root: Path, values: Iterable[str]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for raw in values:
        value = Path(raw).expanduser()
        resolved.append(
            value.resolve(strict=False)
            if value.is_absolute()
            else (root / value).resolve(strict=False)
        )
    return tuple(resolved)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "WorkforceObservationReport",
    "WorkforceObservationSnapshot",
    "WorkforceObserver",
]
