"""Lane 3: durable developer effects — crash recovery, dedupe, Git remote truth."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from jarvis_foundry.connectors.git import GitConnector
from jarvis_foundry.connectors.github_write import (
    GitHubWriteAdapter,
    github_write_dispatch_settings,
    reconcile_github_write_effect,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import execute_fabric_effect, effect_state, fabric_effect_id
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    execute_action,
    seed_builtins,
)

from v3_fixtures import make_bare_remote


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _ctx(tmp_path: Path, database: FoundryDatabase, **kwargs: Any) -> ExecutorContext:
    owned = tmp_path / "owned"
    owned.mkdir(exist_ok=True)
    return ExecutorContext(database=database, owned_root=owned, **kwargs)


def _intent(*actions: str) -> TaskIntent:
    return TaskIntent(task_id="task.lane3", required_actions=actions)


class FakeIssueProvider:
    """Deterministic provider: POST creates a numbered object; GET reads it."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.comments: list[dict[str, Any]] = []
        self.pulls: list[dict[str, Any]] = []
        self.write_calls = 0
        self.read_calls: list[str] = []

    def write(self, endpoint: str, payload: Mapping[str, Any]) -> tuple[int, bytes]:
        self.write_calls += 1
        if endpoint == "/repos/owner/demo/pulls":
            number = len(self.pulls) + 1
            document = {
                "number": number,
                "title": payload["title"],
                "body": payload.get("body", ""),
                "head": {"ref": payload["head"]},
                "base": {"ref": payload["base"]},
                "html_url": f"https://github.com/owner/demo/pull/{number}",
            }
            self.pulls.append(document)
            return 201, json.dumps(document).encode("utf-8")
        comment_match = re.fullmatch(
            r"/repos/owner/demo/issues/(\d+)/comments", endpoint,
        )
        if comment_match is not None:
            comment_id = 900 + len(self.comments) + 1
            document = {
                "id": comment_id,
                "body": payload["body"],
                "issue_url": (
                    f"https://api.github.com/repos/owner/demo/issues/"
                    f"{comment_match.group(1)}"
                ),
                "html_url": (
                    f"https://github.com/owner/demo/issues/"
                    f"{comment_match.group(1)}#issuecomment-{comment_id}"
                ),
            }
            self.comments.append(document)
            return 201, json.dumps(document).encode("utf-8")
        assert endpoint == "/repos/owner/demo/issues"
        number = len(self.documents) + 1
        document = {
            "number": number,
            "title": payload["title"],
            "body": payload.get("body", ""),
            "html_url": f"https://github.com/owner/demo/issues/{number}",
        }
        self.documents.append(document)
        return 201, json.dumps(document).encode("utf-8")

    def read(self, endpoint: str) -> tuple[int, bytes]:
        self.read_calls.append(endpoint)
        if endpoint == "/repos/owner/demo/pulls?state=all&per_page=100":
            return 200, json.dumps(self.pulls).encode("utf-8")
        pull_item = re.fullmatch(r"/repos/owner/demo/pulls/(\d+)", endpoint)
        if pull_item is not None:
            number = int(pull_item.group(1))
            if 1 <= number <= len(self.pulls):
                return 200, json.dumps(self.pulls[number - 1]).encode("utf-8")
            return 404, b"{}"
        comment_list = re.fullmatch(
            r"/repos/owner/demo/issues/(\d+)/comments\?per_page=100", endpoint,
        )
        if comment_list is not None:
            return 200, json.dumps(self.comments).encode("utf-8")
        comment_item = re.fullmatch(r"/repos/owner/demo/issues/comments/(\d+)", endpoint)
        if comment_item is not None:
            comment_id = int(comment_item.group(1))
            for document in self.comments:
                if document["id"] == comment_id:
                    return 200, json.dumps(document).encode("utf-8")
            return 404, b"{}"
        if "?state=all" in endpoint:
            return 200, json.dumps(self.documents).encode("utf-8")
        match = re.search(r"/issues/(\d+)$", endpoint)
        if match is not None:
            number = int(match.group(1))
            if 1 <= number <= len(self.documents):
                return 200, json.dumps(self.documents[number - 1]).encode("utf-8")
        return 404, b"{}"


