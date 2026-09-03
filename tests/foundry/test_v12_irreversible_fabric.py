"""V12 irreversible drivers integrated through the canonical Fabric path."""
from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Mapping

import pytest

from jarvis_foundry.connectors.account_activation import CredentialHandle, ProviderIdentity
from jarvis_foundry.connectors.gmail_api import (
    GmailApiProvider,
    GmailResponse,
    GmailSendAuthorization,
)
from jarvis_foundry.connectors.github_write import (
    GitHubRepositoryAuthorization,
    GitHubWriteAdapter,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    CapabilityMetadata,
    CapabilityTrust,
    ExecutorContext,
    TaskIntent,
    capability_metadata,
    execute_action,
    get_spec,
    register_capability,
    seed_builtins,
)

from v3_fixtures import make_bare_remote


OWNER_EMAIL = "owner@example.com"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    return database


def _git_fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    owned = tmp_path / "owned"
    owned.mkdir()
    remote = make_bare_remote(tmp_path / "remote.git")
    worktree = owned / "repo"
    subprocess.run(["git", "clone", "-q", str(remote), str(worktree)], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.email", "v12@local"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "V12"], check=True)
    (worktree / "change.txt").write_text("fabric effect\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "change.txt"], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-qm", "fabric fixture"], check=True)
    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return owned, worktree, remote, head


def test_github_publish_fabric_path_returns_verified_receipt_and_replays(tmp_path: Path) -> None:
    database = _db(tmp_path)
    owned, worktree, remote, head = _git_fixture(tmp_path)
    documents: list[dict[str, Any]] = []
    write_calls = 0

    def write(_endpoint: str, request: Mapping[str, Any]) -> tuple[int, bytes]:
        nonlocal write_calls
        write_calls += 1
        document = {
            "number": 71,
            "title": request["title"],
            "body": request["body"],
            "head": {"ref": request["head"]},
            "base": {"ref": request["base"]},
            "html_url": "https://github.com/owner/demo/pull/71",
        }
        documents.append(document)
        return 201, json.dumps(document).encode("utf-8")

    def read(endpoint: str) -> tuple[int, bytes]:
        if "?state=all" in endpoint:
            return 200, json.dumps(documents).encode("utf-8")
        return 200, json.dumps(documents[-1]).encode("utf-8")

    adapter = GitHubWriteAdapter(
        transport=write,
        read_transport=read,
        dispatch_enabled=True,
        authorized_repositories=("owner/demo",),
    )
    context = ExecutorContext(
        database=database,
        owned_root=owned,
        github_write_adapter=adapter,
        github_writes_enabled=True,
        github_authorized_repositories=("owner/demo",),
        github_repository_authorization=GitHubRepositoryAuthorization(
            authorization_id="owner-grant:fabric-github",
            repository="owner/demo",
            remote=str(remote),
            owner_authorized=True,
            dispatch_enabled=True,
        ),
    )
    intent = TaskIntent(
        task_id="mission-fabric-github",
        required_actions=("github.pull_request.publish",),
        unattended=False,
    )
    payload = {
        "worktree": str(worktree),
        "remote": str(remote),
        "owner": "owner",
        "repo": "demo",
        "base": "main",
        "head_sha": head,
        "title": "Fabric V12 PR",
        "body": "fixture only",
    }
    first = execute_action(
        database, context, intent, "github.pull_request.publish", payload,
    )
    replay = execute_action(
        database, context, intent, "github.pull_request.publish", payload,
    )
    assert first.success is True
    assert first.verified.value == "MATCH"
    assert first.receipt["effect_class"] == "IRREVERSIBLE_OR_HIGH_COST"
    assert first.receipt["state"] == "VERIFIED"
    assert first.claimed_effect["provider_ref"] == "https://github.com/owner/demo/pull/71"
    assert replay.success is True
    assert replay.claimed_effect["replayed"] is True
    assert replay.claimed_effect["performed"] is False
    assert write_calls == 1


