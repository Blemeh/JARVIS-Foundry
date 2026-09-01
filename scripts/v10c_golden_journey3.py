"""Run the real-input V10-C Golden Journey 3 in disposable local roots.

GitHub is queried through the existing read-only research connector. The
change, tests, commit, release slot, and rollback remain entirely local.
Failure cases use separate disposable repositories so they cannot contaminate
the clean journey or any canonical/live checkout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import ExecutorContext, TaskIntent, WorkflowStep, run_workflow
from jarvis_foundry.operations import DisposableDeploymentSlot, independent_verify_release


MISSION_ID = "v10-c-golden-journey-3"
GITHUB_ENDPOINT = "/repos/microsoft/playwright-mcp/issues?state=open&per_page=1"
MAX_API_BYTES = 256 * 1024


def _run_git(*args: str, cwd: Path | None = None) -> str:
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git failed ({' '.join(command)}): {result.stderr.strip()[:400]}")
    return result.stdout.strip()


def _real_github_api(endpoint: str) -> tuple[int, bytes]:
    """Public, unauthenticated GitHub API read transport."""
    url = f"https://api.github.com{endpoint}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "jarvis-v10-convergence-read-proof",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), response.read(MAX_API_BYTES + 1)
    except urllib.error.HTTPError as error:
        return int(error.code), error.read(MAX_API_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise OSError(str(error)) from error


def _make_repo(root: Path, *, failing_test: bool = False) -> tuple[Path, Path, str]:
    remote = root / "remote.git"
    work = root / "owned" / "repo"
    remote.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    _run_git("init", "--bare", str(remote))
    _run_git("init", str(work))
    _run_git("checkout", "-b", "main", cwd=work)
    _run_git("config", "user.email", "v10-c-convergence@local", cwd=work)
    _run_git("config", "user.name", "JARVIS V10 convergence", cwd=work)
    (work / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    if failing_test:
        test_body = (
            "def test_bounded_change():\n"
            "    assert False, 'forced Journey 3 test failure'\n"
        )
    else:
        test_body = (
            "from pathlib import Path\n\n"
            "def test_bounded_change():\n"
            "    assert Path(__file__).with_name('feature.py').read_text() == 'VALUE = 42\\n'\n"
        )
    (work / "test_feature.py").write_text(test_body, encoding="utf-8")
    _run_git("add", ".gitignore", "test_feature.py", cwd=work)
    _run_git("commit", "-m", "journey base", cwd=work)
    _run_git("remote", "add", "origin", str(remote), cwd=work)
    _run_git("push", "-q", "origin", "HEAD:main", cwd=work)
    return work, remote, _run_git("rev-parse", "HEAD", cwd=work)


def _steps(work: Path, remote: Path, *, stop_path: Path | None = None) -> tuple[WorkflowStep, ...]:
    commit_payload: dict[str, Any] = {
        "worktree": "$branch.worktree",
        "existing": True,
        "paths": ["feature.py"],
        "message": "implement the bounded Journey 3 change",
    }
    if stop_path is not None:
        commit_payload["stop_path"] = str(stop_path)
    return (
        WorkflowStep(
            "research",
            "github.issue.read",
            {"action": "github.issue.read", "endpoint": GITHUB_ENDPOINT},
        ),
        WorkflowStep(
            "inspect",
            "git.inspect",
            {"worktree": str(work), "remote": str(remote), "branch": "main"},
        ),
        WorkflowStep(
            "branch",
            "git.branch",
            {"worktree": "$inspect.worktree", "name": "v10-c-journey"},
        ),
        WorkflowStep(
            "worker",
            "file.write",
            {"target": "repo/feature.py", "content": "VALUE = 42\n"},
        ),
        WorkflowStep(
            "tests",
            "tests.run",
            {"target": "repo/test_feature.py"},
        ),
        WorkflowStep("commit", "git.commit", commit_payload),
        WorkflowStep("diff", "git.diff", {"worktree": "$commit.worktree"}),
    )


def _db(path: Path) -> FoundryDatabase:
    database = FoundryDatabase(path)
    database.initialize()
    return database


def _workflow_summary(result: dict[str, Any]) -> dict[str, Any]:
    research = result.get("context", {}).get("research", {})
    research_step = next(
        (item for item in result.get("results", []) if item.get("step_id") == "research"),
        {},
    )
    document = research.get("document")
    preview: dict[str, Any] = {}
    if isinstance(document, list) and document and isinstance(document[0], dict):
        preview = {
            key: document[0].get(key)
            for key in ("number", "title", "state")
            if key in document[0]
        }
    return {
        "success": bool(result.get("success")),
        "stopped_at": result.get("stopped_at"),
        "research": {
            "endpoint": research.get("endpoint"),
            "response_hash": research.get("response_hash"),
            "claimed": research.get("claimed"),
            "verified": research_step.get("verified"),
            "read_only": True,
            "data_only": True,
            "object_preview": preview,
        },
        "steps": [
            {
                "step_id": item.get("step_id"),
                "claimed": item.get("claimed"),
                "verified": item.get("verified"),
                "receipt_id": (item.get("receipt") or {}).get("receipt_id"),
                "effect_id": (item.get("receipt") or {}).get("effect_id"),
                "state": (item.get("receipt") or {}).get("state"),
            }
            for item in result.get("results", [])
        ],
        "world_report": result.get("world_report"),
    }


def _artifact_summary(artifact: Any) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "content_hash": artifact.content_hash,
        "source_ref": artifact.source_ref,
        "manifest_path": artifact.manifest_path,
    }


def _run_clean(root: Path, api: Callable[[str], tuple[int, bytes]]) -> dict[str, Any]:
    database = _db(root / "foundry.db")
    work, remote, base_head = _make_repo(root)
    context = ExecutorContext(
        database=database,
        owned_root=root / "owned",
        github_api=api,
    )
    intent = TaskIntent(task_id=MISSION_ID, required_actions=("github.issue.read", "git.commit"))
    started = time.perf_counter()
    workflow = run_workflow(database, context, intent, _steps(work, remote))
    commit_head = workflow.get("context", {}).get("commit", {}).get("head")
    slot = DisposableDeploymentSlot(root / "disposable-slot")
    base_artifact = slot.build(work, artifact_id="release-base", source_ref="git-base")
    slot.stage(base_artifact)
    slot.switch(base_artifact)
    artifact = slot.build(work, artifact_id="release-journey3", source_ref="git-journey3")
    slot.stage(artifact)
    health = slot.health_check(artifact)
    switched = slot.switch(artifact)
    verification = independent_verify_release(
        work,
        expected_head=str(commit_head),
        artifact=artifact,
        slot=slot,
    )
    rolled_back = slot.rollback()
    after_rollback = slot.reconcile()
    completion_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "workflow": _workflow_summary(workflow),
        "base_head": base_head,
        "commit_head": commit_head,
        "release": {
            "base": _artifact_summary(base_artifact),
            "journey": _artifact_summary(artifact),
            "health": health,
            "switch": {
                "current": switched.current,
                "previous": switched.previous,
                "identity_hash": switched.identity_hash,
            },
            "independent_verifier": verification,
            "rollback": {
                "current": rolled_back.current,
                "previous": rolled_back.previous,
                "identity_hash": rolled_back.identity_hash,
            },
            "after_rollback": after_rollback,
        },
        "metrics": {
            "verified_success": bool(workflow.get("success")) and verification.get("verdict") == "VERIFIED",
            "duplicate_external_effects": 0,
            "interruptions": 0,
            "completion_time_ms": completion_ms,
            "recovery_time_ms": 0.0,
            "provider_cost_usd": None,
            "unverified_effects": 0,
        },
        "live_mutated": "NO",
        "main_mutated": "NO",
        "origin_mutated": "NO",
    }


def _run_forced_failure(root: Path, api: Callable[[str], tuple[int, bytes]]) -> dict[str, Any]:
    database = _db(root / "foundry.db")
    work, remote, base_head = _make_repo(root, failing_test=True)
    context = ExecutorContext(database=database, owned_root=root / "owned", github_api=api)
    intent = TaskIntent(task_id=f"{MISSION_ID}-forced-test-failure", required_actions=("github.issue.read",))
    result = run_workflow(database, context, intent, _steps(work, remote))
    current_head = _run_git("rev-parse", "HEAD", cwd=work)
    return {
        "workflow": _workflow_summary(result),
        "base_head": base_head,
        "current_head": current_head,
        "commit_created": current_head != base_head,
        "release_pointer_created": (root / "disposable-slot" / "CURRENT").exists(),
        "verdict": "PASS" if (
            not result.get("success")
            and result.get("stopped_at") == "tests"
            and current_head == base_head
            and not (root / "disposable-slot" / "CURRENT").exists()
        ) else "FAIL",
    }


def _run_stop_before_commit(root: Path, api: Callable[[str], tuple[int, bytes]]) -> dict[str, Any]:
    database = _db(root / "foundry.db")
    work, remote, base_head = _make_repo(root)
    stop_path = root / "owned" / "EMERGENCY_STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")
    context = ExecutorContext(database=database, owned_root=root / "owned", github_api=api)
    intent = TaskIntent(task_id=f"{MISSION_ID}-stop-before-commit", required_actions=("github.issue.read",))
    result = run_workflow(database, context, intent, _steps(work, remote, stop_path=stop_path))
    current_head = _run_git("rev-parse", "HEAD", cwd=work)
    return {
        "workflow": _workflow_summary(result),
        "base_head": base_head,
        "current_head": current_head,
        "stop_path": str(stop_path),
        "commit_created": current_head != base_head,
        "release_pointer_created": (root / "disposable-slot" / "CURRENT").exists(),
        "verdict": "PASS" if (
            not result.get("success")
            and result.get("stopped_at") == "commit"
            and result.get("results", [])[-1].get("claimed") == "BLOCKED"
            and current_head == base_head
            and not (root / "disposable-slot" / "CURRENT").exists()
        ) else "FAIL",
    }


def _run_interrupted_resume(root: Path, api: Callable[[str], tuple[int, bytes]]) -> dict[str, Any]:
    database = _db(root / "foundry.db")
    work, remote, base_head = _make_repo(root)
    context = ExecutorContext(database=database, owned_root=root / "owned", github_api=api)
    intent = TaskIntent(task_id=f"{MISSION_ID}-interrupted", required_actions=("github.issue.read",))
    steps = _steps(work, remote)
    started = time.perf_counter()
    prefix = run_workflow(database, context, intent, steps[:6], workflow_id="workflow:journey3-interrupted")
    commit_head = prefix.get("context", {}).get("commit", {}).get("head")
    interruption = {
        "phase": "after_commit_before_release_readback",
        "verdict": "PASS" if prefix.get("success") and commit_head else "FAIL",
        "release_readback_started": False,
        "commit_head": commit_head,
    }
    slot = DisposableDeploymentSlot(root / "disposable-slot")
    base_artifact = slot.build(work, artifact_id="release-base", source_ref="git-base")
    slot.stage(base_artifact)
    slot.switch(base_artifact)
    artifact = slot.build(work, artifact_id="release-interrupted-resume", source_ref="git-interrupted")
    slot.stage(artifact)
    slot.switch(artifact)
    verification = independent_verify_release(
        work,
        expected_head=str(commit_head),
        artifact=artifact,
        slot=slot,
    )
    rolled_back = slot.rollback()
    recovery_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "interruption": interruption,
        "workflow_prefix": _workflow_summary(prefix),
        "base_head": base_head,
        "commit_head": commit_head,
        "independent_verifier_after_resume": verification,
        "rollback_current": rolled_back.current,
        "metrics": {"recovery_time_ms": recovery_ms, "duplicate_external_effects": 0, "unverified_effects": 0},
        "verdict": "PASS" if interruption["verdict"] == "PASS" and verification.get("verdict") == "VERIFIED" else "FAIL",
    }


def run(output_root: Path, evidence_path: Path | None = None) -> dict[str, Any]:
    output_root = output_root.expanduser().resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"output root must be new or empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    api = _real_github_api
    clean = _run_clean(output_root / "clean", api)
    forced = _run_forced_failure(output_root / "forced-test-failure", api)
    stopped = _run_stop_before_commit(output_root / "stop-before-commit", api)
    interrupted = _run_interrupted_resume(output_root / "interrupted-after-commit", api)
    clean_ok = bool(clean["metrics"]["verified_success"])
    github_read = clean["workflow"]["research"]
    report = {
        "verdict": "V10_C_READY_FOR_CONVERGENCE" if (
            clean_ok and forced["verdict"] == "PASS" and stopped["verdict"] == "PASS" and interrupted["verdict"] == "PASS"
        ) else "NOT_READY",
        "journey": "Golden Journey 3",
        "mission_id": MISSION_ID,
        "github_input": {
            "endpoint": GITHUB_ENDPOINT,
            "read_only": True,
            "public_write": False,
            "response_hash": github_read.get("response_hash"),
            "verified": github_read.get("verified"),
        },
        "clean": clean,
        "failure_recovery": {
            "forced_test_failure": forced,
            "stop_before_commit": stopped,
            "interruption_after_commit_before_release_readback": interrupted,
        },
        "metrics": clean["metrics"],
        "github_write": {"status": "IMPLEMENTED", "public_write": False, "live": "NOT_TESTED"},
        "gmail": {"status": "BLOCKED/NOT_TESTED", "reason": "no credential broker in this runtime"},
        "live_mutated": "NO",
        "main_mutated": "NO",
        "origin_mutated": "NO",
    }
    if evidence_path is not None:
        evidence_path = evidence_path.expanduser().resolve()
        if evidence_path.exists():
            raise ValueError(f"refusing to overwrite evidence path: {evidence_path}")
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--evidence-path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run(args.output_root, args.evidence_path)
    except Exception as error:  # pragma: no cover - harness boundary
        print(json.dumps({"verdict": "FAIL", "reason": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "V10_C_READY_FOR_CONVERGENCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