def _write_ctx(
    tmp_path: Path, database: FoundryDatabase, provider: FakeIssueProvider,
) -> ExecutorContext:
    return _ctx(
        tmp_path,
        database,
        github_write=provider.write,
        github_api=provider.read,
        github_writes_enabled=True,
        github_authorized_repositories=("owner/demo",),
    )


def _issue_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "owner": "owner",
        "repo": "demo",
        "title": "Lane 3 durable effect",
        "body": "exactly-once body",
    }
    payload.update(overrides)
    return payload


def test_github_issue_duplicate_intent_never_posts_twice(tmp_path: Path) -> None:
    database = _db(tmp_path)
    provider = FakeIssueProvider()
    ctx = _write_ctx(tmp_path, database, provider)
    seed_builtins(database)

    first = execute_action(
        database, ctx, _intent("github.issue.create"),
        "github.issue.create", _issue_payload(),
    )
    assert first.success is True
    assert provider.write_calls == 1

    second = execute_action(
        database, ctx, _intent("github.issue.create"),
        "github.issue.create", _issue_payload(),
    )
    assert second.success is True
    assert second.claimed_effect["replayed"] is True
    assert second.claimed_effect["performed"] is False
    assert provider.write_calls == 1


def test_github_issue_crash_after_post_recovers_from_provider_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _db(tmp_path)
    provider = FakeIssueProvider()
    ctx = _write_ctx(tmp_path, database, provider)
    seed_builtins(database)

    import jarvis_foundry.effects as effects_module

    original_append = effects_module._append

    def crash_at_completion(database: FoundryDatabase, event_type: str,
                            payload: dict[str, Any], key_suffix: str,
                            **kwargs: Any) -> None:
        if event_type == "system.action_completed":
            raise RuntimeError("simulated crash before local completion")
        original_append(database, event_type, payload, key_suffix, **kwargs)

    monkeypatch.setattr("jarvis_foundry.effects._append", crash_at_completion)
    payload = _issue_payload(claim_ttl_s=0.01)
    with pytest.raises(RuntimeError, match="simulated crash"):
        execute_action(
            database, ctx, _intent("github.issue.create"),
            "github.issue.create", payload,
        )
    monkeypatch.setattr("jarvis_foundry.effects._append", original_append)
    assert provider.write_calls == 1

    started = [
        event for event in database.events()
        if event.event_type == "system.action_started"
        and event.payload.get("kind") == "github.issue.create"
    ]
    assert len(started) == 1
    action_key = str(started[0].payload["action_key"])
    assert effect_state(database, action_key)["status"] == "STARTED"

    time.sleep(0.03)
    restarted_ctx = _write_ctx(tmp_path, database, provider)
    recovered = execute_action(
        database, restarted_ctx, _intent("github.write.reconcile"),
        "github.write.reconcile",
        {
            "original_effect_id": action_key,
            "original_action": "github.issue.create",
            "request": _issue_payload(),
        },
    )
    assert recovered.success is True
    assert recovered.claimed_effect["claimed"] == "COMPLETED"
    assert recovered.claimed_effect["accepted"] is True
    assert recovered.claimed_effect["original_effect_id"] == action_key
    assert provider.write_calls == 1

    state = effect_state(database, action_key)
    assert state["accepted"] is True
    assert state["terminal_outcome"] == "COMPLETED"
    assert state["reconciled"] is True
    assert provider.documents and len(provider.documents) == 1

    replay = execute_action(
        database, restarted_ctx, _intent("github.issue.create"),
        "github.issue.create", _issue_payload(),
    )
    assert replay.success is True
    assert replay.claimed_effect["replayed"] is True
    assert provider.write_calls == 1


