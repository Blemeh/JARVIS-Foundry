"""V12 first irreversible Gmail effect pack (mock-provider proof only)."""
from __future__ import annotations

import base64
import json
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
    GmailSendEffectDriver,
    GmailSendRequest,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import effect_state
from jarvis_foundry.events import canonical_json


OWNER = "owner@example.com"
TOKEN = "synthetic-v12-token-never-persist"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _credential() -> CredentialHandle:
    return CredentialHandle(
        reference="cred:gmail:v12-fixture",
        provider="gmail",
        account_identity=OWNER,
        auth_type="oauth2",
        observed_scopes=(SEND_SCOPE,),
        state="READY",
        version=1,
    )


def _identity() -> ProviderIdentity:
    return ProviderIdentity(
        provider="gmail",
        account_identity=OWNER,
        provider_account_id=OWNER,
        observed_scopes=(SEND_SCOPE,),
        metadata={"fixture": True},
    )


def _authorization(
    *,
    enabled: bool = True,
    owner_authorized: bool = True,
    trust: str = "ESTABLISHED",
) -> GmailSendAuthorization:
    return GmailSendAuthorization(
        authorization_id="owner-grant:v12-gmail-fixture",
        account_identity=OWNER,
        recipient=OWNER,
        owner_authorized=owner_authorized,
        dispatch_enabled=enabled,
        capability_trust=trust,
    )


class FakeGmail:
    def __init__(self, *, ambiguous_send: bool = False) -> None:
        self.ambiguous_send = ambiguous_send
        self.send_calls = 0
        self.read_calls = 0
        self.messages: dict[str, dict[str, Any]] = {}

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> GmailResponse:
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        if method == "POST" and url.endswith("/users/me/messages/send"):
            self.send_calls += 1
            assert body is not None
            raw = json.loads(body.decode("utf-8"))["raw"]
            message = BytesParser(policy=policy.default).parsebytes(
                base64.urlsafe_b64decode(raw.encode("ascii"))
            )
            provider_id = "message-v12-1"
            self.messages[provider_id] = {
                "id": provider_id,
                "threadId": "thread-v12-1",
                "headers": {
                    "To": message["To"],
                    "From": message["From"],
                    "Subject": message["Subject"],
                    "Message-ID": message["Message-ID"],
                    "X-JARVIS-Effect-Identity": message["X-JARVIS-Effect-Identity"],
                },
            }
            if self.ambiguous_send:
                raise GmailApiError(
                    "fixture timeout after provider accepted message",
                    code="timeout",
                    transient=True,
                )
            return GmailResponse(
                200, {}, json.dumps({
                    "id": provider_id, "threadId": "thread-v12-1",
                }).encode("utf-8"),
            )

        self.read_calls += 1
        if "/users/me/messages?" in url:
            entries = [
                {"id": item["id"], "threadId": item["threadId"]}
                for item in self.messages.values()
            ]
            return GmailResponse(
                200, {}, json.dumps({"messages": entries}).encode("utf-8"),
            )
        marker = "/users/me/messages/"
        if marker in url:
            provider_id = url.split(marker, 1)[1].split("?", 1)[0]
            item = self.messages.get(provider_id)
            if item is None:
                return GmailResponse(404, {}, b'{}')
            document = {
                "id": item["id"],
                "threadId": item["threadId"],
                "payload": {
                    "headers": [
                        {"name": name, "value": value}
                        for name, value in item["headers"].items()
                    ],
                },
            }
            return GmailResponse(200, {}, json.dumps(document).encode("utf-8"))
        return GmailResponse(404, {}, b'{}')


def _provider(
    transport: FakeGmail,
    *,
    enabled: bool = True,
) -> GmailApiProvider:
    return GmailApiProvider(
        OWNER,
        token_resolver=lambda _credential: TOKEN,
        transport=transport,
        send_enabled=enabled,
        authorized_recipients=(OWNER,) if enabled else (),
    )


def _send(
    driver: GmailSendEffectDriver,
    authorization: GmailSendAuthorization,
    *,
    stop_path: Path | None = None,
) -> dict[str, Any]:
    return driver.send(
        authorization=authorization,
        credential=_credential(),
        identity=_identity(),
        recipient=OWNER,
        subject="V12 exact effect",
        body_text="fixture-only owner message",
        mission_id="mission-v12-gmail",
        stop_path=str(stop_path) if stop_path is not None else None,
    )


def test_gmail_authorization_rejects_any_third_party_recipient() -> None:
    try:
        GmailSendAuthorization(
            authorization_id="owner-grant:bad",
            account_identity=OWNER,
            recipient="third-party@example.net",
            owner_authorized=True,
            dispatch_enabled=True,
        )
    except ValueError as error:
        assert "owner's own address" in str(error)
    else:  # pragma: no cover - explicit fail message
        raise AssertionError("third-party Gmail authorization was accepted")


