"""Disposable GitHub/Gmail proof for the canonical V12 effect authority."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Mapping

from jarvis_foundry.connectors.account_activation import (
    CredentialHandle,
    ProviderIdentity,
)
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
from jarvis_foundry.events import canonical_json
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    execute_action,
    seed_builtins,
)

OWNER_EMAIL = "owner@example.com"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AMBIGUOUS_TERMINAL_SEQUENCE = [
    "system.action_started",
    "effect.reconcile_pending",
    "effect.frozen",
    "effect.provider_receipt",
    "effect.readback_observed",
    "effect.reconciled",
    "effect.acceptance_recorded",
    "effect.terminal_truth",
]


def _git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _git_fixture(root: Path) -> tuple[Path, Path, Path, str]:
    seed = root / "seed"
    remote = root / "provider.git"
    owned = root / "owned"
    worktree = owned / "repo"
    seed.mkdir()
    owned.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("config", "user.email", "v12@local", cwd=seed)
    _git("config", "user.name", "V12 Disposable Proof", cwd=seed)
    (seed / "README.md").write_text("disposable provider\n", encoding="utf-8")
    _git("add", "README.md", cwd=seed)
    _git("commit", "-qm", "seed", cwd=seed)
    _git("init", "-q", "--bare", str(remote))
    _git("remote", "add", "origin", str(remote), cwd=seed)
    _git("push", "-q", "origin", "main", cwd=seed)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=remote)
    _git("clone", "-q", str(remote), str(worktree))
    _git("config", "user.email", "v12@local", cwd=worktree)
    _git("config", "user.name", "V12 Disposable Proof", cwd=worktree)
    (worktree / "effect.txt").write_text("disposable effect\n", encoding="utf-8")
    _git("add", "effect.txt", cwd=worktree)
    _git("commit", "-qm", "disposable effect", cwd=worktree)
    return owned, worktree, remote, _git("rev-parse", "HEAD", cwd=worktree)


class DisposableGitHub:
    def __init__(self) -> None:
        self.write_calls = 0
        self.documents: list[dict[str, Any]] = []

    def write(
        self, _endpoint: str, request: Mapping[str, Any],
    ) -> tuple[int, bytes]:
        self.write_calls += 1
        document = {
            "number": 1201,
            "title": request["title"],
            "body": request.get("body", ""),
            "head": {"ref": request["head"]},
            "base": {"ref": request["base"]},
            "html_url": "https://github.invalid/disposable/pull/1201",
        }
        self.documents.append(document)
        raise GitHubWriteTransportError(
            "timeout",
            "disposable timeout after synthetic provider accepted PR",
            ambiguous=True,
        )

    def read(self, endpoint: str) -> tuple[int, bytes]:
        if "?state=all" in endpoint:
            return 200, json.dumps(self.documents).encode("utf-8")
        return 200, json.dumps(self.documents[-1]).encode("utf-8")


class DisposableGmail:
    def __init__(self) -> None:
        self.send_calls = 0
        self.message: dict[str, Any] | None = None

    def __call__(
        self,
        method: str,
        url: str,
        _headers: Mapping[str, str],
        body: bytes | None,
    ) -> GmailResponse:
        if method == "POST":
            self.send_calls += 1
            if body is None:
                raise AssertionError("disposable Gmail send omitted body")
            message = BytesParser(policy=policy.default).parsebytes(
                base64.urlsafe_b64decode(json.loads(body)["raw"])
            )
            self.message = {
                "id": "disposable-message-1",
                "threadId": "disposable-thread-1",
                "payload": {
                    "headers": [
                        {"name": name, "value": message[name]}
                        for name in (
                            "To", "From", "Subject", "Message-ID",
                            "X-JARVIS-Effect-Identity",
                        )
                    ]
                },
            }
            raise GmailApiError(
                "disposable timeout after synthetic owner-self send",
                code="timeout",
                transient=True,
            )
        if "/users/me/messages?" in url:
            return GmailResponse(
                200,
                {},
                b'{"messages":[{"id":"disposable-message-1","threadId":"disposable-thread-1"}]}',
            )
        if self.message is None:
            return GmailResponse(404, {}, b"{}")
        return GmailResponse(200, {}, json.dumps(self.message).encode("utf-8"))


def _events(database: FoundryDatabase, effect_id: str) -> list[str]:
    return [
        event.event_type
        for event in database.events(readonly=True)
        if event.payload.get("action_key") == effect_id
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    output_root = Path(args.output_root).resolve()
    if output_root.exists():
        raise SystemExit("output root must not already exist")
    output_root.mkdir(parents=True)
    database = FoundryDatabase(output_root / "foundry.db")
    database.initialize()
    seed_builtins(database)

    owned, worktree, remote, head = _git_fixture(output_root)
    github = DisposableGitHub()
    github_stop = output_root / "GITHUB_STOP"
    github_context = ExecutorContext(
        database=database,
        owned_root=owned,
        github_write_adapter=GitHubWriteAdapter(
            transport=github.write,
            read_transport=github.read,
            dispatch_enabled=True,
            authorized_repositories=("owner/disposable",),
        ),
        github_writes_enabled=True,
        github_authorized_repositories=("owner/disposable",),
        github_repository_authorization=GitHubRepositoryAuthorization(
            authorization_id="owner-grant:v12-disposable-github",
            repository="owner/disposable",
            remote=str(remote),
            owner_authorized=True,
            dispatch_enabled=True,
        ),
    )
    github_intent = TaskIntent(
        task_id="v12.disposable.github",
        required_actions=("github.pull_request.publish",),
        unattended=False,
    )
    github_payload = {
        "worktree": str(worktree),
        "remote": str(remote),
        "owner": "owner",
        "repo": "disposable",
        "base": "main",
        "head_sha": head,
        "title": "V12 disposable PR",
        "body": "local fixture only",
        "stop_path": str(github_stop),
    }
    github_first = execute_action(
        database,
        github_context,
        github_intent,
        "github.pull_request.publish",
        github_payload,
    )
    github_stop.write_text("reconcile only\n", encoding="utf-8")
    github_reconciled = execute_action(
        database,
        github_context,
        github_intent,
        "github.pull_request.publish",
        github_payload,
    )
    github_stop.unlink(missing_ok=True)
    github_effect_id = str(github_reconciled.claimed_effect.get("effect_id") or "")

    gmail = DisposableGmail()
    gmail_stop = output_root / "GMAIL_STOP"
    credential = CredentialHandle(
        reference="cred:gmail:v12-disposable",
        provider="gmail",
        account_identity=OWNER_EMAIL,
        auth_type="oauth2",
        observed_scopes=(SEND_SCOPE, READ_SCOPE),
        state="READY",
        version=1,
    )
    gmail_context = ExecutorContext(
        database=database,
        owned_root=owned,
        gmail_api_provider=GmailApiProvider(
            OWNER_EMAIL,
            token_resolver=lambda _credential: "disposable-token",
            transport=gmail,
            send_enabled=True,
            authorized_recipients=(OWNER_EMAIL,),
        ),
        gmail_credential=credential,
        gmail_identity=ProviderIdentity(
            provider="gmail",
            account_identity=OWNER_EMAIL,
            provider_account_id=OWNER_EMAIL,
            observed_scopes=(SEND_SCOPE, READ_SCOPE),
        ),
        gmail_send_authorization=GmailSendAuthorization(
            authorization_id="owner-grant:v12-disposable-gmail",
            account_identity=OWNER_EMAIL,
            recipient=OWNER_EMAIL,
            owner_authorized=True,
            dispatch_enabled=True,
        ),
    )
    gmail_intent = TaskIntent(
        task_id="v12.disposable.gmail",
        required_actions=("gmail.send",),
        unattended=False,
    )
    gmail_payload = {
        "recipient": OWNER_EMAIL,
        "subject": "V12 disposable owner-self message",
        "body_text": "local fixture only",
        "stop_path": str(gmail_stop),
    }
    gmail_first = execute_action(
        database, gmail_context, gmail_intent, "gmail.send", gmail_payload,
    )
    gmail_stop.write_text("reconcile only\n", encoding="utf-8")
    gmail_reconciled = execute_action(
        database, gmail_context, gmail_intent, "gmail.send", gmail_payload,
    )
    gmail_stop.unlink(missing_ok=True)
    gmail_effect_id = str(gmail_reconciled.claimed_effect.get("effect_id") or "")

    starts_before_public_guards = sum(
        event.event_type == "system.action_started"
        for event in database.events(readonly=True)
    )
    disabled_context = ExecutorContext(database=database, owned_root=owned)
    public_github = execute_action(
        database,
        disabled_context,
        TaskIntent(
            task_id="v12.public.github.disabled",
            required_actions=("github.pull_request.publish",),
            unattended=False,
        ),
        "github.pull_request.publish",
        {**github_payload, "title": "PUBLIC WRITE MUST REMAIN DISABLED"},
    )
    public_gmail = execute_action(
        database,
        disabled_context,
        TaskIntent(
            task_id="v12.public.gmail.disabled",
            required_actions=("gmail.send",),
            unattended=False,
        ),
        "gmail.send",
        {**gmail_payload, "subject": "PUBLIC SEND MUST REMAIN DISABLED"},
    )
    starts_after_public_guards = sum(
        event.event_type == "system.action_started"
        for event in database.events(readonly=True)
    )

    github_events = _events(database, github_effect_id)
    gmail_events = _events(database, gmail_effect_id)
    github_state = effect_state(database, github_effect_id)
    gmail_state = effect_state(database, gmail_effect_id)
    serialized_events = canonical_json(
        [event.payload for event in database.events(readonly=True)]
    )
    report: dict[str, Any] = {
        "proof": "JARVIS_V12_DISPOSABLE_EXTERNAL_EFFECTS",
        "fixtures": {
            "github": "local bare Git remote plus synthetic PR API",
            "gmail": "synthetic owner-self provider",
            "public_provider_writes": False,
        },
        "github": {
            "first": github_first.as_dict(),
            "reconciled_under_stop": github_reconciled.as_dict(),
            "provider_write_calls": github.write_calls,
            "effect_id": github_effect_id,
            "effect_state": github_state,
            "effect_events": github_events,
        },
        "gmail": {
            "first": gmail_first.as_dict(),
            "reconciled_under_stop": gmail_reconciled.as_dict(),
            "provider_send_calls": gmail.send_calls,
            "effect_id": gmail_effect_id,
            "effect_state": gmail_state,
            "effect_events": gmail_events,
        },
        "public_guards": {
            "github": public_github.as_dict(),
            "gmail": public_gmail.as_dict(),
            "effect_claims_before": starts_before_public_guards,
            "effect_claims_after": starts_after_public_guards,
        },
        "duplicate_effects": {
            "github": max(0, github_events.count("system.action_started") - 1),
            "gmail": max(0, gmail_events.count("system.action_started") - 1),
        },
        "secrets_persisted": "disposable-token" in serialized_events,
    }
    report["passed"] = all(
        (
            github_first.claimed.value == "UNKNOWN",
            github_first.claimed_effect.get("journal_state") == "FROZEN",
            github_reconciled.success,
            github_reconciled.claimed_effect.get("performed") is False,
            github.write_calls == 1,
            github_events == AMBIGUOUS_TERMINAL_SEQUENCE,
            github_state.get("accepted") is True,
            gmail_first.claimed.value == "UNKNOWN",
            gmail_first.claimed_effect.get("journal_state") == "FROZEN",
            gmail_reconciled.success,
            gmail_reconciled.claimed_effect.get("performed") is False,
            gmail.send_calls == 1,
            gmail_events == AMBIGUOUS_TERMINAL_SEQUENCE,
            gmail_state.get("accepted") is True,
            public_github.claimed.value == "BLOCKED",
            public_gmail.claimed.value == "BLOCKED",
            starts_before_public_guards == starts_after_public_guards,
            report["duplicate_effects"] == {"github": 0, "gmail": 0},
            report["secrets_persisted"] is False,
        )
    )
    destination = output_root / "V12_EXTERNAL_EFFECTS_DISPOSABLE_PROOF.json"
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps({
        "passed": report["passed"],
        "evidence": str(destination),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