def test_github_write_reconcile_requires_exact_intent(tmp_path: Path) -> None:
    database = _db(tmp_path)
    provider = FakeIssueProvider()
    ctx = _write_ctx(tmp_path, database, provider)
    seed_builtins(database)
    first = execute_action(
        database, ctx, _intent("github.issue.create"),
        "github.issue.create", _issue_payload(),
    )
    action_key = str(first.claimed_effect["action_key"])
    reads_before = len(provider.read_calls)

    tampered = execute_action(
        database, _write_ctx(tmp_path, database, provider),
        _intent("github.write.reconcile"),
        "github.write.reconcile",
        {
            "original_effect_id": action_key,
            "original_action": "github.issue.create",
            "request": _issue_payload(title="a different title"),
        },
    )
    assert tampered.claimed_effect["claimed"] == "BLOCKED"
    assert tampered.claimed_effect["error_code"] == "request_identity_mismatch"
    assert len(provider.read_calls) == reads_before


def test_github_write_reconcile_finalizes_one_provider_object(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    provider = FakeIssueProvider()
    adapter = GitHubWriteAdapter(
        transport=provider.write,
        read_transport=provider.read,
        dispatch_enabled=True,
        authorized_repositories=("owner/demo",),
    )
    payload = _issue_payload()
    request = _provider_request(database, payload)

    def lost_response() -> dict[str, Any]:
        provider.write(
            "/repos/owner/demo/issues",
            {"title": payload["title"], "body": payload["body"]},
        )
        raise TimeoutError("response lost after provider accepted the issue")

    timed_out = execute_fabric_effect(
        database,
        action_key=request,
        system_id="github",
        target_ref="github.issue.create",
        payload_hash="lane3",
        kind="github.issue.create",
        connector_id="fabric:cap.github.issue.create",
        request_identity={
            "action_kind": "github.issue.create",
            "input_hash": _fabric_hash(payload),
        },
        perform=lost_response,
        claim_ttl_s=0.01,
    )
    assert timed_out["journal_state"] == "FROZEN"
    assert provider.write_calls == 1

    stop = tmp_path / "STOP"
    stop.write_text("owner stop", encoding="utf-8")
    recovered = reconcile_github_write_effect(
        database,
        action_key=request,
        adapter=adapter,
        action_kind="github.issue.create",
        endpoint="/repos/owner/demo/issues",
        request_payload=payload,
    )
    assert recovered["claimed"] == "COMPLETED"
    assert recovered["accepted"] is True
    assert recovered["performed"] is False
    assert recovered["reconciliation_required"] is False
    assert provider.write_calls == 1
    assert effect_state(database, request)["accepted"] is True
    assert effect_state(database, request)["terminal_outcome"] == "COMPLETED"

    replayed = reconcile_github_write_effect(
        database,
        action_key=request,
        adapter=adapter,
        action_kind="github.issue.create",
        endpoint="/repos/owner/demo/issues",
        request_payload=payload,
    )
    assert replayed["replayed"] is True
    assert replayed["accepted"] is True
    assert provider.write_calls == 1


def test_github_pull_request_create_recovers_after_lost_response(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    provider = FakeIssueProvider()
    adapter = GitHubWriteAdapter(
        transport=provider.write,
        read_transport=provider.read,
        dispatch_enabled=True,
        authorized_repositories=("owner/demo",),
    )
    payload = {
        "owner": "owner",
        "repo": "demo",
        "title": "Recovered pull request",
        "head": "jarvis/effect-recovery",
        "base": "main",
        "body": "one provider object",
    }
    endpoint = "/repos/owner/demo/pulls"
    request = fabric_effect_id(
        system_id="github",
        target_ref="github.pull_request.create",
        capability_id="cap.github.pull_request.create",
        capability_version="1.0.0",
        action_kind="github.pull_request.create",
        input_payload=payload,
    )

    def lost_response() -> dict[str, Any]:
        provider.write(endpoint, {
            "title": payload["title"],
            "head": payload["head"],
            "base": payload["base"],
            "body": payload["body"],
        })
        raise TimeoutError("response lost after the provider opened the PR")

    timed_out = execute_fabric_effect(
        database,
        action_key=request,
        system_id="github",
        target_ref="github.pull_request.create",
        payload_hash="lane3",
        kind="github.pull_request.create",
        connector_id="fabric:cap.github.pull_request.create",
        request_identity={
            "action_kind": "github.pull_request.create",
            "input_hash": _fabric_hash(payload),
        },
        perform=lost_response,
        claim_ttl_s=0.01,
    )
    assert timed_out["journal_state"] == "FROZEN"

    recovered = reconcile_github_write_effect(
        database,
        action_key=request,
        adapter=adapter,
        action_kind="github.pull_request.create",
        endpoint=endpoint,
        request_payload=payload,
    )
    assert recovered["claimed"] == "COMPLETED"
    assert recovered["accepted"] is True
    assert recovered["number"] == 1
    assert provider.write_calls == 1
    assert provider.pulls[0]["head"] == {"ref": "jarvis/effect-recovery"}


def _fabric_hash(payload: Mapping[str, Any]) -> str:
    from jarvis_foundry.connectors.github_write import github_write_input_hash

    return github_write_input_hash(payload)


def _provider_request(database: FoundryDatabase, payload: Mapping[str, Any]) -> str:
    return fabric_effect_id(
        system_id="github",
        target_ref="github.issue.create",
        capability_id="cap.github.issue.create",
        capability_version="1.0.0",
        action_kind="github.issue.create",
        input_payload=dict(payload),
    )


def _clone(tmp_path: Path) -> tuple[Path, Path, str]:
    bare = make_bare_remote(tmp_path / "remote.git")
    work = tmp_path / "owned" / "repo"
    work.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "lane3@local"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "lane3"], check=True)
    base = work / "base.txt"
    base.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "base"], check=True)
    head = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return work, bare, head


