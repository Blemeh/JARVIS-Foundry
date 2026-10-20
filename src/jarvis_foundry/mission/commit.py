"""Local commit after verified PASS (WP5).

``commit_after_pass`` is the ONLY commit path in the loop. It refuses
without a PASS verification verdict for the mission step, refuses when the
action policy does not ALLOW the commit (capability ``git_local_commit``
must be READY/ACTIVE, target inside the registered root, no active veto),
and only then creates one deterministic local commit in the mission's
registered worktree. Canonical databases and live Hermes are never touched.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from ..control.action_envelope import evaluate_and_record
from ..control.broker import emergency_stop_reason
from ..control.policy import ActionPolicy
from ..control.redaction import redact_text
from ..database import FoundryDatabase
from ..domain import ActionDecision, ActionEnvelope
from ..effects import (
    abandon_effect,
    COMPLETED,
    FAILED,
    FROZEN,
    STARTED,
    effect_state,
    execute_fabric_effect,
    fabric_effect_id,
    freeze_effect,
    reconcile_effect,
)
from ..v3_fault import invoke_fault_hook
from ..workers.result_contract import RESULT_FILENAME
from .verification import latest_verdict_for_step

try:
    from ..evolution import binding as _binding
except ImportError:  # noqa: BLE001 - without the binding module there is
    # no bound scope to enforce; improvement missions cannot materialize
    # on such a runtime (the bridge refuses), so only legacy missions
    # reach the commit path and they keep the unbounded behavior.
    _binding = None  # type: ignore[assignment]


class CommitError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    from ..platform.windows_subprocess import headless_run_kwargs

    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False,
        **headless_run_kwargs(),
    )
    if result.returncode != 0:
        raise CommitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _commit_effect_id(
    *, mission_id: str, step_id: str, run_id: str,
    worktree: Path, envelope: ActionEnvelope,
) -> str:
    """Bind the governed local commit to one stable effect identity."""
    return fabric_effect_id(
        system_id=str(envelope.resource_id or "git-local"),
        target_ref=str(worktree),
        capability_id="git_local_commit",
        capability_version="v1",
        action_kind=str(envelope.action_kind or "git_commit"),
        input_payload={
            "mission_id": mission_id,
            "step_id": step_id,
            "run_id": run_id,
            "action_id": envelope.action_id,
        },
    )


def _abandon_superseded_commit_claims(
    database: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str,
    current_run_id: str,
    worktree: Path,
    envelope: ActionEnvelope,
) -> list[str]:
    """Close commit claims left STARTED by a superseded run for this step.

    The commit effect identity binds the dispatching ``run_id``, so a step
    that is re-dispatched under a new run (repair after a killed builder)
    writes a different identity. If the earlier claim never invoked the
    connector (no matching commit is durable in Git), it can never complete
    and would otherwise stay ``STARTED`` forever, which the pre-live
    duplicate/ambiguity audit reads as an unreconciled effect.

    Only claims for this exact mission and step whose Git commit receipt is
    absent are abandoned, and the current run is never touched. Nothing is
    re-performed and no completion is fabricated.
    """
    abandoned: list[str] = []
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT action_key, status FROM system_actions "
            "WHERE kind = 'git_commit' AND status = ?",
            (STARTED,),
        ).fetchall()
    for row in rows:
        candidate = str(row["action_key"])
        if candidate == _commit_effect_id(
            mission_id=mission_id, step_id=step_id, run_id=current_run_id,
            worktree=worktree, envelope=envelope,
        ):
            continue
        state = effect_state(database, candidate)
        identity = state.get("request_identity") or {}
        candidate_step = str(identity.get("step_id") or "")
        candidate_run = str(identity.get("run_id") or "")
        if (
            str(identity.get("mission_id") or "") != mission_id
            or candidate_step != step_id
            or not candidate_run
            or candidate_run == current_run_id
        ):
            continue
        landed = _recorded_commit(
            worktree,
            f"jarvis: {mission_id} {candidate_step} verified PASS "
            f"(run {candidate_run})",
        )
        if landed is not None:
            # The superseded attempt did reach Git: record the truth instead
            # of abandoning it. Freeze the interrupted claim (freeze only
            # applies to STARTED effects) and reconcile it against the exact
            # commit receipt that is durable in the worktree.
            freeze_effect(
                database, candidate,
                reason="superseded run committed before its claim completed",
            )
            reconcile_effect(
                database, candidate,
                provider_state={"exists": True, "commit_id": landed},
            )
            continue
        abandon_effect(
            database, candidate,
            reason=(
                "commit claim superseded by a fresh run for the same mission "
                "step; the connector was never invoked and no commit exists"
            ),
        )
        abandoned.append(candidate)
    return abandoned




def close_mission_commit_claims(
    database: FoundryDatabase,
    *,
    mission_id: str,
    worktree: Path | None = None,
) -> list[str]:
    """Close this mission's commit claims that can never complete.

    Called when a mission reaches a terminal state. Every commit effect
    identity for the mission binds the dispatching run, so a claim left
    STARTED by a run that never reached the connector -- because the mission
    blocked on a later step, or the process died -- will never resolve on its
    own. The pre-live ambiguity audit correctly refuses to promote while such
    a claim remains non-terminal, so the mission terminal closure is the
    last authority able to reconcile it.

    Truth is decided from Git, never assumed: a claim whose run did commit is
    frozen and reconciled against that exact receipt; a claim with no commit
    anywhere in the worktree history is abandoned as never-performed.
    Nothing is re-performed and no completion is fabricated.
    """
    closed: list[str] = []
    root = worktree.resolve() if worktree is not None else None
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT action_key FROM system_actions "
            "WHERE kind = 'git_commit' AND status = ?",
            (STARTED,),
        ).fetchall()
    for row in rows:
        candidate = str(row["action_key"])
        state = effect_state(database, candidate)
        identity = state.get("request_identity") or {}
        if str(identity.get("mission_id") or "") != mission_id:
            continue
        step_id = str(identity.get("step_id") or "")
        run_id = str(identity.get("run_id") or "")
        landed = None
        if root is not None and step_id and run_id:
            landed = _recorded_commit(
                root,
                f"jarvis: {mission_id} {step_id} verified PASS (run {run_id})",
            )
        if landed is not None:
            freeze_effect(
                database, candidate,
                reason="mission closed after its commit landed without a receipt",
            )
            reconcile_effect(
                database, candidate,
                provider_state={"exists": True, "commit_id": landed},
            )
            closed.append(candidate)
            continue
        abandon_effect(
            database, candidate,
            reason=(
                "mission reached a terminal state with this commit claim "
                "never dispatched; the connector was never invoked"
            ),
        )
        closed.append(candidate)
    return closed

def _changed_paths(worktree: Path) -> set[str]:
    """Repo-relative posix paths changed in the worktree, minus artifacts.

    Covers staged, unstaged, and untracked files so a scope check cannot be
    dodged by leaving a file unadded.  Control/build artifacts (result
    file, prescribed plan file, Python caches) are tolerated evidence, not
    scope — they never stage and never trigger a refusal.
    """
    changed: set[str] = set()
    for args in (
        ("diff", "--cached", "--name-only"),
        ("diff", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        changed.update(
            name for name in _git(worktree, *args).splitlines() if name.strip()
        )
    if _binding is not None:
        changed = {
            name for name in changed if not _binding.scope_ignored(name)
        }
    return {name for name in changed if name.strip() != RESULT_FILENAME}


def _worktree_has_committable_change(worktree: Path) -> bool:
    """True when the worktree holds anything to commit besides artifacts.

    ``commit_after_pass`` deliberately resets ``RESULT_FILENAME`` out of the
    index, so a worktree whose only difference is the worker result file has
    nothing to commit.  Control/build artifacts (plan file, caches) are
    tolerated evidence with the same standing.
    """
    return bool(_changed_paths(worktree))


def _recorded_commit(root: Path, message: str) -> str | None:
    """Find the exact deterministic commit receipt in durable Git truth."""
    raw = _git(root, "log", "--format=%H%x00%B%x00")
    parts = raw.split("\x00")
    for index in range(0, len(parts) - 1, 2):
        commit_id = parts[index].strip()
        body = parts[index + 1].strip()
        if commit_id and body == message:
            return commit_id
    return None


def _commit_result(root: Path, commit_id: str, message: str) -> dict[str, Any]:
    baseline = _git(root, "rev-parse", f"{commit_id}^")
    return {
        "claimed": COMPLETED,
        "performed": False,
        "baseline_commit": baseline,
        "commit_head": commit_id,
        "message": message,
        **_commit_diff_metadata(root, baseline, commit_id),
    }


def _commit_diff_metadata(
    root: Path, baseline: str, commit_id: str,
) -> dict[str, Any]:
    """Collect optional diff details without changing commit-effect truth.

    Git for Windows can parse ``<base>..<head>`` as a path when the working
    tree is long enough. Pass each revision as its own argument and terminate
    revision parsing with ``--``. If auxiliary metadata still fails after the
    commit is durable, keep the commit result truthful and mark only its
    metadata as degraded.
    """
    try:
        diff_stat = _git(
            root, "diff", "--stat", baseline, commit_id, "--",
        )
        files_changed = _git(
            root, "diff", "--name-only", baseline, commit_id, "--",
        ).splitlines()
    except (CommitError, subprocess.TimeoutExpired, OSError) as error:
        return {
            "diff_stat": None,
            "files_changed": [],
            "diff_metadata_state": "DEGRADED",
            "diff_metadata_error_code": "commit_diff_metadata_failed",
            "diff_metadata_error_type": type(error).__name__,
            "diff_metadata_error_detail": redact_text(str(error))[:300],
        }
    return {
        "diff_stat": diff_stat,
        "files_changed": files_changed,
        "diff_metadata_state": "COMPLETE",
        "diff_metadata_error_code": None,
        "diff_metadata_error_type": None,
        "diff_metadata_error_detail": None,
    }


def commit_after_pass(
    database: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str,
    run_id: str,
    worktree: Path,
    policy: ActionPolicy,
    envelope: ActionEnvelope,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create one local commit for a verified step; return before/after state.

    Refuses (CommitError) when: the step has no PASS verdict; the policy does
    not ALLOW the commit; or git fails. Never commits on FAIL/BLOCKED/
    NOT_TESTED — the PASS verdict is checked first, from the durable ledger.
    """
    verdict = latest_verdict_for_step(database, mission_id, step_id)
    if verdict is None:
        raise CommitError(f"no verification verdict for {mission_id} step {step_id}")
    if verdict["status"] != "PASS" or verdict.get("verdict") != "PASS":
        raise CommitError(
            f"refusing commit: step {step_id} verdict is "
            f"{verdict['status']} (commit requires verified PASS)")
    if verdict.get("builder_run_id") and verdict["builder_run_id"] != run_id:
        raise CommitError(
            f"refusing commit: run {run_id} does not match the PASS verdict's "
            f"builder run {verdict['builder_run_id']}")

    # A bound improvement mission commits only inside its authorized scope;
    # anything else fails closed here rather than landing unbounded.  Legacy
    # missions (no improvement lineage) keep the unbounded path.
    scope: dict[str, Any] | None = None
    if _binding is not None:
        try:
            scope = _binding.commit_scope_for_mission(database, mission_id)
        except Exception as error:  # noqa: BLE001 - fail closed, never crash past
            raise CommitError(
                f"refusing commit: bound scope does not resolve: {error}"
            ) from error

    decision = evaluate_and_record(database, policy, envelope)
    if decision.decision != ActionDecision.ALLOWED:
        raise CommitError(f"commit not allowed: {decision.reason}")

    def _finalize(result: dict[str, Any]) -> dict[str, Any]:
        """Attach the JARVIS-owned candidate envelope to a real commit.

        Legacy (unscoped) commits pass through untouched.  A bound commit
        whose head did not advance is a no-change replay, not a candidate.
        Recording is idempotent, so the crash-after-commit replay path and
        the COMPLETED-effect replay path converge on one envelope.
        """
        if scope is None or _binding is None:
            return result
        commit_head = str(result.get("commit_head") or "")
        baseline_commit = str(result.get("baseline_commit") or "")
        if not commit_head or commit_head == baseline_commit:
            return result
        try:
            tree_sha = _git(worktree, "rev-parse", f"{commit_head}^{{tree}}")
            candidate = _binding.record_candidate(
                database,
                mission_id=mission_id, step_id=step_id, run_id=run_id,
                verdict_id=str(verdict.get("verdict_id") or ""),
                resource_id=str(scope["resource_id"]),
                improvement_id=str(scope["improvement_id"]),
                baseline_sha=str(scope["baseline_sha"]),
                commit_head=commit_head, tree_sha=tree_sha,
                worktree=str(worktree),
                files_changed=list(result.get("files_changed") or []),
            )
        except Exception as error:  # noqa: BLE001 - fail closed, never crash past
            raise CommitError(
                f"commit landed but the candidate envelope refused: {error}"
            ) from error
        result["candidate_id"] = candidate["candidate_id"]
        result["tree_sha"] = tree_sha
        result["candidate_deduplicated"] = candidate["deduplicated"]
        return result

    worktree = worktree.resolve()
    message = f"jarvis: {mission_id} {step_id} verified PASS (run {run_id})"
    action_key = _commit_effect_id(
        mission_id=mission_id, step_id=step_id, run_id=run_id,
        worktree=worktree, envelope=envelope,
    )

    # A repair run re-dispatches the step under a new run identity, so the
    # superseded attempt's claim can never complete. Close it before this
    # run's own exact-identity recovery, otherwise it stays STARTED forever
    # and the pre-live ambiguity audit correctly refuses to proceed.
    _abandon_superseded_commit_claims(
        database,
        mission_id=mission_id,
        step_id=step_id,
        current_run_id=run_id,
        worktree=worktree,
        envelope=envelope,
    )

    # A process may die after Git durably accepted the commit but before the
    # effect journal received its terminal event. Reconstruct from Git, freeze
    # the interrupted claim, and reconcile it before continuation. Nothing is
    # re-dispatched and the same logical effect identity is retained.
    state = effect_state(database, action_key)
    if state.get("status") in {STARTED, FROZEN}:
        landed = _recorded_commit(worktree, message)
        if landed is not None:
            if state.get("status") == STARTED:
                freeze_effect(
                    database, action_key,
                    reason="process interrupted after local commit dispatch",
                )
            reconciliation = reconcile_effect(
                database, action_key,
                provider_state={"exists": True, "commit_id": landed},
            )
            if reconciliation.get("reconciled") is True:
                recovered = _commit_result(worktree, landed, message)
                recovered.update({
                    "action_key": action_key,
                    "journal_state": FROZEN,
                    "replayed": True,
                    "reconciled": True,
                })
                return _finalize(recovered)
        else:
            reconcile_effect(
                database, action_key,
                provider_state={"exists": False, "commit_id": None},
            )
        raise CommitError(
            "local commit effect is unresolved after interruption; "
            "reconciliation refused a blind retry"
        )
    if state.get("status") == FAILED:
        raise CommitError("local commit effect previously failed")

    def perform() -> dict[str, Any]:
        before = _git(worktree, "rev-parse", "HEAD")
        if scope is not None:
            changed = _changed_paths(worktree)
            outside = _binding.check_commit_scope(
                tuple(scope["allowed_paths"]), changed)
            if outside:
                raise CommitError(
                    "refusing commit: worktree holds changes outside the "
                    f"bound scope: {', '.join(outside)}"
                )
            to_stage = sorted(changed & {
                str(path) for path in scope["allowed_paths"]})
            if not to_stage:
                raise CommitError(
                    "refusing commit: nothing committable inside the bound scope"
                )
            _git(worktree, "reset", "-q", "--", RESULT_FILENAME)
            _git(worktree, "add", "--", *to_stage)
            _git(worktree, "reset", "-q", "--", RESULT_FILENAME)
        else:
            _git(worktree, "reset", "-q", "--", RESULT_FILENAME)
            _git(worktree, "add", "-A")
            _git(worktree, "reset", "-q", "--", RESULT_FILENAME)
        _git(worktree, "commit", "-m", message)
        after = _git(worktree, "rev-parse", "HEAD")
        invoke_fault_hook(fault_hook, "post effect dispatch")
        return {
            "claimed": COMPLETED,
            "performed": True,
            "baseline_commit": before,
            "commit_head": after,
            "message": message,
            **_commit_diff_metadata(worktree, before, after),
        }

    def _claim_worker_commit(
        head: str, baseline: str,
    ) -> dict[str, Any] | None:
        """Claim a worker-landed commit as the candidate, if one exists.

        The worker may commit inside its own step (it holds the worktree
        capability).  When HEAD advanced past the pinned baseline with a
        clean tree, the change is real verified work — envelope it rather
        than leaving it unenveloped.  The diff is scope-checked first: a
        worker commit outside the bound scope fails closed.  Returns None
        when HEAD is still the baseline (true no-change).
        """
        if not head or head.lower() == baseline:
            return None
        try:
            names = _git(
                worktree, "diff", "--name-only", baseline, head, "--",
            ).splitlines()
        except (CommitError, subprocess.TimeoutExpired, OSError) as error:
            raise CommitError(
                f"refusing claim: cannot diff worker head {head[:12]}... "
                f"against baseline: {error}"
            ) from error
        changed = {name for name in names if name.strip()}
        changed = {
            name for name in changed
            if not _binding.scope_ignored(name)
        }
        if not changed:
            return None
        outside = _binding.check_commit_scope(
            tuple(scope["allowed_paths"]), changed)
        if outside:
            raise CommitError(
                "refusing claim: worker committed outside the bound "
                f"scope: {', '.join(outside)}"
            )
        return {
            "claimed": COMPLETED,
            "performed": False,
            "worker_committed": True,
            "baseline_commit": baseline,
            "commit_head": head,
            "message": message,
            "files_changed": sorted(changed),
            "diff_stat": "",
            "diff_metadata_state": "COMPLETE",
            "diff_metadata_error_code": None,
            "diff_metadata_error_type": None,
            "diff_metadata_error_detail": None,
            "action_key": action_key,
        }

    def dispatch_effect() -> dict[str, Any]:
        result = execute_fabric_effect(
            database,
            action_key=action_key,
            system_id=str(envelope.resource_id or "git-local"),
            target_ref=str(worktree),
            payload_hash=action_key.removeprefix("effect:"),
            kind=str(envelope.action_kind or "git_commit"),
            connector_id="git-local-commit",
            expected_effects=("create one verified local commit",),
            action_id=envelope.action_id,
            request_identity={
                "mission_id": mission_id,
                "step_id": step_id,
                "run_id": run_id,
            },
            perform=perform,
            stop_check=lambda: emergency_stop_reason(
                database, policy.stop_path
            ) is not None,
        )
        if result.get("journal_state") != COMPLETED:
            raise CommitError(
                "local commit effect did not complete: "
                f"{result.get('error_code') or result.get('journal_state') or 'UNKNOWN'}"
            )
        return result

    # A completed logical effect is immutable: replay its recorded commit
    # result even if the worktree is now clean or has later changes. This also
    # preserves whether its auxiliary diff metadata was complete or degraded.
    if state.get("status") == COMPLETED:
        return _finalize(dispatch_effect())

    # A verified step that changed nothing in the worktree has nothing to
    # commit. Dispatching `git commit` anyway fails on an empty index and the
    # mission loop records BLOCKED, so a verified read-only mission would be
    # reported as a failure. Report the honest no-change result instead: the
    # PASS stands, no effect is dispatched, and the mission continues.
    if not _worktree_has_committable_change(worktree):
        head = _git(worktree, "rev-parse", "HEAD")
        if scope is not None and _binding is not None:
            claimed = _claim_worker_commit(
                head, str(scope["baseline_sha"]).lower())
            if claimed is not None:
                return _finalize(claimed)
        return {
            "performed": False,
            "no_change": True,
            "reason": (
                "worktree unchanged after verified PASS; no commit was needed"
            ),
            "baseline_commit": head,
            "commit_head": head,
            "message": message,
            "diff_stat": "",
            "files_changed": [],
            "diff_metadata_state": "NOT_REQUIRED",
            "diff_metadata_error_code": None,
            "diff_metadata_error_type": None,
            "diff_metadata_error_detail": None,
            "action_key": action_key,
        }

    invoke_fault_hook(fault_hook, "pre effect dispatch")
    if scope is not None and _binding is not None:
        # Fail fast before the effect journal opens a claim: a scope refusal
        # is not a failed effect, so it must not leave a FAILED claim behind.
        # perform() re-checks authoritatively inside the claim window.
        outside = _binding.check_commit_scope(
            tuple(scope["allowed_paths"]), _changed_paths(worktree))
        if outside:
            raise CommitError(
                "refusing commit: worktree holds changes outside the "
                f"bound scope: {', '.join(outside)}"
            )
    return _finalize(dispatch_effect())