def test_disabled_probation_and_stop_refuse_before_claim_or_dispatch(tmp_path: Path) -> None:
    database = _db(tmp_path)
    transport = FakeGmail()
    enabled_provider = _provider(transport)
    disabled_provider = _provider(transport, enabled=False)

    disabled = _send(
        GmailSendEffectDriver(database, disabled_provider),
        _authorization(),
    )
    probation = _send(
        GmailSendEffectDriver(database, enabled_provider),
        _authorization(trust="PROBATION"),
    )
    stop = tmp_path / "STOP"
    stop.write_text("stop", encoding="utf-8")
    stopped = _send(
        GmailSendEffectDriver(database, enabled_provider),
        _authorization(),
        stop_path=stop,
    )
    assert disabled["error_code"] == "gmail_sends_disabled"
    assert probation["error_code"] == "probation_capability_irreversible_effect_forbidden"
    assert stopped["error_code"] == "stop_requested"
    assert transport.send_calls == 0
    assert not [
        event for event in database.events()
        if event.event_type == "system.action_started"
    ]


def test_gmail_receipt_readback_acceptance_and_restart_replay(tmp_path: Path) -> None:
    database = _db(tmp_path)
    transport = FakeGmail()
    provider = _provider(transport)
    first = _send(
        GmailSendEffectDriver(database, provider), _authorization(),
    )
    assert first["claimed"] == "COMPLETED"
    assert first["provider_id"] == "message-v12-1"
    assert first["provider_ref"] == "gmail:message:message-v12-1"
    assert first["readback_status"] == "MATCH"
    assert first["accepted"] is True
    assert first["terminal_truth"] is True
    assert first["external_effects"] == 1
    assert transport.send_calls == 1

    request = GmailSendRequest.build(
        account_identity=OWNER,
        recipient=OWNER,
        subject="V12 exact effect",
        body_text="fixture-only owner message",
    )
    state = effect_state(database, request.effect_id)
    assert state["status"] == "COMPLETED"
    assert state["terminal_outcome"] == "COMPLETED"
    assert state["accepted"] is True

    restarted_provider = _provider(transport, enabled=False)
    # Reconciliation remains readable after dispatch is disabled, but the
    # owner authorization remains exact and the journal blocks a second send.
    replay = _send(
        GmailSendEffectDriver(database, restarted_provider), _authorization(),
    )
    assert replay["claimed"] == "COMPLETED"
    assert replay["replayed"] is True
    assert replay["performed"] is False
    assert transport.send_calls == 1

    replay_enabled = _send(
        GmailSendEffectDriver(database, provider), _authorization(),
    )
    assert replay_enabled["claimed"] == "COMPLETED"
    assert replay_enabled["replayed"] is True
    assert replay_enabled["performed"] is False
    assert transport.send_calls == 1

    serialized = canonical_json([event.payload for event in database.events()])
    assert TOKEN not in serialized
    assert "fixture-only owner message" not in serialized


def test_stop_after_ambiguous_send_reconciles_without_retry(tmp_path: Path) -> None:
    database = _db(tmp_path)
    transport = FakeGmail(ambiguous_send=True)
    provider = _provider(transport)
    driver = GmailSendEffectDriver(database, provider)
    first = _send(driver, _authorization())
    assert first["claimed"] == "UNKNOWN"
    assert first["journal_state"] == "FROZEN"
    assert first["error_code"] == "timeout_after_action"
    assert transport.send_calls == 1

    stop = tmp_path / "STOP"
    stop.write_text("stop", encoding="utf-8")
    reconciled = _send(driver, _authorization(), stop_path=stop)
    assert reconciled["claimed"] == "COMPLETED"
    assert reconciled["reconciled"] is True
    assert reconciled["replayed"] is True
    assert reconciled["performed"] is False
    assert reconciled["provider_id"] == "message-v12-1"
    assert transport.send_calls == 1


def test_missing_or_duplicate_readback_stays_unknown_and_frozen(tmp_path: Path) -> None:
    database = _db(tmp_path)
    transport = FakeGmail(ambiguous_send=True)
    provider = _provider(transport)
    driver = GmailSendEffectDriver(database, provider)
    first = _send(driver, _authorization())
    assert first["journal_state"] == "FROZEN"

    transport.messages["message-v12-2"] = dict(
        transport.messages["message-v12-1"], id="message-v12-2",
    )
    second = _send(driver, _authorization())
    assert second["claimed"] == "UNKNOWN"
    assert second["readback_status"] == "DUPLICATE"
    assert second["duplicate_prevented"] is True
    assert second["reconciliation_required"] is True
    assert transport.send_calls == 1
