"""Real executors for fabric actions.

Each handler performs the effect through an existing connector or a bounded
local tool, then returns the claimed payload used by verification. Nothing
here writes into ``src/``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..control.broker import emergency_stop_reason
from ..control.redaction import redact_text
from ..connectors.filesystem import FilesystemConnector
from ..connectors.git import GitConnector
from ..connectors.github_research import GitHubResearchConnector
from ..connectors.account_activation import CredentialHandle, ProviderIdentity
from ..connectors.gmail_api import (
    GmailApiProvider,
    GmailSendAuthorization,
    GmailSendEffectDriver,
)
from ..connectors.local_artifacts import (
    ArtifactPostWriteError,
    ArtifactValidationError,
    InjectedArtifactFailure,
    LocalArtifactConnector,
    LocalArtifactError,
)
from ..connectors.github_write import (
    GitHubPullRequestEffectDriver,
    GitHubRepositoryAuthorization,
    GitHubWriteAdapter,
    GitPushTransport,
)
from ..database import FoundryDatabase
from ..effects import (
    COMPLETED,
    FROZEN,
    effect_state,
    execute_fabric_effect,
    finalize_verified_effect,
)
from .model import ClaimOutcome, FabricError


@dataclass(slots=True)
class ExecutorContext:
    database: FoundryDatabase
    owned_root: Path
    github_api: Callable[[str], tuple[int, bytes]] | None = None
    github_write: Callable[[str, Mapping[str, Any]], tuple[int, bytes]] | None = None
    github_write_adapter: GitHubWriteAdapter | None = None
    github_writes_enabled: bool = False
    github_authorized_repositories: tuple[str, ...] = ()
    github_repository_authorization: GitHubRepositoryAuthorization | None = None
    github_push_transport: GitPushTransport | None = None
    gmail_api_provider: GmailApiProvider | None = None
    gmail_credential: CredentialHandle | None = None
    gmail_identity: ProviderIdentity | None = None
    gmail_send_authorization: GmailSendAuthorization | None = None
    doc_transport: Callable[[str], tuple[int, bytes]] | None = None
    mcp_backend: Any | None = None
    mcp_mutation_verifier: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None
    operator_executable_allowlist: tuple[str | Path, ...] = ()
    operator_environment_allowlist: tuple[str, ...] = ()
    artifact_source_roots: tuple[str | Path, ...] = ()
    operator_connector: Any | None = None
    windows_operator_backend: Any | None = None

    def __post_init__(self) -> None:
        self.owned_root = Path(self.owned_root).expanduser().resolve()
        self.owned_root.mkdir(parents=True, exist_ok=True)
        self.operator_executable_allowlist = tuple(
            str(Path(value).expanduser().resolve(strict=True))
            for value in self.operator_executable_allowlist
        )
        self.operator_environment_allowlist = tuple(
            str(value) for value in self.operator_environment_allowlist
        )
        self.artifact_source_roots = tuple(
            str(Path(value).expanduser().resolve(strict=True))
            for value in self.artifact_source_roots
        )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def filesystem_write(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    target = str(payload["target"])
    content = str(payload["content"])
    path = (ctx.owned_root / target).resolve()
    try:
        path.relative_to(ctx.owned_root)
    except ValueError as error:
        raise FabricError("file.write target escapes owned root") from error
    path.parent.mkdir(parents=True, exist_ok=True)
    connector = FilesystemConnector(ctx.owned_root)
    attempt = connector.request(
        "file.write", target, content,
        idempotency_key=str(payload.get("idempotency_key") or f"write:{target}"),
    )
    snap = connector.snapshot(target)
    path = ctx.owned_root / target
    observed_text = path.read_text(encoding="utf-8") if path.is_file() else None
    observed_ok = (
        observed_text is not None
        and observed_text.replace("\r\n", "\n") == content.replace("\r\n", "\n")
    )
    return {
        "claimed": "COMPLETED" if observed_ok else attempt.outcome,
        "target": target,
        "path": str(path),
        "content_hash": snap.content_hash,
        "exists": snap.exists,
        "error_code": "" if observed_ok else attempt.error_code,
    }


def filesystem_append(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    target = str(payload["target"])
    content = str(payload["content"])
    path = (ctx.owned_root / target).resolve()
    try:
        path.relative_to(ctx.owned_root)
    except ValueError as error:
        raise FabricError("file.append target escapes owned root") from error
    path.parent.mkdir(parents=True, exist_ok=True)
    connector = FilesystemConnector(ctx.owned_root)
    connector.perform(target, content, action="file.append")
    snap = connector.snapshot(target)
    observed = path.read_text(encoding="utf-8") if path.is_file() else ""
    ok = content.replace("\r\n", "\n") in observed.replace("\r\n", "\n")
    return {
        "claimed": "COMPLETED" if ok else "FAILED",
        "target": target,
        "path": str(path),
        "content_hash": snap.content_hash,
        "exists": snap.exists,
        "appended": ok,
        "error_code": "" if ok else "append_not_observed",
    }


def filesystem_read(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    target = str(payload["target"])
    connector = FilesystemConnector(ctx.owned_root)
    attempt = connector.request("file.read", target, None, idempotency_key=f"read:{target}")
    snap = connector.snapshot(target)
    text = None
    if snap.exists and snap.content is not None:
        text = snap.content.decode("utf-8")
    return {
        "claimed": attempt.outcome,
        "target": target,
        "exists": snap.exists,
        "content_hash": snap.content_hash,
        "text": text,
        "error_code": attempt.error_code,
    }


def file_replace(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    target = str(payload["target"])
    old = str(payload["old"])
    new = str(payload["new"])
    read = filesystem_read(ctx, {"target": target})
    if not read.get("exists") or read.get("text") is None:
        return {
            "claimed": "FAILED", "target": target, "exists": False,
            "error_code": "missing_file",
        }
    text = str(read["text"])
    if old not in text:
        return {
            "claimed": "FAILED", "target": target, "exists": True,
            "error_code": "pattern_not_found",
        }
    written = filesystem_write(ctx, {
        "target": target,
        "content": text.replace(old, new, 1),
        "idempotency_key": str(payload.get("idempotency_key") or f"replace:{target}"),
    })
    path = ctx.owned_root / target
    observed_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    replaced = new in observed_text
    written["replaced"] = replaced
    if replaced:
        snap = FilesystemConnector(ctx.owned_root).snapshot(target)
        written["claimed"] = "COMPLETED"
        written["exists"] = True
        written["content_hash"] = snap.content_hash
        written["error_code"] = ""
    else:
        written["claimed"] = "FAILED"
        written["error_code"] = written.get("error_code") or "replace_not_observed"
    return written


def dir_list(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    target = str(payload.get("target") or ".")
    path = (ctx.owned_root / target).resolve() if target != "." else ctx.owned_root
    try:
        path.relative_to(ctx.owned_root)
    except ValueError as error:
        raise FabricError("dir.list target escapes owned root") from error
    if not path.is_dir():
        return {"claimed": "FAILED", "target": target, "names": [], "error_code": "not_a_directory"}
    names = sorted(
        item.name for item in path.iterdir()
        if item.name != FilesystemConnector.LEDGER_NAME
    )
    return {
        "claimed": "COMPLETED",
        "target": target,
        "path": str(path),
        "names": names,
        "count": len(names),
    }


def _git_connector(ctx: ExecutorContext) -> GitConnector:
    return GitConnector(ctx.database, owned_root=ctx.owned_root)


def git_inspect(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    worktree = Path(payload.get("worktree") or ctx.owned_root / "repo")
    remote = payload.get("remote") or str(worktree / ".git")
    connector = _git_connector(ctx)
    identity = connector.inspect(worktree=worktree, remote=remote, branch=str(payload.get("branch") or "main"))
    return {
        "claimed": "COMPLETED",
        "worktree": str(worktree),
        "head": identity.get("local_head"),
        "dirty": identity.get("dirty"),
        "identity": identity,
    }


def git_diff(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    worktree = Path(payload.get("worktree") or ctx.owned_root / "repo")
    observed = _git_connector(ctx).diff(worktree)
    return {"claimed": "COMPLETED", **observed}


def git_branch(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    worktree = Path(payload["worktree"])
    observed = _git_connector(ctx).create_branch(worktree, str(payload["name"]))
    return {"claimed": "COMPLETED", **observed}


def git_commit(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    worktree = Path(payload["worktree"])
    message = str(payload["message"])
    stop_value = payload.get("stop_path")
    if stop_value:
        stop_path = Path(str(stop_value))
        if not stop_path.is_absolute():
            stop_path = ctx.owned_root / stop_path
        stop_path = stop_path.expanduser().resolve(strict=False)
        try:
            stop_path.relative_to(ctx.owned_root)
        except ValueError as error:
            raise FabricError("git.commit stop_path escapes owned root") from error
        if stop_path.exists():
            return {
                "claimed": "BLOCKED",
                "error_code": "stop_active",
                "worktree": str(worktree),
                "stop_path": str(stop_path),
            }
    connector = _git_connector(ctx)
    if payload.get("existing"):
        paths = tuple(str(item) for item in (payload.get("paths") or ()))
        head = connector.commit_existing(worktree, paths, message)
    else:
        changes = dict(payload["changes"])
        head = connector.create_commit(worktree, changes, message)
    observed = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    return {
        "claimed": "COMPLETED",
        "worktree": str(worktree),
        "head": head,
        "observed_head": observed.stdout.strip(),
        "message": message,
    }


def git_read_ref(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    remote = payload["remote"]
    branch = str(payload.get("branch") or "main")
    head = GitConnector.read_ref(remote, branch)
    return {
        "claimed": "COMPLETED" if head else "FAILED",
        "remote": str(remote),
        "branch": branch,
        "head": head,
    }


def git_log(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    worktree = Path(payload.get("worktree") or ctx.owned_root / "repo")
    owned = _git_connector(ctx)._owned_worktree(worktree)
    max_count = max(1, min(int(payload.get("max_count") or 10), 50))
    text = GitConnector._run(owned, "log", f"-{max_count}", "--format=%H%x09%s")
    commits: list[dict[str, str]] = []
    for line in text.splitlines():
        sha, _, subject = line.partition("\t")
        if sha:
            commits.append({"sha": sha, "subject": subject})
    head = commits[0]["sha"] if commits else None
    return {
        "claimed": "COMPLETED" if head else "FAILED",
        "worktree": str(owned),
        "head": head,
        "commits": commits,
        "count": len(commits),
    }


def git_push(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    worktree = Path(payload["worktree"])
    remote = payload["remote"]
    branch = str(payload.get("branch") or "main")
    action_id = str(payload.get("action_id") or f"fabric-push:{branch}")
    pushed = _git_connector(ctx).push(
        action_id=action_id, worktree=worktree, remote=remote, branch=branch,
    )
    observed = GitConnector.read_ref(remote, branch)
    outcome = str(pushed.get("outcome") or "FAILED")
    remote_head = pushed.get("remote_head") or observed
    claimed = "COMPLETED" if outcome == "COMPLETED" and remote_head and remote_head == observed else "FAILED"
    return {
        "claimed": claimed,
        "worktree": str(worktree),
        "remote": str(remote),
        "branch": branch,
        "remote_head": remote_head,
        "observed_remote_head": observed,
        "performed": pushed.get("performed"),
        "action_id": action_id,
    }


def github_research(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    connector = GitHubResearchConnector(api_transport=ctx.github_api)
    action = str(payload.get("action") or "github.repo.read")
    if action not in {
        "github.repo.read", "github.commit.read", "github.issue.read",
        "github.pull_request.read", "github.check.read", "github.release.read",
        "github.search.read",
    }:
        action = "github.repo.read"
    endpoint = str(payload["endpoint"])
    attempt, document = connector.read_api(action, endpoint)
    return {
        "claimed": attempt.outcome,
        "endpoint": endpoint,
        "document": document,
        "response_hash": (attempt.after_state or {}).get("response_hash"),
        "error_code": attempt.error_code,
        "provider_ref": attempt.provider_ref,
    }


def github_identity(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    connector = GitHubResearchConnector(api_transport=ctx.github_api)
    probe = connector.identity_probe()
    login = probe.get("authenticated_login")
    return {
        "claimed": "COMPLETED" if login else "FAILED",
        "authenticated_login": login,
        "rate_limit": probe.get("rate_limit"),
        "probe": probe,
    }


def github_issue_create(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Create an issue through the explicit GitHub write seam."""
    owner = str(payload["owner"])
    repo = str(payload["repo"])
    title = str(payload["title"])
    body = str(payload.get("body") or "")
    endpoint = f"/repos/{owner}/{repo}/issues"
    return _github_write(
        ctx,
        "github.issue.create",
        endpoint,
        {"title": title, "body": body},
    )


