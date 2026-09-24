"""V3 Discovery and Building: Git external-effect adapter (WP5).

``GitEffectAdapter`` executes git push effects through the EXISTING
effects journal (exactly-once, STOP/veto-gated, ALLOW-ticket required)
and reconciles against PROVIDER-SIDE truth via ``git ls-remote`` —
never the local exit code.

Ambiguity contract (spec §3.9 / reconciliation rules §6): an ambiguous
push (timeout or crash window) freezes via ``effect.reconcile_pending``
semantics and MUST be resolved by ``reconcile_push`` before any retry.
The adapter never blind-retries: a second push of the same identity is
refused while reconciliation is pending.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from .database import FoundryDatabase
from .effects import COMPLETED, FAILED, FROZEN, effect_identity, effect_state
from .events import Event
from .platform.windows_subprocess import headless_run_kwargs
from .v3_fault import V3InjectedFault, invoke_fault_hook


class GitReconcilePending(RuntimeError):
    """Raised when a push may or may not have landed remotely.

    Callers must run ``reconcile_push`` (ls-remote) before ANY retry;
    blind retry duplicates external effects.
    """

    def __init__(self, action_key: str) -> None:
        super().__init__(
            f"git push {action_key} ambiguous — reconcile via ls-remote "
            "before retry (never blind-retry)")
        self.action_key = action_key


def ls_remote_head(remote: str | Path, ref: str = "main") -> str | None:
    """PROVIDER-SIDE truth: the remote's current head for ``ref``."""
    out = subprocess.run(
        ["git", "ls-remote", str(remote), ref],
        capture_output=True, text=True, timeout=60, **headless_run_kwargs())
    if out.returncode != 0:
        raise RuntimeError(
            f"git ls-remote failed against {remote}: {out.stderr.strip()}")
    value = out.stdout.split()
    return value[0] if value else None


def preflight_identity(*, worktree: str | Path, remote: str | Path,
                       branch: str = "main",
                       expected_head: str | None = None,
                       reject_dirty: bool = False) -> dict[str, Any]:
    """Resolve worktree/branch/remote identity BEFORE any mutation.

    T-17 mitigation: wrong-target binding fails closed here. The
    returned identity is what the effect journal records as provenance.
    """
    worktree = Path(worktree)
    if not (worktree / ".git").exists():
        raise ValueError(f"not a git worktree: {worktree}")
    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30, **headless_run_kwargs())
    if head.returncode != 0:
        raise ValueError(f"cannot resolve HEAD in {worktree}: {head.stderr.strip()}")
    status = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True, text=True, timeout=30, **headless_run_kwargs())
    dirty = bool(status.stdout.strip())
    if reject_dirty and dirty:
        raise ValueError(
            f"dirty worktree: refusing git effect for {worktree}")
    remote_head = ls_remote_head(remote, branch)
    identity = {
        "worktree": str(worktree.resolve()),
        "branch": branch,
        "local_head": head.stdout.strip(),
        "remote": str(remote),
        "remote_head": remote_head,
        "dirty": dirty,
        "expected_head": expected_head,
    }
    if expected_head is not None and expected_head != head.stdout.strip():
        # A caller pinning expected_head means 'the commit I built'; we
        # never commit here (commits are a separate recorded effect), so
        # a mismatch refuses the binding.
        raise ValueError(
            f"expected_head mismatch: pinned {expected_head}, "
            f"HEAD is {head.stdout.strip()} — refusing to bind")
    return identity


