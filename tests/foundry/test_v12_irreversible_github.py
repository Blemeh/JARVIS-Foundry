"""V12 first irreversible GitHub effect pack (fixture/local proof only)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from jarvis_foundry.connectors.github_write import (
    GitHubPullRequestEffectDriver,
    GitHubPullRequestPlan,
    GitHubRepositoryAuthorization,
    GitHubWriteAdapter,
    GitHubWriteTransportError,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import effect_state

from v3_fixtures import make_bare_remote


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _repo(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    owned = tmp_path / "owned"
    owned.mkdir()
    bare = make_bare_remote(tmp_path / "provider.git")
    worktree = owned / "repo"
    subprocess.run(["git", "clone", "-q", str(bare), str(worktree)], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "v12@local"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.name", "V12 Fixture"],
        check=True,
    )
    (worktree / "effect.txt").write_text("irreversible fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "effect.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-q", "-m", "fixture change"],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return owned, worktree, bare, head


def _authorization(
    remote: Path,
    *,
    enabled: bool = True,
    owner_authorized: bool = True,
    trust: str = "ESTABLISHED",
) -> GitHubRepositoryAuthorization:
    return GitHubRepositoryAuthorization(
        authorization_id="owner-grant:v12-github-fixture",
        repository="owner/demo",
        remote=str(remote),
        allowed_base_branches=("main",),
        owner_authorized=owner_authorized,
        dispatch_enabled=enabled,
        capability_trust=trust,
    )


class FakeGitHub:
    def __init__(self, *, ambiguous_create: bool = False) -> None:
        self.ambiguous_create = ambiguous_create
        self.write_calls = 0
        self.read_calls: list[str] = []
        self.documents: list[dict[str, Any]] = []

    def write(
        self, endpoint: str, payload: Mapping[str, Any],
    ) -> tuple[int, bytes]:
        self.write_calls += 1
        document = {
            "number": 41,
            "title": payload["title"],
            "body": payload.get("body", ""),
            "head": {"ref": payload["head"]},
            "base": {"ref": payload["base"]},
            "html_url": "https://github.com/owner/demo/pull/41",
        }
        self.documents.append(document)
        if self.ambiguous_create:
            raise GitHubWriteTransportError(
                "timeout", "fixture timeout after provider accepted PR", ambiguous=True,
            )
        return 201, json.dumps(document).encode("utf-8")

    def read(self, endpoint: str) -> tuple[int, bytes]:
        self.read_calls.append(endpoint)
        if "?state=all" in endpoint:
            return 200, json.dumps(self.documents).encode("utf-8")
        if endpoint.endswith("/41") and self.documents:
            return 200, json.dumps(self.documents[-1]).encode("utf-8")
        return 404, b'{}'


def _adapter(provider: FakeGitHub, *, dispatch_enabled: bool = True) -> GitHubWriteAdapter:
    return GitHubWriteAdapter(
        transport=provider.write,
        read_transport=provider.read,
        dispatch_enabled=dispatch_enabled,
        authorized_repositories=("owner/demo",),
    )


def _publish(
    driver: GitHubPullRequestEffectDriver,
    authorization: GitHubRepositoryAuthorization,
    worktree: Path,
    remote: Path,
    head: str,
    *,
    stop_path: Path | None = None,
) -> dict[str, Any]:
    return driver.publish(
        authorization=authorization,
        worktree=worktree,
        owner="owner",
        repo="demo",
        remote=str(remote),
        base="main",
        head_sha=head,
        title="V12 deterministic effect",
        body="fixture-only pull request",
        mission_id="mission-v12-github",
        stop_path=str(stop_path) if stop_path is not None else None,
    )


def test_github_provider_dispatch_is_disabled_by_default() -> None:
    calls = 0

    def transport(_endpoint: str, _payload: Mapping[str, Any]) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        return 201, b'{}'

    adapter = GitHubWriteAdapter(
        transport=transport,
        authorized_repositories=("owner/demo",),
    )
    result = adapter.write(
        "github.pull_request.create",
        "/repos/owner/demo/pulls",
        {"title": "blocked", "head": "topic", "base": "main", "body": ""},
    )
    assert result["claimed"] == "BLOCKED"
    assert result["error_code"] == "github_writes_disabled"
    assert calls == 0


def test_policy_and_probation_refuse_before_any_journal_claim(tmp_path: Path) -> None:
    database = _db(tmp_path)
    owned, worktree, remote, head = _repo(tmp_path)
    provider = FakeGitHub()
    driver = GitHubPullRequestEffectDriver(
        database, owned_root=owned, adapter=_adapter(provider),
    )

    disabled = _publish(
        driver, _authorization(remote, enabled=False), worktree, remote, head,
    )
    probation = _publish(
        driver, _authorization(remote, trust="PROBATION"), worktree, remote, head,
    )
    assert disabled["error_code"] == "github_writes_disabled"
    assert probation["error_code"] == "probation_capability_irreversible_effect_forbidden"
    assert disabled["external_effects"] == probation["external_effects"] == 0
    assert provider.write_calls == 0
    assert not [
        event for event in database.events()
        if event.event_type == "system.action_started"
    ]


def test_branch_push_pr_readback_acceptance_and_restart_replay(tmp_path: Path) -> None:
    database = _db(tmp_path)
    owned, worktree, remote, head = _repo(tmp_path)
    provider = FakeGitHub()
    pushes = 0

    def push(work: Path, target: str, branch: str, expected: str) -> None:
        nonlocal pushes
        pushes += 1
        subprocess.run(
            ["git", "-C", str(work), "push", target, f"{expected}:refs/heads/{branch}"],
            check=True,
            capture_output=True,
        )

    driver = GitHubPullRequestEffectDriver(
        database,
        owned_root=owned,
        adapter=_adapter(provider),
        push_transport=push,
    )
    first = _publish(driver, _authorization(remote), worktree, remote, head)
    assert first["claimed"] == "COMPLETED"
    assert first["stage"] == "complete"
    assert first["number"] == 41
    assert first["provider_ref"] == "https://github.com/owner/demo/pull/41"
    assert first["readback_status"] == "MATCH"
    assert first["accepted"] is True
    assert first["terminal_truth"] is True
    assert first["external_effects"] == 2
    assert pushes == provider.write_calls == 1

    plan = GitHubPullRequestPlan.build(
        owner="owner", repo="demo", remote=str(remote), base="main",
        head_sha=head, title="V12 deterministic effect",
        body="fixture-only pull request",
    )
    assert effect_state(database, plan.push_effect_id)["accepted"] is True
    assert effect_state(database, plan.pull_request_effect_id)["accepted"] is True

    restarted = GitHubPullRequestEffectDriver(
        database,
        owned_root=owned,
        adapter=_adapter(provider, dispatch_enabled=False),
        push_transport=push,
    )
    replay = _publish(restarted, _authorization(remote), worktree, remote, head)
    assert replay["claimed"] == "COMPLETED"
    assert replay["replayed"] is True
    assert replay["performed"] is False
    assert pushes == provider.write_calls == 1


def test_stop_after_ambiguous_push_reconciles_without_pr_or_repush(tmp_path: Path) -> None:
    database = _db(tmp_path)
    owned, worktree, remote, head = _repo(tmp_path)
    provider = FakeGitHub()
    pushes = 0

    def land_then_timeout(work: Path, target: str, branch: str, expected: str) -> None:
        nonlocal pushes
        pushes += 1
        subprocess.run(
            ["git", "-C", str(work), "push", target, f"{expected}:refs/heads/{branch}"],
            check=True,
            capture_output=True,
        )
        raise TimeoutError("fixture timeout after remote accepted branch")

    driver = GitHubPullRequestEffectDriver(
        database,
        owned_root=owned,
        adapter=_adapter(provider),
        push_transport=land_then_timeout,
    )
    first = _publish(driver, _authorization(remote), worktree, remote, head)
    assert first["claimed"] == "UNKNOWN"
    assert first["stage"] == "push"
    assert first["journal_state"] == "FROZEN"

    stop = tmp_path / "STOP"
    stop.write_text("stop", encoding="utf-8")
    second = _publish(
        driver, _authorization(remote), worktree, remote, head, stop_path=stop,
    )
    assert second["claimed"] == "BLOCKED"
    assert second["stage"] == "before_pull_request"
    assert second["external_effects"] == 1
    assert second["push_effect"]["accepted"] is True
    assert second["push_effect"]["reconciled"] is True
    assert pushes == 1
    assert provider.write_calls == 0


def test_stop_after_ambiguous_pr_reconciles_and_never_blind_retries(tmp_path: Path) -> None:
    database = _db(tmp_path)
    owned, worktree, remote, head = _repo(tmp_path)
    provider = FakeGitHub(ambiguous_create=True)
    driver = GitHubPullRequestEffectDriver(
        database, owned_root=owned, adapter=_adapter(provider),
    )
    first = _publish(driver, _authorization(remote), worktree, remote, head)
    assert first["claimed"] == "UNKNOWN"
    assert first["stage"] == "pull_request"
    assert first["journal_state"] == "FROZEN"
    assert provider.write_calls == 1

    stop = tmp_path / "STOP"
    stop.write_text("stop", encoding="utf-8")
    restarted = GitHubPullRequestEffectDriver(
        database,
        owned_root=owned,
        adapter=_adapter(provider, dispatch_enabled=False),
    )
    reconciled = _publish(
        restarted,
        _authorization(remote),
        worktree,
        remote,
        head,
        stop_path=stop,
    )
    assert reconciled["claimed"] == "COMPLETED"
    assert reconciled["stage"] == "pull_request_reconciliation_under_stop"
    assert reconciled["reconciled"] is True
    assert reconciled["performed"] is False
    assert reconciled["number"] == 41
    assert provider.write_calls == 1