def github_pull_request_create(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Open a pull request via injected write transport. No silent live mutation."""
    owner = str(payload["owner"])
    repo = str(payload["repo"])
    title = str(payload["title"])
    head = str(payload["head"])
    base = str(payload.get("base") or "main")
    body = str(payload.get("body") or "")
    endpoint = f"/repos/{owner}/{repo}/pulls"
    return _github_write(
        ctx,
        "github.pull_request.create",
        endpoint,
        {"title": title, "head": head, "base": base, "body": body},
    )


def github_pull_request_publish(
    ctx: ExecutorContext, payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Run deterministic branch/push/PR through the V12 journaled driver."""

    authorization = ctx.github_repository_authorization
    if authorization is None:
        return {
            "claimed": "BLOCKED", "performed": False,
            "error_code": "owner_repository_authorization_required",
        }
    adapter = ctx.github_write_adapter
    if adapter is None:
        if ctx.github_write is None:
            return {
                "claimed": "BLOCKED", "performed": False,
                "error_code": "github_write_transport_required",
            }
        adapter = GitHubWriteAdapter(
            transport=ctx.github_write,
            read_transport=ctx.github_api,
            dispatch_enabled=ctx.github_writes_enabled,
            authorized_repositories=ctx.github_authorized_repositories,
        )
        ctx.github_write_adapter = adapter
    request = payload.get("_adapter_request")
    effect_id = request.get("effect_id") if isinstance(request, Mapping) else None
    driver = GitHubPullRequestEffectDriver(
        ctx.database,
        owned_root=ctx.owned_root,
        adapter=adapter,
        push_transport=ctx.github_push_transport,
    )
    return driver.publish(
        authorization=authorization,
        worktree=str(payload["worktree"]),
        owner=str(payload["owner"]),
        repo=str(payload["repo"]),
        remote=str(payload["remote"]),
        base=str(payload.get("base") or "main"),
        head_sha=str(payload["head_sha"]),
        title=str(payload["title"]),
        body=str(payload.get("body") or ""),
        effect_id=str(effect_id) if effect_id else None,
        mission_id=str(payload.get("mission_id") or "") or None,
        stop_path=str(payload.get("stop_path")) if payload.get("stop_path") else None,
    )


def gmail_send(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Send one owner-only message through the V12 journaled Gmail driver."""

    if (
        ctx.gmail_api_provider is None
        or ctx.gmail_credential is None
        or ctx.gmail_identity is None
        or ctx.gmail_send_authorization is None
    ):
        return {
            "claimed": "BLOCKED", "performed": False,
            "error_code": "gmail_send_activation_required",
        }
    request = payload.get("_adapter_request")
    effect_id = request.get("effect_id") if isinstance(request, Mapping) else None
    driver = GmailSendEffectDriver(ctx.database, ctx.gmail_api_provider)
    return driver.send(
        authorization=ctx.gmail_send_authorization,
        credential=ctx.gmail_credential,
        identity=ctx.gmail_identity,
        recipient=str(payload["recipient"]),
        subject=str(payload["subject"]),
        body_text=str(payload["body_text"]),
        effect_id=str(effect_id) if effect_id else None,
        mission_id=str(payload.get("mission_id") or "") or None,
        stop_path=str(payload.get("stop_path")) if payload.get("stop_path") else None,
    )


def _github_write(
    ctx: ExecutorContext,
    action_kind: str,
    endpoint: str,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Run one explicit write with stable identity and optional readback."""

    adapter = ctx.github_write_adapter
    if adapter is None:
        transport = ctx.github_write
        if transport is None:
            raise FabricError(f"{action_kind} requires an explicit write transport")
        # Preserve the historical callback shape while reusing one lifecycle
        # adapter for this executor context. Durable dedupe belongs to the
        # existing effect/ledger spine, while this cached adapter prevents a
        # second provider request during an ambiguous in-process retry.
        adapter = GitHubWriteAdapter(
            transport=transport,
            read_transport=ctx.github_api,
            dispatch_enabled=ctx.github_writes_enabled,
            authorized_repositories=ctx.github_authorized_repositories,
        )
        ctx.github_write_adapter = adapter
    result = adapter.write(action_kind, endpoint, request)
    # A provider response is only a claim.  When an independent read
    # transport exists, reconcile the provider identity before Fabric can
    # accept the action.  Without it the verifier remains NOT_TESTED.
    if (
        result.get("claimed") == "COMPLETED"
        and adapter.read_transport is not None
        and result.get("reconciled") is not True
    ):
        result = adapter.reconcile(str(result.get("idempotency_key") or ""))
    return result


def _browser_connector(payload: Mapping[str, Any]) -> Any:
    from ..connectors.web_research import PlaywrightBrowserConnector

    allowed_hosts = payload.get("allowed_hosts")
    if isinstance(allowed_hosts, str):
        allowed_hosts = (allowed_hosts,)
    return PlaywrightBrowserConnector(
        browser_name=str(payload.get("browser_name") or "chromium"),
        headless=bool(payload.get("headless", True)),
        timeout_ms=int(payload.get("timeout_ms") or 30_000),
        allowed_hosts=allowed_hosts,
        allow_insecure_localhost=bool(payload.get("allow_insecure_localhost", False)),
    )


def _browser_stop_check(
    ctx: ExecutorContext, payload: Mapping[str, Any],
) -> Callable[[], bool]:
    identities = {"GLOBAL"}
    for key in ("mission_id", "workflow_id", "stop_scope"):
        value = payload.get(key)
        values = (value,) if isinstance(value, str) else (value or ())
        identities.update(str(item) for item in values if str(item).strip())

    def check() -> bool:
        return bool(emergency_stop_reason(
            ctx.database,
            payload.get("stop_path"),
            identities=identities,
        ))

    return check


def _browser_session_kwargs(payload: Mapping[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "profile_id": str(payload.get("profile_id") or "default"),
        "owner_scope": str(payload.get("owner_scope") or "owner-local"),
        "persist_session": bool(payload.get("persist_session", True)),
        "credential_reference": payload.get("credential_reference"),
        "auth_marker": payload.get("auth_marker"),
        "expired_url_prefixes": payload.get("expired_url_prefixes"),
        "reauthenticate": payload.get("reauthenticate"),
        "allowed_action_methods": payload.get("allowed_action_methods") or ("POST",),
        "max_action_requests": int(payload.get("max_action_requests") or 8),
    }
    for key in ("site_origin", "session_state_path", "session_ref"):
        if payload.get(key) is not None:
            kwargs[key] = payload[key]
    return kwargs


def _browser_attempt_payload(
    attempt: Any,
    *,
    page: Any | None = None,
    session: Any | None = None,
    effect_id: str | None = None,
    session_persisted: bool | None = None,
) -> dict[str, Any]:
    after = dict(attempt.after_state or {})
    before = dict(attempt.before_state or {})
    status = None
    if session is not None:
        try:
            status = session.session_status().to_evidence()
        except Exception:
            status = None
    page_evidence = page.to_evidence() if page is not None else {}
    outcome = str(attempt.outcome or "UNKNOWN")
    if outcome == "COMPLETED":
        claimed = "COMPLETED"
    elif outcome == "FAILED":
        claimed = "FAILED"
    elif outcome == "BLOCKED":
        claimed = "BLOCKED"
    elif outcome == "CANCELLED" and attempt.error_code == "stop_requested":
        claimed = "BLOCKED"
    else:
        claimed = "UNKNOWN"
    result: dict[str, Any] = {
        "claimed": claimed,
        "attempt_id": attempt.attempt_id,
        "connector_id": attempt.connector_id,
        "target_ref": attempt.target_ref,
        "idempotency_key": attempt.idempotency_key,
        "payload_hash": attempt.payload_hash,
        "final_url": after.get("final_url") or attempt.provider_ref,
        "status": page_evidence.get("status") or after.get("status"),
        "source_sha256": page_evidence.get("source_sha256"),
        "source_bytes": page_evidence.get("source_bytes"),
        "document_id": page_evidence.get("document_id") or before.get("document_id"),
        "page_revision": after.get("page_revision") or page_evidence.get("page_revision"),
        "structural_hash": after.get("structural_hash") or page_evidence.get("structural_hash"),
        "target_id": after.get("target_id") or before.get("target_id"),
        "action": after.get("action") or before.get("action"),
        "readback": after.get("readback"),
        "readback_verified": bool(after.get("readback_verified")),
        "download": after.get("download"),
        "upload": after.get("upload"),
        "wait_state": after.get("wait_state") or before.get("wait_state"),
        "session": status,
        "session_persisted": session_persisted,
        "authority": "DATA_ONLY",
        "provider_ref": attempt.provider_ref,
        "reconciled": bool(attempt.reconciled),
        "performed": bool(
            attempt.outcome == "COMPLETED"
            or after.get("action_request_seen")
        ),
        "action_request_count": after.get("action_request_count", 0),
        "action_request_seen": bool(after.get("action_request_seen")),
        "effect_id": effect_id,
        "error_code": attempt.error_code,
        "evidence_refs": list(attempt.evidence_refs),
        "cost": dict(attempt.cost),
    }
    if page is not None:
        result["redirects"] = list(page.redirects)
        result["target_count"] = page.target_count
    return result


def browser_read(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read one bounded page through the existing Playwright substrate."""

    from ..connectors.web_research import WebResearchError

    url = str(payload.get("url") or payload.get("target_ref") or "")
    stop_check = _browser_stop_check(ctx, payload)
    session_requested = any(
        payload.get(key) is not None
        for key in (
            "session_state_path", "site_origin", "auth_marker",
            "credential_reference",
        )
    )
    try:
        connector = _browser_connector(payload)
        if session_requested:
            with connector.session(
                stop_check=stop_check,
                **_browser_session_kwargs(payload),
            ) as session:
                capture = session.navigate(url)
                status = session.session_status()
                if bool(payload.get("require_authenticated")):
                    if payload.get("auth_marker") is None:
                        return {
                            "claimed": "BLOCKED",
                            "error_code": "auth_marker_required",
                            "session": status.to_evidence(),
                            "authority": "DATA_ONLY",
                        }
                    status = session.ensure_authenticated()
                    if not status.authenticated:
                        return {
                            "claimed": "BLOCKED",
                            "error_code": "session_expired" if status.state == "EXPIRED"
                            else "session_not_authenticated",
                            "session": status.to_evidence(),
                            "authority": "DATA_ONLY",
                        }
                return _browser_attempt_payload(
                    capture.attempt, page=capture.page, session=session,
                ) | {
                    "claimed": (
                        capture.attempt.outcome
                        if capture.attempt.outcome in {"COMPLETED", "FAILED"}
                        else "BLOCKED"
                        if capture.attempt.error_code == "stop_requested"
                    else "UNKNOWN",
                    ),
                    "authority": "DATA_ONLY",
                }
        capture = connector.read(url, stop_check=stop_check)
        return _browser_attempt_payload(capture.attempt, page=capture.page) | {
            "claimed": capture.attempt.outcome
            if capture.attempt.outcome in {"COMPLETED", "FAILED"}
            else "BLOCKED" if capture.attempt.error_code == "stop_requested"
            else "UNKNOWN",
            "authority": "DATA_ONLY",
        }
    except (ImportError, WebResearchError, TypeError, ValueError) as error:
        return {
            "claimed": "FAILED",
            "error_code": "browser_configuration_error",
            "detail": redact_text(str(error))[:300],
            "authority": "DATA_ONLY",
        }


def browser_execute(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run one bounded browser action under the canonical effect journal."""

    from ..connectors.web_research import BrowserTargetError, WebResearchError

    identity = payload.get("_adapter_request")
    identity = identity if isinstance(identity, Mapping) else {}
    url = str(payload.get("url") or payload.get("target_ref") or "")
    target_id = str(payload.get("target_id") or "")
    role = str(payload.get("role") or "")
    name = str(payload.get("name") or "")
    action = str(payload.get("action") or "click")
    if "download_root" in payload:
        return {
            "claimed": "BLOCKED",
            "error_code": "download_policy_override_refused",
            "authority": "DATA_ONLY",
        }
    if "upload_root" in payload:
        return {
            "claimed": "BLOCKED",
            "error_code": "upload_policy_override_refused",
            "authority": "DATA_ONLY",
        }
    if action == "download":
        if payload.get("allow_downloads") is not True:
            return {
                "claimed": "BLOCKED",
                "error_code": "download_not_authorized",
                "authority": "DATA_ONLY",
            }
        if not isinstance(payload.get("download_target"), str) or not str(
            payload["download_target"]
        ).strip():
            return {
                "claimed": "FAILED",
                "error_code": "download_target_required",
                "authority": "DATA_ONLY",
            }
    if action == "upload":
        if payload.get("allow_uploads") is not True:
            return {
                "claimed": "BLOCKED",
                "error_code": "upload_not_authorized",
                "authority": "DATA_ONLY",
            }
        if not isinstance(payload.get("upload_source"), str) or not str(
            payload["upload_source"]
        ).strip():
            return {
                "claimed": "FAILED",
                "error_code": "upload_source_required",
                "authority": "DATA_ONLY",
            }
        readback_target = payload.get("readback_target")
        if (
            not isinstance(readback_target, Mapping)
            or not isinstance(readback_target.get("role"), str)
            or not str(readback_target["role"]).strip()
            or not isinstance(readback_target.get("name"), str)
            or not str(readback_target["name"]).strip()
            or str(readback_target.get("wait_state") or "visible")
            not in {"attached", "visible", "enabled"}
        ):
            return {
                "claimed": "BLOCKED",
                "error_code": "upload_postcondition_required",
                "authority": "DATA_ONLY",
            }
    browser_identity = {
        "url": url,
        "target_id": target_id,
        "role": role,
        "name": name,
        "action": action,
        "value": payload.get("value"),
        "download_target": payload.get("download_target"),
        "upload_source": payload.get("upload_source"),
        "readback_target": payload.get("readback_target"),
    }
    fallback_hash = hashlib.sha256(
        json.dumps(browser_identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    effect_id = str(identity.get("effect_id") or payload.get("effect_id") or "")
    action_key = effect_id or str(
        payload.get("idempotency_key") or f"browser-effect:{fallback_hash}"
    )
    idempotency_key = str(
        identity.get("idempotency_key")
        or payload.get("idempotency_key")
        or action_key
    )
    request_hash = str(identity.get("input_hash") or "")
    if len(request_hash) != 64:
        request_hash = hashlib.sha256(
            json.dumps(dict(payload), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    before = {
        "url": url,
        "target_id": target_id,
        "role": role,
        "name": name,
        "action": action,
        "download_target": payload.get("download_target"),
        "upload_source": payload.get("upload_source"),
        "readback_target": payload.get("readback_target"),
        "authority": "DATA_ONLY",
    }
    stop_check = _browser_stop_check(ctx, payload)
    try:
        connector = _browser_connector(payload)
    except (ImportError, WebResearchError, TypeError, ValueError) as error:
        return {
            "claimed": "FAILED",
            "error_code": "browser_configuration_error",
            "detail": redact_text(str(error))[:300],
            "authority": "DATA_ONLY",
        }
    state_path = payload.get("session_state_path")
    state_path = (
        Path(state_path).expanduser().resolve(strict=False)
        if state_path else None
    )
    metadata_path = Path(f"{state_path}.meta.json") if state_path else None
    persisted = False

    def perform() -> Mapping[str, Any]:
        nonlocal persisted
        try:
            session_kwargs = _browser_session_kwargs(payload)
            if action == "download":
                session_kwargs.update({
                    "allow_downloads": True,
                    "download_root": ctx.owned_root,
                    "max_download_bytes": int(
                        payload.get("max_download_bytes") or 32 * 1024 * 1024
                    ),
                })
            if action == "upload":
                session_kwargs.update({
                    "allow_uploads": True,
                    "upload_root": ctx.owned_root,
                    "max_upload_bytes": int(
                        payload.get("max_upload_bytes") or 32 * 1024 * 1024
                    ),
                })
            with connector.session(
                stop_check=stop_check,
                **session_kwargs,
            ) as session:
                capture = session.navigate(url)
                if not capture.success:
                    return _browser_attempt_payload(
                        capture.attempt, page=capture.page, session=session,
                        effect_id=effect_id or None,
                    )
                if bool(payload.get("require_authenticated")):
                    if payload.get("auth_marker") is None:
                        return {
                            "claimed": "BLOCKED",
                            "error_code": "auth_marker_required",
                            "session": session.session_status().to_evidence(),
                            "authority": "DATA_ONLY",
                        }
                    auth = session.ensure_authenticated()
                    if not auth.authenticated:
                        return {
                            "claimed": "BLOCKED",
                            "error_code": "session_expired" if auth.state == "EXPIRED"
                            else "session_not_authenticated",
                            "session": auth.to_evidence(),
                            "authority": "DATA_ONLY",
                        }
                target = session.discover_target(
                    target_id=target_id,
                    role=role,
                    name=name,
                    action=action,
                )
                attempt = session.execute(
                    target,
                    value=payload.get("value"),
                    stop_check=stop_check,
                    effect_id=effect_id or None,
                    idempotency_key=idempotency_key,
                    readback_target=(
                        payload.get("readback_target")
                        if isinstance(payload.get("readback_target"), Mapping)
                        else None
                    ),
                    download_target=(
                        str(payload["download_target"])
                        if action == "download"
                        else None
                    ),
                    upload_source=(
                        str(payload["upload_source"])
                        if action == "upload"
                        else None
                    ),
                )
                result = _browser_attempt_payload(
                    attempt,
                    page=capture.page,
                    session=session,
                    effect_id=effect_id or None,
                )
            persisted = bool(
                state_path is not None
                and metadata_path is not None
                and state_path.is_file()
                and metadata_path.is_file()
            )
            result["session_persisted"] = persisted
            return result
        except BrowserTargetError as error:
            return {
                "claimed": "FAILED",
                "error_code": "browser_target_error",
                "detail": redact_text(str(error))[:300],
                "authority": "DATA_ONLY",
            }
        except (ImportError, WebResearchError) as error:
            return {
                "claimed": "FAILED",
                "error_code": "browser_configuration_error",
                "detail": redact_text(str(error))[:300],
                "authority": "DATA_ONLY",
            }

    return execute_fabric_effect(
        ctx.database,
        action_key=action_key,
        system_id="browser",
        target_ref=url or "browser:unknown",
        payload_hash=request_hash,
        kind="browser.web.execute",
        connector_id=connector.spec.connector_id,
        before_state_capture=before,
        expected_effects=("bounded browser action",),
        action_id=str(identity.get("request_id") or action_key),
        request_identity=identity,
        perform=perform,
        stop_check=stop_check,
        claim_ttl_s=float(payload.get("claim_ttl_s") or 300.0),
    )


def _browser_effect_row(
    database: FoundryDatabase,
    action_key: str,
) -> Mapping[str, Any] | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT action_key, system_id, target_ref, payload_hash, kind, status "
            "FROM system_actions WHERE action_key = ?",
            (action_key,),
        ).fetchone()
    if row is None:
        return None
    return {
        "action_key": str(row[0]),
        "system_id": str(row[1]),
        "target_ref": str(row[2]),
        "payload_hash": str(row[3]),
        "kind": str(row[4]),
        "status": str(row[5]),
    }


def _browser_recorded_result(
    database: FoundryDatabase,
    action_key: str,
) -> dict[str, Any] | None:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM events "
            "WHERE event_type IN "
            "('effect.terminal_truth', 'effect.frozen', "
            "'system.action_completed', 'system.action_failed') "
            "AND json_extract(payload_json, '$.action_key') = ? "
            "ORDER BY sequence DESC",
            (action_key,),
        ).fetchall()
    for _event_type, payload_json in rows:
        payload = json.loads(payload_json)
        result = payload.get("result")
        if isinstance(result, Mapping):
            return dict(result)
    return None


def browser_reconcile(
    ctx: ExecutorContext,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconcile one terminal browser effect without repeating the action."""

    from ..connectors.web_research import BrowserTargetError, WebResearchError

    action_key = str(
        payload.get("original_effect_id") or payload.get("effect_id") or ""
    ).strip()
    if not action_key:
        return {
            "claimed": "BLOCKED",
            "error_code": "original_effect_id_required",
            "authority": "DATA_ONLY",
        }
    row = _browser_effect_row(ctx.database, action_key)
    if row is None:
        return {
            "claimed": "BLOCKED",
            "error_code": "effect_not_found",
            "authority": "DATA_ONLY",
        }
    if row["system_id"] != "browser" or row["kind"] != "browser.web.execute":
        return {
            "claimed": "BLOCKED",
            "error_code": "effect_identity_mismatch",
            "authority": "DATA_ONLY",
        }
    state = effect_state(ctx.database, action_key)
    if state.get("status") not in {COMPLETED, "FAILED", FROZEN}:
        return {
            "claimed": "BLOCKED",
            "error_code": "effect_not_terminal",
            "authority": "DATA_ONLY",
        }
    before = state.get("before_state")
    before = before if isinstance(before, Mapping) else {}
    original_action = str(
        payload.get("original_action") or before.get("action") or ""
    )
    if original_action != str(before.get("action") or original_action):
        return {
            "claimed": "BLOCKED",
            "error_code": "effect_identity_mismatch",
            "authority": "DATA_ONLY",
        }
    recorded = _browser_recorded_result(ctx.database, action_key)
    if recorded is None:
        return {
            "claimed": "UNKNOWN",
            "error_code": "original_effect_evidence_missing",
            "authority": "DATA_ONLY",
            "original_effect_id": action_key,
        }

    provider_id = "conn-playwright-v10"
    provider_ref = str(row["target_ref"])
    browser_readback: dict[str, Any]
    reconciled_result = dict(recorded)

    if original_action == "download":
        download = recorded.get("download")
        if not isinstance(download, Mapping):
            return {
                "claimed": "UNKNOWN",
                "error_code": "download_evidence_missing",
                "authority": "DATA_ONLY",
                "original_effect_id": action_key,
            }
        path_value = download.get("path")
        expected_hash = download.get("sha256")
        expected_size = download.get("size_bytes")
        try:
            path = Path(str(path_value)).expanduser().resolve(strict=True)
            path.relative_to(ctx.owned_root)
            content = path.read_bytes()
        except (OSError, ValueError):
            content = b""
            path = None
        matches = (
            path is not None
            and isinstance(expected_hash, str)
            and isinstance(expected_size, int)
            and len(content) == expected_size
            and hashlib.sha256(content).hexdigest() == expected_hash
        )
        browser_readback = {
            "verified": matches,
            "matched": matches,
            "download_path": str(path) if path is not None else None,
            "sha256": (
                hashlib.sha256(content).hexdigest() if path is not None else None
            ),
            "size_bytes": len(content) if path is not None else None,
            "authority": "DATA_ONLY",
        }
    else:
        postcondition = payload.get("readback_target")
        if not isinstance(postcondition, Mapping):
            return {
                "claimed": "BLOCKED",
                "error_code": "reconciliation_postcondition_required",
                "authority": "DATA_ONLY",
                "original_effect_id": action_key,
            }
        expected_before = before.get("readback_target")
        if original_action == "upload":
            upload = recorded.get("upload")
            recorded_precondition = (
                upload.get("precondition")
                if isinstance(upload, Mapping)
                else None
            )
            if (
                not isinstance(expected_before, Mapping)
                or expected_before.get("role") != postcondition.get("role")
                or expected_before.get("name") != postcondition.get("name")
                or expected_before.get("wait_state")
                != str(postcondition.get("wait_state") or "visible")
                or not isinstance(recorded_precondition, Mapping)
                or recorded_precondition.get("matched") is not False
                or recorded_precondition.get("role")
                != postcondition.get("role")
                or recorded_precondition.get("name")
                != postcondition.get("name")
                or recorded_precondition.get("wait_state")
                != str(postcondition.get("wait_state") or "visible")
            ):
                return {
                    "claimed": "BLOCKED",
                    "error_code": "effect_identity_mismatch",
                    "authority": "DATA_ONLY",
                    "original_effect_id": action_key,
                }
            if (
                not isinstance(upload, Mapping)
                or not isinstance(recorded_precondition, Mapping)
            ):
                return {
                    "claimed": "UNKNOWN",
                    "error_code": "upload_evidence_missing",
                    "authority": "DATA_ONLY",
                    "original_effect_id": action_key,
                }
        url = str(payload.get("url") or row["target_ref"])
        try:
            connector = _browser_connector(payload)
            provider_id = connector.spec.connector_id
            with connector.session(
                stop_check=None,
                **_browser_session_kwargs(payload),
            ) as session:
                capture = session.navigate(url)
                if not capture.success:
                    return _browser_attempt_payload(
                        capture.attempt,
                        page=capture.page,
                        session=session,
                        effect_id=action_key,
                    ) | {
                        "claimed": "UNKNOWN",
                        "original_effect_id": action_key,
                    }
                attempt = session.reconcile(
                    role=str(postcondition.get("role") or ""),
                    name=str(postcondition.get("name") or ""),
                    target_id=str(
                        postcondition.get("target_id")
                        or f"reconcile-{action_key[-24:]}"
                    ),
                    wait_state=str(
                        postcondition.get("wait_state") or "visible"
                    ),
                    timeout_ms=int(
                        postcondition.get("timeout_ms")
                        or connector.timeout_ms
                    ),
                    effect_id=action_key,
                    idempotency_key=str(
                        payload.get("idempotency_key")
                        or f"browser-reconcile:{action_key}"
                    ),
                )
                result = _browser_attempt_payload(
                    attempt,
                    page=capture.page,
                    session=session,
                    effect_id=action_key,
                )
        except (
            BrowserTargetError,
            ImportError,
            WebResearchError,
            TypeError,
            ValueError,
        ) as error:
            return {
                "claimed": "UNKNOWN",
                "error_code": "browser_reconciliation_failed",
                "detail": redact_text(str(error))[:300],
                "authority": "DATA_ONLY",
                "original_effect_id": action_key,
            }
        browser_readback = dict(result.get("readback") or {})
        provider_ref = str(result.get("final_url") or row["target_ref"])
        if original_action == "upload":
            upload = dict(reconciled_result["upload"])
            upload["postcondition"] = {
                **browser_readback,
                "matched": bool(browser_readback.get("matched")),
            }
            reconciled_result["upload"] = upload

    if (
        browser_readback.get("verified") is not True
        or browser_readback.get("matched") is not True
    ):
        return {
            "claimed": "UNKNOWN",
            "error_code": "reconciliation_not_verified",
            "authority": "DATA_ONLY",
            "original_effect_id": action_key,
            "readback": browser_readback,
        }

    reconciled_result.update({
        "claimed": "COMPLETED",
        "provider_ref": provider_ref,
        "readback_verified": True,
        "reconciled": True,
        "authority": "DATA_ONLY",
        "error_code": None,
    })
    provider_receipt = {
        "provider_id": provider_id,
        "provider_ref": provider_ref,
        "receipt_sha256": hashlib.sha256(
            json.dumps(
                browser_readback,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    readback = {
        "status": "MATCH",
        "matches": True,
        "provider_ref": provider_ref,
        "browser_readback": browser_readback,
    }
    accepted = finalize_verified_effect(
        ctx.database,
        action_key,
        provider_receipt=provider_receipt,
        readback=readback,
        result=reconciled_result,
        acceptance_evidence={
            "authority": "browser_exact_postcondition",
            "receipt_sha256": provider_receipt["receipt_sha256"],
        },
        reconciled=True,
    )
    return {
        **accepted,
        "claimed": "COMPLETED",
        "original_effect_id": action_key,
        "readback": readback,
        "authority": "DATA_ONLY",
    }


def browser_wait(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Wait for one exact browser state through the canonical Playwright runtime."""

    from ..connectors.web_research import BrowserTargetError, WebResearchError

    identity = payload.get("_adapter_request")
    identity = identity if isinstance(identity, Mapping) else {}
    url = str(payload.get("url") or payload.get("target_ref") or "")
    stop_check = _browser_stop_check(ctx, payload)
    try:
        connector = _browser_connector(payload)
        with connector.session(
            stop_check=stop_check,
            **_browser_session_kwargs(payload),
        ) as session:
            capture = session.navigate(url)
            if not capture.success:
                return _browser_attempt_payload(
                    capture.attempt,
                    page=capture.page,
                    session=session,
                )
            if bool(payload.get("require_authenticated")):
                if payload.get("auth_marker") is None:
                    return {
                        "claimed": "BLOCKED",
                        "error_code": "auth_marker_required",
                        "session": session.session_status().to_evidence(),
                        "authority": "DATA_ONLY",
                    }
                auth = session.ensure_authenticated()
                if not auth.authenticated:
                    return {
                        "claimed": "BLOCKED",
                        "error_code": (
                            "session_expired"
                            if auth.state == "EXPIRED"
                            else "session_not_authenticated"
                        ),
                        "session": auth.to_evidence(),
                        "authority": "DATA_ONLY",
                    }
            attempt = session.wait_for_target(
                target_id=str(payload.get("target_id") or ""),
                role=str(payload.get("role") or ""),
                name=str(payload.get("name") or ""),
                state=str(payload.get("wait_state") or "visible"),
                timeout_ms=int(
                    payload.get("timeout_ms") or connector.timeout_ms
                ),
                stop_check=stop_check,
                idempotency_key=str(
                    identity.get("idempotency_key")
                    or payload.get("idempotency_key")
                    or ""
                )
                or None,
            )
            return _browser_attempt_payload(
                attempt,
                page=capture.page,
                session=session,
            )
    except (BrowserTargetError, ImportError, WebResearchError, TypeError, ValueError) as error:
        return {
            "claimed": "FAILED",
            "error_code": "browser_configuration_error",
            "detail": redact_text(str(error))[:300],
            "authority": "DATA_ONLY",
        }


def http_read(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read JSON from a loopback HTTP service. Not an open network proxy."""
    from urllib.parse import urlsplit

    from ..connectors.http import HttpServiceConnector

    base = str(payload["base_url"])
    path = str(payload.get("path") or "/state")
    host = (urlsplit(base).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise FabricError("http.read is limited to loopback services")
    connector = HttpServiceConnector(base, timeout_s=float(payload.get("timeout_s") or 2.0))
    attempt, document = connector.read_document(path)
    return {
        "claimed": attempt.outcome,
        "base_url": connector.base_url,
        "path": path,
        "document": document,
        "response_hash": (attempt.after_state or {}).get("response_hash"),
        "error_code": attempt.error_code,
    }


def http_write(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Write JSON to a loopback HTTP service, then read the observed state."""
    from urllib.parse import urlsplit

    from ..connectors.http import HttpServiceConnector

    base = str(payload["base_url"])
    path = str(payload.get("path") or "/write")
    host = (urlsplit(base).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise FabricError("http.write is limited to loopback services")
    body = dict(payload.get("body") or {})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key:
        raise FabricError("http.write requires idempotency_key")
    connector = HttpServiceConnector(base, timeout_s=float(payload.get("timeout_s") or 2.0))
    attempt = connector.write(path, body, idempotency_key=key)
    observed = None
    if attempt.outcome == "COMPLETED":
        _, observed = connector.read_document("/state")
    return {
        "claimed": attempt.outcome,
        "base_url": connector.base_url,
        "path": path,
        "idempotency_key": key,
        "response_hash": (attempt.after_state or {}).get("response_hash"),
        "observed_state": observed,
        "replayed": attempt.replayed,
        "error_code": attempt.error_code,
    }


def http_doc_read(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    hosts = tuple(str(item) for item in (payload.get("allowed_hosts") or ()))
    connector = GitHubResearchConnector(
        api_transport=ctx.github_api,
        doc_transport=ctx.doc_transport,
        allowed_doc_hosts=hosts,
    )
    attempt, content = connector.read_document(str(payload["url"]))
    return {
        "claimed": attempt.outcome,
        "url": str(payload["url"]),
        "text": None if content is None else content.text,
        "raw_sha256": None if content is None else content.raw_sha256,
        "final_url": None if content is None else content.final_url,
        "error_code": attempt.error_code,
    }


def system_runtime(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Honest local runtime identity plus durable control snapshot."""
    from ..diagnostics.runtime import diagnostic_snapshot
    from ..runtime.authority import runtime_source_identity

    identity = runtime_source_identity()
    snapshot = diagnostic_snapshot(ctx.database)
    control = snapshot.get("runtime_control") or {}
    return {
        "claimed": "COMPLETED",
        "python": sys.version.split()[0],
        "pid": os.getpid(),
        "source_sha": identity.get("source_sha"),
        "source_kind": identity.get("source_kind"),
        "code_origin": identity.get("code_origin"),
        "runtime_status": control.get("status"),
        "correlation": snapshot.get("correlation"),
        "runtime_control": control,
    }


def system_diagnose(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    git = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, encoding="utf-8",
        timeout=15, check=False,
    )
    gh = shutil.which("gh")
    disk = shutil.disk_usage(ctx.owned_root)
    return {
        "claimed": "COMPLETED",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "git": git.stdout.strip() if git.returncode == 0 else None,
        "gh_present": bool(gh),
        "owned_root_exists": ctx.owned_root.is_dir(),
        "free_bytes": int(disk.free),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
    }


def tests_run(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    target = Path(payload["target"])
    if not target.is_absolute():
        target = ctx.owned_root / target
    resolved = target.resolve()
    try:
        resolved.relative_to(ctx.owned_root)
    except ValueError as error:
        raise FabricError("tests.run target escapes owned root") from error
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(resolved), "-q"],
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
        cwd=str(ctx.owned_root),
    )
    return {
        "claimed": "COMPLETED" if result.returncode == 0 else "FAILED",
        "returncode": result.returncode,
        "target": str(resolved),
        "stdout": result.stdout[-2000:],
        "stderr": result.stderr[-1000:],
    }


def artifact_write(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    return filesystem_write(ctx, payload)


def _artifact_connector(
    ctx: ExecutorContext, payload: Mapping[str, Any]
) -> LocalArtifactConnector:
    if "source_root" in payload or "source_roots" in payload:
        raise ArtifactValidationError(
            "artifact source roots are trusted ExecutorContext policy, not action input"
        )
    return LocalArtifactConnector(
        ctx.owned_root,
        source_roots=ctx.artifact_source_roots,
    )


def _artifact_source(payload: Mapping[str, Any]) -> str:
    source = payload.get("source")
    if not isinstance(source, (str, Path)) or not str(source).strip():
        raise ArtifactValidationError("source is required")
    return str(source)


def _artifact_target(payload: Mapping[str, Any]) -> str:
    target = payload.get("target")
    if not isinstance(target, (str, Path)) or not str(target).strip():
        raise ArtifactValidationError("target is required")
    return str(target)


def _artifact_parse_options(payload: Mapping[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {
        "expected_columns": payload.get("expected_columns"),
        "sheet": payload.get("sheet"),
    }
    for name in ("allow_formulas", "allow_external_links"):
        value = payload.get(name, False)
        if not isinstance(value, bool):
            raise ArtifactValidationError(f"{name} must be boolean")
        options[name] = value
    return options


def _artifact_bool_option(
    payload: Mapping[str, Any],
    name: str,
    *,
    default: bool = False,
) -> bool:
    value = payload.get(name, default)
    if not isinstance(value, bool):
        raise ArtifactValidationError(f"{name} must be boolean")
    return value


def _artifact_blocked(error: LocalArtifactError) -> dict[str, Any]:
    if isinstance(error, ArtifactPostWriteError):
        return {
            "claimed": "UNKNOWN",
            "error_code": error.error_code,
            "error": str(error),
            **error.evidence,
        }
    return {
        "claimed": "BLOCKED",
        "error_code": getattr(error, "error_code", "artifact_error"),
        "error": str(error),
    }


def _artifact_interrupted(error: InjectedArtifactFailure) -> dict[str, Any]:
    return {
        "claimed": "UNKNOWN",
        "error_code": "partial_failure",
        "error": str(error),
        **error.evidence,
    }


def artifact_parse(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        parsed = _artifact_connector(ctx, payload).parse(
            _artifact_source(payload),
            **_artifact_parse_options(payload),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
    except InjectedArtifactFailure as error:
        return _artifact_interrupted(error)
    result: dict[str, Any] = {
        "claimed": "COMPLETED",
        "source": parsed.source.as_dict(),
        "source_ref": parsed.source.source_ref,
        "source_sha256": parsed.source.sha256,
        "format": parsed.source.format,
        "kind": parsed.kind,
        "details": dict(parsed.details),
    }
    if parsed.table is not None:
        result["schema"] = list(parsed.table.headers)
        result["row_count"] = parsed.table.row_count
        result["column_count"] = parsed.table.column_count
        result["preview_rows"] = parsed.table.as_dict()["preview_rows"]
    return result


def artifact_table_summary(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        connector = _artifact_connector(ctx, payload)
        parsed = connector.parse(
            _artifact_source(payload),
            **_artifact_parse_options(payload),
        )
        summary = connector.summarize_table(
            parsed,
            group_by=payload.get("group_by"),
            sum_column=payload.get("sum_column"),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
    return {
        "claimed": "COMPLETED",
        "source": parsed.source.as_dict(),
        "source_ref": parsed.source.source_ref,
        "source_sha256": parsed.source.sha256,
        "format": parsed.source.format,
        "summary": summary,
        **summary,
    }


def artifact_docx_create(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        connector = _artifact_connector(ctx, payload)
        parsed = connector.parse(
            _artifact_source(payload),
            **_artifact_parse_options(payload),
        )
        summary = connector.summarize_table(
            parsed,
            group_by=payload.get("group_by"),
            sum_column=payload.get("sum_column"),
        )
        receipt = connector.create_docx_report(
            parsed, summary,
            target_ref=_artifact_target(payload),
            idempotency_key=str(payload.get("idempotency_key") or f"artifact.docx:{_artifact_target(payload)}"),
            title=str(payload.get("title") or "JARVIS local data report"),
            fault_after_write=_artifact_bool_option(
                payload, "fault_after_write"
            ),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
    except InjectedArtifactFailure as error:
        return _artifact_interrupted(error)
    return {
        "claimed": "COMPLETED",
        "source": parsed.source.as_dict(),
        "source_ref": parsed.source.source_ref,
        "source_sha256": parsed.source.sha256,
        "summary": summary,
        **receipt.as_dict(),
    }


def artifact_docx_verify(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        connector = _artifact_connector(ctx, payload)
        readback = connector.verify_docx(
            _artifact_target(payload),
            expected_sha256=payload.get("expected_sha256"),
            required_values=payload.get("required_values") or (),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
    return {
        "claimed": "COMPLETED" if readback.get("verified") else "FAILED",
        "target_ref": readback.get("target_ref"),
        "output_sha256": readback.get("sha256"),
        "readback": readback,
        "readback_verified": bool(readback.get("verified")),
    }


def artifact_table_export(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        connector = _artifact_connector(ctx, payload)
        parsed = connector.parse(
            _artifact_source(payload),
            **_artifact_parse_options(payload),
        )
        receipt = connector.export_table(
            parsed,
            target_ref=_artifact_target(payload),
            idempotency_key=str(payload.get("idempotency_key") or f"artifact.table:{_artifact_target(payload)}"),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
    return {
        "claimed": "COMPLETED",
        "source_ref": parsed.source.source_ref,
        "source_sha256": parsed.source.sha256,
        **receipt.as_dict(),
    }


def artifact_pdf_copy(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        connector = _artifact_connector(ctx, payload)
        parsed = connector.parse(_artifact_source(payload))
        receipt = connector.copy_pdf(
            parsed,
            target_ref=_artifact_target(payload),
            idempotency_key=str(payload.get("idempotency_key") or f"artifact.pdf:{_artifact_target(payload)}"),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
    return {
        "claimed": "COMPLETED",
        "source_ref": parsed.source.source_ref,
        "source_sha256": parsed.source.sha256,
        **receipt.as_dict(),
    }


def artifact_pptx_copy(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        connector = _artifact_connector(ctx, payload)
        parsed = connector.parse(_artifact_source(payload))
        receipt = connector.copy_pptx(
            parsed,
            target_ref=_artifact_target(payload),
            idempotency_key=str(payload.get("idempotency_key") or f"artifact.pptx:{_artifact_target(payload)}"),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
    return {
        "claimed": "COMPLETED",
        "source_ref": parsed.source.source_ref,
        "source_sha256": parsed.source.sha256,
        **receipt.as_dict(),
    }


def artifact_image_transform(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        connector = _artifact_connector(ctx, payload)
        parsed = connector.parse(_artifact_source(payload))
        receipt = connector.transform_image(
            parsed,
            target_ref=_artifact_target(payload),
            idempotency_key=str(payload.get("idempotency_key") or f"artifact.image:{_artifact_target(payload)}"),
            width=payload.get("width"),
            height=payload.get("height"),
            output_format=payload.get("output_format"),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
    return {
        "claimed": "COMPLETED",
        "source_ref": parsed.source.source_ref,
        "source_sha256": parsed.source.sha256,
        **receipt.as_dict(),
    }


def produce_research_artifact(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    research = github_research(ctx, {
        "action": payload.get("action") or "github.repo.read",
        "endpoint": payload["endpoint"],
    })
    document = research.get("document") or {}
    target = str(payload.get("target") or "research-artifact.md")
    lines = [
        "# Research artifact",
        "",
        f"endpoint: {payload['endpoint']}",
        f"response_hash: {research.get('response_hash')}",
        "",
        "```json",
        json.dumps(document, indent=2, sort_keys=True)[:8000],
        "```",
        "",
    ]
    written = filesystem_write(ctx, {"target": target, "content": "\n".join(lines)})
    return {
        "claimed": "COMPLETED" if written.get("exists") and research.get("claimed") == "COMPLETED" else "FAILED",
        "target": target,
        "path": written.get("path"),
        "content_hash": written.get("content_hash"),
        "research": research,
    }


def script_run(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    target = Path(payload["target"])
    if not target.is_absolute():
        target = ctx.owned_root / target
    resolved = target.resolve()
    try:
        resolved.relative_to(ctx.owned_root)
    except ValueError as error:
        raise FabricError("script.run target escapes owned root") from error
    if resolved.suffix != ".py":
        raise FabricError("script.run only executes .py files")
    result = subprocess.run(
        [sys.executable, str(resolved), *list(payload.get("args") or ())],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
        cwd=str(ctx.owned_root),
    )
    return {
        "claimed": "COMPLETED" if result.returncode == 0 else "FAILED",
        "returncode": result.returncode,
        "target": str(resolved),
        "stdout": result.stdout[-2000:],
        "stderr": result.stderr[-1000:],
    }


def script_hash_file(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(payload["path"])
    if not path.is_absolute():
        path = ctx.owned_root / path
    digest = _sha256_file(path)
    return {
        "claimed": "COMPLETED",
        "path": str(path),
        "sha256": digest,
    }


def mcp_invoke(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Invoke one discovered MCP tool through its subordinate backend.

    The surrounding Fabric loop supplies canonical request/effect identity.
    Read-only results become bounded DATA_ONLY observations. Mutating results
    remain unverified until an independent JARVIS readback callback reconciles
    them; the MCP server cannot verify its own effect.
    """

    if ctx.mcp_backend is None:
        return {
            "claimed": "BLOCKED",
            "error_code": "mcp_backend_unavailable",
            "authority": "DATA_ONLY",
        }
    request = payload.get("_adapter_request")
    if not isinstance(request, Mapping):
        raise FabricError("MCP invocation requires the canonical adapter request")
    arguments = payload.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        raise FabricError("MCP invocation arguments must be an object")
    action_kind = str(request.get("action_kind") or "")
    if not action_kind.startswith("mcp."):
        raise FabricError("MCP invocation action identity is invalid")
    timeout_ms = payload.get("timeout_ms")
    timeout_s = (
        float(timeout_ms) / 1000.0
        if timeout_ms is not None else None
    )
    stop_identities = set(request.get("stop_scope") or ())
    stop_identities.update({"GLOBAL", str(request.get("mission_id") or "")})
    effect_class = str(request.get("effect_class") or "")
    try:
        receipt = ctx.mcp_backend.invoke_action(
            action_kind,
            dict(arguments),
            invocation_id=str(request.get("request_id")),
            timeout_s=timeout_s,
            stop_check=lambda: bool(
                emergency_stop_reason(
                    ctx.database,
                    payload.get("stop_path"),
                    identities=stop_identities,
                )
            ),
            effect_context=request,
        )
    except BaseException as error:  # noqa: BLE001 - preserve effect ambiguity
        from ..mcp_backend import (
            McpBackendError,
            McpContractError,
            McpInvocationCancelled,
            McpInvocationTimeout,
        )

        if isinstance(error, (McpInvocationTimeout, McpInvocationCancelled)):
            if effect_class != "READ_ONLY":
                raise TimeoutError(str(error)) from error
            return {
                "claimed": "FAILED",
                "authority": "DATA_ONLY",
                "bounded": True,
                "effect_class": effect_class,
                "error_code": (
                    "mcp_cancelled"
                    if isinstance(error, McpInvocationCancelled) else "mcp_timeout"
                ),
            }
        if isinstance(error, (McpBackendError, McpContractError)) and effect_class == "READ_ONLY":
            return {
                "claimed": "FAILED",
                "authority": "DATA_ONLY",
                "bounded": True,
                "effect_class": effect_class,
                "error_code": type(error).__name__,
            }
        if isinstance(error, McpBackendError) and not isinstance(
            error, McpContractError
        ):
            raise TimeoutError(
                "MCP mutation provider outcome is unavailable"
            ) from error
        raise
    normalized = receipt.as_dict()
    result = normalized.get("result") or {}
    claimed: dict[str, Any] = {
        "claimed": receipt.outcome,
        "authority": "DATA_ONLY",
        "server_id": receipt.server_id,
        "server_identity": receipt.server_identity,
        "tool_name": receipt.tool_name,
        "invocation_id": receipt.invocation_id,
        "protocol_version": receipt.protocol_version,
        "session_generation": receipt.session_generation,
        "arguments_sha256": receipt.arguments_sha256,
        "result_sha256": receipt.result_sha256,
        "bounded": True,
        "truncated": bool(result.get("truncated")),
        "mcp_receipt": normalized,
        "effect_id": str(request.get("effect_id") or ""),
        "effect_class": str(request.get("effect_class") or ""),
        "idempotency_key": str(request.get("idempotency_key") or ""),
        "evidence_refs": [
            f"mcp-server:{receipt.server_identity}",
            f"mcp-invocation:{receipt.invocation_id}",
            f"mcp-result:sha256:{receipt.result_sha256}",
        ],
    }
    claimed["provider_receipt"] = {
        "provider_id": f"mcp:{receipt.server_identity}",
        "provider_ref": f"mcp-invocation:{receipt.invocation_id}",
        "server_id": receipt.server_id,
        "server_identity": receipt.server_identity,
        "tool_name": receipt.tool_name,
        "invocation_id": receipt.invocation_id,
        "protocol_version": receipt.protocol_version,
        "session_generation": receipt.session_generation,
        "arguments_sha256": receipt.arguments_sha256,
        "result_sha256": receipt.result_sha256,
    }
    if receipt.error_code:
        claimed["error_code"] = receipt.error_code
    if effect_class == "READ_ONLY":
        claimed["observation"] = result
        return claimed
    claimed.update({"readback_verified": False, "reconciled": False})
    if receipt.outcome != "COMPLETED":
        claimed.update({
            "claimed": "UNKNOWN",
            "ambiguous": True,
            "error_code": claimed.get("error_code") or "mcp_provider_error_ambiguous",
        })
        return claimed
    if ctx.mcp_mutation_verifier is None:
        claimed.update({
            "claimed": "UNKNOWN",
            "ambiguous": True,
            "error_code": "independent_readback_required",
        })
        return claimed
    try:
        verification = ctx.mcp_mutation_verifier({
            "request": dict(request),
            "arguments": dict(arguments),
            "receipt": normalized,
            "owned_root": str(ctx.owned_root),
            "mode": "post_dispatch_readback",
        })
    except Exception as error:  # noqa: BLE001 - ambiguity must fail closed
        claimed.update({
            "claimed": "UNKNOWN",
            "ambiguous": True,
            "error_code": "mcp_readback_failed",
            "detail": redact_text(str(error))[:300],
        })
        return claimed
    if not isinstance(verification, Mapping):
        claimed.update({
            "claimed": "UNKNOWN",
            "ambiguous": True,
            "error_code": "mcp_readback_invalid",
        })
        return claimed
    readback = verification.get("readback")
    accepted = (
        verification.get("readback_verified") is True
        and isinstance(readback, Mapping)
        and readback.get("status") == "MATCH"
        and readback.get("matches") is True
    )
    claimed["readback_verified"] = accepted
    claimed["reconciled"] = accepted
    if verification.get("after_state_ref"):
        claimed["after_state_ref"] = str(verification["after_state_ref"])
    if isinstance(readback, Mapping):
        claimed["readback"] = dict(readback)
    if isinstance(verification.get("acceptance_evidence"), Mapping):
        claimed["acceptance_evidence"] = dict(
            verification["acceptance_evidence"]
        )
    for evidence_ref in verification.get("evidence_refs") or ():
        if isinstance(evidence_ref, str) and evidence_ref.strip():
            claimed["evidence_refs"].append(evidence_ref.strip())
    if not accepted:
        claimed.update({
            "claimed": "UNKNOWN",
            "ambiguous": True,
            "error_code": "mcp_readback_not_accepted",
        })
    return claimed


def _mcp_provider_receipt(
    claimed: Mapping[str, Any],
    verification: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    for candidate in (
        claimed.get("provider_receipt"),
        verification.get("provider_receipt") if verification is not None else None,
    ):
        if (
            isinstance(candidate, Mapping)
            and candidate.get("provider_id")
            and candidate.get("provider_ref")
        ):
            return dict(candidate)
    raw = claimed.get("mcp_receipt")
    if not isinstance(raw, Mapping):
        return None
    required = (
        "server_id", "server_identity", "tool_name", "invocation_id",
        "protocol_version", "session_generation", "arguments_sha256",
        "result_sha256",
    )
    if any(raw.get(name) in (None, "") for name in required):
        return None
    return {
        "provider_id": f"mcp:{raw['server_identity']}",
        "provider_ref": f"mcp-invocation:{raw['invocation_id']}",
        **{name: raw[name] for name in required},
    }


def finalize_mcp_mutation_effect(
    ctx: ExecutorContext,
    payload: Mapping[str, Any],
    claimed: Mapping[str, Any],
) -> dict[str, Any]:
    """Accept one MCP mutation only after independent matching readback.

    This runs after the generic effect driver reaches COMPLETED or FROZEN.  It
    is also safe on replay and under STOP: it can observe/reconcile an existing
    claim, but it never invokes the MCP provider a second time.
    """

    request = payload.get("_adapter_request")
    if not isinstance(request, Mapping):
        return dict(claimed)
    if str(request.get("effect_class") or "") == "READ_ONLY":
        return dict(claimed)
    action_key = str(request.get("effect_id") or claimed.get("action_key") or "")
    if not action_key:
        return dict(claimed)
    state = effect_state(ctx.database, action_key)
    if claimed.get("terminal_truth") is True or state.get("accepted") is True:
        return dict(claimed)
    if state.get("status") not in {COMPLETED, FROZEN}:
        return dict(claimed)

    result = dict(claimed)
    readback = result.get("readback")
    verification: Mapping[str, Any] | None = None
    matches = (
        result.get("readback_verified") is True
        and isinstance(readback, Mapping)
        and readback.get("status") == "MATCH"
        and readback.get("matches") is True
    )
    if not matches and ctx.mcp_mutation_verifier is not None:
        arguments = payload.get("arguments") or {}
        try:
            observed = ctx.mcp_mutation_verifier({
                "request": dict(request),
                "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
                "receipt": dict(result.get("mcp_receipt") or {}),
                "owned_root": str(ctx.owned_root),
                "mode": "reconciliation",
                "effect_state": dict(state),
            })
        except Exception as error:  # noqa: BLE001 - preserve UNKNOWN/FROZEN
            result.update({
                "claimed": "UNKNOWN",
                "error_code": "mcp_reconciliation_failed",
                "detail": redact_text(str(error))[:300],
            })
            return result
        if isinstance(observed, Mapping):
            verification = observed
            readback = observed.get("readback")
            matches = (
                observed.get("readback_verified") is True
                and isinstance(readback, Mapping)
                and readback.get("status") == "MATCH"
                and readback.get("matches") is True
            )
            if isinstance(readback, Mapping):
                result["readback"] = dict(readback)
            if observed.get("after_state_ref"):
                result["after_state_ref"] = str(observed["after_state_ref"])
            if isinstance(observed.get("acceptance_evidence"), Mapping):
                result["acceptance_evidence"] = dict(
                    observed["acceptance_evidence"]
                )
            refs = result.setdefault("evidence_refs", [])
            if isinstance(refs, list):
                for evidence_ref in observed.get("evidence_refs") or ():
                    if (
                        isinstance(evidence_ref, str)
                        and evidence_ref.strip()
                        and evidence_ref.strip() not in refs
                    ):
                        refs.append(evidence_ref.strip())
    provider_receipt = _mcp_provider_receipt(result, verification)
    if not matches or provider_receipt is None or not isinstance(readback, Mapping):
        result.update({
            "claimed": "UNKNOWN",
            "readback_verified": False,
            "reconciled": False,
            "error_code": "mcp_reconciliation_required",
        })
        return result

    result.update({
        "claimed": "COMPLETED",
        "authority": "DATA_ONLY",
        "bounded": True,
        "effect_id": action_key,
        "effect_class": str(request.get("effect_class") or ""),
        "idempotency_key": str(request.get("idempotency_key") or ""),
        "readback_verified": True,
        "reconciled": True,
        "provider_receipt": provider_receipt,
    })
    try:
        descriptor = ctx.mcp_backend.client.descriptor_for_action(
            str(request.get("action_kind") or "")
        )
    except Exception:  # noqa: BLE001 - readback can still remain unresolved
        descriptor = None
    if descriptor is not None:
        result.setdefault("server_id", descriptor.server_id)
        result.setdefault("server_identity", descriptor.server_identity)
        result.setdefault("tool_name", descriptor.tool_name)
    if isinstance(readback.get("sha256"), str) and readback.get("sha256"):
        result.setdefault("result_sha256", readback["sha256"])
    result.setdefault("result_sha256", str(request.get("input_hash") or ""))
    refs = result.setdefault("evidence_refs", [])
    if isinstance(refs, list):
        provider_ref = f"provider:{provider_receipt['provider_ref']}"
        if provider_ref not in refs:
            refs.append(provider_ref)
    acceptance = result.get("acceptance_evidence")
    if not isinstance(acceptance, Mapping):
        acceptance = {
            "authority": "independent_mcp_readback",
            "effect_id": action_key,
            "result_sha256": result.get("result_sha256"),
        }
    return finalize_verified_effect(
        ctx.database,
        action_key,
        provider_receipt=provider_receipt,
        readback=dict(readback),
        result=result,
        acceptance_evidence=dict(acceptance),
        reconciled=True,
    )


HANDLERS: dict[str, Callable[[ExecutorContext, Mapping[str, Any]], dict[str, Any]]] = {
    "file.write": filesystem_write,
    "file.read": filesystem_read,
    "file.replace": file_replace,
    "file.append": filesystem_append,
    "dir.list": dir_list,
    "git.inspect": git_inspect,
    "git.diff": git_diff,
    "git.branch": git_branch,
    "git.commit": git_commit,
    "git.read_ref": git_read_ref,
    "git.log": git_log,
    "git.push": git_push,
    "github.repo.read": github_research,
    "github.issue.read": github_research,
    "github.pull_request.read": github_research,
    "github.commit.read": github_research,
    "github.check.read": github_research,
    "github.release.read": github_research,
    "github.search.read": github_research,
    "github.identity.read": github_identity,
    "github.issue.create": github_issue_create,
    "github.pull_request.create": github_pull_request_create,
    "github.pull_request.publish": github_pull_request_publish,
    "gmail.send": gmail_send,
    "http.read": http_read,
    "http.write": http_write,
    "http.doc.read": http_doc_read,
    "browser.web.read": browser_read,
    "browser.web.wait": browser_wait,
    "browser.web.reconcile": browser_reconcile,
    "browser.web.execute": browser_execute,
    "system.diagnose": system_diagnose,
    "system.runtime": system_runtime,
    "tests.run": tests_run,
    "artifact.write": artifact_write,
    "artifact.research": produce_research_artifact,
    "artifact.parse": artifact_parse,
    "artifact.table.summary": artifact_table_summary,
    "artifact.docx.create": artifact_docx_create,
    "artifact.docx.verify": artifact_docx_verify,
    "artifact.table.export": artifact_table_export,
    "artifact.pdf.copy": artifact_pdf_copy,
    "artifact.pptx.copy": artifact_pptx_copy,
    "artifact.image.transform": artifact_image_transform,
    "hash.file.sha256": script_hash_file,
    "script.run": script_run,
}


def dispatch(ctx: ExecutorContext, action_kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    handler = mcp_invoke if action_kind.startswith("mcp.") else HANDLERS.get(action_kind)
    bound = dict(payload)
    if action_kind.startswith("github.") and action_kind.endswith(".read"):
        bound.setdefault("action", action_kind)
    if handler is None:
        from .computer_operator import dispatch_computer_operator

        result = dispatch_computer_operator(ctx, action_kind, bound)
        if result is None:
            from .personal_operator import dispatch_personal_operator

            result = dispatch_personal_operator(
                ctx.owned_root,
                action_kind,
                bound,
                executable_allowlist=ctx.operator_executable_allowlist,
                environment_allowlist=ctx.operator_environment_allowlist,
                artifact_source_roots=ctx.artifact_source_roots,
            )
            if result is None:
                raise FabricError(f"no executor for {action_kind}")
    else:
        result = handler(ctx, bound)
    if "claimed" not in result:
        result["claimed"] = ClaimOutcome.UNKNOWN.value
    return result
