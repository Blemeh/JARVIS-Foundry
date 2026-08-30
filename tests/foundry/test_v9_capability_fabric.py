"""V9 capability fabric: selection, verification, gap expansion, restart."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    CapabilitySpec,
    ExecutorContext,
    Kind,
    POOR_HASH_CANDIDATE_SOURCE,
    Readiness,
    TaskIntent,
    UnattendedMode,
    VerifyVerdict,
    WorkflowStep,
    acquire_hash_capability,
    catalog_snapshot,
    execute_action,
    execute_for_spine,
    observed_readiness,
    record_outcome,
    register_capability,
    reliability,
    route_provider,
    run_intent,
    run_workflow,
    seed_builtins,
    select_capability,
    unattended_mode_for,
)
from jarvis_foundry.fabric.model import ObservedReadiness, Semantic
from jarvis_foundry.runtime.provider_config import ProviderSettings

from v3_fixtures import make_bare_remote


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _ctx(tmp_path: Path, database: FoundryDatabase, **kwargs) -> ExecutorContext:
    owned = tmp_path / "owned"
    owned.mkdir()
    return ExecutorContext(database=database, owned_root=owned, **kwargs)


def _intent(*actions: str, preferred: str | None = None) -> TaskIntent:
    return TaskIntent(task_id="task.v9", required_actions=actions, preferred=preferred)


def _clone_repo(tmp_path: Path, owned: Path) -> tuple[Path, Path]:
    bare = make_bare_remote(tmp_path / "remote.git")
    work = owned / "repo"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "v9@local"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "v9-test"], check=True)
    (work / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "base"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "HEAD:main"], check=True)
    return work, bare


def test_unknown_stays_unknown_until_verified(tmp_path: Path) -> None:
    database = _db(tmp_path)
    seed_builtins(database)
    ready = observed_readiness(database, "cap.file.write")
    assert ready.state is Readiness.UNKNOWN
    assert ready.last_outcome is None
    with pytest.raises(Exception):
        ObservedReadiness(
            capability_id="cap.file.write",
            state=Readiness.READY,
            last_outcome="SUCCESS",
        )


def test_existing_capability_completes_real_filesystem_and_git_work(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    work, _bare = _clone_repo(tmp_path, ctx.owned_root)
    result = run_intent(
        database,
        ctx,
        _intent("file.write", "git.commit", "system.diagnose"),
        {
            "file.write": {"target": "notes.txt", "content": "hello fabric"},
            "git.commit": {
                "worktree": str(work),
                "changes": {"change.txt": "v9\n"},
                "message": "v9 fabric commit",
            },
            "system.diagnose": {},
        },
    )
    assert result["success"] is True
    assert (ctx.owned_root / "notes.txt").read_text(encoding="utf-8") == "hello fabric"
    head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert len(head) == 40
    assert observed_readiness(database, "cap.file.write").state is Readiness.READY


def test_preferred_failure_falls_back_to_alternate(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    register_capability(database, CapabilitySpec(
        capability_id="cap.file.write.broken", version="1.0.0", kind=Kind.CONNECTOR,
        action_kind="file.write", semantic=Semantic.WRITE, executor="filesystem",
        unattended=UnattendedMode.ALLOWED,
    ))
    record_outcome(
        database, "cap.file.write.broken",
        success=True, verified=VerifyVerdict.MATCH, note="seed ready",
    )
    record_outcome(
        database, "cap.file.write.broken",
        success=False, verified=VerifyVerdict.MISMATCH, note="broke",
    )
    assert observed_readiness(database, "cap.file.write.broken").state is Readiness.DEGRADED
    selection = select_capability(
        database,
        _intent("file.write", preferred="cap.file.write.broken"),
        "file.write",
    )
    assert selection.capability_id == "cap.file.write"
    result = execute_action(
        database, ctx, _intent("file.write"), "file.write",
        {"target": "ok.txt", "content": "alt"},
    )
    assert result.success is True
    assert (ctx.owned_root / "ok.txt").read_text(encoding="utf-8") == "alt"


def test_missing_capability_detected_then_built_and_used(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    sample = ctx.owned_root / "payload.bin"
    sample.write_bytes(b"abc123")
    result = run_intent(
        database, ctx, _intent("hash.file.sha256"),
        {"hash.file.sha256": {"path": str(sample)}},
    )
    assert result["success"] is True
    digest = result["results"][0]["claimed_effect"]["sha256"]
    assert digest == hashlib.sha256(b"abc123").hexdigest()
    assert observed_readiness(database, "cap.hash.file.sha256").state is Readiness.READY


def test_poor_candidate_is_rejected(tmp_path: Path) -> None:
    database = _db(tmp_path)
    owned = tmp_path / "owned"
    owned.mkdir()
    report = acquire_hash_capability(
        database, owned, source=POOR_HASH_CANDIDATE_SOURCE,
    )
    assert report["status"] == "REJECTED"
    snapshot = catalog_snapshot(database)
    ids = [row["spec"]["capability_id"] for row in snapshot["capabilities"]]
    assert "cap.hash.file.sha256" not in ids


def test_degraded_capability_becomes_unavailable(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    execute_action(
        database, ctx, _intent("file.write"), "file.write",
        {"target": "x.txt", "content": "1"},
        claim_override={
            "claimed": "COMPLETED",
            "target": "ghost.txt",
            "path": str(ctx.owned_root / "ghost.txt"),
            "content_hash": "sha256:dead",
        },
    )
    assert observed_readiness(database, "cap.file.write").state is Readiness.DEGRADED
    selection = select_capability(database, _intent("file.write"), "file.write")
    assert selection.available is False
    assert selection.gap is not None


def test_restart_preserves_capability_knowledge(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    execute_action(
        database, ctx, _intent("system.diagnose"), "system.diagnose", {},
    )
    reopened = FoundryDatabase(database.path)
    assert observed_readiness(reopened, "cap.system.diagnose").state is Readiness.READY
    assert catalog_snapshot(reopened)["count"] >= 1


def test_claim_mismatch_is_caught_by_verification(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    result = execute_action(
        database, ctx, _intent("file.write"), "file.write",
        {"target": "real.txt", "content": "nope"},
        claim_override={
            "claimed": "COMPLETED",
            "target": "missing.txt",
            "path": str(ctx.owned_root / "missing.txt"),
            "content_hash": "sha256:00",
        },
    )
    assert result.claimed.value == "COMPLETED"
    assert result.verified is VerifyVerdict.MISMATCH
    assert not (ctx.owned_root / "missing.txt").exists()


def test_several_capabilities_cooperate_on_research_artifact(tmp_path: Path) -> None:
    database = _db(tmp_path)
    payload = {"full_name": "owner/demo", "description": "research target"}

    def fetch(endpoint: str) -> tuple[int, bytes]:
        return 200, json.dumps(payload).encode("utf-8")

    ctx = _ctx(tmp_path, database, github_api=fetch)
    seed_builtins(database)
    result = run_intent(
        database, ctx, _intent("artifact.research"),
        {"artifact.research": {"endpoint": "/repos/owner/demo", "target": "report.md"}},
    )
    assert result["success"] is True
    text = (ctx.owned_root / "report.md").read_text(encoding="utf-8")
    assert "owner/demo" in text
    assert "Research artifact" in text


def test_github_issue_create_uses_injected_transport(tmp_path: Path) -> None:
    database = _db(tmp_path)

    def write(endpoint: str, request: dict) -> tuple[int, bytes]:
        assert endpoint == "/repos/owner/demo/issues"
        body = json.dumps({
            "number": 7,
            "html_url": "https://github.com/owner/demo/issues/7",
            **request,
        })
        return 201, body.encode("utf-8")

    ctx = _ctx(tmp_path, database, github_write=write)
    seed_builtins(database)
    result = execute_action(
        database, ctx, _intent("github.issue.create"), "github.issue.create",
        {"owner": "owner", "repo": "demo", "title": "v9", "body": "from fabric"},
    )
    assert result.success is True
    assert result.claimed_effect["number"] == 7


def test_github_issue_read_uses_research_connector(tmp_path: Path) -> None:
    database = _db(tmp_path)

    def fetch(endpoint: str) -> tuple[int, bytes]:
        assert endpoint.endswith("/issues/7")
        return 200, json.dumps({"number": 7, "title": "v9"}).encode("utf-8")

    ctx = _ctx(tmp_path, database, github_api=fetch)
    seed_builtins(database)
    result = execute_action(
        database, ctx, _intent("github.issue.read"), "github.issue.read",
        {"action": "github.issue.read", "endpoint": "/repos/owner/demo/issues/7"},
    )
    assert result.success is True
    assert result.claimed_effect["document"]["number"] == 7


def test_github_search_read(tmp_path: Path) -> None:
    database = _db(tmp_path)

    def fetch(endpoint: str) -> tuple[int, bytes]:
        assert "search/issues" in endpoint
        return 200, json.dumps({"total_count": 1, "items": [{"title": "found"}]}).encode("utf-8")

    ctx = _ctx(tmp_path, database, github_api=fetch)
    seed_builtins(database)
    result = execute_action(
        database, ctx, _intent("github.search.read"), "github.search.read",
        {"action": "github.search.read", "endpoint": "/search/issues?q=repo:owner/demo"},
    )
    assert result.success is True
    assert result.claimed_effect["document"]["total_count"] == 1


def test_provider_refuses_unattended_contributor_without_consent() -> None:
    settings = ProviderSettings(
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="sk-test-not-used-here-000000",
        source="test",
    )
    assert unattended_mode_for(
        settings, hermes_noninteractive_consent=False,
    ) is UnattendedMode.INTERACTIVE_REQUIRED
    route = route_provider(
        settings=settings, unattended=True, hermes_noninteractive_consent=False,
    )
    assert route.selected is False
    assert "unattended" in route.reason
    allowed = route_provider(
        settings=settings, unattended=True, hermes_noninteractive_consent=True,
    )
    assert allowed.selected is True


def test_unattended_llm_dispatch_is_not_silently_selected(tmp_path: Path) -> None:
    database = _db(tmp_path)
    seed_builtins(database)
    selection = select_capability(database, _intent("llm.dispatch"), "llm.dispatch")
    assert selection.available is False
    assert selection.gap is not None
    assert "INTERACTIVE_REQUIRED" in selection.reason


def test_live_github_identity_when_gh_is_authenticated() -> None:
    from jarvis_foundry.connectors.github_research import GitHubResearchConnector
    try:
        probe = GitHubResearchConnector().identity_probe()
    except Exception as error:
        pytest.skip(f"live GitHub probe unavailable: {error}")
    login = probe.get("authenticated_login")
    if not login:
        pytest.skip("gh is not authenticated for /user")
    assert isinstance(login, str) and login
    remaining = (probe.get("rate_limit") or {}).get("remaining")
    assert remaining is None or isinstance(remaining, int)


def test_dir_list_and_cli_catalog(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    (ctx.owned_root / "alpha.txt").write_text("a", encoding="utf-8")
    listed = execute_action(
        database, ctx, _intent("dir.list"), "dir.list", {"target": "."},
    )
    assert listed.success is True
    assert "alpha.txt" in listed.claimed_effect["names"]

    from jarvis_foundry.cli.main import main
    home = tmp_path / "cli-home"
    home.mkdir()
    code = main(["--home", str(home), "fabric", "catalog"])
    assert code == 0
    refuse = main(["fabric", "catalog"])
    assert refuse == 1


def test_git_inspect_after_commit(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    work, bare = _clone_repo(tmp_path, ctx.owned_root)
    committed = execute_action(
        database, ctx, _intent("git.commit"), "git.commit",
        {"worktree": str(work), "changes": {"seen.txt": "inspect\n"}, "message": "inspect me"},
    )
    assert committed.success is True
    inspected = execute_action(
        database, ctx, _intent("git.inspect"), "git.inspect",
        {"worktree": str(work), "remote": str(bare)},
    )
    assert inspected.success is True
    assert inspected.claimed_effect["head"] == committed.claimed_effect["head"]


def test_tests_and_owned_script_run(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    (ctx.owned_root / "tool.py").write_text("print('script-ok')\n", encoding="utf-8")
    (ctx.owned_root / "test_tool.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8",
    )
    result = run_intent(
        database, ctx, _intent("script.run", "tests.run"),
        {
            "script.run": {"target": "tool.py"},
            "tests.run": {"target": "test_tool.py"},
        },
    )
    assert result["success"] is True
    assert "script-ok" in result["results"][0]["claimed_effect"]["stdout"]


def test_workflow_issue_inspect_branch_commit_test_and_receipt(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(
        tmp_path, database,
        github_api=lambda endpoint: (
            200,
            json.dumps({"number": 7, "title": "fix timeout", "state": "open"}).encode(),
        ),
    )
    seed_builtins(database)
    work, bare = _clone_repo(tmp_path, ctx.owned_root)
    (ctx.owned_root / "test_timeout.py").write_text(
        "def test_timeout_constant():\n    assert True\n", encoding="utf-8",
    )
    result = run_workflow(
        database, ctx, _intent("github.issue.read"),
        (
            WorkflowStep(
                "issue", "github.issue.read",
                {"action": "github.issue.read", "endpoint": "/repos/acme/demo/issues/7"},
            ),
            WorkflowStep(
                "inspect", "git.inspect",
                {"worktree": str(work), "remote": str(bare)},
            ),
            WorkflowStep(
                "branch", "git.branch",
                {"worktree": "$inspect.worktree", "name": "v9-timeout"},
            ),
            WorkflowStep(
                "commit", "git.commit",
                {
                    "worktree": "$branch.worktree",
                    "changes": {"timeout.py": "TIMEOUT = 30\n"},
                    "message": "fix timeout from issue 7",
                },
            ),
            WorkflowStep("diff", "git.diff", {"worktree": "$commit.worktree"}),
            WorkflowStep("tests", "tests.run", {"target": "test_timeout.py"}),
            WorkflowStep(
                "receipt", "file.write",
                {"target": "receipt.txt", "content": "$commit.head"},
            ),
        ),
    )
    assert result["success"] is True
    assert result["stopped_at"] is None
    assert result["context"]["issue"]["document"]["number"] == 7
    assert result["context"]["branch"]["branch"] == "v9-timeout"
    assert result["context"]["diff"]["dirty"] is False
    receipt = (ctx.owned_root / "receipt.txt").read_text(encoding="utf-8")
    assert receipt == result["context"]["commit"]["head"]
    current = subprocess.run(
        ["git", "-C", str(work), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert current == "v9-timeout"
    assert (work / "timeout.py").read_text(encoding="utf-8") == "TIMEOUT = 30\n"


def test_workflow_diagnose_replace_run_and_list(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    result = run_workflow(
        database, ctx, _intent("system.diagnose"),
        (
            WorkflowStep("diag", "system.diagnose", {}),
            WorkflowStep(
                "seed", "file.write",
                {"target": "broken.py", "content": "print('broken')\n"},
            ),
            WorkflowStep(
                "repair", "file.replace",
                {"target": "broken.py", "old": "broken", "new": "repaired"},
            ),
            WorkflowStep("run", "script.run", {"target": "broken.py"}),
            WorkflowStep("list", "dir.list", {"target": "."}),
        ),
    )
    assert result["success"] is True
    assert result["context"]["diag"]["pid"]
    assert "repaired" in result["context"]["run"]["stdout"]
    assert "broken.py" in result["context"]["list"]["names"]
    repaired = (ctx.owned_root / "broken.py").read_text(encoding="utf-8")
    assert "repaired" in repaired
    assert "broken" not in repaired


def test_workflow_continue_after_failed_step(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    result = run_workflow(
        database, ctx, _intent("file.read"),
        (
            WorkflowStep(
                "missing", "file.read", {"target": "absent.txt"}, on_failure="continue",
            ),
            WorkflowStep(
                "write", "file.write", {"target": "present.txt", "content": "ok"},
            ),
        ),
    )
    assert result["success"] is False
    assert result["stopped_at"] is None
    assert result["results"][0]["verified"] == "MISMATCH"
    assert result["results"][1]["claimed"] == "COMPLETED"
    assert (ctx.owned_root / "present.txt").read_text(encoding="utf-8") == "ok"


def test_spine_journals_verified_effect_for_mission_step(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    receipt = execute_for_spine(
        database, ctx,
        mission_id="mission-fabric-1",
        step_id="write-notes",
        action_kind="file.write",
        payload={"target": "notes.txt", "content": "spine"},
    )
    assert receipt["success"] is True
    assert receipt["verified"] == "MATCH"
    assert receipt["mission_id"] == "mission-fabric-1"
    journaled = [
        event for event in database.events()
        if event.event_type == "fabric.spine.result"
    ]
    assert len(journaled) == 1
    assert (ctx.owned_root / "notes.txt").read_text(encoding="utf-8") == "spine"


def test_reliability_prefers_the_capability_with_more_verified_successes(
        tmp_path: Path) -> None:
    database = _db(tmp_path)
    seed_builtins(database)
    register_capability(database, CapabilitySpec(
        capability_id="cap.file.write.peer", version="1.0.0", kind=Kind.CONNECTOR,
        action_kind="file.write", semantic=Semantic.WRITE, executor="filesystem",
        unattended=UnattendedMode.ALLOWED,
    ))
    record_outcome(
        database, "cap.file.write", success=True, verified=VerifyVerdict.MATCH, note="once",
    )
    record_outcome(
        database, "cap.file.write.peer", success=True, verified=VerifyVerdict.MATCH, note="a",
    )
    record_outcome(
        database, "cap.file.write.peer", success=True, verified=VerifyVerdict.MATCH, note="b",
    )
    assert reliability(database, "cap.file.write.peer")["successes"] == 2
    selection = select_capability(database, _intent("file.write"), "file.write")
    assert selection.capability_id == "cap.file.write.peer"


def test_workflow_commit_and_push_to_owned_remote(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    work, bare = _clone_repo(tmp_path, ctx.owned_root)
    result = run_workflow(
        database, ctx, _intent("git.commit"),
        (
            WorkflowStep(
                "inspect", "git.inspect",
                {"worktree": str(work), "remote": str(bare)},
            ),
            WorkflowStep(
                "commit", "git.commit",
                {
                    "worktree": "$inspect.worktree",
                    "changes": {"pushed.txt": "from fabric\n"},
                    "message": "fabric push proof",
                },
            ),
            WorkflowStep(
                "push", "git.push",
                {
                    "worktree": "$commit.worktree",
                    "remote": str(bare),
                    "branch": "main",
                    "action_id": "fabric-push-main",
                },
            ),
        ),
    )
    assert result["success"] is True
    remote_head = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert remote_head == result["context"]["commit"]["head"]
    assert result["context"]["push"]["observed_remote_head"] == remote_head


def test_workflow_branch_push_and_pull_request_create_then_read(
        tmp_path: Path) -> None:
    created = {}

    def write(endpoint: str, request: dict) -> tuple[int, bytes]:
        created["pr"] = {
            "number": 11,
            "title": request["title"],
            "html_url": "https://example.invalid/acme/demo/pull/11",
            "head": {"ref": request["head"]},
            "base": {"ref": request["base"]},
        }
        return 201, json.dumps(created["pr"]).encode()

    def api(endpoint: str) -> tuple[int, bytes]:
        if endpoint == "/repos/acme/demo/pulls/11" and "pr" in created:
            return 200, json.dumps(created["pr"]).encode()
        return 404, b"{}"

    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database, github_api=api, github_write=write)
    seed_builtins(database)
    work, bare = _clone_repo(tmp_path, ctx.owned_root)
    result = run_workflow(
        database, ctx, _intent("git.branch"),
        (
            WorkflowStep(
                "inspect", "git.inspect",
                {"worktree": str(work), "remote": str(bare)},
            ),
            WorkflowStep(
                "branch", "git.branch",
                {"worktree": "$inspect.worktree", "name": "v9-pr"},
            ),
            WorkflowStep(
                "commit", "git.commit",
                {
                    "worktree": "$branch.worktree",
                    "changes": {"feature.txt": "pr body\n"},
                    "message": "open a fabric pull request",
                },
            ),
            WorkflowStep(
                "push", "git.push",
                {
                    "worktree": "$commit.worktree",
                    "remote": str(bare),
                    "branch": "v9-pr",
                    "action_id": "fabric-push-v9-pr",
                },
            ),
            WorkflowStep(
                "open", "github.pull_request.create",
                {
                    "owner": "acme", "repo": "demo",
                    "title": "fabric pull request",
                    "head": "v9-pr", "base": "main",
                    "body": "opened from capability fabric",
                },
            ),
            WorkflowStep(
                "read", "github.pull_request.read",
                {
                    "action": "github.pull_request.read",
                    "endpoint": "/repos/acme/demo/pulls/11",
                },
            ),
        ),
    )
    assert result["success"] is True
    assert result["context"]["open"]["number"] == 11
    assert result["context"]["read"]["document"]["number"] == 11
    assert result["context"]["push"]["branch"] == "v9-pr"