class GitEffectAdapter:
    """Git push/pull effects with provider-side reconciliation."""

    def __init__(self, database: FoundryDatabase, *, source_id: str = "v3-git",
                 fault_hook: Callable[[str], None] | None = None) -> None:
        self._database = database
        self._source_id = source_id
        self._fault_hook = fault_hook

    def push(
        self, *,
        action_id: str,
        worktree: str | Path,
        remote: str | Path,
        branch: str = "main",
        expected_head: str | None = None,
        execute: Callable[[], subprocess.CompletedProcess[str]] | None = None,
        stop_path: str | None = None,
    ) -> dict[str, Any]:
        """Push once, journal exactly-once, reconcile honestly.

        ``execute`` injects the real push command (production passes a
        ``git push`` runner; tests pass stubs). Outcomes:

        - COMPLETED: remote head advanced to our local head (verified).
        - FAILED: push rejected cleanly; recorded, no retry here.
        - AMBIGUOUS: timeout/crash window -> reconcile_pending; retrying
          raises until reconcile_push resolves it.
        """
        from .control.broker import emergency_stop_reason

        stop = emergency_stop_reason(self._database, stop_path)
        if stop:
            raise PermissionError(f"git push stopped: {stop}")

        key = f"v3-git:{action_id}"
        state = effect_state(self._database, key)

        if state["status"] in (COMPLETED,):
            return {"outcome": "COMPLETED", "action_key": key,
                    "performed": False}
        if state["status"] == FROZEN:
            with self._database.connect() as connection:
                reconciled = connection.execute(
                    "SELECT 1 FROM events WHERE event_type = "
                    "'effect.reconciled' AND json_extract(payload_json, "
                    "'$.action_key') = ? LIMIT 1", (key,)).fetchone()
            if reconciled:
                return {"outcome": "COMPLETED", "action_key": key,
                        "performed": False, "reconciled": True}
            raise GitReconcilePending(key)
        if state["status"] == FAILED:
            # Clean rejection is terminal for this identity; a NEW push
            # after repair is a NEW action_id by definition.
            raise RuntimeError(
                f"git push {action_id} previously failed — record a new "
                "action after repairing the cause; no silent re-push")
        if state["status"] == "STARTED":
            # A crash between the durable claim and any terminal event. The
            # push may or may not have landed: never re-execute; freeze for
            # ls-remote reconciliation exactly like an ambiguous timeout.
            if not state.get("claim_active"):
                self._append("effect.frozen", {
                    "action_key": key,
                    "reason": "interrupted git push — frozen pending "
                              "ls-remote reconciliation"}, "frozen.interrupted")
            raise GitReconcilePending(key)

        identity = preflight_identity(
            worktree=worktree, remote=remote, branch=branch,
            expected_head=expected_head, reject_dirty=True)

        self._append("system.action_started", {
            "action_key": key, "action_id": action_id,
            "target_ref": identity["remote"],
            "expected_head": identity["local_head"],
            "before_state_capture": {"remote_head": identity["remote_head"]},
            "expected_effects": [
                f"{identity['branch']} at "
                f"{identity['local_head'][:12]} on {identity['remote']}"],
            "kind": "git.push", "identity": identity}, "started")

        try:
            invoke_fault_hook(self._fault_hook, "mid git push")
            result = (execute or self._default_execute)(
                worktree, remote, branch)
        except V3InjectedFault as injected:
            try:
                self._mark_ambiguous(
                    key, action_id,
                    "fault injected during push; remote state unknown")
            except V3InjectedFault:
                pass
            raise injected
        except (TimeoutError, subprocess.TimeoutExpired):
            self._append("effect.reconcile_pending", {
                "action_key": key, "action_id": action_id,
                "timeout_at": None, "reason": "push timed out; remote "
                "state unknown"}, "pending")
            self._append("effect.frozen", {
                "action_key": key,
                "reason": "ambiguous push outcome — frozen pending "
                          "ls-remote reconciliation"}, "frozen.pending")
            invoke_fault_hook(self._fault_hook, "post effect.reconcile_pending")
            raise GitReconcilePending(key)
        except Exception as exc:  # noqa: BLE001 — recorded, never retried here
            self._append("system.action_failed", {
                "action_key": key, "kind": "git.push",
                "reason": f"push rejected: {exc}",
                "after_state": {"remote_head":
                                ls_remote_head_safe(remote, branch)}},
                "failed")
            return {"outcome": "FAILED", "action_key": key,
                    "reason": str(exc), "performed": True}

        actual_remote = ls_remote_head(remote, branch)
        local_head = identity["local_head"]
        if actual_remote == local_head:
            self._append("system.action_completed", {
                "action_key": key, "action_id": action_id,
                "after_state": {"remote_head": actual_remote},
                "provider_state": {"source": "git ls-remote",
                                   "head": actual_remote},
                "cost": {}, "consequence": {
                    "expected_effects": [f"{branch} at {local_head[:12]}"],
                    "verification_required": False}}, "completed")
            return {"outcome": "COMPLETED", "action_key": key,
                    "remote_head": actual_remote, "performed": True}

        # The push claims success but ls-remote disagrees (or it failed
        # silently): ambiguity, not completion.
        reason = ("push did not advance the remote — ls-remote shows "
                  f"{actual_remote!r} != expected {local_head[:12]!r}"
                  if actual_remote else
                  "push produced no remote ref — branch absent upstream")
        self._append("effect.reconcile_mismatch", {
            "action_key": key, "expected": local_head,
            "actual": actual_remote}, "mismatch")
        self._append("system.action_failed", {
            "action_key": key, "kind": "git.push", "reason": reason,
            "after_state": {"remote_head": actual_remote}}, "failed.mismatch")
        return {"outcome": "FAILED", "action_key": key, "reason": reason,
                "performed": True}

    def reconcile_pending(self, *, action_id: str,
                          remote: str | Path, branch: str = "main",
                          expected_head: str) -> dict[str, Any]:
        """Resolve an ambiguous push via PROVIDER-SIDE truth."""
        key = f"v3-git:{action_id}"
        actual_remote = ls_remote_head(remote, branch)
        if actual_remote == expected_head:
            self._append("effect.reconciled", {
                "action_key": key,
                "provider_claim": {"source": "git ls-remote",
                                   "head": actual_remote}},
                "reconciled")
            return {"reconciled": True, "action_key": key,
                    "remote_head": actual_remote}
        self._append("effect.reconcile_mismatch", {
            "action_key": key, "expected": expected_head,
            "actual": actual_remote}, "mismatch.recheck")
        return {"reconciled": False, "action_key": key,
                "remote_head": actual_remote,
                "reason": "remote does not carry the expected head"}

    def _append(self, event_type: str, payload: dict[str, Any],
                suffix: str) -> None:
        key = payload["action_key"]
        self._database.append(Event.create(
            event_type, payload,
            source_type="supervisor", source_id=self._source_id,
            idempotency_key=f"{key}.{suffix}"))

    def _mark_ambiguous(self, key: str, action_id: str, reason: str) -> None:
        """Persist a pending/frozen prefix before an injected crash escapes."""
        self._append("effect.reconcile_pending", {
            "action_key": key, "action_id": action_id,
            "timeout_at": None, "reason": reason}, "pending")
        self._append("effect.frozen", {
            "action_key": key,
            "reason": "ambiguous push outcome — frozen pending "
                      "ls-remote reconciliation"}, "frozen.pending")

    @staticmethod
    def _default_execute(worktree: str | Path, remote: str | Path,
                         branch: str) -> subprocess.CompletedProcess[str]:
        out = subprocess.run(
            ["git", "-C", str(worktree), "push", str(remote), branch],
            capture_output=True, text=True, timeout=90, **headless_run_kwargs())
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or "git push failed")
        return out


def ls_remote_head_safe(remote: str | Path, ref: str = "main") -> str | None:
    try:
        return ls_remote_head(remote, ref)
    except Exception:  # noqa: BLE001 — best-effort evidence capture
        return None
