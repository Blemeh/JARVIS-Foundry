"""Narrow Gmail API activation adapter.

This is the first real-account communication seam, not an OAuth client or a
mail-sending fleet.  Credential material is supplied only by an explicit
broker callback; the existing ``CredentialHandle`` remains opaque.  The
adapter implements account identity and a bounded read probe, plus dry-run
scope eligibility.  It deliberately has no send method, so an unconfigured
runtime cannot turn a capability declaration into a live mail effect.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..control.redaction import redact_text
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
    ) -> None:
        if not isinstance(account_identity, str) or not _EMAIL.fullmatch(account_identity.strip()):
            raise ValueError("Gmail account_identity must be an email address")
        if not base_url.startswith("https://") or any(char in base_url for char in ("\n", "\r")):
            raise ValueError("Gmail base_url must be HTTPS")
        self.account_identity = account_identity.strip()
        self._token_resolver = token_resolver
        self._transport = transport or _urllib_transport
        self.base_url = base_url.rstrip("/")
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


__all__ = [
    "GmailApiError",
    "GmailApiProvider",
    "GmailResponse",
    "GmailTokenResolver",
    "GmailTransport",
]
