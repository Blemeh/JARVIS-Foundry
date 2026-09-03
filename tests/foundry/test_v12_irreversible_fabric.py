"""V12 irreversible drivers integrated through the canonical Fabric path."""
from __future__ import annotations

import base64
import json
import subprocess
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Mapping

import pytest

from jarvis_foundry.connectors.account_activation import CredentialHandle, ProviderIdentity
from jarvis_foundry.connectors.gmail_api import (
    GmailApiError,
    GmailApiProvider,
    GmailResponse,
    GmailSendAuthorization,
)
from jarvis_foundry.connectors.github_write import (
    GitHubRepositoryAuthorization,
    GitHubWriteAdapter,
    GitHubWriteTransportError,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import effect_state
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
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

TERMINAL_EFFECT_SEQUENCE = [
    "system.action_started",
    "system.action_completed",
    "effect.provider_receipt",
    "effect.readback_observed",
    "effect.reconciled",
    "effect.acceptance_recorded",
    "effect.terminal_truth",
]


def _assert_terminal_sequence(
    database: FoundryDatabase,
    effect_id: str,
    *,
    ambiguity_reconciled: bool = False,
) -> None:
    events = [
        event.event_type
        for event in database.events(readonly=True)
        if event.payload.get("action_key") == effect_id
    ]
    expected = list(TERMINAL_EFFECT_SEQUENCE)
    if ambiguity_reconciled:
        expected[1:2] = ["effect.reconcile_pending", "effect.frozen"]
    assert events == expected
    state = effect_state(database, effect_id)
    assert state["accepted"] is True
    assert state["terminal_outcome"] == "COMPLETED"


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
    _assert_terminal_sequence(
        database, first.claimed_effect["push_effect"]["action_key"]
    )
    _assert_terminal_sequence(database, first.claimed_effect["effect_id"])


class FabricGmail:
    def __init__(self, *, ambiguous_send: bool = False) -> None:
        self.ambiguous_send = ambiguous_send
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
            if self.ambiguous_send:
                raise GmailApiError(
                    "fixture timeout after owner-self send",
                    code="timeout",
                    transient=True,
                )
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
        observed_scopes=(SEND_SCOPE, READ_SCOPE),
        state="READY",
        version=1,
    )
    identity = ProviderIdentity(
        provider="gmail",
        account_identity=OWNER_EMAIL,
        provider_account_id=OWNER_EMAIL,
        observed_scopes=(SEND_SCOPE, READ_SCOPE),
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
    _assert_terminal_sequence(database, first.claimed_effect["effect_id"])


def test_github_fabric_reconciles_ambiguous_pr_under_stop(tmp_path: Path) -> None:
    database = _db(tmp_path)
    owned, worktree, remote, head = _git_fixture(tmp_path)
    documents: list[dict[str, Any]] = []
    write_calls = 0

    def write(_endpoint: str, request: Mapping[str, Any]) -> tuple[int, bytes]:
        nonlocal write_calls
        write_calls += 1
        document = {
            "number": 72,
            "title": request["title"],
            "body": request["body"],
            "head": {"ref": request["head"]},
            "base": {"ref": request["base"]},
            "html_url": "https://github.com/owner/demo/pull/72",
        }
        documents.append(document)
        raise GitHubWriteTransportError(
            "timeout",
            "fixture timeout after provider accepted PR",
            ambiguous=True,
        )

    def read(endpoint: str) -> tuple[int, bytes]:
        if "?state=all" in endpoint:
            return 200, json.dumps(documents).encode("utf-8")
        return 200, json.dumps(documents[-1]).encode("utf-8")

    stop_path = tmp_path / "STOP"
    context = ExecutorContext(
        database=database,
        owned_root=owned,
        github_write_adapter=GitHubWriteAdapter(
            transport=write,
            read_transport=read,
            dispatch_enabled=True,
            authorized_repositories=("owner/demo",),
        ),
        github_writes_enabled=True,
        github_authorized_repositories=("owner/demo",),
        github_repository_authorization=GitHubRepositoryAuthorization(
            authorization_id="owner-grant:fabric-github-ambiguous",
            repository="owner/demo",
            remote=str(remote),
            owner_authorized=True,
            dispatch_enabled=True,
        ),
    )
    intent = TaskIntent(
        task_id="mission-fabric-github-ambiguous",
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
        "title": "Fabric V12 ambiguous PR",
        "body": "fixture only",
        "stop_path": str(stop_path),
    }
    first = execute_action(
        database, context, intent, "github.pull_request.publish", payload,
    )
    assert first.claimed.value == "UNKNOWN"
    assert first.claimed_effect["journal_state"] == "FROZEN"
    stop_path.write_text("stop\n", encoding="utf-8")
    reconciled = execute_action(
        database, context, intent, "github.pull_request.publish", payload,
    )
    assert reconciled.success is True
    assert reconciled.claimed_effect["performed"] is False
    assert reconciled.claimed_effect["reconciled"] is True
    assert write_calls == 1
    _assert_terminal_sequence(
        database,
        reconciled.claimed_effect["effect_id"],
        ambiguity_reconciled=True,
    )


def test_gmail_fabric_reconciles_ambiguous_send_under_stop(tmp_path: Path) -> None:
    database = _db(tmp_path)
    transport = FabricGmail(ambiguous_send=True)
    provider = GmailApiProvider(
        OWNER_EMAIL,
        token_resolver=lambda _credential: "fixture-token",
        transport=transport,
        send_enabled=True,
        authorized_recipients=(OWNER_EMAIL,),
    )
    credential = CredentialHandle(
        reference="cred:gmail:fabric-v12-ambiguous",
        provider="gmail",
        account_identity=OWNER_EMAIL,
        auth_type="oauth2",
        observed_scopes=(SEND_SCOPE, READ_SCOPE),
        state="READY",
        version=1,
    )
    identity = ProviderIdentity(
        provider="gmail",
        account_identity=OWNER_EMAIL,
        provider_account_id=OWNER_EMAIL,
        observed_scopes=(SEND_SCOPE, READ_SCOPE),
    )
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        gmail_api_provider=provider,
        gmail_credential=credential,
        gmail_identity=identity,
        gmail_send_authorization=GmailSendAuthorization(
            authorization_id="owner-grant:fabric-gmail-ambiguous",
            account_identity=OWNER_EMAIL,
            recipient=OWNER_EMAIL,
            owner_authorized=True,
            dispatch_enabled=True,
        ),
    )
    intent = TaskIntent(
        task_id="mission-fabric-gmail-ambiguous",
        required_actions=("gmail.send",),
        unattended=False,
    )
    stop_path = tmp_path / "STOP"
    payload = {
        "recipient": OWNER_EMAIL,
        "subject": "Fabric V12 ambiguous message",
        "body_text": "fixture only",
        "stop_path": str(stop_path),
    }
    first = execute_action(database, context, intent, "gmail.send", payload)
    assert first.claimed.value == "UNKNOWN"
    assert first.claimed_effect["journal_state"] == "FROZEN"
    stop_path.write_text("stop\n", encoding="utf-8")
    reconciled = execute_action(database, context, intent, "gmail.send", payload)
    assert reconciled.success is True
    assert reconciled.claimed_effect["performed"] is False
    assert reconciled.claimed_effect["reconciled"] is True
    assert transport.send_calls == 1
    _assert_terminal_sequence(
        database,
        reconciled.claimed_effect["effect_id"],
        ambiguity_reconciled=True,
    )


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
        metadata=metadata,
        trust=CapabilityTrust.PROBATION,
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
