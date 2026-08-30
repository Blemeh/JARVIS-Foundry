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
from ..database import FoundryDatabase
from .model import ClaimOutcome, FabricError


@dataclass(slots=True)
class ExecutorContext:
    database: FoundryDatabase
    owned_root: Path
    github_api: Callable[[str], tuple[int, bytes]] | None = None
    github_write: Callable[[str, Mapping[str, Any]], tuple[int, bytes]] | None = None

    def __post_init__(self) -> None:
        self.owned_root = Path(self.owned_root).expanduser().resolve()
        self.owned_root.mkdir(parents=True, exist_ok=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def filesystem_write(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    target = str(payload["target"])
    content = str(payload["content"])
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
    changes = dict(payload["changes"])
    message = str(payload["message"])
    connector = _git_connector(ctx)
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
    """Create an issue via brokered ``gh api``. Transport is injectable.

    Live GitHub writes are not performed unless the caller supplies a real
    transport. Default tests inject a fake.
    """
    owner = str(payload["owner"])
    repo = str(payload["repo"])
    title = str(payload["title"])
    body = str(payload.get("body") or "")
    endpoint = f"/repos/{owner}/{repo}/issues"
    request = {"title": title, "body": body}
    transport = ctx.github_write
    if transport is None:
        raise FabricError("github.issue.create requires an explicit write transport")
    status, raw = transport(endpoint, request)
    document = None
    if status in {200, 201}:
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
    number = document.get("number") if isinstance(document, dict) else None
    return {
        "claimed": "COMPLETED" if number is not None else "FAILED",
        "endpoint": endpoint,
        "number": number,
        "html_url": document.get("html_url") if isinstance(document, dict) else None,
        "status": status,
        "title": title,
    }


def github_pull_request_create(ctx: ExecutorContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Open a pull request via injected write transport. No silent live mutation."""
    owner = str(payload["owner"])
    repo = str(payload["repo"])
    title = str(payload["title"])
    head = str(payload["head"])
    base = str(payload.get("base") or "main")
    body = str(payload.get("body") or "")
    endpoint = f"/repos/{owner}/{repo}/pulls"
    transport = ctx.github_write
    if transport is None:
        raise FabricError("github.pull_request.create requires an explicit write transport")
    status, raw = transport(endpoint, {
        "title": title, "head": head, "base": base, "body": body,
    })
    document = None
    if status in {200, 201}:
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
    number = document.get("number") if isinstance(document, dict) else None
    return {
        "claimed": "COMPLETED" if number is not None else "FAILED",
        "endpoint": endpoint,
        "number": number,
        "html_url": document.get("html_url") if isinstance(document, dict) else None,
        "head": head,
        "base": base,
        "status": status,
        "title": title,
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
    "dir.list": dir_list,
    "git.inspect": git_inspect,
    "git.diff": git_diff,
    "git.branch": git_branch,
    "git.commit": git_commit,
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
    "system.diagnose": system_diagnose,
    "tests.run": tests_run,
    "artifact.write": artifact_write,
    "artifact.research": produce_research_artifact,
    "hash.file.sha256": script_hash_file,
    "script.run": script_run,
}


def dispatch(ctx: ExecutorContext, action_kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(action_kind)
    if handler is None:
        raise FabricError(f"no executor for {action_kind}")
    result = handler(ctx, payload)
    if "claimed" not in result:
        result["claimed"] = ClaimOutcome.UNKNOWN.value
    return result
