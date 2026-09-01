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
    FabricRoleAdapter,
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

    def read(endpoint: str) -> tuple[int, bytes]:
        assert endpoint == "/repos/owner/demo/issues/7"
        body = json.dumps({
            "number": 7,
            "html_url": "https://github.com/owner/demo/issues/7",
            "title": "v9",
            "body": "from fabric",
        })
        return 200, body.encode("utf-8")

    ctx = _ctx(tmp_path, database, github_write=write, github_api=read)
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


def test_observed_unknown_provider_is_not_selected() -> None:
    from jarvis_foundry.roles.router import ProviderRouteCandidate

    settings = ProviderSettings(
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="sk-test-not-used-here-000000",
        source="test",
    )
    unknown = route_provider(
        settings=settings, unattended=True, hermes_noninteractive_consent=True,
        observations=(ProviderRouteCandidate(
            provider="opencode_go",
            model="muse-spark-1.2-contributor",
            health="UNKNOWN",
            auth_state="UNKNOWN",
            availability="UNKNOWN",
        ),),
    )
    assert unknown.selected is False
    ready = route_provider(
        settings=settings, unattended=True, hermes_noninteractive_consent=True,
        observations=(ProviderRouteCandidate(
            provider="opencode_go",
            model="muse-spark-1.2-contributor",
            health="READY",
            auth_state="READY",
            availability="READY",
        ),),
    )
    assert ready.selected is True


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
    assert result["results"][0]["claimed"] == "FAILED"
    assert result["results"][0]["verified"] == "MATCH"
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


def test_mission_loop_builder_writes_through_fabric(tmp_path: Path) -> None:
    from jarvis_foundry.mission.loop import PASSED, MissionLoop
    from test_mission_loop import ScriptedAdapter, mission_fixture, step_kwargs

    database, repository, policy, router = mission_fixture(tmp_path)
    inner = ScriptedAdapter(repository, artifact_name="notes.txt")
    adapter = FabricRoleAdapter(database, owned_root=repository, inner=inner)
    outcome = MissionLoop(database, router, adapter, policy=policy).run_step(
        **step_kwargs(
            target=repository,
            expected_artifacts=("notes.txt",),
            instructions="fabric notes from mission",
        ),
    )
    assert outcome.outcome == PASSED
    assert inner.attempts["builder"] == 0
    assert inner.attempts["repair"] == 0
    text = (repository / "notes.txt").read_text(encoding="utf-8")
    assert "fabric notes from mission" in text.replace("\r\n", "\n")
    spine = [
        event for event in database.events()
        if event.event_type == "fabric.spine.result"
    ]
    assert len(spine) == 1
    assert spine[0].payload["success"] is True
    assert spine[0].payload["action_kind"] == "file.write"


def test_mission_loop_commit_uses_fabric_json_payload(tmp_path: Path) -> None:
    from jarvis_foundry.mission.loop import PASSED, MissionLoop
    from jarvis_foundry.roles.adapter import FakeHermesAdapter
    from test_mission_loop import mission_fixture, step_kwargs

    database, repository, policy, router = mission_fixture(tmp_path)
    inner = FakeHermesAdapter(working_root=repository)
    adapter = FabricRoleAdapter(database, owned_root=repository, inner=inner)
    payload = {
        "changes": {"mission.txt": "from mission\n"},
        "message": "fabric mission commit",
    }
    before = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    outcome = MissionLoop(database, router, adapter, policy=policy).run_step(
        **step_kwargs(
            capability="git_local_commit",
            target=repository,
            instructions=json.dumps(payload),
            expected_effects=("create local commit",),
            rollback={"kind": "git_reset_soft"},
        ),
    )
    assert outcome.outcome == PASSED
    head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head != before
    assert "from mission" in (repository / "mission.txt").read_text(encoding="utf-8")
    builders = [item for item in inner.received if item.role == "builder"]
    assert builders == []


