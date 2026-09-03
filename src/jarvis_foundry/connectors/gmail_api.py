"""Narrow Gmail API activation and owner-only send adapter.

This is the first real-account communication seam, not an OAuth client or a
mail-sending fleet.  Credential material is supplied only by an explicit
broker callback; the existing ``CredentialHandle`` remains opaque.  The
adapter implements account identity and a bounded read probe, dry-run scope
eligibility, and the first irreversible send effect. Send remains disabled by
default, is restricted to the configured owner's own address, and only reaches
terminal success after provider receipt plus independent readback.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any, Callable, Mapping

from ..control.broker import emergency_stop_reason
from ..control.redaction import redact_text
from ..database import FoundryDatabase
from ..effects import (
    COMPLETED,
    FAILED,
    FROZEN,
    STARTED,
    effect_state,
    execute_fabric_effect,
    fabric_effect_id,
    finalize_verified_effect,
    freeze_effect,
)
from ..events import canonical_json
from .account_activation import (
    CredentialHandle,
    ProbeReceipt,
    ProviderError,
    ProviderIdentity,
)
from .contract import ConnectorSpec

_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
_MAX_RESPONSE_BYTES = 256 * 1024
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

_READ_SCOPE_ALIASES = frozenset({
    "gmail.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
})
_DRAFT_SCOPE_ALIASES = frozenset({
    "gmail.compose",
    "https://www.googleapis.com/auth/gmail.compose",
})
_SEND_SCOPE_ALIASES = frozenset({
    "gmail.send",
    "https://www.googleapis.com/auth/gmail.send",
})
_MODIFY_SCOPE_ALIASES = frozenset({
    "gmail.modify",
    "https://www.googleapis.com/auth/gmail.modify",
})


@dataclass(frozen=True, slots=True)
class GmailResponse:
    """Bounded transport response with no credential fields."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class GmailApiError(ProviderError):
    """A Gmail API request failed inside the provider boundary."""


GmailTransport = Callable[[str, str, Mapping[str, str], bytes | None], GmailResponse]
GmailTokenResolver = Callable[[CredentialHandle], str | None]


@dataclass(frozen=True, slots=True)
class GmailSendAuthorization:
    """Exact owner grant for sending only to the owner's own mailbox."""

    authorization_id: str
    account_identity: str
    recipient: str
    owner_authorized: bool = False
    dispatch_enabled: bool = False
    capability_trust: str = "ESTABLISHED"

    def __post_init__(self) -> None:
        authorization_id = str(self.authorization_id).strip()
        account = str(self.account_identity).strip()
        recipient = str(self.recipient).strip()
        if not authorization_id:
            raise ValueError("Gmail authorization_id is required")
        if not _EMAIL.fullmatch(account) or not _EMAIL.fullmatch(recipient):
            raise ValueError("Gmail authorization identities must be email addresses")
        if account.casefold() != recipient.casefold():
            raise ValueError("Gmail send authorization is restricted to the owner's own address")
        object.__setattr__(self, "authorization_id", authorization_id)
        object.__setattr__(self, "account_identity", account)
        object.__setattr__(self, "recipient", recipient)
        object.__setattr__(self, "capability_trust", str(self.capability_trust).upper())

    def scope_refusal(self, *, account_identity: str, recipient: str) -> str | None:
        if account_identity.casefold() != self.account_identity.casefold():
            return "gmail_account_not_authorized"
        if recipient.casefold() != self.recipient.casefold():
            return "recipient_not_authorized"
        if recipient.casefold() != account_identity.casefold():
            return "recipient_must_be_owner"
        return None

    def refusal(self, *, account_identity: str, recipient: str) -> str | None:
        scope = self.scope_refusal(
            account_identity=account_identity, recipient=recipient,
        )
        if scope:
            return scope
        if self.capability_trust == "PROBATION":
            return "probation_capability_irreversible_effect_forbidden"
        if not self.owner_authorized:
            return "owner_authorization_required"
        if not self.dispatch_enabled:
            return "gmail_sends_disabled"
        return None


