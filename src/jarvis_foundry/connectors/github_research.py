"""Read-only GitHub research connector on the canonical V5 connector contract.

This connector gives JARVIS a bounded, evidence-preserving read path into
real GitHub repositories (metadata, commits, issues, PRs, CI runs,
releases) and bounded HTTPS document fetches for official documentation
research. It deliberately implements NO second registry, resource store,
or credential store:

- registration/routing/quota stay in ``connectors.registry``;
- attempt evidence stays in ``connectors.evidence``;
- account activation stays in ``connectors.account_activation``;
- provider-side Git truth stays in ``v3_git`` / ``connectors.git``.

Authentication is brokered by an ALREADY CONFIGURED GitHub CLI session
(``gh api``); no token value ever enters this process, prompts, or
evidence. The connector performs READ actions only — every declared
action has the ``read`` semantic, so the action-semantics check can
never demand effect tickets for it, and mutating GitHub work remains
owned by the existing Git effect adapter.

Untrusted content rule: response documents are DATA. They are hashed,
bounded, and returned for higher layers to record as observations or
claims; nothing in a response can change routing, scope, vetoes, or
STOP.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.parse
import uuid
from typing import Any, Callable

from ..control.redaction import contains_secret_like
from .contract import ConnectorAttempt, ConnectorSpec

_GITHUB_API_HOST = "api.github.com"
_MAX_DOCUMENT_BYTES = 256 * 1024
_READ_ACTIONS = {
    "github.repo.read": "read",
    "github.commit.read": "read",
    "github.issue.read": "read",
    "github.pull_request.read": "read",
    "github.check.read": "read",
    "github.release.read": "read",
    "github.search.read": "read",
    "http.doc.read": "read",
}


class GitHubResearchError(ValueError):
    """A research target or request shape is outside the read contract."""


class _TransportFailure(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _gh_api_transport(endpoint: str) -> tuple[int, bytes]:
    """Default brokered-auth transport: an already configured ``gh api``.

    The GitHub CLI resolves credentials from its own keyring; this process
    never sees a token. Exit codes map onto deterministic error codes.
    """
    result = subprocess.run(
        ["gh", "api", endpoint], capture_output=True, timeout=30, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        if "rate limit" in stderr.lower():
            raise _TransportFailure("rate_limited", stderr.strip())
        if "Not Found" in stderr or "HTTP 404" in stderr:
            raise _TransportFailure("http_404", stderr.strip())
        if "Bad credentials" in stderr or "auth" in stderr.lower():
            raise _TransportFailure("auth_unavailable", stderr.strip())
        raise _TransportFailure("transport_error", stderr.strip()[:400])
    return 200, result.stdout


def _https_get_transport(url: str) -> tuple[int, bytes]:
    """Bounded HTTPS GET for documentation pages (no redirects followed)."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url, headers={"Accept": "text/html,application/json;q=0.9,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), response.read(_MAX_DOCUMENT_BYTES + 1)
    except urllib.error.HTTPError as error:
        return int(error.code), b""
    except (TimeoutError, urllib.error.URLError, OSError) as error:
        raise _TransportFailure("transport_error", str(error)) from error


