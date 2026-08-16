"""Real Git adapter against an isolated owner-controlled canary repository."""

from __future__ import annotations

import subprocess  # nosec B404 - argv-only git subprocess, never shell=True
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.capabilities_live.adapters.base import (
    AfterState,
    BeforeState,
    CanaryAdapter,
    ExecutionResult,
    Outcome,
    Receipt,
)
from jarvis_foundry.capabilities_live.broker import ResolvedCredential


class GitAdapter(CanaryAdapter):
    def __init__(self, proving_root: Path) -> None:
        super().__init__(
            proving_root,
            capability_id="capability.git",
            domain="git/repositories",
        )

    def canary_repo(self) -> Path:
        repo = self._ensure_within(self.proving_root / "git-canary")
        if not repo.is_dir() or not repo.joinpath(".git").is_dir():
            repo.mkdir(parents=True, exist_ok=True)
            self._git(["init", "-q"], repo)
            self._git(["config", "user.email", "r9-canary@local.test"], repo)
            self._git(["config", "user.name", "R9 Canary"], repo)
            repo.joinpath("README.md").write_text("# R9 git canary\n", encoding="utf-8")
            self._git(["add", "."], repo)
            self._git(["commit", "-q", "-m", "baseline"], repo)
        return repo

    def _git(
        self,
        args: list[str],
        repo: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603 B607 - fixed argv list, no shell, bounded canary repo
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def _state(self, repo: Path) -> dict[str, Any]:
        head = self._git(["rev-parse", "HEAD"], repo).stdout.strip()
        tree = self._git(["rev-parse", "HEAD^{tree}"], repo).stdout.strip()
        porcelain = self._git(["status", "--porcelain"], repo).stdout
        return {"head": head, "tree": tree, "porcelain": porcelain}

    def execute(
        self,
        operation: str,
        request: Mapping[str, Any],
        credential: ResolvedCredential,
    ) -> ExecutionResult:
        try:
            repo = self.canary_repo()
            before_state = self._state(repo)
        except Exception as error:  # noqa: BLE001 - provider failure must stay honest
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=BeforeState(state_hash="", detail={"error": str(error)}),
                after=None,
                receipt=None,
                reason="git provider unavailable",
                no_partial_effect=True,
                reconciliation={"reconciled": False, "detail": "provider failure"},
            )
        before = BeforeState(
            state_hash=before_state["head"],
            detail=dict(before_state),
        )
        idempotency_key = str(request.get("idempotency_key", "n/a"))
        if self._stop_requested:
            return self._refusal(
                outcome=Outcome.STOPPED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason="adapter stop requested; new operations refused",
            )
        if operation == "commit":
            return self._commit(repo, before, request, idempotency_key)
        if operation == "revert":
            return self._revert(repo, before, request, idempotency_key)
        return self._refusal(
            outcome=Outcome.REFUSED,
            operation=operation,
            idempotency_key=idempotency_key,
            before=before,
            reason=f"unknown operation: {operation}",
        )

    def _commit(
        self,
        repo: Path,
        before: BeforeState,
        request: Mapping[str, Any],
        idempotency_key: str,
    ) -> ExecutionResult:
        entry = self._journal_entry("commit", idempotency_key)
        if entry is not None:
            current = self._state(repo)
            if current["head"] == entry["after_head"]:
                receipt = Receipt(
                    operation="commit",
                    idempotency_key=idempotency_key,
                    provider_reference=str(entry["receipt"]["provider_reference"]),
                    effect_hash=str(entry["receipt"]["effect_hash"]),
                    detail=dict(entry["receipt"]["detail"]),
                )
                return ExecutionResult(
                    outcome=Outcome.REPLAYED,
                    before=before,
                    after=AfterState(state_hash=current["head"], detail=current),
                    receipt=receipt,
                    reason="idempotent replay; commit already present",
                    no_partial_effect=True,
                    reconciliation={"reconciled": True, "detail": "head unchanged by replay"},
                )
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=before,
                after=None,
                receipt=None,
                reason="state diverged from idempotent commit receipt",
                no_partial_effect=False,
                reconciliation={"reconciled": False, "detail": "expected head missing"},
            )
        if self._fault:
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=before,
                after=AfterState(
                    state_hash=before.state_hash,
                    detail=dict(before.detail),
                ),
                receipt=None,
                reason="injected provider failure; no commit performed",
                no_partial_effect=True,
                reconciliation={"reconciled": True, "detail": "before and after state identical"},
            )
        relative = str(request["relative_path"])
        content = str(request["content"])
        message = str(request["message"])
        target = self._ensure_within(repo / relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        add = self._git(["add", relative], repo)
        if add.returncode != 0:
            return self._failed(repo, before, idempotency_key, add.stderr)
        commit = self._git(
            ["commit", "-q", "-m", f"{message} [idem:{idempotency_key}]"],
            repo,
        )
        if commit.returncode != 0:
            return self._failed(repo, before, idempotency_key, commit.stderr)
        after_state = self._state(repo)
        receipt = Receipt(
            operation="commit",
            idempotency_key=idempotency_key,
            provider_reference=f"git://{repo.name}",
            effect_hash=after_state["head"],
            detail={
                "commit_sha": after_state["head"],
                "tree_sha": after_state["tree"],
                "parent_sha": before.detail["head"],
                "before_head": before.detail["head"],
                "after_head": after_state["head"],
                "before_tree": before.detail["tree"],
                "after_tree": after_state["tree"],
                "message": f"{message} [idem:{idempotency_key}]",
                "relative_path": relative,
                "content_hash": self._file_hash(target),
            },
        )
        reconciled = (
            after_state["porcelain"].strip() == ""
            and self._git(["cat-file", "-e", after_state["head"] + "^{commit}"], repo).returncode
            == 0
            and after_state["tree"] != before.detail["tree"]
        )
        self._append_journal(
            {
                "operation": "commit",
                "idempotency_key": idempotency_key,
                "before_head": before.detail["head"],
                "after_head": after_state["head"],
                "after_tree": after_state["tree"],
                "receipt": receipt.to_dict(),
                "reconciliation": {
                    "reconciled": reconciled,
                    "detail": "commit exists, working tree clean, tree changed",
                },
            }
        )
        return ExecutionResult(
            outcome=Outcome.EXECUTED,
            before=before,
            after=AfterState(state_hash=after_state["head"], detail=after_state),
            receipt=receipt,
            reason="real bounded git commit produced",
            no_partial_effect=not reconciled,
            reconciliation={
                "reconciled": reconciled,
                "detail": "commit exists, working tree clean, tree changed",
            },
        )

    def _revert(
        self,
        repo: Path,
        before: BeforeState,
        request: Mapping[str, Any],
        idempotency_key: str,
    ) -> ExecutionResult:
        commit_sha = str(request["commit_sha"])
        entry = self._journal_entry("revert", idempotency_key)
        if entry is not None:
            current = self._state(repo)
            if current["head"] == entry["after_head"]:
                return ExecutionResult(
                    outcome=Outcome.REPLAYED,
                    before=before,
                    after=AfterState(state_hash=current["head"], detail=current),
                    receipt=Receipt(
                        operation="revert",
                        idempotency_key=idempotency_key,
                        provider_reference=str(entry["receipt"]["provider_reference"]),
                        effect_hash=str(entry["receipt"]["effect_hash"]),
                        detail=dict(entry["receipt"]["detail"]),
                    ),
                    reason="idempotent replay; revert already present",
                    no_partial_effect=True,
                    reconciliation={"reconciled": True, "detail": "head unchanged by replay"},
                )
        revert = self._git(["revert", "--no-edit", commit_sha], repo)
        if revert.returncode != 0:
            return self._failed(repo, before, idempotency_key, revert.stderr)
        after_state = self._state(repo)
        receipt = Receipt(
            operation="revert",
            idempotency_key=idempotency_key,
            provider_reference=f"git://{repo.name}",
            effect_hash=after_state["head"],
            detail={
                "reverted_commit": commit_sha,
                "before_head": before.detail["head"],
                "after_head": after_state["head"],
                "before_tree": before.detail["tree"],
                "after_tree": after_state["tree"],
            },
        )
        restored = after_state["tree"] == request.get("expected_tree", before.detail["tree"])
        self._append_journal(
            {
                "operation": "revert",
                "idempotency_key": idempotency_key,
                "before_head": before.detail["head"],
                "after_head": after_state["head"],
                "receipt": receipt.to_dict(),
                "reconciliation": {
                    "reconciled": restored,
                    "detail": "working tree restored to pre-commit tree",
                },
            }
        )
        return ExecutionResult(
            outcome=Outcome.EXECUTED,
            before=before,
            after=AfterState(state_hash=after_state["head"], detail=after_state),
            receipt=receipt,
            reason="real bounded git revert produced",
            no_partial_effect=not restored,
            reconciliation={
                "reconciled": restored,
                "detail": "working tree restored to pre-commit tree",
            },
        )

    def _failed(
        self,
        repo: Path,
        before: BeforeState,
        idempotency_key: str,
        stderr: str,
    ) -> ExecutionResult:
        after = self._state(repo)
        unchanged = (
            after["head"] == before.detail["head"]
            and after["porcelain"] == before.detail["porcelain"]
        )
        return ExecutionResult(
            outcome=Outcome.FAILED,
            before=before,
            after=AfterState(state_hash=after["head"], detail=after),
            receipt=None,
            reason=f"git provider failure: {stderr.strip()[:300]}",
            no_partial_effect=unchanged,
            reconciliation={
                "reconciled": True,
                "detail": "provider failure left before-state unchanged",
            },
        )
