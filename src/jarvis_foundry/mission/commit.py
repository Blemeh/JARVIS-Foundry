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
from typing import Any

from ..control.action_envelope import evaluate_and_record
from ..control.policy import ActionPolicy
from ..database import FoundryDatabase
from ..domain import ActionDecision, ActionEnvelope
from ..workers.result_contract import RESULT_FILENAME
from .verification import latest_verdict_for_step


class CommitError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    if result.returncode != 0:
        raise CommitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def commit_after_pass(
    database: FoundryDatabase,
    *,
    mission_id: str,
    step_id: str,
    run_id: str,
    worktree: Path,
    policy: ActionPolicy,
    envelope: ActionEnvelope,
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
    before = _git(worktree, "rev-parse", "HEAD")
    message = f"jarvis: {mission_id} {step_id} verified PASS (run {run_id})"
    _git(worktree, "reset", "-q", "--", RESULT_FILENAME)
    _git(worktree, "add", "-A")
    _git(worktree, "reset", "-q", "--", RESULT_FILENAME)
    _git(worktree, "commit", "-m", message)
    after = _git(worktree, "rev-parse", "HEAD")
    return {
        "baseline_commit": before,
        "commit_head": after,
        "message": message,
        "diff_stat": _git(worktree, "diff", "--stat", f"{before}..{after}"),
        "files_changed": _git(worktree, "diff", "--name-only", f"{before}..{after}").splitlines(),
    }