class GitHubResearchConnector:
    """Read-only GitHub/docs research attempts with durable-safe evidence."""

    spec = ConnectorSpec(
        connector_id="conn-github-research-v1",
        kind="github_research",
        version="1.0.0",
        actions=dict(_READ_ACTIONS),
        capabilities={
            "identity_probe": "PROBING",
            "repo_read": "PROBING",
            "doc_read": "PROBING",
        },
        retry_policy={"max_attempts": 2, "backoff_s": [0, 0.2]},
        target_kinds=("git_remote", "http_endpoint"),
        idempotency_strategy="provider-key",
        reconciliation_strategy="provider-query",
        supports_cancellation=False,
        cost_metadata={"unit": "request", "cost_known": True},
        evidence_fields=("attempt_id", "outcome", "provider_ref", "response_hash"),
    )

    def __init__(
        self,
        *,
        api_transport: Callable[[str], tuple[int, bytes]] | None = None,
        doc_transport: Callable[[str], tuple[int, bytes]] | None = None,
        allowed_doc_hosts: tuple[str, ...] = (),
    ) -> None:
        self._api_transport = api_transport or _gh_api_transport
        self._doc_transport = doc_transport or _https_get_transport
        self._allowed_doc_hosts = frozenset(
            host.lower() for host in allowed_doc_hosts)

    @staticmethod
    def _attempt_id() -> str:
        return f"gh-research-{uuid.uuid4().hex}"

    @staticmethod
    def _hash(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def _finish(
        self,
        *,
        action_kind: str,
        endpoint: str,
        body: bytes,
        status: int,
        error_code: str | None = None,
        provider_ref_override: str | None = None,
    ) -> ConnectorAttempt:
        outcome = "COMPLETED" if status == 200 else "FAILED"
        if provider_ref_override is not None:
            provider_ref = provider_ref_override
        elif action_kind.startswith("github."):
            provider_ref = f"https://{_GITHUB_API_HOST}/{endpoint.lstrip('/')}"
        else:
            provider_ref = endpoint
        return ConnectorAttempt(
            attempt_id=self._attempt_id(),
            connector_id=self.spec.connector_id,
            action_kind=action_kind,
            target_ref=endpoint,
            idempotency_key=f"{action_kind}:{endpoint}",
            payload_hash=self._hash(body),
            outcome=outcome,
            after_state={"response_hash": self._hash(body),
                         "bytes": len(body)},
            provider_ref=provider_ref,
            error_code=error_code,
            evidence_refs=(f"research:{endpoint}",),
            cost={"requests": 1},
            reconciled=outcome == "COMPLETED",
        )

    def read_api(
        self, action_kind: str, endpoint: str,
    ) -> tuple[ConnectorAttempt, dict[str, Any] | None]:
        """One bounded READ against api.github.com via the brokered transport."""
        if action_kind not in _READ_ACTIONS:
            raise GitHubResearchError(
                f"undeclared research action: {action_kind!r}")
        endpoint = self._validate_endpoint(endpoint)
        try:
            status, body = self._api_transport(endpoint)
        except _TransportFailure as failure:
            return self._finish(
                action_kind=action_kind, endpoint=endpoint, body=b"",
                status=0, error_code=failure.error_code), None
        except (TimeoutError, OSError):
            return self._finish(
                action_kind=action_kind, endpoint=endpoint, body=b"",
                status=0, error_code="timeout"), None
        if status != 200:
            return self._finish(
                action_kind=action_kind, endpoint=endpoint, body=b"",
                status=status, error_code=f"http_{status}"), None
        if len(body) > _MAX_DOCUMENT_BYTES:
            body = body[:_MAX_DOCUMENT_BYTES]
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._finish(
                action_kind=action_kind, endpoint=endpoint, body=b"",
                status=0, error_code="malformed_response"), None
        if contains_secret_like(json.dumps(document, default=str)[:8192]):
            return self._finish(
                action_kind=action_kind, endpoint=endpoint,
                body=json.dumps({"refused": "secret_like"}).encode("utf-8"),
                status=0, error_code="secret_like_refused"), None
        return self._finish(
            action_kind=action_kind, endpoint=endpoint, body=body, status=200,
        ), document

    def read_document(self, url: str) -> tuple[ConnectorAttempt, bytes]:
        """Bounded HTTPS document fetch for official docs (DATA only)."""
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise GitHubResearchError("document URL must be HTTPS")
        if (self._allowed_doc_hosts
                and parsed.hostname.lower() not in self._allowed_doc_hosts):
            raise GitHubResearchError(
                f"host {parsed.hostname} is not allow-listed for doc reads")
        try:
            status, body = self._doc_transport(url)
        except _TransportFailure as failure:
            return self._finish(
                action_kind="http.doc.read", endpoint=url, body=b"",
                status=0, error_code=failure.error_code,
                provider_ref_override=url), b""
        except (TimeoutError, OSError):
            return self._finish(
                action_kind="http.doc.read", endpoint=url, body=b"",
                status=0, error_code="timeout",
                provider_ref_override=url), b""
        truncated = False
        if len(body) > _MAX_DOCUMENT_BYTES:
            body = body[:_MAX_DOCUMENT_BYTES]
            truncated = True
        attempt = self._finish(
            action_kind="http.doc.read", endpoint=url, body=body,
            status=status, provider_ref_override=url)
        attempt.after_state["truncated"] = truncated
        return attempt, body

    def identity_probe(self) -> dict[str, Any]:
        """Authenticated identity + rate-limit truth from the live API."""
        user_attempt, user = self.read_api("github.repo.read", "/user")
        rate_attempt, rate = self.read_api(
            "github.repo.read", "/rate_limit")
        login = None
        if isinstance(user, dict):
            login = user.get("login")
        core = {}
        if isinstance(rate, dict):
            core = (rate.get("resources") or {}).get("core") or {}
        return {
            "authenticated_login": login,
            "rate_limit": {
                "limit": core.get("limit"),
                "remaining": core.get("remaining"),
                "reset_epoch": core.get("reset"),
            },
            "attempts": [user_attempt.to_evidence(), rate_attempt.to_evidence()],
        }

    @staticmethod
    def _validate_endpoint(endpoint: str) -> str:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise GitHubResearchError("endpoint is required")
        endpoint = endpoint.strip()
        if any(ord(char) < 0x20 for char in endpoint):
            raise GitHubResearchError("endpoint contains control characters")
        if "://" in endpoint or any(char.isspace() for char in endpoint):
            # Absolute URLs (including foreign hosts) are refused: this
            # connector reads api.github.com paths only.
            raise GitHubResearchError(
                "endpoint must be an api.github.com path, not a URL")
        candidate = endpoint[1:] if endpoint.startswith("/") else endpoint
        parsed = urllib.parse.urlsplit(
            f"https://{_GITHUB_API_HOST}/{candidate}")
        if parsed.hostname != _GITHUB_API_HOST:
            raise GitHubResearchError("endpoint must stay on api.github.com")
        lowered = candidate.lower()
        if lowered.startswith(("search/code",)):
            raise GitHubResearchError("code search is outside the read scope")
        return "/" + candidate


__all__ = [
    "GitHubResearchConnector",
    "GitHubResearchError",
]