def test_github_issue_comment_dedupe_readback_and_recovery(tmp_path: Path) -> None:
    database = _db(tmp_path)
    provider = FakeIssueProvider()
    ctx = _write_ctx(tmp_path, database, provider)
    seed_builtins(database)
    payload = {
        "owner": "owner",
        "repo": "demo",
        "number": 7,
        "body": "exact owner text — never rewritten\nsecond line",
    }

    first = execute_action(
        database, ctx, _intent("github.issue.comment.create"),
        "github.issue.comment.create", payload,
    )
    assert first.success is True
    assert first.claimed_effect["claimed"] == "COMPLETED"
    assert first.claimed_effect["reconciled"] is True
    assert first.claimed_effect["readback_status"] == "MATCH"
    assert provider.write_calls == 1
    assert provider.comments[0]["body"] == payload["body"]

    second = execute_action(
        database, ctx, _intent("github.issue.comment.create"),
        "github.issue.comment.create", payload,
    )
    assert second.success is True
    assert second.claimed_effect["replayed"] is True
    assert provider.write_calls == 1

    key = str(first.claimed_effect["action_key"])
    state = effect_state(database, key)
    assert state["status"] == "COMPLETED"
    assert state["after_state"]

    def lost_response() -> dict[str, Any]:
        provider.write(
            "/repos/owner/demo/issues/7/comments",
            {"body": second_payload["body"]},
        )
        raise TimeoutError("response lost after provider accepted the comment")

    second_payload = {**payload, "body": "another comment body"}
    from jarvis_foundry.connectors.github_write import github_write_input_hash

    second_key = fabric_effect_id(
        system_id="github",
        target_ref="github.issue.comment.create",
        capability_id="cap.github.issue.comment.create",
        capability_version="1.0.0",
        action_kind="github.issue.comment.create",
        input_payload=second_payload,
    )
    timed_out = execute_fabric_effect(
        database,
        action_key=second_key,
        system_id="github",
        target_ref="github.issue.comment.create",
        payload_hash="lane3",
        kind="github.issue.comment.create",
        connector_id="fabric:cap.github.issue.comment.create",
        request_identity={
            "action_kind": "github.issue.comment.create",
            "input_hash": github_write_input_hash(second_payload),
        },
        perform=lost_response,
        claim_ttl_s=0.01,
    )
    assert timed_out["journal_state"] == "FROZEN"
    writes_after_crash = provider.write_calls

    recovered = execute_action(
        database, _write_ctx(tmp_path, database, provider),
        _intent("github.write.reconcile"),
        "github.write.reconcile",
        {
            "original_effect_id": second_key,
            "original_action": "github.issue.comment.create",
            "request": second_payload,
        },
    )
    assert recovered.success is True
    assert recovered.claimed_effect["claimed"] == "COMPLETED"
    assert recovered.claimed_effect["accepted"] is True
    assert provider.write_calls == writes_after_crash
    assert len(provider.comments) == 2


