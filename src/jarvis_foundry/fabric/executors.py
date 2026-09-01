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

from ..connectors.filesystem import FilesystemConnector
from ..connectors.git import GitConnector
from ..connectors.github_research import GitHubResearchConnector
from ..connectors.local_artifacts import (
    ArtifactValidationError,
    LocalArtifactConnector,
    LocalArtifactError,
)
from ..connectors.github_write import GitHubWriteAdapter
from ..database import FoundryDatabase
from .model import ClaimOutcome, FabricError


@dataclass(slots=True)
class ExecutorContext:
    database: FoundryDatabase
    owned_root: Path
    github_api: Callable[[str], tuple[int, bytes]] | None = None
    github_write: Callable[[str, Mapping[str, Any]], tuple[int, bytes]] | None = None
    github_write_adapter: GitHubWriteAdapter | None = None
    doc_transport: Callable[[str], tuple[int, bytes]] | None = None

    def __post_init__(self) -> None:
        self.owned_root = Path(self.owned_root).expanduser().resolve()
        self.owned_root.mkdir(parents=True, exist_ok=True)


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


def _github_write(
    ctx: ExecutorContext,
    action_kind: str,
    endpoint: str,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Run one explicit write with stable identity and optional readback."""

    if ctx.github_write_adapter is not None:
        return ctx.github_write_adapter.write(action_kind, endpoint, request)
    transport = ctx.github_write
    if transport is None:
        raise FabricError(f"{action_kind} requires an explicit write transport")
    # Preserve the historical callback shape while applying the same
    # lifecycle semantics as a caller-owned adapter.  The instance is
    # intentionally per action; durable dedupe belongs to the existing
    # effect/ledger spine, while a reusable GitHubWriteAdapter provides
    # process-level ambiguity protection when the caller opts into it.
    adapter = GitHubWriteAdapter(
        transport=transport,
        read_transport=ctx.github_api,
    )
    return adapter.write(action_kind, endpoint, request)


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
    source_roots = payload.get("source_roots")
    if source_roots is None and payload.get("source_root") is not None:
        source_roots = [payload.get("source_root")]
    return LocalArtifactConnector(ctx.owned_root, source_roots=source_roots)


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


def _artifact_blocked(error: LocalArtifactError) -> dict[str, Any]:
    return {
        "claimed": "BLOCKED",
        "error_code": getattr(error, "error_code", "artifact_error"),
        "error": str(error),
    }


def artifact_parse(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        parsed = _artifact_connector(ctx, payload).parse(
            _artifact_source(payload),
            **_artifact_parse_options(payload),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
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
            fault_after_write=bool(payload.get("fault_after_write", False)),
        )
    except LocalArtifactError as error:
        return _artifact_blocked(error)
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
    "http.read": http_read,
    "http.write": http_write,
    "http.doc.read": http_doc_read,
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
    handler = HANDLERS.get(action_kind)
    if handler is None:
        raise FabricError(f"no executor for {action_kind}")
    bound = dict(payload)
    if action_kind.startswith("github.") and action_kind.endswith(".read"):
        bound.setdefault("action", action_kind)
    result = handler(ctx, bound)
    if "claimed" not in result:
        result["claimed"] = ClaimOutcome.UNKNOWN.value
    return result
