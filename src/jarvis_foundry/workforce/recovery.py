"""Restart audit and bounded reconciliation for V13 workforce sessions.

This module is deliberately subordinate to the existing workforce gateway,
process controller, verifier, effect journal, and owner controls.  Auditing is
read-only.  Reconciliation can only project durable truth for a process that
was already spawned and registered by the existing gateway/controller path.
It never prepares worktrees, launches workers, schedules work, selects a
provider, runs a MissionLoop, or removes partial work.
"""
from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..clock import parse, utc_now
from ..database import FoundryDatabase
from ..events import Event
from ..supervisor.worker import (
    WorkerController,
    WorkerStatus,
    load_adapter_pids,
    worker_lease_key,
)
from .contracts import (
    TERMINAL_SESSION_STATUSES,
    ResourceClaim,
    WorkPackage,
    WorkerSession,
)
from .gateway import GatewayResult, WorkforceGateway, WorkforceGatewayError
from .observation import WorkforceObservationReport, WorkforceObserver
from .state import (
    WorkforceState,
    _reduce_workforce_events,
    _stop_reason,
)
from .worktrees import (
    WorkforceResourceError,
    WorktreeManager,
    WorktreePreparation,
)

_SESSION_EVENTS = frozenset({
    "workforce.session_bound",
    "workforce.session_completed",
    "workforce.session_heartbeat",
    "workforce.session_started",
    "workforce.session_state_changed",
    "workforce.result_linked",
})
_CLAIM_EVENTS = frozenset({
    "workforce.resource_bound",
    "workforce.resource_claimed",
    "workforce.resource_expired",
    "workforce.resource_released",
    "workforce.resource_renewed",
})
_CANONICAL_EVENT_TYPES = (
    "agent_run.completed",
    "agent_run.failed",
    "agent_run.heartbeat",
    "agent_run.started",
    "effect.frozen",
    "effect.reconciled",
    "effect.terminal_truth",
    "provider.effect_reconciled",
    "provider.effect_reconciliation_required",
    "system.action_completed",
    "system.action_failed",
    "system.action_started",
    "verification.accepted",
    "verification.blocked",
    "verification.failed",
    "verification.not_tested",
    "verification.passed",
    "verification.started",
)
_EFFECT_OUTCOME_EVENTS = frozenset({
    "effect.terminal_truth",
    "provider.effect_reconciled",
    "system.action_completed",
    "system.action_failed",
})
_BINDING_RESULT_KEYS = frozenset({
    "claim_id",
    "claim_lease_expires_at",
    "mission_step_contract_hash",
    "mission_step_contract_id",
    "package_hash",
    "pid",
    "resource_id",
    "worker_lease_key",
})
_UNSAFE_RECONCILIATION_CODES = frozenset({
    "DUPLICATE_ACTIVE_CLAIM",
    "DUPLICATE_ACTIVE_SESSION",
    "DUPLICATE_SESSION_RUN_ID",
    "DUPLICATE_WORKTREE_IDENTITY",
    "PROCESS_IDENTITY_MISMATCH",
    "WORKTREE_BASE_NOT_ANCESTOR",
    "WORKTREE_BRANCH_MISMATCH",
    "WORKTREE_REPOSITORY_MISMATCH",
    "WORKTREE_ROOT_MISMATCH",
    "WORKTREE_UNAVAILABLE",
})


@dataclass(frozen=True, slots=True)
class RecoveryIssue:
    code: str
    subject_type: str
    subject_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class LeaseRecoveryState:
    lease_key: str
    owner_id: str
    expires_at: str
    heartbeat_at: str
    expired: bool


@dataclass(frozen=True, slots=True)
class WorktreeRecoveryState:
    package_id: str
    resource_id: str
    source_repository: str
    worktree_path: str
    branch_name: str
    base_sha: str
    exists: bool
    worktree_root: str | None
    repository_common_dir: str | None
    expected_common_dir: str | None
    observed_branch: str | None
    head_sha: str | None
    base_present: bool
    base_is_ancestor: bool
    dirty: bool
    changed_paths: tuple[str, ...]
    valid: bool
    preserve_required: bool
    issues: tuple[RecoveryIssue, ...]


@dataclass(frozen=True, slots=True)
class VerifierRecoveryState:
    verdict_id: str
    mission_id: str
    subject_id: str
    builder_run_id: str
    verifier_run_id: str | None
    status: str
    accepted: bool
    acceptance_event_id: str | None
    reason: str | None
    findings: tuple[str, ...]
    missing_proof: tuple[str, ...]
    scope_violations: tuple[str, ...]

    @property
    def pending(self) -> bool:
        return self.status == "STARTED" or (
            self.status == "PASS" and not self.accepted
        )


@dataclass(frozen=True, slots=True)
class EffectRecoveryState:
    run_id: str
    action_key: str
    provider: str
    status: str
    pending: bool
    reconciled: bool
    requirement_event_ids: tuple[str, ...]
    resolution_event_ids: tuple[str, ...]
    outcome_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionRecoveryState:
    package_id: str
    session: WorkerSession
    category: str
    process_backed: bool
    process_identity_valid: bool
    process_alive: bool | None
    pid: int | None
    process_record: dict[str, Any]
    agent_run_status: str | None
    agent_run_failure_class: str | None
    agent_result: dict[str, Any]
    partial_result: bool
    claim_ids: tuple[str, ...]
    leases: tuple[LeaseRecoveryState, ...]
    verifiers: tuple[VerifierRecoveryState, ...]
    effects: tuple[EffectRecoveryState, ...]
    issues: tuple[RecoveryIssue, ...]


@dataclass(frozen=True, slots=True)
class PackageRecoveryState:
    package: WorkPackage
    status: str
    version: int
    reason: str
    stop_reason: str | None
    worktrees: tuple[WorktreeRecoveryState, ...]
    sessions: tuple[SessionRecoveryState, ...]
    claims: tuple[ResourceClaim, ...]
    leases: tuple[LeaseRecoveryState, ...]
    verifiers: tuple[VerifierRecoveryState, ...]
    effects: tuple[EffectRecoveryState, ...]
    issues: tuple[RecoveryIssue, ...]


@dataclass(frozen=True, slots=True)
class WorkforceRecoveryReport:
    packages: tuple[PackageRecoveryState, ...]
    issues: tuple[RecoveryIssue, ...]
    audited_at: str

    def package(self, package_id: str) -> PackageRecoveryState:
        for item in self.packages:
            if item.package.package_id == package_id:
                return item
        raise KeyError(package_id)

    @property
    def duplicate_issues(self) -> tuple[RecoveryIssue, ...]:
        return tuple(
            issue for issue in self.issues
            if issue.code.startswith("DUPLICATE_")
        )


