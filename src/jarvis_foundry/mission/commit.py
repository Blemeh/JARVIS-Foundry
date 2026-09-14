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
        "diff_stat": _git(root, "diff", "--stat", f"{baseline}..{commit_id}"),
        "files_changed": _git(
            root, "diff", "--name-only", f"{baseline}..{commit_id}"
        ).splitlines(),
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

    decision = evaluate_and_record(database, policy, envelope)
    if decision.decision != ActionDecision.ALLOWED:
        raise CommitError(f"commit not allowed: {decision.reason}")

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
                return recovered
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
            "diff_stat": _git(
                worktree, "diff", "--stat", f"{before}..{after}"
            ),
            "files_changed": _git(
                worktree, "diff", "--name-only", f"{before}..{after}"
            ).splitlines(),
        }

    invoke_fault_hook(fault_hook, "pre effect dispatch")
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