def test_github_write_dispatch_settings_default_off_and_env_optin(
    tmp_path: Path,
) -> None:
    default = github_write_dispatch_settings(environ={})
    assert default.writes_enabled is False
    assert default.authorized_repositories == ()
    assert default.repository_authorization is None
    assert default.executor_kwargs()["github_writes_enabled"] is False

    opted_in = github_write_dispatch_settings(environ={
        "JARVIS_GITHUB_WRITES_ENABLED": "true",
        "JARVIS_GITHUB_AUTHORIZED_REPOSITORIES": "owner/demo; owner/other",
    })
    assert opted_in.writes_enabled is True
    assert opted_in.authorized_repositories == ("owner/demo", "owner/other")

    grant = tmp_path / "github-grant.json"
    grant.write_text(json.dumps({
        "authorization_id": "owner-grant:lane3",
        "repository": "owner/demo",
        "remote": "https://github.com/owner/demo.git",
        "allowed_base_branches": ["main"],
        "owner_authorized": True,
        "dispatch_enabled": True,
    }), encoding="utf-8")
    with_grant = github_write_dispatch_settings(environ={
        "JARVIS_GITHUB_WRITES_ENABLED": "yes",
        "JARVIS_GITHUB_AUTHORIZED_REPOSITORIES": "owner/demo",
        "JARVIS_GITHUB_AUTHORIZATION_FILE": str(grant),
    })
    authorization = with_grant.repository_authorization
    assert authorization is not None
    assert authorization.owner_authorized is True
    assert authorization.refusal(
        repository="owner/demo",
        remote="https://github.com/owner/demo.git",
        base="main",
    ) is None

    with pytest.raises(Exception, match="owner/repo"):
        github_write_dispatch_settings(environ={
            "JARVIS_GITHUB_WRITES_ENABLED": "true",
            "JARVIS_GITHUB_AUTHORIZED_REPOSITORIES": "not-a-repository",
        })
    with pytest.raises(Exception, match="must also appear"):
        github_write_dispatch_settings(environ={
            "JARVIS_GITHUB_WRITES_ENABLED": "true",
            "JARVIS_GITHUB_AUTHORIZED_REPOSITORIES": "owner/other",
            "JARVIS_GITHUB_AUTHORIZATION_FILE": str(grant),
        })