@dataclass(frozen=True, slots=True)
class ProcessReconciliation:
    package_id: str
    session_id: str
    run_id: str
    outcome: str
    detail: str
    before_category: str
    after_category: str | None = None


@dataclass(frozen=True, slots=True)
class WorkforceReconciliationReport:
    before: WorkforceRecoveryReport
    actions: tuple[ProcessReconciliation, ...]
    after: WorkforceRecoveryReport


@dataclass(frozen=True, slots=True)
class _AuditRows:
    state: WorkforceState
    selected_ids: tuple[str, ...]
    events: tuple[Event, ...]
    agent_runs: dict[str, dict[str, Any]]
    leases: tuple[dict[str, Any], ...]
    verdicts: tuple[dict[str, Any], ...]
    system_actions: dict[str, dict[str, Any]]


class WorkforceRecovery:
    """Reconstruct and reconcile workforce truth after a process restart."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        stop_path: str | Path | None = None,
        gateway: WorkforceGateway | None = None,
        workers: WorkerController | None = None,
        worktrees: WorktreeManager | None = None,
        observer: WorkforceObserver | None = None,
    ) -> None:
        self.database = database
        self.stop_path = (
            Path(stop_path).expanduser().resolve()
            if stop_path is not None
            else None
        )
        self.gateway = gateway or WorkforceGateway(
            database,
            stop_path=self.stop_path,
            source_id="workforce-recovery",
        )
        self.workers = workers or WorkerController(database)
        self.worktrees = worktrees or WorktreeManager(
            database,
            stop_path=self.stop_path,
            source_id="workforce-recovery",
        )
        self.observer = observer or WorkforceObserver(
            database,
            source_id="workforce-recovery-observer",
        )
        for name, value in (
            ("gateway", self.gateway),
            ("workers", self.workers),
            ("worktrees", self.worktrees),
            ("observer", self.observer),
        ):
            owned = getattr(value, "database", None)
            if owned is None or Path(owned.path) != Path(database.path):
                raise ValueError(f"{name} must use the recovery database")

    def observe(
        self,
        *,
        package_id: str | None = None,
        now: datetime | str | None = None,
    ) -> WorkforceObservationReport:
        """Run the existing read-oriented observer even while STOP is active."""

        return self.observer.observe(package_id=package_id, now=now)

    def audit(
        self,
        *,
        package_id: str | None = None,
        now: datetime | str | None = None,
    ) -> WorkforceRecoveryReport:
        """Rebuild restart truth without changing the ledger or filesystem."""

        observed_at = parse(now) if isinstance(now, str) else (now or utc_now())
        rows = self._read_rows(package_id)
        state = rows.state
        selected_ids = rows.selected_ids
        sessions, session_parse_issues = _reconstruct_sessions(
            rows.events,
            selected_ids,
        )
        claims, claim_parse_issues = _reconstruct_claims(
            rows.events,
            selected_ids,
        )
        preparations, preparation_parse_issues = _preparations(
            rows.events,
            selected_ids,
        )
        accepted = _accepted_verdicts(rows.events)
        verifiers = _verifier_states(rows.verdicts, accepted)
        effects = _effect_states(
            rows.events,
            rows.system_actions,
            selected_ids=selected_ids,
            sessions=sessions,
        )
        lease_states = tuple(
            _lease_state(row, observed_at)
            for row in rows.leases
        )
        pid_records = load_adapter_pids(self.workers.pid_table_path())

        issues: list[RecoveryIssue] = [
            *session_parse_issues,
            *claim_parse_issues,
            *preparation_parse_issues,
        ]
        issues.extend(_duplicate_identity_issues(
            rows.events,
            selected_ids=selected_ids,
            sessions=sessions,
            claims=claims,
            preparations=preparations,
            effects=effects,
        ))

        worktree_states: dict[str, tuple[WorktreeRecoveryState, ...]] = {}
        for current_id in selected_ids:
            package = state.package(current_id).package
            package_preparations = tuple(
                item for item in preparations
                if item.package_id == current_id
            )
            audited = tuple(
                self._audit_worktree(package, item)
                for item in package_preparations
            )
            worktree_states[current_id] = audited
            for item in audited:
                issues.extend(item.issues)
            if (
                not package_preparations
                and state.package(current_id).status != "PLANNED"
            ):
                issues.append(RecoveryIssue(
                    "MISSING_WORKTREE_PREPARATION",
                    "package",
                    current_id,
                    "non-planned package has no durable worktree preparation",
                ))

        session_states: dict[str, list[SessionRecoveryState]] = defaultdict(list)
        for session in sorted(
            sessions.values(),
            key=lambda item: (item.package_id, item.attempt, item.session_id),
        ):
            package = state.package(session.package_id).package
            package_claims = tuple(
                claim for claim in claims.values()
                if claim.package_id == session.package_id
            )
            run_verifiers = tuple(
                item for item in verifiers
                if item.builder_run_id == session.run_id
            )
            run_effects = tuple(
                item for item in effects
                if item.run_id == session.run_id
            )
            process_record = dict(pid_records.get(session.run_id) or {})
            process_backed = bool(process_record)
            process_status = (
                self.workers.status(session.run_id)
                if process_backed
                else None
            )
            identity_valid = (
                _process_identity_valid(package, session, process_record)
                if process_backed
                else False
            )
            agent_row = rows.agent_runs.get(session.run_id, {})
            agent_result = _json_object(agent_row.get("result_json"))
            package_worktrees = worktree_states.get(session.package_id, ())
            dirty = any(item.dirty for item in package_worktrees)
            meaningful_session_result = {
                key: value for key, value in session.result.items()
                if key not in _BINDING_RESULT_KEYS and value not in (None, "", [], {})
            }
            session_issues = _session_issues(
                package,
                session,
                process_record=process_record,
                process_status=process_status,
                process_identity_valid=identity_valid,
                agent_row=agent_row,
                claims=package_claims,
                leases=lease_states,
            )
            issues.extend(session_issues)
            category = _session_category(
                session,
                agent_status=(
                    str(agent_row.get("status"))
                    if agent_row.get("status") is not None
                    else None
                ),
                process_backed=process_backed,
                process_alive=(
                    process_status.alive
                    if process_status is not None
                    else None
                ),
            )
            session_states[session.package_id].append(SessionRecoveryState(
                package_id=session.package_id,
                session=session,
                category=category,
                process_backed=process_backed,
                process_identity_valid=identity_valid,
                process_alive=(
                    process_status.alive
                    if process_status is not None
                    else None
                ),
                pid=_as_int(process_record.get("pid")),
                process_record=process_record,
                agent_run_status=(
                    str(agent_row.get("status"))
                    if agent_row.get("status") is not None
                    else None
                ),
                agent_run_failure_class=(
                    str(agent_row.get("failure_class"))
                    if agent_row.get("failure_class") is not None
                    else None
                ),
                agent_result=agent_result,
                partial_result=bool(
                    meaningful_session_result or agent_result or dirty
                ),
                claim_ids=tuple(sorted(
                    claim.claim_id for claim in package_claims
                    if claim.session_id in {None, session.session_id, session.run_id}
                )),
                leases=tuple(
                    item for item in lease_states
                    if (
                        item.owner_id == session.run_id
                        or item.lease_key == worker_lease_key(session.run_id)
                    )
                ),
                verifiers=run_verifiers,
                effects=run_effects,
                issues=session_issues,
            ))

        package_states: list[PackageRecoveryState] = []
        for current_id in selected_ids:
            current = state.package(current_id)
            package_claims = tuple(sorted(
                (
                    claim for claim in claims.values()
                    if claim.package_id == current_id
                ),
                key=lambda item: item.claim_id,
            ))
            package_sessions = tuple(session_states.get(current_id, ()))
            run_ids = {item.session.run_id for item in package_sessions}
            package_verifiers = tuple(
                item for item in verifiers
                if item.builder_run_id in run_ids
            )
            package_effects = tuple(
                item for item in effects
                if item.run_id in run_ids
            )
            package_lease_keys = {
                worker_lease_key(run_id) for run_id in run_ids
            }
            package_lease_keys.update(
                str(item.session.result.get("worker_lease_key") or "")
                for item in package_sessions
            )
            package_lease_keys.update(
                f"workforce-resource:{item.resource_id}"
                for item in worktree_states.get(current_id, ())
            )
            package_leases = tuple(
                item for item in lease_states
                if (
                    item.owner_id in run_ids
                    or item.lease_key in package_lease_keys
                )
            )
            package_issues = tuple(
                issue for issue in issues
                if _issue_belongs_to_package(
                    issue,
                    current_id,
                    run_ids=run_ids,
                    claim_ids={item.claim_id for item in package_claims},
                    worktree_paths={
                        _path_identity(item.worktree_path)
                        for item in worktree_states.get(current_id, ())
                    },
                )
            )
            package_states.append(PackageRecoveryState(
                package=current.package,
                status=current.status,
                version=current.version,
                reason=current.reason,
                stop_reason=_stop_reason(
                    self.database,
                    self.stop_path,
                    package=current.package,
                ),
                worktrees=worktree_states.get(current_id, ()),
                sessions=package_sessions,
                claims=package_claims,
                leases=package_leases,
                verifiers=package_verifiers,
                effects=package_effects,
                issues=package_issues,
            ))
        return WorkforceRecoveryReport(
            packages=tuple(package_states),
            issues=tuple(_dedupe_issues(issues)),
            audited_at=observed_at.isoformat(),
        )

    def reconcile_spawned(
        self,
        *,
        package_id: str | None = None,
    ) -> WorkforceReconciliationReport:
        """Reconcile only already-spawned, identity-verified process sessions."""

        before = self.audit(package_id=package_id)
        actions: list[ProcessReconciliation] = []
        for package_state in before.packages:
            active = tuple(
                item for item in package_state.sessions
                if item.session.status not in TERMINAL_SESSION_STATUSES
            )
            if not active:
                continue
            unsafe = tuple(
                issue for issue in package_state.issues
                if issue.code in _UNSAFE_RECONCILIATION_CODES
            )
            for session in active:
                if package_state.stop_reason:
                    actions.append(ProcessReconciliation(
                        package_state.package.package_id,
                        session.session.session_id,
                        session.session.run_id,
                        "SKIPPED_STOP",
                        package_state.stop_reason,
                        session.category,
                    ))
                    continue
                if unsafe:
                    actions.append(ProcessReconciliation(
                        package_state.package.package_id,
                        session.session.session_id,
                        session.session.run_id,
                        "SKIPPED_UNSAFE",
                        "; ".join(
                            f"{item.code}: {item.detail}" for item in unsafe
                        ),
                        session.category,
                    ))
                    continue
                if any(item.pending for item in session.effects):
                    actions.append(ProcessReconciliation(
                        package_state.package.package_id,
                        session.session.session_id,
                        session.session.run_id,
                        "SKIPPED_EFFECT_PENDING",
                        "provider effect requires explicit canonical reconciliation",
                        session.category,
                    ))
                    continue
                if not session.process_backed:
                    actions.append(ProcessReconciliation(
                        package_state.package.package_id,
                        session.session.session_id,
                        session.session.run_id,
                        "SKIPPED_NOT_PROCESS_BACKED",
                        "session has no durable gateway/controller PID record",
                        session.category,
                    ))
                    continue
                if not session.process_identity_valid:
                    actions.append(ProcessReconciliation(
                        package_state.package.package_id,
                        session.session.session_id,
                        session.session.run_id,
                        "SKIPPED_UNSAFE_PROCESS_IDENTITY",
                        "durable PID record does not match the workforce binding",
                        session.category,
                    ))
                    continue
                try:
                    outcome = self.gateway.reconcile_process(
                        package_state.package.package_id,
                        self.workers,
                    )
                except (PermissionError, WorkforceGatewayError) as error:
                    actions.append(ProcessReconciliation(
                        package_state.package.package_id,
                        session.session.session_id,
                        session.session.run_id,
                        "ERROR",
                        f"{type(error).__name__}: {error}",
                        session.category,
                    ))
                    continue
                if isinstance(outcome, WorkerStatus):
                    action = ProcessReconciliation(
                        package_state.package.package_id,
                        session.session.session_id,
                        session.session.run_id,
                        "ALIVE" if outcome.alive else "UNCHANGED",
                        (
                            "registered process remains alive"
                            if outcome.alive
                            else "controller reported no live process"
                        ),
                        session.category,
                    )
                elif isinstance(outcome, GatewayResult):
                    action = ProcessReconciliation(
                        package_state.package.package_id,
                        session.session.session_id,
                        session.session.run_id,
                        "RECONCILED",
                        (
                            f"package={outcome.package_status}; "
                            f"canonical_outcome={outcome.outcome}; "
                            f"reason={outcome.reason or 'none'}"
                        ),
                        session.category,
                    )
                else:
                    raise TypeError(
                        "WorkforceGateway.reconcile_process returned "
                        f"{type(outcome).__name__}"
                    )
                actions.append(action)
        after = self.audit(package_id=package_id)
        final_categories = {
            (item.package_id, item.session.session_id): item.category
            for package in after.packages
            for item in package.sessions
        }
        completed = tuple(
            ProcessReconciliation(
                package_id=item.package_id,
                session_id=item.session_id,
                run_id=item.run_id,
                outcome=item.outcome,
                detail=item.detail,
                before_category=item.before_category,
                after_category=final_categories.get(
                    (item.package_id, item.session_id)
                ),
            )
            for item in actions
        )
        return WorkforceReconciliationReport(
            before=before,
            actions=completed,
            after=after,
        )

    def _read_rows(
        self,
        package_id: str | None,
    ) -> _AuditRows:
        with self.database.read_snapshot() as snapshot:
            connection = snapshot._connection()
            workforce_rows = connection.execute(
                "SELECT * FROM events WHERE event_type LIKE 'workforce.%' "
                "ORDER BY sequence"
            ).fetchall()
            workforce_events = [
                self.database._row_to_event(row) for row in workforce_rows
            ]
            state = _reduce_workforce_events(workforce_events)
            selected_ids = tuple(sorted(
                key for key in state.packages
                if package_id is None or key == package_id
            ))
            if package_id is not None and package_id not in state.packages:
                raise KeyError(package_id)
            mission_ids = tuple(sorted({
                str(state.package(current_id).package.mission_id)
                for current_id in selected_ids
                if state.package(current_id).package.mission_id is not None
            }))
            reconstructed_sessions, _issues = _reconstruct_sessions(
                workforce_events,
                selected_ids,
            )
            run_ids = tuple(sorted(
                session.run_id for session in reconstructed_sessions.values()
            ))
            canonical_rows = _related_event_rows(
                connection,
                run_ids=run_ids,
                mission_ids=mission_ids,
            )
            events_with_rows = [
                *zip(workforce_events, workforce_rows),
                *(
                    (self.database._row_to_event(row), row)
                    for row in canonical_rows
                ),
            ]
            by_id = {
                event.event_id: (event, int(row["sequence"]))
                for event, row in events_with_rows
            }
            ordered = tuple(
                event for event, _sequence in sorted(
                    by_id.values(),
                    key=lambda item: item[1],
                )
            )
            action_keys = tuple(sorted({
                str(event.payload.get("effect_action_key") or "")
                for event in ordered
                if event.payload.get("effect_action_key")
            }))
            effect_rows = _effect_event_rows(connection, action_keys)
            for row in effect_rows:
                event = self.database._row_to_event(row)
                if event.event_id in by_id:
                    continue
                by_id[event.event_id] = (event, int(row["sequence"]))
            ordered = tuple(
                event for event, _sequence in sorted(
                    by_id.values(),
                    key=lambda item: item[1],
                )
            )
            agent_rows = _rows_for_ids(
                connection,
                table="agent_runs",
                column="run_id",
                values=run_ids,
            )
            verdict_rows = _rows_for_any(
                connection,
                table="verification_verdicts",
                filters=(
                    ("builder_run_id", run_ids),
                    ("mission_id", mission_ids),
                ),
            )
            leases = tuple(
                dict(row) for row in connection.execute(
                    "SELECT * FROM leases ORDER BY lease_key"
                ).fetchall()
            )
            system_actions = {
                str(row["action_key"]): dict(row)
                for row in _rows_for_ids(
                    connection,
                    table="system_actions",
                    column="action_key",
                    values=action_keys,
                )
            }
        return _AuditRows(
            state=state,
            selected_ids=selected_ids,
            events=ordered,
            agent_runs={
                str(row["run_id"]): dict(row)
                for row in agent_rows
            },
            leases=leases,
            verdicts=tuple(dict(row) for row in verdict_rows),
            system_actions=system_actions,
        )

    def _audit_worktree(
        self,
        package: WorkPackage,
        prepared: WorktreePreparation,
    ) -> WorktreeRecoveryState:
        issues: list[RecoveryIssue] = []
        expected = Path(prepared.worktree_path).expanduser().resolve(
            strict=False
        )
        if prepared.package_id != package.package_id:
            issues.append(RecoveryIssue(
                "PREPARATION_PACKAGE_MISMATCH",
                "worktree",
                str(expected),
                "prepared worktree names another package",
            ))
        if (
            expected
            != Path(package.worktree_path).expanduser().resolve(strict=False)
        ):
            issues.append(RecoveryIssue(
                "WORKTREE_ROOT_MISMATCH",
                "worktree",
                str(expected),
                "package and preparation name different worktree paths",
            ))
        if prepared.branch_name != package.branch_name:
            issues.append(RecoveryIssue(
                "WORKTREE_BRANCH_MISMATCH",
                "worktree",
                str(expected),
                "package and preparation name different branches",
            ))
        if prepared.base_sha != package.source_sha:
            issues.append(RecoveryIssue(
                "WORKTREE_BASE_NOT_ANCESTOR",
                "worktree",
                str(expected),
                "package and preparation name different base commits",
            ))

        exists = expected.is_dir()
        root: Path | None = None
        observed_branch: str | None = None
        head: str | None = None
        common: Path | None = None
        expected_common: Path | None = None
        base_present = False
        base_is_ancestor = False
        dirty = False
        changed_paths: tuple[str, ...] = ()
        try:
            self.worktrees._verify_existing(prepared)
        except WorkforceResourceError as error:
            issues.append(RecoveryIssue(
                "WORKTREE_UNAVAILABLE",
                "worktree",
                str(expected),
                str(error),
            ))
        if exists:
            try:
                root = Path(_git(expected, "rev-parse", "--show-toplevel")).resolve()
                observed_branch = (
                    _git(root, "branch", "--show-current") or "DETACHED"
                )
                head = _git(root, "rev-parse", "HEAD")
                base_present = _git_check(
                    root,
                    "cat-file",
                    "-e",
                    f"{prepared.base_sha}^{{commit}}",
                )
                base_is_ancestor = bool(
                    base_present and _git_check(
                        root,
                        "merge-base",
                        "--is-ancestor",
                        prepared.base_sha,
                        head,
                    )
                )
                changed_paths = _changed_paths(root)
                dirty = bool(changed_paths)
                common = _git_path(
                    root,
                    _git(root, "rev-parse", "--git-common-dir"),
                )
                source = Path(prepared.source_repository).expanduser().resolve()
                expected_common = _git_path(
                    source,
                    _git(source, "rev-parse", "--git-common-dir"),
                )
            except (OSError, RuntimeError, WorkforceResourceError) as error:
                issues.append(RecoveryIssue(
                    "WORKTREE_UNAVAILABLE",
                    "worktree",
                    str(expected),
                    str(error),
                ))
        if root is not None and root != expected:
            issues.append(RecoveryIssue(
                "WORKTREE_ROOT_MISMATCH",
                "worktree",
                str(expected),
                f"Git root is {root}",
            ))
        if observed_branch is not None and observed_branch != prepared.branch_name:
            issues.append(RecoveryIssue(
                "WORKTREE_BRANCH_MISMATCH",
                "worktree",
                str(expected),
                (
                    f"expected {prepared.branch_name}, "
                    f"observed {observed_branch}"
                ),
            ))
        if exists and not base_is_ancestor:
            issues.append(RecoveryIssue(
                "WORKTREE_BASE_NOT_ANCESTOR",
                "worktree",
                str(expected),
                (
                    f"base {prepared.base_sha} is not an ancestor of "
                    f"{head or 'UNKNOWN'}"
                ),
            ))
        if (
            common is not None
            and expected_common is not None
            and _path_identity(common) != _path_identity(expected_common)
        ):
            issues.append(RecoveryIssue(
                "WORKTREE_REPOSITORY_MISMATCH",
                "worktree",
                str(expected),
                (
                    f"expected common Git directory {expected_common}, "
                    f"observed {common}"
                ),
            ))
        valid = exists and not any(
            issue.code in {
                "PREPARATION_PACKAGE_MISMATCH",
                "WORKTREE_BASE_NOT_ANCESTOR",
                "WORKTREE_BRANCH_MISMATCH",
                "WORKTREE_REPOSITORY_MISMATCH",
                "WORKTREE_ROOT_MISMATCH",
                "WORKTREE_UNAVAILABLE",
            }
            for issue in issues
        )
        return WorktreeRecoveryState(
            package_id=prepared.package_id,
            resource_id=prepared.resource_id,
            source_repository=prepared.source_repository,
            worktree_path=str(expected),
            branch_name=prepared.branch_name,
            base_sha=prepared.base_sha,
            exists=exists,
            worktree_root=str(root) if root is not None else None,
            repository_common_dir=(
                str(common) if common is not None else None
            ),
            expected_common_dir=(
                str(expected_common)
                if expected_common is not None
                else None
            ),
            observed_branch=observed_branch,
            head_sha=head,
            base_present=base_present,
            base_is_ancestor=base_is_ancestor,
            dirty=dirty,
            changed_paths=changed_paths,
            valid=valid,
            preserve_required=dirty or not valid,
            issues=tuple(_dedupe_issues(issues)),
        )


def _related_event_rows(
    connection,
    *,
    run_ids: Sequence[str],
    mission_ids: Sequence[str],
):
    if not run_ids and not mission_ids:
        return ()
    event_placeholders = ", ".join("?" for _ in _CANONICAL_EVENT_TYPES)
    clauses: list[str] = []
    args: list[Any] = [*_CANONICAL_EVENT_TYPES]
    if run_ids:
        placeholders = ", ".join("?" for _ in run_ids)
        clauses.extend([
            f"json_extract(payload_json, '$.run_id') IN ({placeholders})",
            (
                "json_extract(payload_json, '$.builder_run_id') "
                f"IN ({placeholders})"
            ),
        ])
        args.extend(run_ids)
        args.extend(run_ids)
    if mission_ids:
        placeholders = ", ".join("?" for _ in mission_ids)
        clauses.append(
            f"json_extract(payload_json, '$.mission_id') IN ({placeholders})"
        )
        args.extend(mission_ids)
    rows = connection.execute(
        "SELECT * FROM events "
        f"WHERE event_type IN ({event_placeholders}) "
        f"AND ({' OR '.join(clauses)}) ORDER BY sequence",
        tuple(args),
    ).fetchall()
    return tuple(rows)


def _effect_event_rows(connection, action_keys: Sequence[str]):
    if not action_keys:
        return ()
    event_types = (
        "effect.frozen",
        "effect.reconciled",
        "effect.terminal_truth",
        "system.action_completed",
        "system.action_failed",
        "system.action_started",
    )
    type_placeholders = ", ".join("?" for _ in event_types)
    key_placeholders = ", ".join("?" for _ in action_keys)
    return tuple(connection.execute(
        "SELECT * FROM events "
        f"WHERE event_type IN ({type_placeholders}) "
        "AND json_extract(payload_json, '$.action_key') "
        f"IN ({key_placeholders}) ORDER BY sequence",
        (*event_types, *action_keys),
    ).fetchall())


def _rows_for_ids(
    connection,
    *,
    table: str,
    column: str,
    values: Sequence[str],
):
    if not values:
        return ()
    placeholders = ", ".join("?" for _ in values)
    return tuple(connection.execute(
        f"SELECT * FROM {table} WHERE {column} IN ({placeholders})",
        tuple(values),
    ).fetchall())


def _rows_for_any(
    connection,
    *,
    table: str,
    filters: Sequence[tuple[str, Sequence[str]]],
):
    clauses: list[str] = []
    args: list[str] = []
    for column, values in filters:
        if not values:
            continue
        placeholders = ", ".join("?" for _ in values)
        clauses.append(f"{column} IN ({placeholders})")
        args.extend(values)
    if not clauses:
        return ()
    return tuple(connection.execute(
        f"SELECT * FROM {table} WHERE {' OR '.join(clauses)} ORDER BY rowid",
        tuple(args),
    ).fetchall())


def _reconstruct_sessions(
    events: Iterable[Event],
    selected_ids: Sequence[str],
) -> tuple[dict[str, WorkerSession], tuple[RecoveryIssue, ...]]:
    selected = set(selected_ids)
    sessions: dict[str, WorkerSession] = {}
    issues: list[RecoveryIssue] = []
    for event in events:
        if event.event_type not in _SESSION_EVENTS:
            continue
        if str(event.payload.get("package_id") or "") not in selected:
            continue
        try:
            session = WorkerSession.from_payload(event.payload)
        except (KeyError, TypeError, ValueError) as error:
            issues.append(RecoveryIssue(
                "MALFORMED_SESSION_EVENT",
                "event",
                event.event_id,
                str(error),
            ))
            continue
        sessions[session.session_id] = session
    return sessions, tuple(issues)


def _reconstruct_claims(
    events: Iterable[Event],
    selected_ids: Sequence[str],
) -> tuple[dict[str, ResourceClaim], tuple[RecoveryIssue, ...]]:
    selected = set(selected_ids)
    claims: dict[str, ResourceClaim] = {}
    versions: dict[str, int] = {}
    issues: list[RecoveryIssue] = []
    for event in events:
        if event.event_type not in _CLAIM_EVENTS:
            continue
        if str(event.payload.get("package_id") or "") not in selected:
            continue
        try:
            claim = ResourceClaim.from_payload(event.payload)
        except (KeyError, TypeError, ValueError) as error:
            issues.append(RecoveryIssue(
                "MALFORMED_RESOURCE_EVENT",
                "event",
                event.event_id,
                str(error),
            ))
            continue
        prior = claims.get(claim.claim_id)
        prior_version = versions.get(claim.claim_id, 0)
        if prior is not None and prior.status in {"RELEASED", "EXPIRED"}:
            continue
        expected_status = event.payload.get("expected_status")
        if (
            prior is not None
            and expected_status is not None
            and str(expected_status) != prior.status
        ):
            continue
        try:
            expected_version = event.payload.get("expected_version")
            if (
                expected_version is not None
                and int(expected_version) != prior_version
            ):
                continue
            declared_version = event.payload.get("version")
            next_version = (
                int(declared_version)
                if declared_version is not None
                else prior_version + 1
            )
        except (TypeError, ValueError) as error:
            issues.append(RecoveryIssue(
                "MALFORMED_RESOURCE_EVENT",
                "event",
                event.event_id,
                f"invalid resource claim version: {error}",
            ))
            continue
        if next_version != prior_version + 1:
            continue
        claims[claim.claim_id] = claim
        versions[claim.claim_id] = next_version
    return claims, tuple(issues)


def _preparations(
    events: Iterable[Event],
    selected_ids: Sequence[str],
) -> tuple[tuple[WorktreePreparation, ...], tuple[RecoveryIssue, ...]]:
    selected = set(selected_ids)
    values: list[WorktreePreparation] = []
    issues: list[RecoveryIssue] = []
    for event in events:
        if event.event_type != "workforce.worktree_prepared":
            continue
        if str(event.payload.get("package_id") or "") not in selected:
            continue
        try:
            values.append(WorktreePreparation.from_payload(event.payload))
        except (KeyError, TypeError, ValueError) as error:
            issues.append(RecoveryIssue(
                "MALFORMED_WORKTREE_EVENT",
                "event",
                event.event_id,
                str(error),
            ))
    return tuple(values), tuple(issues)


def _accepted_verdicts(events: Iterable[Event]) -> dict[str, Event]:
    accepted: dict[str, Event] = {}
    for event in events:
        if not (
            event.event_type == "verification.accepted"
            and event.source_type == "supervisor"
            and event.source_id == "mission-loop"
            and event.payload.get("decision") == "ACCEPT"
        ):
            continue
        verdict_id = str(event.payload.get("verdict_id") or "")
        if verdict_id:
            accepted[verdict_id] = event
    return accepted


def _verifier_states(
    rows: Iterable[Mapping[str, Any]],
    accepted: Mapping[str, Event],
) -> tuple[VerifierRecoveryState, ...]:
    values: list[VerifierRecoveryState] = []
    for row in rows:
        verdict_id = str(row.get("verdict_id") or "")
        receipt = accepted.get(verdict_id)
        values.append(VerifierRecoveryState(
            verdict_id=verdict_id,
            mission_id=str(row.get("mission_id") or ""),
            subject_id=str(row.get("subject_id") or ""),
            builder_run_id=str(row.get("builder_run_id") or ""),
            verifier_run_id=(
                str(row.get("verifier_run_id"))
                if row.get("verifier_run_id") is not None
                else None
            ),
            status=str(row.get("status") or "UNKNOWN"),
            accepted=receipt is not None,
            acceptance_event_id=(
                receipt.event_id if receipt is not None else None
            ),
            reason=(
                str(row.get("reason"))
                if row.get("reason") is not None
                else None
            ),
            findings=_json_strings(row.get("findings_json")),
            missing_proof=_json_strings(row.get("missing_proof_json")),
            scope_violations=_json_strings(
                row.get("scope_violations_json")
            ),
        ))
    return tuple(sorted(values, key=lambda item: item.verdict_id))


def _effect_states(
    events: Sequence[Event],
    system_actions: Mapping[str, Mapping[str, Any]],
    *,
    selected_ids: Sequence[str],
    sessions: Mapping[str, WorkerSession],
) -> tuple[EffectRecoveryState, ...]:
    selected_runs = {
        session.run_id for session in sessions.values()
        if session.package_id in set(selected_ids)
    }
    required: dict[tuple[str, str], list[Event]] = defaultdict(list)
    resolved: dict[tuple[str, str], list[Event]] = defaultdict(list)
    by_action: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        action_key = str(
            event.payload.get("effect_action_key")
            or event.payload.get("action_key")
            or ""
        )
        run_id = str(event.payload.get("run_id") or "")
        if action_key:
            by_action[action_key].append(event)
        if run_id not in selected_runs or not action_key:
            continue
        identity = (run_id, action_key)
        if event.event_type == "provider.effect_reconciliation_required":
            required[identity].append(event)
        elif event.event_type == "provider.effect_reconciled":
            resolved[identity].append(event)
    values: list[EffectRecoveryState] = []
    for identity, requirements in sorted(required.items()):
        run_id, action_key = identity
        action_events = by_action.get(action_key, [])
        canonical_resolutions = [
            event for event in action_events
            if event.event_type == "effect.reconciled"
        ]
        resolution_events = [*resolved.get(identity, []), *canonical_resolutions]
        outcomes = [
            event for event in action_events
            if event.event_type in _EFFECT_OUTCOME_EVENTS
        ]
        journal = system_actions.get(action_key, {})
        status = str(journal.get("status") or "UNKNOWN")
        if canonical_resolutions:
            status = "RECONCILED"
        elif any(event.event_type == "effect.frozen" for event in action_events):
            status = "FROZEN"
        elif any(
            event.event_type == "system.action_completed"
            for event in action_events
        ):
            status = "COMPLETED"
        elif any(
            event.event_type == "system.action_failed"
            for event in action_events
        ):
            status = "FAILED"
        elif any(
            event.event_type == "system.action_started"
            for event in action_events
        ):
            status = "STARTED"
        values.append(EffectRecoveryState(
            run_id=run_id,
            action_key=action_key,
            provider=str(
                requirements[-1].payload.get("provider") or ""
            ),
            status=status,
            pending=not resolution_events,
            reconciled=bool(resolution_events),
            requirement_event_ids=tuple(
                event.event_id for event in requirements
            ),
            resolution_event_ids=tuple(
                event.event_id for event in resolution_events
            ),
            outcome_event_ids=tuple(event.event_id for event in outcomes),
        ))
    return tuple(values)


def _duplicate_identity_issues(
    events: Sequence[Event],
    *,
    selected_ids: Sequence[str],
    sessions: Mapping[str, WorkerSession],
    claims: Mapping[str, ResourceClaim],
    preparations: Sequence[WorktreePreparation],
    effects: Sequence[EffectRecoveryState],
) -> tuple[RecoveryIssue, ...]:
    selected = set(selected_ids)
    issues: list[RecoveryIssue] = []
    package_creations = Counter(
        str(event.payload.get("package_id") or "")
        for event in events
        if (
            event.event_type == "workforce.package_created"
            and str(event.payload.get("package_id") or "") in selected
        )
    )
    for package_id, count in package_creations.items():
        if count > 1:
            issues.append(RecoveryIssue(
                "DUPLICATE_PACKAGE_IDENTITY",
                "package",
                package_id,
                f"{count} package creation events name the same package",
            ))
    terminal_transitions: dict[str, list[str]] = defaultdict(list)
    for event in events:
        if event.event_type != "workforce.package_state_changed":
            continue
        package_id = str(event.payload.get("package_id") or "")
        status = str(event.payload.get("status") or "")
        if package_id in selected and status in {
            "ACCEPTED", "CANCELLED", "REJECTED"
        }:
            terminal_transitions[package_id].append(status)
    for package_id, outcomes in terminal_transitions.items():
        if len(outcomes) > 1:
            issues.append(RecoveryIssue(
                "DUPLICATE_PACKAGE_OUTCOME",
                "package",
                package_id,
                f"multiple terminal outcomes were recorded: {outcomes}",
            ))

    preparation_groups: dict[
        str,
        dict[tuple[str, str], list[WorktreePreparation]],
    ] = {
        "path": defaultdict(list),
        "resource": defaultdict(list),
        "branch": defaultdict(list),
    }
    for item in preparations:
        preparation_groups["path"][
            ("path", _path_identity(item.worktree_path))
        ].append(item)
        preparation_groups["resource"][
            ("resource", item.resource_id.casefold())
        ].append(item)
        preparation_groups["branch"][
            (
                "branch",
                _path_identity(item.source_repository)
                + "::"
                + item.branch_name.casefold(),
            )
        ].append(item)
    for groups in preparation_groups.values():
        for values in groups.values():
            package_names = {item.package_id for item in values}
            if len(values) > 1 and (
                len(package_names) > 1 or len(values) > len(package_names)
            ):
                issues.append(RecoveryIssue(
                    "DUPLICATE_WORKTREE_IDENTITY",
                    "worktree",
                    values[0].worktree_path,
                    (
                        "worktree path, resource, or branch identity is shared by "
                        f"{sorted(package_names)}"
                    ),
                ))

    active_sessions: dict[str, list[WorkerSession]] = defaultdict(list)
    run_sessions: dict[str, list[WorkerSession]] = defaultdict(list)
    for session in sessions.values():
        run_sessions[session.run_id].append(session)
        if session.status not in TERMINAL_SESSION_STATUSES:
            active_sessions[session.package_id].append(session)
    for package_id, values in active_sessions.items():
        if len(values) > 1:
            issues.append(RecoveryIssue(
                "DUPLICATE_ACTIVE_SESSION",
                "package",
                package_id,
                f"active sessions: {[item.session_id for item in values]}",
            ))
    for run_id, values in run_sessions.items():
        identities = {item.session_id for item in values}
        if len(identities) > 1:
            issues.append(RecoveryIssue(
                "DUPLICATE_SESSION_RUN_ID",
                "run",
                run_id,
                f"run is bound to sessions {sorted(identities)}",
            ))

    active_claims = [
        claim for claim in claims.values() if claim.status == "ACTIVE"
    ]
    for label, key in (
        ("package", lambda claim: claim.package_id),
        ("resource", lambda claim: claim.resource_id.casefold()),
        ("worktree", lambda claim: _path_identity(claim.worktree_path)),
    ):
        grouped: dict[str, list[ResourceClaim]] = defaultdict(list)
        for claim in active_claims:
            grouped[key(claim)].append(claim)
        for identity, values in grouped.items():
            if len(values) > 1:
                issues.append(RecoveryIssue(
                    "DUPLICATE_ACTIVE_CLAIM",
                    label,
                    identity,
                    f"active claims: {[item.claim_id for item in values]}",
                ))

    for effect in effects:
        if len(effect.requirement_event_ids) > 1:
            issues.append(RecoveryIssue(
                "DUPLICATE_EFFECT_REQUIREMENT",
                "effect",
                effect.action_key,
                (
                    f"{len(effect.requirement_event_ids)} reconciliation "
                    "requirements name the same run/effect"
                ),
            ))
        outcome_types = Counter(
            event.event_type for event in events
            if event.event_id in set(effect.outcome_event_ids)
        )
        if any(count > 1 for count in outcome_types.values()):
            issues.append(RecoveryIssue(
                "DUPLICATE_EFFECT_OUTCOME",
                "effect",
                effect.action_key,
                f"duplicate outcome events: {dict(outcome_types)}",
            ))
    return tuple(_dedupe_issues(issues))


def _session_issues(
    package: WorkPackage,
    session: WorkerSession,
    *,
    process_record: Mapping[str, Any],
    process_status: WorkerStatus | None,
    process_identity_valid: bool,
    agent_row: Mapping[str, Any],
    claims: Sequence[ResourceClaim],
    leases: Sequence[LeaseRecoveryState],
) -> tuple[RecoveryIssue, ...]:
    issues: list[RecoveryIssue] = []
    if process_record and not process_identity_valid:
        issues.append(RecoveryIssue(
            "PROCESS_IDENTITY_MISMATCH",
            "run",
            session.run_id,
            "durable PID record does not match package/session binding",
        ))
    if (
        session.status in TERMINAL_SESSION_STATUSES
        and process_status is not None
        and process_status.alive
    ):
        issues.append(RecoveryIssue(
            "TERMINAL_SESSION_PROCESS_ALIVE",
            "run",
            session.run_id,
            "terminal workforce session still has a live owned process",
        ))
    agent_status = str(agent_row.get("status") or "")
    if (
        session.status not in TERMINAL_SESSION_STATUSES
        and agent_status in {"COMPLETED", "FAILED", "CANCELLED"}
    ):
        issues.append(RecoveryIssue(
            "CANONICAL_TERMINAL_NOT_LINKED",
            "run",
            session.run_id,
            (
                f"agent_run is {agent_status} while workforce session is "
                f"{session.status}"
            ),
        ))
    matching_claims = [
        claim for claim in claims
        if claim.session_id in {None, session.session_id, session.run_id}
    ]
    if session.result.get("claim_id") and not any(
        claim.claim_id == session.result.get("claim_id")
        for claim in claims
    ):
        issues.append(RecoveryIssue(
            "SESSION_CLAIM_MISSING",
            "run",
            session.run_id,
            f"bound claim {session.result.get('claim_id')} is absent",
        ))
    if len([
        claim for claim in matching_claims if claim.status == "ACTIVE"
    ]) > 1:
        issues.append(RecoveryIssue(
            "DUPLICATE_ACTIVE_CLAIM",
            "run",
            session.run_id,
            "session is associated with multiple active mutable-scope claims",
        ))
    lease_keys = {item.lease_key for item in leases}
    if process_record and worker_lease_key(session.run_id) not in lease_keys:
        issues.append(RecoveryIssue(
            "WORKER_LEASE_MISSING",
            "run",
            session.run_id,
            "durable process record has no worker lease row",
        ))
    resource_id = str(session.result.get("resource_id") or "")
    if resource_id and session.status not in TERMINAL_SESSION_STATUSES:
        expected = f"workforce-resource:{resource_id}"
        if expected not in lease_keys:
            issues.append(RecoveryIssue(
                "RESOURCE_LEASE_MISSING",
                "run",
                session.run_id,
                f"active session has no resource lease {expected}",
            ))
    return tuple(_dedupe_issues(issues))


def _session_category(
    session: WorkerSession,
    *,
    agent_status: str | None,
    process_backed: bool,
    process_alive: bool | None,
) -> str:
    if (
        session.status in TERMINAL_SESSION_STATUSES
        or agent_status in {"COMPLETED", "FAILED", "CANCELLED"}
    ):
        return "TERMINAL"
    if process_backed:
        return "ACTIVE" if process_alive else "DEAD"
    return "UNBACKED"


def _process_identity_valid(
    package: WorkPackage,
    session: WorkerSession,
    record: Mapping[str, Any],
) -> bool:
    pid = _as_int(record.get("pid"))
    session_pid = _as_int(session.result.get("pid"))
    if pid is None or session_pid is None or pid != session_pid:
        return False
    if str(record.get("run_id") or "") != session.run_id:
        return False
    if str(record.get("worker_type") or "") != "workforce-canonical-mission":
        return False
    working_directory = str(record.get("working_directory") or "")
    if not working_directory or (
        _path_identity(working_directory)
        != _path_identity(package.worktree_path)
    ):
        return False
    if package.mission_id is not None and (
        str(record.get("mission_id") or "") != package.mission_id
    ):
        return False
    if package.step_id is not None and (
        str(record.get("step_id") or "") != package.step_id
    ):
        return False
    return True


def _lease_state(
    row: Mapping[str, Any],
    now: datetime,
) -> LeaseRecoveryState:
    expires_at = str(row.get("expires_at") or "")
    try:
        expired = parse(expires_at) <= now
    except (TypeError, ValueError):
        expired = True
    return LeaseRecoveryState(
        lease_key=str(row.get("lease_key") or ""),
        owner_id=str(row.get("owner_id") or ""),
        expires_at=expires_at,
        heartbeat_at=str(row.get("heartbeat_at") or ""),
        expired=expired,
    )


def _issue_belongs_to_package(
    issue: RecoveryIssue,
    package_id: str,
    *,
    run_ids: set[str],
    claim_ids: set[str],
    worktree_paths: set[str],
) -> bool:
    if issue.subject_type == "package":
        return issue.subject_id == package_id
    if issue.subject_type == "run":
        return issue.subject_id in run_ids
    if issue.subject_type == "claim":
        return issue.subject_id in claim_ids
    if (
        issue.code == "DUPLICATE_ACTIVE_CLAIM"
        and any(claim_id in issue.detail for claim_id in claim_ids)
    ):
        return True
    if issue.subject_type == "worktree":
        return _path_identity(issue.subject_id) in worktree_paths
    return package_id in issue.detail


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return (
        {str(key): item for key, item in value.items()}
        if isinstance(value, Mapping)
        else {}
    )


def _json_strings(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw)
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return (
        tuple(str(item) for item in value)
        if isinstance(value, list)
        else ()
    )


def _as_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _path_identity(value: str | Path) -> str:
    return str(
        Path(value).expanduser().resolve(strict=False)
    ).replace("\\", "/").casefold()


def _git_path(root: Path, value: str) -> Path:
    path = Path(value)
    return (
        path.resolve(strict=False)
        if path.is_absolute()
        else (root / path).resolve(strict=False)
    )


def _git(root: Path, *args: str) -> str:
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
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"git {' '.join(args)} failed"
        )
    return result.stdout.strip()


def _git_raw(root: Path, *args: str) -> str:
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
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"git {' '.join(args)} failed"
        )
    return result.stdout


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
    raise RuntimeError(
        result.stderr.strip()
        or result.stdout.strip()
        or f"git {' '.join(args)} failed"
    )


def _changed_paths(root: Path) -> tuple[str, ...]:
    fields = _git_raw(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record or len(record) < 4:
            continue
        status = record[:2]
        path = record[3:].replace("\\", "/")
        if path:
            paths.append(path)
        if ("R" in status or "C" in status) and index < len(fields):
            source = fields[index].replace("\\", "/")
            index += 1
            if source:
                paths.append(source)
    return tuple(sorted(set(paths)))


def _dedupe_issues(
    issues: Iterable[RecoveryIssue],
) -> tuple[RecoveryIssue, ...]:
    return tuple(dict.fromkeys(issues))


__all__ = [
    "EffectRecoveryState",
    "LeaseRecoveryState",
    "PackageRecoveryState",
    "ProcessReconciliation",
    "RecoveryIssue",
    "SessionRecoveryState",
    "VerifierRecoveryState",
    "WorkforceReconciliationReport",
    "WorkforceRecovery",
    "WorkforceRecoveryReport",
    "WorktreeRecoveryState",
]