@dataclass(frozen=True, slots=True)
class GmailSendRequest:
    """Secret-safe stable identity for one exact self-addressed message."""

    account_identity: str
    recipient: str
    subject: str
    body_text: str
    content_hash: str
    message_id: str
    effect_id: str
    intent_id: str
    authorization_id: str | None = None

    @classmethod
    def build(
        cls,
        *,
        account_identity: str,
        recipient: str,
        subject: str,
        body_text: str,
        effect_id: str | None = None,
        authorization_id: str | None = None,
    ) -> "GmailSendRequest":
        account = str(account_identity).strip()
        target = str(recipient).strip()
        if not _EMAIL.fullmatch(account) or not _EMAIL.fullmatch(target):
            raise ValueError("Gmail send identities must be email addresses")
        subject_value = str(subject).strip()
        if not subject_value or len(subject_value) > 998:
            raise ValueError("Gmail subject is required and bounded")
        body_value = str(body_text)
        if len(body_value.encode("utf-8")) > 128 * 1024:
            raise ValueError("Gmail message body exceeds the bounded limit")
        identity = {
            "account_identity": account.casefold(),
            "recipient": target.casefold(),
            "subject": subject_value,
            "body_hash": hashlib.sha256(body_value.encode("utf-8")).hexdigest(),
        }
        content_hash = _digest(identity)
        stable_effect = effect_id or fabric_effect_id(
            system_id="gmail",
            target_ref=target.casefold(),
            capability_id="cap.gmail.send",
            capability_version="1.0.0",
            action_kind="gmail.send",
            input_payload=identity,
        )
        return cls(
            account_identity=account,
            recipient=target,
            subject=subject_value,
            body_text=body_value,
            content_hash=content_hash,
            message_id=f"<jarvis-{content_hash[:32]}@effects.local>",
            effect_id=str(stable_effect),
            intent_id=f"gmail-send-intent:{content_hash}",
            authorization_id=(
                str(authorization_id).strip() if authorization_id else None
            ),
        )

    def raw_message(self) -> str:
        message = EmailMessage(policy=SMTP)
        message["From"] = self.account_identity
        message["To"] = self.recipient
        message["Subject"] = self.subject
        message["Message-ID"] = self.message_id
        message["X-JARVIS-Effect-Identity"] = self.content_hash
        message.set_content(self.body_text)
        return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _safe_error(value: object) -> str:
    return redact_text(str(value)).strip()[:300]


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
) -> GmailResponse:
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return GmailResponse(
                status=int(response.status),
                headers=dict(response.headers.items()),
                body=response.read(_MAX_RESPONSE_BYTES + 1),
            )
    except urllib.error.HTTPError as error:
        return GmailResponse(
            status=int(error.code),
            headers=dict(error.headers.items()) if error.headers else {},
            body=error.read(_MAX_RESPONSE_BYTES + 1),
        )
    except (TimeoutError, urllib.error.URLError, OSError) as error:
        raise GmailApiError(
            "Gmail API transport failed",
            code="timeout" if isinstance(error, TimeoutError) else "transport_error",
            transient=True,
        ) from error