def test_unmapped_script_capability_still_uses_inner_adapter(tmp_path: Path) -> None:
    from jarvis_foundry.mission.loop import PASSED, MissionLoop
    from jarvis_foundry.roles.adapter import FakeHermesAdapter
    from test_mission_loop import mission_fixture, step_kwargs

    database, repository, policy, router = mission_fixture(tmp_path)
    inner = FakeHermesAdapter(working_root=repository)
    adapter = FabricRoleAdapter(database, owned_root=repository, inner=inner)
    outcome = MissionLoop(database, router, adapter, policy=policy).run_step(
        **step_kwargs(
            capability="run_registered_local_commands",
            target=repository,
            expected_artifacts=("result.txt",),
        ),
    )
    assert outcome.outcome == PASSED
    assert any(item.role == "builder" for item in inner.received)


def test_git_read_ref_http_read_and_doc_read_compose(tmp_path: Path) -> None:
    from test_http_connector import _Fixture

    database = _db(tmp_path)
    seed_builtins(database)
    ctx = _ctx(tmp_path, database)
    work, bare = _clone_repo(tmp_path, ctx.owned_root)
    committed = execute_action(
        database, ctx, _intent("git.commit"), "git.commit",
        {"worktree": str(work), "changes": {"ref.txt": "head\n"}, "message": "read-ref"},
    )
    assert committed.success is True
    subprocess.run(
        ["git", "-C", str(work), "push", "-q", "origin", "HEAD:main"], check=True,
    )
    ref = execute_action(
        database, ctx, _intent("git.read_ref"), "git.read_ref",
        {"remote": str(bare), "branch": "main"},
    )
    assert ref.success is True
    assert ref.claimed_effect["head"] == committed.claimed_effect["head"]

    fixture = _Fixture()
    try:
        http = execute_action(
            database, ctx, _intent("http.read"), "http.read",
            {"base_url": fixture.url, "path": "/state"},
        )
    finally:
        fixture.close()
    assert http.success is True
    assert http.claimed_effect["document"]["value"] == "initial"

    def fetch_doc(url: str):
        from jarvis_foundry.connectors.github_research import DocumentResponse
        assert url.startswith("https://docs.example/")
        return DocumentResponse(status=200, headers={}, body=b"# official guide\n")

    ctx.doc_transport = fetch_doc
    document = execute_action(
        database, ctx, _intent("http.doc.read"), "http.doc.read",
        {"url": "https://docs.example/guide", "allowed_hosts": ("docs.example",)},
    )
    assert document.success is True
    assert "guide" in (document.claimed_effect.get("text") or "")


def test_cli_fabric_run_workflow(tmp_path: Path) -> None:
    from jarvis_foundry.cli.main import main

    home = tmp_path / "cli-home"
    home.mkdir()
    (home / "workflow.json").write_text(json.dumps({
        "task_id": "cli.workflow",
        "steps": [{
            "step_id": "write",
            "action_kind": "file.write",
            "payload": {"target": "cli.txt", "content": "from-cli"},
        }],
    }), encoding="utf-8")
    code = main(["--home", str(home), "fabric", "run", "--workflow", "workflow.json"])
    assert code == 0
    written = (home / "fabric-work" / "cli.txt").read_text(encoding="utf-8")
    assert "from-cli" in written.replace("\r\n", "\n")


def test_github_commit_check_release_workflow(tmp_path: Path) -> None:
    database = _db(tmp_path)

    def fetch(endpoint: str) -> tuple[int, bytes]:
        if "/commits/" in endpoint:
            return 200, json.dumps({"sha": "abc123", "commit": {"message": "fix"}}).encode()
        if "/actions/runs" in endpoint:
            return 200, json.dumps({
                "total_count": 1,
                "workflow_runs": [{"conclusion": "success"}],
            }).encode()
        if "/releases/" in endpoint:
            return 200, json.dumps({"tag_name": "v1.0.0", "draft": False}).encode()
        return 404, b"{}"

    ctx = _ctx(tmp_path, database, github_api=fetch)
    seed_builtins(database)
    result = run_workflow(
        database, ctx, _intent("github.commit.read"),
        (
            WorkflowStep(
                "commit", "github.commit.read",
                {"endpoint": "/repos/acme/demo/commits/abc123"},
            ),
            WorkflowStep(
                "check", "github.check.read",
                {"endpoint": "/repos/acme/demo/actions/runs"},
            ),
            WorkflowStep(
                "release", "github.release.read",
                {"endpoint": "/repos/acme/demo/releases/latest"},
            ),
            WorkflowStep(
                "note", "file.write",
                {"target": "research/sha.txt", "content": "$commit.document.sha"},
            ),
        ),
    )
    assert result["success"] is True
    assert result["results"][0]["claimed_effect"]["document"]["sha"] == "abc123"
    assert result["results"][1]["claimed_effect"]["document"]["total_count"] == 1
    assert result["results"][2]["claimed_effect"]["document"]["tag_name"] == "v1.0.0"
    text = (ctx.owned_root / "research" / "sha.txt").read_text(encoding="utf-8")
    assert "abc123" in text.replace("\r\n", "\n")