class FabricGmail:
    def __init__(self) -> None:
        self.send_calls = 0
        self.message: dict[str, Any] | None = None

    def __call__(
        self, method: str, url: str, _headers: Mapping[str, str], body: bytes | None,
    ) -> GmailResponse:
        if method == "POST":
            self.send_calls += 1
            assert body is not None
            raw = json.loads(body)["raw"]
            parsed = BytesParser(policy=policy.default).parsebytes(
                base64.urlsafe_b64decode(raw),
            )
            self.message = {
                "id": "fabric-message-1",
                "threadId": "fabric-thread-1",
                "payload": {"headers": [
                    {"name": name, "value": parsed[name]}
                    for name in (
                        "To", "From", "Subject", "Message-ID",
                        "X-JARVIS-Effect-Identity",
                    )
                ]},
            }
            return GmailResponse(200, {}, b'{"id":"fabric-message-1","threadId":"fabric-thread-1"}')
        if "/users/me/messages?" in url:
            return GmailResponse(
                200, {}, b'{"messages":[{"id":"fabric-message-1","threadId":"fabric-thread-1"}]}',
            )
        assert self.message is not None
        return GmailResponse(200, {}, json.dumps(self.message).encode("utf-8"))


def test_gmail_send_fabric_path_is_verified_and_replay_safe(tmp_path: Path) -> None:
    database = _db(tmp_path)
    transport = FabricGmail()
    provider = GmailApiProvider(
        OWNER_EMAIL,
        token_resolver=lambda _credential: "fixture-token",
        transport=transport,
        send_enabled=True,
        authorized_recipients=(OWNER_EMAIL,),
    )
    credential = CredentialHandle(
        reference="cred:gmail:fabric-v12",
        provider="gmail",
        account_identity=OWNER_EMAIL,
        auth_type="oauth2",
        observed_scopes=(SEND_SCOPE,),
        state="READY",
        version=1,
    )
    identity = ProviderIdentity(
        provider="gmail",
        account_identity=OWNER_EMAIL,
        provider_account_id=OWNER_EMAIL,
        observed_scopes=(SEND_SCOPE,),
    )
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        gmail_api_provider=provider,
        gmail_credential=credential,
        gmail_identity=identity,
        gmail_send_authorization=GmailSendAuthorization(
            authorization_id="owner-grant:fabric-gmail",
            account_identity=OWNER_EMAIL,
            recipient=OWNER_EMAIL,
            owner_authorized=True,
            dispatch_enabled=True,
        ),
    )
    intent = TaskIntent(
        task_id="mission-fabric-gmail",
        required_actions=("gmail.send",),
        unattended=False,
    )
    payload = {
        "recipient": OWNER_EMAIL,
        "subject": "Fabric V12 message",
        "body_text": "fixture only",
    }
    first = execute_action(database, context, intent, "gmail.send", payload)
    replay = execute_action(database, context, intent, "gmail.send", payload)
    assert first.success is True
    assert first.verified.value == "MATCH"
    assert first.receipt["effect_class"] == "IRREVERSIBLE_OR_HIGH_COST"
    assert first.receipt["state"] == "VERIFIED"
    assert first.claimed_effect["provider_ref"] == "gmail:message:fabric-message-1"
    assert replay.success is True
    assert replay.claimed_effect["replayed"] is True
    assert replay.claimed_effect["performed"] is False
    assert transport.send_calls == 1


@pytest.mark.parametrize(
    "capability_id,action_kind",
    [
        ("cap.github.pull_request.publish", "github.pull_request.publish"),
        ("cap.gmail.send", "gmail.send"),
    ],
)
def test_probation_capability_is_blocked_before_executor_or_effect_claim(
    tmp_path: Path, capability_id: str, action_kind: str,
) -> None:
    database = _db(tmp_path)
    spec = get_spec(database, capability_id)
    assert spec is not None
    metadata = capability_metadata(database, capability_id)
    assert isinstance(metadata, CapabilityMetadata)
    register_capability(
        database,
        spec,
        note="self-expanded capability remains probationary",
        metadata=replace(metadata, trust_state=CapabilityTrust.PROBATION),
    )
    before = len([
        event for event in database.events()
        if event.event_type == "system.action_started"
    ])
    result = execute_action(
        database,
        ExecutorContext(database=database, owned_root=tmp_path / "owned"),
        TaskIntent(
            task_id=f"mission-probation-{capability_id.split('.')[-1]}",
            required_actions=(action_kind,),
            unattended=False,
        ),
        action_kind,
        {},
    )
    after = len([
        event for event in database.events()
        if event.event_type == "system.action_started"
    ])
    assert result.claimed.value == "BLOCKED"
    assert result.verified.value == "UNKNOWN"
    assert "PROBATION" in result.note
    assert after == before