class GmailApiProvider:
    """Gmail identity/read activation with a safe external-effect seam."""

    provider = "gmail"
    connector_identity = "conn-gmail-api-v1"
    read_scopes = ("https://www.googleapis.com/auth/gmail.readonly",)
    reconciliation_strategy = "provider-query"
    spec = ConnectorSpec(
        connector_id=connector_identity,
        kind="gmail_api",
        version="1.0.0",
        actions={
            "gmail.read": "read",
            "gmail.draft": "communicate",
            "gmail.send": "communicate",
            "gmail.labels": "write",
        },
        capabilities={
            "identity_probe": "PROBING",
            "read_probe": "PROBING",
            "draft": "DISCOVERED",
            "send": "DISCOVERED",
            "live_account": "PROBING",
        },
        retry_policy={"max_attempts": 1, "backoff_s": []},
        target_kinds=("gmail_account", "gmail_message"),
        idempotency_strategy="provider-key",
        reconciliation_strategy="provider-query",
        supports_cancellation=False,
        cost_metadata={"unit": "gmail_api_request", "cost_known": True},
        evidence_fields=("provider", "account_identity", "provider_ref", "content_hash", "outcome"),
    )

    def __init__(
        self,
        account_identity: str,
        *,
        token_resolver: GmailTokenResolver | None = None,
        transport: GmailTransport | None = None,
        base_url: str = _BASE_URL,
        send_enabled: bool = False,
        authorized_recipients: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(account_identity, str) or not _EMAIL.fullmatch(account_identity.strip()):
            raise ValueError("Gmail account_identity must be an email address")
        if not base_url.startswith("https://") or any(char in base_url for char in ("\n", "\r")):
            raise ValueError("Gmail base_url must be HTTPS")
        self.account_identity = account_identity.strip()
        self._token_resolver = token_resolver
        self._transport = transport or _urllib_transport
        self.base_url = base_url.rstrip("/")
        self.send_enabled = bool(send_enabled)
        recipients: set[str] = set()
        for recipient in authorized_recipients:
            value = str(recipient).strip()
            if not _EMAIL.fullmatch(value):
                raise ValueError("authorized Gmail recipients must be email addresses")
            if value.casefold() != self.account_identity.casefold():
                raise ValueError("Gmail sends are restricted to the owner's own address")
            recipients.add(value.casefold())
        self.authorized_recipients = frozenset(recipients)
        self._eligibility: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _scope_ready(scopes: tuple[str, ...], aliases: frozenset[str]) -> bool:
        return bool(set(scopes) & aliases)

    def _token(self, credential: CredentialHandle) -> str:
        if credential.provider != self.provider:
            raise GmailApiError("credential provider does not match Gmail", code="provider_mismatch")
        if credential.state != "READY":
            raise GmailApiError(
                f"Gmail credential state is {credential.state}",
                code="credential_not_ready",
            )
        if self._token_resolver is None:
            raise GmailApiError(
                "Gmail credential material is not available in this runtime",
                code="credential_material_unavailable",
            )
        try:
            token = self._token_resolver(credential)
        except Exception as error:
            raise GmailApiError(
                "Gmail credential material resolver failed",
                code="credential_material_unavailable",
            ) from error
        if not isinstance(token, str) or not token.strip():
            raise GmailApiError(
                "Gmail credential material is unavailable",
                code="credential_material_unavailable",
            )
        return token.strip()

    def send_refusal(
        self,
        credential: CredentialHandle,
        identity: ProviderIdentity,
        request: GmailSendRequest,
    ) -> str | None:
        """Return a pre-claim metadata/configuration refusal, if any."""

        if not self.send_enabled:
            return "gmail_sends_disabled"
        if request.recipient.casefold() not in self.authorized_recipients:
            return "recipient_not_authorized"
        if request.recipient.casefold() != self.account_identity.casefold():
            return "recipient_must_be_owner"
        if credential.provider != self.provider or identity.provider != self.provider:
            return "provider_mismatch"
        if credential.state != "READY":
            return "credential_not_ready"
        if (
            credential.account_identity.casefold() != self.account_identity.casefold()
            or identity.account_identity.casefold() != self.account_identity.casefold()
        ):
            return "identity_mismatch"
        if not self._scope_ready(credential.observed_scopes, _SEND_SCOPE_ALIASES):
            return "send_scope_not_observed"
        if not self._scope_ready(credential.observed_scopes, _READ_SCOPE_ALIASES):
            return "readback_scope_not_observed"
        if self._token_resolver is None:
            return "credential_material_unavailable"
        return None

    def _path(self, path: str) -> str:
        if not isinstance(path, str) or not path.startswith("/users/me/"):
            raise ValueError("Gmail API path must stay under /users/me")
        segments = path.strip("/").split("/")
        if any(part in {"", ".", ".."} for part in segments):
            raise ValueError("Gmail API path contains an unsafe segment")
        return path

    def _request(
        self,
        credential: CredentialHandle,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
    ) -> Any:
        token = self._token(credential)
        url = f"{self.base_url}{self._path(path)}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = self._transport(method, url, headers, body)
        except GmailApiError:
            raise
        except (TimeoutError, OSError) as error:
            raise GmailApiError(
                "Gmail API transport failed",
                code="timeout" if isinstance(error, TimeoutError) else "transport_error",
                transient=True,
            ) from error
        if len(response.body) > _MAX_RESPONSE_BYTES:
            raise GmailApiError("Gmail response exceeded the bounded limit", code="response_too_large")
        if response.status == 401:
            raise GmailApiError("Gmail credentials were rejected", code="auth_rejected")
        if response.status == 403:
            raise GmailApiError("Gmail policy or scope denied the request", code="scope_or_policy")
        if response.status == 429:
            raise GmailApiError("Gmail rate limit was reached", code="rate_limited", transient=True)
        if response.status >= 500:
            raise GmailApiError("Gmail service is temporarily unavailable", code=f"http_{response.status}", transient=True)
        if response.status < 200 or response.status >= 300:
            raise GmailApiError("Gmail API request was rejected", code=f"http_{response.status}")
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GmailApiError("Gmail response was malformed", code="malformed_response") from error

    def identity(self, credential: CredentialHandle) -> ProviderIdentity:
        profile = self._request(credential, "/users/me/profile")
        if not isinstance(profile, Mapping):
            raise GmailApiError("Gmail profile response was not an object", code="malformed_response")
        email = profile.get("emailAddress")
        if not isinstance(email, str) or not _EMAIL.fullmatch(email.strip()):
            raise GmailApiError("Gmail profile did not expose an account identity", code="identity_missing")
        email = email.strip()
        if email.casefold() != self.account_identity.casefold():
            raise GmailApiError("Gmail account identity does not match the configured account", code="identity_mismatch")
        return ProviderIdentity(
            provider=self.provider,
            account_identity=email,
            provider_account_id=email,
            observed_scopes=tuple(sorted(set(credential.observed_scopes))),
            metadata={"profile_read": True, "provider_account_id": email},
        )

    def read_probe(self, credential: CredentialHandle, identity: ProviderIdentity) -> ProbeReceipt:
        if identity.account_identity.casefold() != self.account_identity.casefold():
            raise GmailApiError("Gmail identity does not match the provider account", code="identity_mismatch")
        threads = self._request(credential, "/users/me/threads?maxResults=1")
        if not isinstance(threads, Mapping):
            raise GmailApiError("Gmail thread response was not an object", code="malformed_response")
        entries = threads.get("threads")
        entries = entries if isinstance(entries, list) else []
        first_id = entries[0].get("id") if entries and isinstance(entries[0], Mapping) else None
        safe_observation = {
            "result_size_estimate": threads.get("resultSizeEstimate"),
            "thread_count": len(entries),
            "first_thread_id_hash": _digest(str(first_id)) if first_id is not None else None,
        }
        return ProbeReceipt(
            provider=self.provider,
            account_identity=identity.account_identity,
            resource_kind="thread_collection",
            resource_id="users-me-threads",
            provider_ref=f"{self.provider}:users/me/threads",
            content_hash=_digest(safe_observation),
            content_size=len(canonical_json(safe_observation).encode("utf-8")),
            bounded=True,
            untrusted_content=True,
            metadata={
                "read_only": True,
                "thread_present": first_id is not None,
                "result_size_estimate": threads.get("resultSizeEstimate"),
            },
        )

    def capabilities_for(self, scopes: tuple[str, ...]) -> dict[str, str]:
        observed = tuple(scopes)
        return {
            "gmail.read": "READY" if self._scope_ready(observed, _READ_SCOPE_ALIASES) else "DISCOVERED",
            "gmail.draft": "READY" if self._scope_ready(observed, _DRAFT_SCOPE_ALIASES) else "DISCOVERED",
            "gmail.send": "READY" if self._scope_ready(observed, _SEND_SCOPE_ALIASES) else "DISCOVERED",
            "gmail.labels": "READY" if self._scope_ready(observed, _MODIFY_SCOPE_ALIASES) else "DISCOVERED",
        }

    def write_eligibility(
        self,
        credential: CredentialHandle,
        identity: ProviderIdentity,
        capability: str,
        target: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if identity.account_identity.casefold() != self.account_identity.casefold():
            raise GmailApiError("Gmail identity does not match the provider account", code="identity_mismatch")
        if not isinstance(target, str) or not target.strip() or not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("Gmail effect target and idempotency key are required")
        required = {
            "gmail.draft": _DRAFT_SCOPE_ALIASES,
            "gmail.send": _SEND_SCOPE_ALIASES,
            "gmail.labels": _MODIFY_SCOPE_ALIASES,
        }.get(capability)
        if required is None:
            return {"scope_eligible": False, "provider_ref": None, "reason": "capability is not declared"}
        scope_eligible = self._scope_ready(credential.observed_scopes, required)
        effect_id = _digest({"capability": capability, "target": target, "idempotency_key": idempotency_key})[:24]
        provider_ref = f"{self.provider}:dry-run:{effect_id}" if scope_eligible else None
        self._eligibility[idempotency_key] = {
            "capability": capability,
            "target": target,
            "scope_eligible": scope_eligible,
            "provider_ref": provider_ref,
        }
        return {
            "scope_eligible": scope_eligible,
            "provider_ref": provider_ref,
            "reason": None if scope_eligible else "required Gmail scope was not observed",
        }

    def reconcile(self, idempotency_key: str) -> dict[str, Any] | None:
        # Eligibility is a dry run; no external effect has been issued by
        # this adapter and therefore there is no provider receipt to claim.
        return None

    @staticmethod
    def _headers(document: Mapping[str, Any]) -> dict[str, str]:
        payload = document.get("payload")
        entries = payload.get("headers") if isinstance(payload, Mapping) else None
        if not isinstance(entries, list):
            return {}
        headers: dict[str, str] = {}
        for item in entries:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                headers[name.casefold()] = value.strip()
        return headers

    def _readback_document(
        self,
        credential: CredentialHandle,
        request: GmailSendRequest,
        document: Mapping[str, Any],
    ) -> dict[str, Any]:
        provider_id = document.get("id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            return {
                "status": "UNKNOWN", "matches": False,
                "reason": "provider_id_missing",
            }
        observed = self._request(
            credential,
            f"/users/me/messages/{provider_id}?format=metadata&metadataHeaders=To"
            "&metadataHeaders=From&metadataHeaders=Subject"
            "&metadataHeaders=Message-ID&metadataHeaders=X-JARVIS-Effect-Identity",
        )
        if not isinstance(observed, Mapping):
            return {
                "status": "UNKNOWN", "matches": False,
                "reason": "malformed_response",
            }
        headers = self._headers(observed)
        matches = (
            observed.get("id") == provider_id
            and headers.get("to", "").casefold() == request.recipient.casefold()
            and headers.get("from", "").casefold() == request.account_identity.casefold()
            and headers.get("subject") == request.subject
            and headers.get("message-id") == request.message_id
            and headers.get("x-jarvis-effect-identity") == request.content_hash
        )
        return {
            "status": "MATCH" if matches else "MISMATCH",
            "matches": matches,
            "provider_id": provider_id,
            "provider_ref": f"gmail:message:{provider_id}",
            "thread_id": observed.get("threadId"),
            "message_id": request.message_id,
            "content_hash": request.content_hash,
        }

    def send_message(
        self,
        credential: CredentialHandle,
        identity: ProviderIdentity,
        request: GmailSendRequest,
    ) -> dict[str, Any]:
        """Dispatch one pre-authorized message and immediately read it back."""

        refusal = self.send_refusal(credential, identity, request)
        if refusal:
            return {
                "claimed": "BLOCKED", "performed": False,
                "error_code": refusal,
            }
        document = self._request(
            credential,
            "/users/me/messages/send",
            method="POST",
            body=json.dumps(
                {"raw": request.raw_message()},
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        if not isinstance(document, Mapping):
            raise GmailApiError(
                "Gmail send response was not an object", code="malformed_response",
            )
        provider_id = document.get("id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise GmailApiError(
                "Gmail send response omitted message id", code="provider_id_missing",
            )
        receipt = {
            "provider_id": provider_id,
            "provider_ref": f"gmail:message:{provider_id}",
            "thread_id": document.get("threadId"),
            "message_id": request.message_id,
            "content_hash": request.content_hash,
        }
        try:
            readback = self._readback_document(
                credential, request, document,
            )
        except GmailApiError as error:
            readback = {
                "status": "UNKNOWN",
                "matches": False,
                "reason": f"readback_{error.code}",
                "provider_id": provider_id,
                "provider_ref": receipt["provider_ref"],
            }
        return {
            "claimed": "COMPLETED" if readback.get("matches") else "UNKNOWN",
            "performed": True,
            "intent_id": request.intent_id,
            "effect_id": request.effect_id,
            "idempotency_key": request.effect_id,
            "provider_id": provider_id,
            "provider_ref": receipt["provider_ref"],
            "thread_id": document.get("threadId"),
            "message_id": request.message_id,
            "content_hash": request.content_hash,
            "subject_hash": hashlib.sha256(request.subject.encode("utf-8")).hexdigest(),
            "readback": readback,
            "readback_status": readback.get("status"),
            "reconciliation_required": readback.get("matches") is not True,
            "error_code": (
                None if readback.get("matches") is True
                else str(readback.get("reason") or "independent_readback_required")
            ),
            "receipt": receipt,
        }

    def reconcile_send(
        self,
        credential: CredentialHandle,
        request: GmailSendRequest,
    ) -> dict[str, Any]:
        """Find one sent message by stable RFC 822 Message-ID and read it back."""

        query = urllib.parse.urlencode({
            "q": f"rfc822msgid:{request.message_id}",
            "maxResults": 10,
        })
        collection = self._request(
            credential, f"/users/me/messages?{query}",
        )
        messages = collection.get("messages") if isinstance(collection, Mapping) else None
        matches = [
            item for item in (messages if isinstance(messages, list) else [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        ]
        if len(matches) != 1:
            return {
                "claimed": "UNKNOWN",
                "performed": False,
                "intent_id": request.intent_id,
                "effect_id": request.effect_id,
                "message_id": request.message_id,
                "content_hash": request.content_hash,
                "readback": {
                    "status": "NOT_OBSERVED" if not matches else "DUPLICATE",
                    "matches": False,
                    "match_count": len(matches),
                },
                "readback_status": "NOT_OBSERVED" if not matches else "DUPLICATE",
                "reconciliation_required": True,
                "duplicate_prevented": bool(matches),
                "error_code": "not_observed" if not matches else "duplicate_provider_matches",
            }
        readback = self._readback_document(
            credential, request, matches[0],
        )
        provider_id = matches[0]["id"]
        receipt = {
            "provider_id": provider_id,
            "provider_ref": f"gmail:message:{provider_id}",
            "thread_id": matches[0].get("threadId"),
            "message_id": request.message_id,
            "content_hash": request.content_hash,
        }
        return {
            "claimed": "COMPLETED" if readback.get("matches") else "UNKNOWN",
            "performed": False,
            "intent_id": request.intent_id,
            "effect_id": request.effect_id,
            "idempotency_key": request.effect_id,
            "provider_id": provider_id,
            "provider_ref": receipt["provider_ref"],
            "thread_id": matches[0].get("threadId"),
            "message_id": request.message_id,
            "content_hash": request.content_hash,
            "subject_hash": hashlib.sha256(request.subject.encode("utf-8")).hexdigest(),
            "readback": readback,
            "readback_status": readback.get("status"),
            "reconciliation_required": readback.get("matches") is not True,
            "reconciled": readback.get("matches") is True,
            "receipt": receipt,
        }


class GmailSendEffectDriver:
    """Disabled-by-default owner-only Gmail send over the canonical journal."""

    def __init__(self, database: FoundryDatabase, provider: GmailApiProvider) -> None:
        self.database = database
        self.provider = provider

    @staticmethod
    def _blocked(request: GmailSendRequest, code: str) -> dict[str, Any]:
        return {
            "claimed": "BLOCKED",
            "performed": False,
            "intent_id": request.intent_id,
            "effect_id": request.effect_id,
            "error_code": code,
            "external_effects": 0,
        }

    def _stopped(self, stop_path: str | None, mission_id: str | None) -> bool:
        identities = {"GLOBAL"}
        if mission_id:
            identities.add(mission_id)
        return emergency_stop_reason(
            self.database, stop_path, identities=identities,
        ) is not None

    def _finalize(
        self,
        request: GmailSendRequest,
        result: Mapping[str, Any],
        *,
        reconciled: bool,
    ) -> dict[str, Any] | None:
        readback = result.get("readback")
        receipt = result.get("receipt")
        if (
            not isinstance(readback, Mapping)
            or readback.get("matches") is not True
            or not isinstance(receipt, Mapping)
        ):
            return None
        return finalize_verified_effect(
            self.database,
            request.effect_id,
            provider_receipt=receipt,
            readback=readback,
            result=dict(result),
            acceptance_evidence={
                "intent_id": request.intent_id,
                "criterion": "provider metadata matches exact owner-only message identity",
                "authorization_id": request.authorization_id,
            },
            reconciled=reconciled,
        )

    def send(
        self,
        *,
        authorization: GmailSendAuthorization,
        credential: CredentialHandle,
        identity: ProviderIdentity,
        recipient: str,
        subject: str,
        body_text: str,
        effect_id: str | None = None,
        mission_id: str | None = None,
        stop_path: str | None = None,
    ) -> dict[str, Any]:
        request = GmailSendRequest.build(
            account_identity=self.provider.account_identity,
            recipient=recipient,
            subject=subject,
            body_text=body_text,
            effect_id=effect_id,
            authorization_id=authorization.authorization_id,
        )
        state = effect_state(self.database, request.effect_id)
        pending_effect = state.get("status") in {STARTED, COMPLETED, FROZEN}
        refusal = (
            authorization.scope_refusal(
                account_identity=self.provider.account_identity,
                recipient=request.recipient,
            )
            if pending_effect
            else authorization.refusal(
                account_identity=self.provider.account_identity,
                recipient=request.recipient,
            )
        )
        if refusal:
            return self._blocked(request, refusal)
        if state.get("status") == STARTED and state.get("claim_active"):
            return {
                "claimed": "UNKNOWN",
                "performed": False,
                "intent_id": request.intent_id,
                "effect_id": request.effect_id,
                "action_key": request.effect_id,
                "journal_state": STARTED,
                "reconciliation_required": True,
                "error_code": "effect_claim_active",
            }
        if state.get("status") == STARTED:
            freeze_effect(
                self.database,
                request.effect_id,
                reason="interrupted Gmail dispatch requires provider readback",
            )
            state = effect_state(self.database, request.effect_id)
        if state.get("status") in {COMPLETED, FROZEN}:
            try:
                reconciled = self.provider.reconcile_send(credential, request)
            except GmailApiError as error:
                return {
                    "claimed": "UNKNOWN",
                    "performed": False,
                    "intent_id": request.intent_id,
                    "effect_id": request.effect_id,
                    "action_key": request.effect_id,
                    "journal_state": state["status"],
                    "reconciliation_required": True,
                    "error_code": error.code,
                }
            finalized = self._finalize(
                request, reconciled, reconciled=True,
            )
            if finalized is not None:
                finalized.update({"performed": False, "replayed": True})
                return finalized
            return {
                **reconciled,
                "claimed": "UNKNOWN",
                "performed": False,
                "action_key": request.effect_id,
                "journal_state": state["status"],
                "reconciliation_required": True,
            }
        provider_refusal = self.provider.send_refusal(
            credential, identity, request,
        )
        if provider_refusal:
            return self._blocked(request, provider_refusal)
        if self._stopped(stop_path, mission_id):
            return self._blocked(request, "stop_requested")
        if state.get("status") == FAILED:
            return execute_fabric_effect(
                self.database,
                action_key=request.effect_id,
                system_id="gmail",
                target_ref=request.recipient,
                payload_hash=request.content_hash,
                kind="gmail.send",
                connector_id=self.provider.connector_identity,
                perform=lambda: {},
            )

        def perform() -> Mapping[str, Any]:
            try:
                result = self.provider.send_message(
                    credential, identity, request,
                )
            except GmailApiError as error:
                if (
                    error.code in {
                        "timeout", "transport_error", "malformed_response",
                        "response_too_large", "provider_id_missing",
                    }
                    or error.code.startswith("http_5")
                ):
                    raise TimeoutError(
                        "Gmail send timed out after dispatch"
                    ) from error
                raise
            if (
                result.get("claimed") == COMPLETED
                and result.get("readback_status") == "MATCH"
            ):
                return result
            if result.get("claimed") == COMPLETED:
                return {
                    **result,
                    "claimed": "UNKNOWN",
                    "reconciliation_required": True,
                    "error_code": "independent_readback_required",
                }
            return result

        result = execute_fabric_effect(
            self.database,
            action_key=request.effect_id,
            system_id="gmail",
            target_ref=request.recipient,
            payload_hash=request.content_hash,
            kind="gmail.send",
            connector_id=self.provider.connector_identity,
            expected_effects=("send one exact message to the owner's own address",),
            request_identity={
                "intent_id": request.intent_id,
                "account_identity": request.account_identity,
                "recipient": request.recipient,
                "message_id": request.message_id,
                "content_hash": request.content_hash,
                "authorization_id": authorization.authorization_id,
            },
            perform=perform,
            stop_check=lambda: self._stopped(stop_path, mission_id),
        )
        if result.get("claimed") == COMPLETED:
            finalized = self._finalize(request, result, reconciled=False)
            if finalized is not None:
                finalized["performed"] = bool(result.get("performed"))
                finalized["external_effects"] = 1
                return finalized
        return result


__all__ = [
    "GmailApiError",
    "GmailApiProvider",
    "GmailResponse",
    "GmailSendAuthorization",
    "GmailSendEffectDriver",
    "GmailSendRequest",
    "GmailTokenResolver",
    "GmailTransport",
]