def test_http_write_then_read_and_git_log(tmp_path: Path) -> None:
    from test_http_connector import _Fixture

    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    work, _bare = _clone_repo(tmp_path, ctx.owned_root)
    fixture = _Fixture()
    try:
        result = run_workflow(
            database, ctx, _intent("http.write"),
            (
                WorkflowStep("probe", "http.read", {"base_url": fixture.url, "path": "/state"}),
                WorkflowStep("write", "http.write", {
                    "base_url": fixture.url,
                    "path": "/write",
                    "body": {"value": "from-fabric"},
                    "idempotency_key": "fabric-http-1",
                }),
                WorkflowStep("confirm", "http.read", {"base_url": fixture.url, "path": "/state"}),
            ),
        )
    finally:
        fixture.close()
    assert result["success"] is True
    assert result["results"][0]["claimed_effect"]["document"]["value"] == "initial"
    assert result["results"][1]["claimed_effect"]["observed_state"]["value"] == "from-fabric"
    assert result["results"][1]["claimed_effect"]["observed_state"]["last_key"] == "fabric-http-1"
    assert result["results"][2]["claimed_effect"]["document"]["value"] == "from-fabric"

    committed = execute_action(
        database, ctx, _intent("git.commit"), "git.commit",
        {"worktree": str(work), "changes": {"log.txt": "logged\n"}, "message": "log me"},
    )
    assert committed.success is True
    logged = execute_action(
        database, ctx, _intent("git.log"), "git.log",
        {"worktree": str(work), "max_count": 5},
    )
    assert logged.success is True
    assert logged.claimed_effect["head"] == committed.claimed_effect["head"]
    assert logged.claimed_effect["count"] >= 2


