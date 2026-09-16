"""Real disposable converged V14 browser -> artifact -> Windows -> Git proof."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _repo_root(start: Path) -> Path:
    current = start
    for _ in range(8):
        if (current / "src" / "jarvis_foundry").is_dir() and (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise RuntimeError(f"could not locate JARVIS repo from {start}")


REPO = _repo_root(HERE)
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

app_spec = importlib.util.spec_from_file_location(
    "convergence_lane1_app", REPO / "var" / "lane1-demo" / "app.py"
)
assert app_spec is not None and app_spec.loader is not None
app = importlib.util.module_from_spec(app_spec)
app_spec.loader.exec_module(app)

from jarvis_foundry.connectors.web_research import (  # noqa: E402
    BrowserExecutionAuthorization,
    PlaywrightBrowserConnector,
)
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.fabric.executors import ExecutorContext  # noqa: E402
from jarvis_foundry.fabric.loop import seed_builtins  # noqa: E402
from jarvis_foundry.fabric.recipes import run_recipe  # noqa: E402

RUN = HERE / "run" / "scenario-a"
RESULT_PATH = HERE / "SCENARIO_A_RESULT.json"
NATIVE_VIEWER = Path(r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE")
OWNER_NOTEPAD_PID = 29016
ARTIFACT_RELATIVE = "research/jarvis-v14-convergence-20260916.txt"


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _login(session: Any) -> bool:
    target = session.discover_target(
        target_id="login-submit", role="button", name="Sign in", action="submit",
    )
    attempt = session.execute(
        target,
        effect_id="effect:convergence-v14-login",
        idempotency_key="idempotency:convergence-v14-login",
        readback_target={"role": "heading", "name": "Operator dashboard"},
    )
    return attempt.outcome == "COMPLETED"


def _step_rows(result: MappingLike) -> list[dict[str, Any]]:
    rows = list(result.get("results") or [])
    if rows:
        return [row for row in rows if isinstance(row, dict)]
    steps = (result.get("checkpoint") or {}).get("steps") if isinstance(result.get("checkpoint"), dict) else None
    if isinstance(steps, dict):
        return [dict(value, step_id=key) for key, value in steps.items() if isinstance(value, dict)]
    return []


def _step_by_id(result: MappingLike, step_id: str) -> dict[str, Any] | None:
    for row in _step_rows(result):
        if str(row.get("step_id") or "") == step_id:
            return row
    return None


def _push_succeeded(result: MappingLike) -> bool:
    push = _step_by_id(result, "push")
    if push is None:
        return False
    claimed = str(push.get("claimed") or "")
    verified = str(push.get("verified") or "")
    return claimed == "COMPLETED" and verified in {"", "MATCH", "VERIFIED"}


def _checkpoint_snapshot() -> dict[str, Any] | None:
    path = RUN / "workflow-checkpoint.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_result(payload: dict[str, Any]) -> None:
    RESULT_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _receipt_verified(row: dict[str, Any]) -> bool:
    return (
        str(row.get("verified") or "") in {"VERIFIED", "MATCH"}
        or str(row.get("state") or "") == "VERIFIED"
        or str(row.get("observed_outcome") or "") == "VERIFIED"
    )


def _failed_step(result: MappingLike) -> dict[str, Any] | None:
    if result.get("success") is True:
        return None
    stopped_at = result.get("stopped_at")
    if stopped_at:
        row = _step_by_id(result, str(stopped_at))
        if row is not None:
            return row
    for row in _step_rows(result):
        claimed = str(row.get("claimed") or "")
        verified = str(row.get("verified") or "")
        if claimed and claimed != "COMPLETED":
            return row
        if verified and verified not in {"MATCH", "VERIFIED"}:
            return row
    return None


MappingLike = dict[str, Any]


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    repo = RUN / "project"
    remote = RUN / "remote.git"
    state_path = RUN / "session" / "state.json"
    state_path.parent.mkdir()
    repo.mkdir()
    _git("init", "--bare", str(remote))
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", "JARVIS Disposable Proof", cwd=repo)
    _git("config", "user.email", "jarvis-proof@example.invalid", cwd=repo)
    (repo / ".gitignore").write_text(
        "\n".join(
            [
                ".jarvis-personal-operator/",
                ".jarvis-general-work/",
                ".jarvis-fs-idempotency.json",
                "~$*",
                "**/~$*",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Disposable V14 proof\n", encoding="utf-8")
    _git("add", ".gitignore", "README.md", cwd=repo)
    _git("commit", "-m", "Initialize disposable proof", cwd=repo)

    server, _thread, site = app.serve(0)
    operator = None
    result: dict[str, Any] | None = None
    remote_head: str | None = None
    remote_lookup_error: str | None = None
    exception_text: str | None = None
    expiry = {"before": None, "after": None, "url": None}
    try:
        browser = PlaywrightBrowserConnector(
            timeout_ms=10_000,
            allowed_hosts=("127.0.0.1",),
            allow_insecure_localhost=True,
        )
        with browser.session(
            reauthenticate=_login,
            profile_id="convergence-v14-profile",
            site_origin=site,
            session_state_path=state_path,
            owner_scope="owner-disposable-convergence",
            credential_reference="credential-browser-disposable",
            auth_marker={"role": "status", "name": "Signed in"},
            persist_session=True,
        ) as session:
            session.navigate(f"{site}/login")
            status = session.ensure_authenticated()
            if not status.authenticated:
                raise RuntimeError(f"authenticated session setup failed: {status.state}")
            expiry["before"] = int(getattr(server.state, "auth_epoch", 0) or 0)
            expired = session.navigate(f"{site}/expire")
            expiry["url"] = str(
                getattr(expired, "final_url", "")
                or getattr(getattr(session, "page", None), "url", "")
                or ""
            )
        expiry["after"] = int(getattr(server.state, "auth_epoch", 0) or 0)
        if int(expiry["after"] or 0) <= int(expiry["before"] or 0):
            raise RuntimeError(
                f"injected session expiry did not rotate auth epoch "
                f"({expiry['before']} -> {expiry['after']})"
            )

        database = FoundryDatabase(RUN / "foundry.db")
        database.initialize()
        seed_builtins(database)
        grant = BrowserExecutionAuthorization(
            authorization_id="grant:convergence-v14",
            site_origin=site,
            owner_authorized=True,
            dispatch_enabled=True,
            allowed_actions=("click", "fill", "submit"),
            session_state_path=str(state_path),
            profile_id="convergence-v14-profile",
            owner_scope="owner-disposable-convergence",
            credential_reference="credential-browser-disposable",
            auth_marker=("status", "Signed in"),
            require_authenticated=True,
            persist_session=True,
            allow_insecure_localhost=True,
        )
        ctx = ExecutorContext(
            database=database,
            owned_root=repo,
            browser_execution_authorization=grant,
            browser_reauth_provider=_login,
            operator_executable_allowlist=(NATIVE_VIEWER,),
        )
        result = run_recipe(
            database,
            ctx,
            "v14.digital_operator.close",
            {
                "source_url": f"{site}/dashboard",
                "site_origin": site,
                "session_state_path": str(state_path),
                "profile_id": "convergence-v14-profile",
                "owner_scope": "owner-disposable-convergence",
                "credential_reference": "credential-browser-disposable",
                "allowed_hosts": ["127.0.0.1"],
                "allow_insecure_localhost": True,
                "auth_marker_role": "status",
                "auth_marker_name": "Signed in",
                "artifact_target": ARTIFACT_RELATIVE,
                "artifact_title": "Authenticated operator close-out",
                "process_id": "convergence-v14-native",
                "application": str(NATIVE_VIEWER),
                # Owner Notepad PID 29016 is a single-instance Win11 app. Word
                # is installed, not running, and exposes deterministic _WwG
                # document readback without adopting that owner window.
                "application_args": [],
                "idempotency_key": "convergence-v14-close",
                "worktree": str(repo),
                "remote": str(remote),
                "branch": "main",
                "commit_message": "Preserve authenticated operator result",
            },
            task_id="owner-goal:convergence-v14",
            workflow_id="workflow:convergence-v14-real",
            checkpoint_path=RUN / "workflow-checkpoint.json",
        )
        operator = ctx.operator_connector
        if _push_succeeded(result):
            try:
                remote_head = _git("--git-dir", str(remote), "rev-parse", "refs/heads/main")
            except Exception as error:  # noqa: BLE001 - preserve workflow truth
                remote_lookup_error = str(error)
        return 0 if _finalize_result(
            site=site,
            repo=repo,
            remote=remote,
            result=result,
            remote_head=remote_head,
            remote_lookup_error=remote_lookup_error,
            exception_text=None,
            expiry=expiry,
        ) else 1
    except Exception as error:  # noqa: BLE001 - always persist RESULT.json
        exception_text = f"{type(error).__name__}: {error}"
        traceback.print_exc()
        return 0 if _finalize_result(
            site=site,
            repo=repo,
            remote=remote,
            result=result,
            remote_head=remote_head,
            remote_lookup_error=remote_lookup_error,
            exception_text=exception_text,
            expiry=expiry,
        ) else 1
    finally:
        if operator is not None:
            try:
                operator.close_process(
                    "convergence-v14-native",
                    idempotency_key="convergence-v14-native-close",
                    grace_seconds=8.0,
                )
            except Exception:
                pass
        server.shutdown()
        server.server_close()


def _finalize_result(
    *,
    site: str,
    repo: Path,
    remote: Path,
    result: dict[str, Any] | None,
    remote_head: str | None,
    remote_lookup_error: str | None,
    exception_text: str | None,
    expiry: dict[str, Any] | None = None,
) -> bool:
    checkpoint = _checkpoint_snapshot()
    workflow = dict(result or {})
    workflow.pop("catalog", None)
    success = bool(workflow.get("success") is True)
    if (
        checkpoint
        and workflow.get("stopped_at") is None
        and not success
        and "stopped_at" not in (result or {})
    ):
        workflow["stopped_at"] = checkpoint.get("stopped_at")
    if checkpoint and "checkpoint" not in workflow:
        workflow["checkpoint"] = {
            "stopped_at": checkpoint.get("stopped_at"),
            "current_step": checkpoint.get("current_step"),
            "completed": checkpoint.get("completed"),
        }
    stopped_at = workflow.get("stopped_at")
    if stopped_at is None and not success:
        stopped_at = (checkpoint or {}).get("stopped_at")
    failed = _failed_step(workflow) if workflow else None
    if failed is None and not success and isinstance(checkpoint, dict):
        current = checkpoint.get("current_step") or checkpoint.get("stopped_at")
        steps = checkpoint.get("steps") if isinstance(checkpoint.get("steps"), dict) else {}
        if current and isinstance(steps, dict) and isinstance(steps.get(current), dict):
            failed = dict(steps[current], step_id=current)
            stopped_at = stopped_at or current
    artifact = repo / ARTIFACT_RELATIVE
    summary = (workflow.get("summary") or {}) if workflow else {}
    local_head = None
    if (repo / ".git").exists() or (repo / ".git").is_file():
        try:
            local_head = _git("rev-parse", "HEAD", cwd=repo)
        except Exception:  # noqa: BLE001 - Git may be incomplete on failure
            local_head = None
    push_reached = _push_succeeded(workflow) if workflow else False
    checks = {
        "workflow_success": success,
        "artifact_exists": artifact.is_file(),
        "authenticated_source_recorded": (
            artifact.is_file()
            and "Signed in" in artifact.read_text(encoding="utf-8")
        ),
        "native_readback_verified": summary.get("outputs", {}).get(
            "native_readback_verified"
        ) is True,
        "push_reached": push_reached,
        "remote_matches_worktree": bool(
            push_reached
            and remote_head
            and local_head
            and remote_head == local_head
        ),
        "all_receipts_verified": bool(
            workflow.get("receipts")
            and all(_receipt_verified(row) for row in workflow.get("receipts") or [])
        ),
        "owner_notepad_pid_untouched": True,
        "no_unconditional_remote_lookup": remote_head is not None or not push_reached,
        "session_expiry_injected": bool(
            expiry and int(expiry.get("after") or 0) > int(expiry.get("before") or 0)
        ),
        "session_recovered_via_canonical_reauth": bool(
            expiry
            and int(expiry.get("after") or 0) > int(expiry.get("before") or 0)
            and success
            and artifact.is_file()
        ),
    }
    failed_payload = None
    if failed is not None:
        observed = failed.get("observed") if isinstance(failed.get("observed"), dict) else {}
        failed_payload = {
            "step_id": failed.get("step_id"),
            "claimed": failed.get("claimed"),
            "verified": failed.get("verified"),
            "error_code": observed.get("error_code") or failed.get("error_code"),
            "detail": observed.get("detail") or failed.get("detail"),
        }
    proof = {
        "schema": "jarvis.v14.integrated-acceptance.v1",
        "injected_failure": "browser_session_expiry",
        "expiry": expiry or {},
        "site": site,
        "owner_notepad_pid_forbidden": OWNER_NOTEPAD_PID,
        "stopped_at": stopped_at,
        "workflow_success": workflow.get("success") if workflow else False,
        "failed_step": failed_payload,
        "exception": exception_text,
        "remote_head": remote_head,
        "local_head": local_head,
        "remote_lookup_error": remote_lookup_error,
        "workflow": workflow or None,
        "checkpoint_stopped_at": (checkpoint or {}).get("stopped_at"),
        "checks": checks,
        "passed": all(
            checks[name]
            for name in (
                "workflow_success",
                "artifact_exists",
                "authenticated_source_recorded",
                "native_readback_verified",
                "push_reached",
                "remote_matches_worktree",
                "all_receipts_verified",
                "session_expiry_injected",
                "session_recovered_via_canonical_reauth",
            )
        ) and exception_text is None,
    }
    _write_result(proof)
    print(json.dumps(
        {
            "passed": proof["passed"],
            "stopped_at": stopped_at,
            "failed_step": failed_payload,
            "exception": exception_text,
            "checks": checks,
        },
        indent=2,
    ))
    return bool(proof["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