def test_collection_scan_truncation_and_duplicates_stay_unknown(
    tmp_path: Path,
) -> None:
    def unused_write(endpoint: str, payload: Mapping[str, Any]) -> tuple[int, bytes]:
        raise AssertionError("reconciliation must never write")

    first_page = [
        {
            "number": number,
            "title": f"other issue {number}",
            "body": "",
            "html_url": f"https://github.com/owner/demo/issues/{number}",
        }
        for number in range(1, 101)
    ]

    def paged_read(_endpoint: str) -> tuple[int, bytes]:
        return 200, json.dumps(first_page).encode("utf-8")

    paged = GitHubWriteAdapter(
        transport=unused_write,
        read_transport=paged_read,
        dispatch_enabled=True,
        authorized_repositories=("owner/demo",),
    )
    truncated = paged.observe_request(
        "github.issue.create", "/repos/owner/demo/issues",
        {"title": "target", "body": ""},
    )
    assert truncated["claimed"] == "UNKNOWN"
    assert truncated["error_code"] == "scan_truncated"
    assert truncated["reconciliation_required"] is True
    assert truncated["scan"]["items"] == 500

    duplicate_document = {
        "number": 5,
        "title": "duplicate target",
        "body": "b",
        "html_url": "https://github.com/owner/demo/issues/5",
    }

    def duplicate_read(_endpoint: str) -> tuple[int, bytes]:
        return 200, json.dumps([duplicate_document, duplicate_document]).encode("utf-8")

    duplicated = GitHubWriteAdapter(
        transport=unused_write,
        read_transport=duplicate_read,
        dispatch_enabled=True,
        authorized_repositories=("owner/demo",),
    )
    ambiguous = duplicated.observe_request(
        "github.issue.create", "/repos/owner/demo/issues",
        {"title": "duplicate target", "body": "b"},
    )
    assert ambiguous["error_code"] == "duplicate_provider_matches"
    assert ambiguous["duplicate_prevented"] is True
    assert ambiguous["reconciliation_required"] is True


def test_git_branch_action_verifies_against_observed_worktree(tmp_path: Path) -> None:
    database = _db(tmp_path)
    work, _bare, _head = _clone(tmp_path)
    ctx = _ctx(tmp_path, database)
    seed_builtins(database)
    result = execute_action(
        database, ctx, _intent("git.branch"), "git.branch",
        {"worktree": str(work), "name": "lane3/topic"},
    )
    assert result.success is True
    assert result.claimed_effect["branch"] == "lane3/topic"


def test_git_push_second_identical_is_already_satisfied_noop(tmp_path: Path) -> None:
    database = _db(tmp_path)
    work, bare, head = _clone(tmp_path)
    connector = GitConnector(database, owned_root=tmp_path / "owned")

    first = connector.push(
        action_id="lane3-push-1", worktree=work, remote=str(bare), branch="main",
    )
    assert first["outcome"] == "COMPLETED"
    assert first["performed"] is True
    assert first["remote_head"] == head

    state = connector.remote_branch_state(str(bare), "main", expected_head=head)
    assert state["exists"] is True
    assert state["head_sha"] == head
    assert state["matches_expected"] is True
    assert state["push_required"] is False

    second = connector.push(
        action_id="lane3-push-2", worktree=work, remote=str(bare), branch="main",
    )
    assert second["outcome"] == "COMPLETED"
    assert second["performed"] is False
    assert second.get("already_satisfied") is True
    assert second["remote_head"] == head
    assert connector.read_ref(str(bare), "main") == head

    missing = connector.remote_branch_state(str(bare), "topic", expected_head=head)
    assert missing["exists"] is False
    assert missing["head_sha"] is None
    assert missing["push_required"] is True


def test_git_push_rejection_reports_truthful_failure(tmp_path: Path) -> None:
    database = _db(tmp_path)
    work, bare, head = _clone(tmp_path)
    connector = GitConnector(database, owned_root=tmp_path / "owned")

    (work / "next.txt").write_text("next\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "next.txt"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "next"], check=True)
    advanced = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "other@local"], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "other"], check=True)
    (other / "divergent.txt").write_text("divergent\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(other), "add", "divergent.txt"], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", "divergent"], check=True)
    subprocess.run(
        ["git", "-C", str(other), "push", "-q", "origin", "HEAD:main"], check=True,
    )

    rejected = connector.push(
        action_id="lane3-push-divergent", worktree=work, remote=str(bare),
        branch="main", expected_head=advanced,
    )
    assert rejected["outcome"] == "FAILED"
    assert connector.read_ref(str(bare), "main") == subprocess.run(
        ["git", "-C", str(other), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