def test_system_runtime_reports_source_identity(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    result = execute_action(
        database, ctx, _intent("system.runtime"), "system.runtime", {},
    )
    assert result.success is True
    assert result.claimed_effect["source_kind"] in {"git_checkout", "installed_package"}
    assert result.claimed_effect["python"]
    assert "runtime_status" in result.claimed_effect


def test_mission_read_registered_files_via_fabric(tmp_path: Path) -> None:
    from jarvis_foundry.control.capabilities import set_capability_state
    from jarvis_foundry.mission.loop import PASSED, MissionLoop
    from jarvis_foundry.roles.adapter import FakeHermesAdapter
    from test_mission_loop import mission_fixture, step_kwargs

    database, repository, policy, router = mission_fixture(tmp_path)
    (repository / "result.txt").write_text("hello-from-disk\n", encoding="utf-8")
    set_capability_state(
        database, "resource-1", "read_registered_files", "READY",
        evidence={"source": "v9-fabric"},
    )
    inner = FakeHermesAdapter(working_root=repository)
    adapter = FabricRoleAdapter(database, owned_root=repository, inner=inner)
    outcome = MissionLoop(database, router, adapter, policy=policy).run_step(
        **step_kwargs(
            capability="read_registered_files",
            target=repository / "result.txt",
            expected_artifacts=("result.txt",),
            expected_effects=("read result.txt",),
        ),
    )
    assert outcome.outcome == PASSED
    builders = [item for item in inner.received if item.role == "builder"]
    assert builders == []


def test_fabric_adapter_uses_registered_resource_not_constructor_root(tmp_path: Path) -> None:
    from jarvis_foundry.mission.loop import PASSED, MissionLoop
    from test_mission_loop import ScriptedAdapter, mission_fixture, step_kwargs

    database, repository, policy, router = mission_fixture(tmp_path)
    decoy = tmp_path / "decoy-owned"
    decoy.mkdir()
    inner = ScriptedAdapter(repository, artifact_name="notes.txt")
    adapter = FabricRoleAdapter(database, owned_root=decoy, inner=inner)
    outcome = MissionLoop(database, router, adapter, policy=policy).run_step(
        **step_kwargs(
            target=repository,
            expected_artifacts=("notes.txt",),
            instructions="from registered resource",
        ),
    )
    assert outcome.outcome == PASSED
    assert (repository / "notes.txt").is_file()
    assert not (decoy / "notes.txt").exists()


def test_worker_stack_structured_write_uses_fabric_without_hermes(tmp_path: Path) -> None:
    from jarvis_foundry.paths import FoundryPaths
    from jarvis_foundry.roles.manifest import ContextManifest
    from jarvis_foundry.roles.results import BLOCKED, COMPLETED
    from jarvis_foundry.runtime._stack import build_worker_stack
    from jarvis_foundry.runtime.config import WorkerRuntimeConfig
    from test_mission_loop import mission_fixture

    database, repository, _policy, _router = mission_fixture(tmp_path)
    home = tmp_path / "foundry-home"
    home.mkdir()
    cfg = WorkerRuntimeConfig(enabled=True, runtime_state_root=str(tmp_path / "state"))
    stack = build_worker_stack(database, FoundryPaths(home), cfg, run_id="fabric")
    assert stack.bridge.use_fabric is True
    written = stack.bridge.dispatch(
        ContextManifest(
            run_id="run-fabric-stack", role="builder",
            mission_id="mission-1", step_id="step-1",
            resource_id="resource-1",
            capability="write_registered_worktree_files",
            provider="opencode_go", model="local",
            isolation_group="g",
            instructions=json.dumps({"target": "from-stack.txt", "content": "via fabric\n"}),
            expected_artifacts=("from-stack.txt",),
        ),
        heartbeat=lambda: None,
    )
    assert written.status == COMPLETED
    assert written.provider == "fabric"
    text = (repository / "from-stack.txt").read_text(encoding="utf-8")
    assert "via fabric" in text.replace("\r\n", "\n")

    unstructured = stack.bridge.dispatch(
        ContextManifest(
            run_id="run-fabric-unstructured", role="builder",
            mission_id="mission-1", step_id="step-2",
            resource_id="resource-1",
            capability="write_registered_worktree_files",
            provider="opencode_go", model="local",
            isolation_group="g",
            instructions="please implement the feature in prose",
            expected_artifacts=("feature.py",),
        ),
        heartbeat=lambda: None,
    )
    assert unstructured.status == BLOCKED
    assert unstructured.error_class == "no_eligible_worker"
    assert not (repository / "feature.py").exists()


def test_lab_stack_does_not_enable_fabric(tmp_path: Path) -> None:
    from jarvis_foundry.paths import FoundryPaths
    from jarvis_foundry.runtime._stack import build_worker_stack
    from jarvis_foundry.runtime.config import WorkerRuntimeConfig

    database = _db(tmp_path)
    home = tmp_path / "foundry-home"
    home.mkdir()
    cfg = WorkerRuntimeConfig(
        enabled=True,
        lab_scripted_hermes=True,
        runtime_state_root=str(tmp_path / "state"),
    )
    stack = build_worker_stack(database, FoundryPaths(home), cfg, run_id="lab")
    assert stack.bridge.use_fabric is False


def test_file_append_and_failed_script_repair(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    written = execute_action(
        database, ctx, _intent("file.write"), "file.write",
        {"target": "log.txt", "content": "start"},
    )
    assert written.success is True
    appended = execute_action(
        database, ctx, _intent("file.append"), "file.append",
        {"target": "log.txt", "content": "\nmore"},
    )
    assert appended.success is True
    text = (ctx.owned_root / "log.txt").read_text(encoding="utf-8")
    assert "start" in text.replace("\r\n", "\n")
    assert "more" in text.replace("\r\n", "\n")

    execute_action(
        database, ctx, _intent("file.write"), "file.write",
        {"target": "tool.py", "content": "raise SystemExit(2)\n"},
    )
    failed = execute_action(
        database, ctx, _intent("script.run"), "script.run",
        {"target": "tool.py"},
    )
    assert failed.success is False
    repaired = execute_action(
        database, ctx, _intent("file.replace"), "file.replace",
        {"target": "tool.py", "old": "raise SystemExit(2)", "new": "print('repaired')"},
    )
    assert repaired.success is True
    # A corrected script is a new logical attempt.  The before-state
    # reference changes its canonical effect identity, so a prior FAILED
    # attempt is never blindly re-executed.
    rerun = execute_action(
        database, ctx, _intent("script.run"), "script.run",
        {"target": "tool.py", "before_state_ref": "after-repair"},
    )
    assert rerun.success is True
    assert "repaired" in (rerun.claimed_effect.get("stdout") or "")


def test_mission_spine_mixed_fabric_hermes_degrade_and_recovery(tmp_path: Path) -> None:
    """Canonical MissionLoop: fabric for structured effects, Hermes for
    general work, degrade falls back, recovery returns to fabric, and a
    failed fabric effect does not also run the worker."""
    from jarvis_foundry.control.capabilities import set_capability_state
    from jarvis_foundry.events import Event
    from jarvis_foundry.mission.loop import PASSED, MissionLoop
    from jarvis_foundry.roles.manifest import ContextManifest
    from jarvis_foundry.roles.results import FAILED
    from jarvis_foundry.workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
    from jarvis_foundry.workers.adapters.registry import WorkerRegistry
    from jarvis_foundry.workers.contracts import WORKER_HERMES, WORKER_OPENHANDS
    from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
    from jarvis_foundry.workers.router import READY, WorkerRouter
    from test_mission_loop import mission_fixture, step_kwargs

    class ArtifactWriter(ScriptedWorkerAdapter):
        def dispatch(self, contract, *, heartbeat):
            names = tuple(contract.expected_artifacts) or ("hermes-reason.txt",)
            for rel in names:
                path = contract.working_root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"hermes:{rel}\n", encoding="utf-8")
            self.outcome = ScriptedOutcome(files_changed=names)
            return super().dispatch(contract, heartbeat=heartbeat)

    database, repository, policy, role_router = mission_fixture(tmp_path)
    set_capability_state(
        database, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "v9-fabric"},
    )
    set_capability_state(
        database, "resource-1", "run_registered_local_commands", "READY",
        evidence={"source": "v9-fabric"},
    )
    worker_router = WorkerRouter()
    worker_router.mark_ready(WORKER_HERMES, state=READY, note="v9", evidence=("test",))
    worker_router.mark_ready(WORKER_OPENHANDS, state=READY, note="v9", evidence=("test",))
    hermes = ArtifactWriter(WORKER_HERMES)
    verifier = ScriptedWorkerAdapter(
        WORKER_OPENHANDS, outcome=ScriptedOutcome(verdict="PASS"))
    adapter = HybridWorkerRoleAdapter(
        database=database,
        router=worker_router,
        registry=WorkerRegistry({WORKER_HERMES: hermes, WORKER_OPENHANDS: verifier}),
        use_fabric=True,
        role_signals={
            "builder": {"ambiguous": True, "root_cause_required": True},
            "repair": {"ambiguous": True, "root_cause_required": True},
            "verifier": {
                "bounded": True, "explicit_acceptance": True,
                "tests_available": True, "narrow_scope": True,
            },
        },
    )
    loop = MissionLoop(database, role_router, adapter, policy=policy)

    def schedule(next_action: str, key: str) -> None:
        database.append(Event.create(
            "mission.directive_interpreted",
            {
                "mission_id": "mission-1",
                "command_type": "REDIRECT",
                "next_action": next_action,
            },
            source_type="owner",
            source_id="v9-fabric",
            idempotency_key=key,
        ))

    fabric_step = loop.run_step(
        **step_kwargs(
            target=repository,
            step_id="step-fabric",
            instructions=json.dumps({"target": "fabric.txt", "content": "from fabric\n"}),
            expected_artifacts=("fabric.txt",),
            expected_effects=("create fabric.txt",),
        ),
    )
    assert fabric_step.outcome == PASSED
    assert "from fabric" in (repository / "fabric.txt").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert [item.role for item in hermes.received] == []
    spine = [event for event in database.events() if event.event_type == "fabric.spine.result"]
    assert len(spine) == 1
    assert spine[0].payload["action_kind"] == "file.write"

    schedule("reason about the next change", "mission.state_changed:mission-1:ACTION_SELECTION:hermes")
    hermes_step = loop.run_step(
        **step_kwargs(
            target=repository,
            step_id="step-hermes",
            capability="run_registered_local_commands",
            instructions="reason about the next change",
            expected_artifacts=("hermes-reason.txt",),
            expected_effects=("create hermes-reason.txt",),
        ),
    )
    assert hermes_step.outcome == PASSED
    assert any(item.role == "builder" for item in hermes.received)
    assert (repository / "hermes-reason.txt").is_file()
    builders_after_hermes = len([item for item in hermes.received if item.role == "builder"])

    seed_builtins(database)
    record_outcome(
        database, "cap.file.write",
        success=False, verified=VerifyVerdict.MISMATCH, note="mission degrade",
    )
    assert observed_readiness(database, "cap.file.write").state is Readiness.DEGRADED
    schedule("create fallback.txt", "mission.state_changed:mission-1:ACTION_SELECTION:fallback")
    fallback = loop.run_step(
        **step_kwargs(
            target=repository,
            step_id="step-fallback",
            instructions=json.dumps({"target": "fallback.txt", "content": "unused\n"}),
            expected_artifacts=("fallback.txt",),
            expected_effects=("create fallback.txt",),
        ),
    )
    assert fallback.outcome == PASSED
    assert (repository / "fallback.txt").is_file()
    assert "hermes:fallback.txt" in (repository / "fallback.txt").read_text(encoding="utf-8")
    builders_after_fallback = len([item for item in hermes.received if item.role == "builder"])
    assert builders_after_fallback == builders_after_hermes + 1
    assert len([event for event in database.events() if event.event_type == "fabric.spine.result"]) == 1

    record_outcome(
        database, "cap.file.write",
        success=True, verified=VerifyVerdict.MATCH, note="mission recover",
    )
    assert observed_readiness(database, "cap.file.write").state is Readiness.READY
    schedule("create recovered.txt", "mission.state_changed:mission-1:ACTION_SELECTION:recovered")
    recovered = loop.run_step(
        **step_kwargs(
            target=repository,
            step_id="step-recovered",
            instructions=json.dumps({"target": "recovered.txt", "content": "fabric again\n"}),
            expected_artifacts=("recovered.txt",),
            expected_effects=("create recovered.txt",),
        ),
    )
    assert recovered.outcome == PASSED
    assert "fabric again" in (repository / "recovered.txt").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert len([item for item in hermes.received if item.role == "builder"]) == builders_after_fallback
    assert len([event for event in database.events() if event.event_type == "fabric.spine.result"]) == 2

    failed = adapter.dispatch(
        ContextManifest(
            run_id="run-fabric-fail", role="builder",
            mission_id="mission-1", step_id="step-fail",
            resource_id="resource-1",
            capability="run_registered_local_commands",
            provider="opencode_go", model="local",
            isolation_group="g",
            instructions=json.dumps({"target": "missing-script.py"}),
        ),
        heartbeat=lambda: None,
    )
    assert failed.status == FAILED
    assert failed.provider == "fabric"
    assert len([item for item in hermes.received if item.role == "builder"]) == builders_after_fallback
