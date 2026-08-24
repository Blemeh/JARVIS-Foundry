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
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..control.redaction import contains_secret_like, redact_text
from .contract import ConnectorAttempt, ConnectorSpec

_GITHUB_API_HOST = "api.github.com"
_MAX_DOCUMENT_BYTES = 256 * 1024
_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
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


@dataclass(frozen=True, slots=True)
class DocumentResponse:
    """Raw transport response for one document fetch hop."""

    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class DocumentContent:
    """Model-visible / durable-safe representation of a fetched document.

    ``text`` is SANITIZED (secret-like assignments redacted); the raw
    bytes exist only transiently and are represented afterwards solely
    by ``raw_sha256`` provenance.
    """

    text: str
    raw_sha256: str
    bytes_len: int
    final_url: str
    redirects: tuple[str, ...]


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirects are NEVER followed by urllib itself.

    Following them is this connector's OWN bounded policy decision (see
    ``read_document``), so every hop can be re-checked against the
    allow-listed research-host set before it happens.
    """

    def redirect_request(self, *_args, **_kwargs):
        return None


def _https_get_transport(url: str) -> DocumentResponse:
    """Bounded HTTPS GET for documentation pages.

    Auto-redirects are explicitly DISABLED at the transport layer; a
    redirect response is returned as-is (status 301/302/...) so the
    connector's host/scheme policy decides every hop.
    """
    import urllib.error
    request = urllib.request.Request(
        url, headers={"Accept": "text/html,application/json;q=0.9,*/*;q=0.5"})
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=30) as response:
            return DocumentResponse(
                status=int(response.status),
                headers=dict(response.headers.items()),
                body=response.read(_MAX_DOCUMENT_BYTES + 1))
    except urllib.error.HTTPError as error:
        return DocumentResponse(
            status=int(error.code),
            headers=dict(error.headers.items()) if error.headers else {},
            body=b"")
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
        from ..control.redaction import redact_payload
        redacted = redact_payload(document)
        attempt = self._finish(
            action_kind=action_kind, endpoint=endpoint, body=body, status=200,
        )
        if redacted != document:
            # Secret-like keys/assignments were normalized to
            # ``[REDACTED]`` before anything durable or returned.
            attempt.after_state["redacted"] = True
        return attempt, redacted

    def read_document(
        self, url: str,
    ) -> tuple[ConnectorAttempt, DocumentContent | None]:
        """Bounded HTTPS document fetch for official docs (DATA only).

        Redirect CONTRACT (implementation == documentation):

        - the initial URL must be HTTPS and (when an allow-list is
          configured) inside it;
        - redirects ARE followed, but at most ``_MAX_REDIRECTS`` hops;
        - EVERY hop target must be HTTPS (no downgrade to http);
        - EVERY hop target host must satisfy research-host policy:
          allow-listed when an allow-list exists, else same-host only;
        - a disallowed or downgraded hop is rejected WITHOUT fetching
          it; a loop or hop overrun fails bounded;
        - the final body is size-capped, hashed for provenance
          (``raw_sha256``), and SANITIZED through the existing JARVIS
          redaction machinery (``control.redaction.redact_text`` — the
          canonical secret-pattern family; no second scanner is
          maintained here) before anything is returned. Raw secret
          material in covered assignment shapes never reaches
          model-visible/durable text.

        No arbitrary network authority is granted: every reachable host
        is either the initial allow-listed/same host or a policy-checked
        redirect target.
        """
        current_url = self._validate_document_url(url)
        visited: list[str] = []
        for _hop in range(_MAX_REDIRECTS + 1):
            if current_url in visited:
                return self._doc_failure(
                    url, "redirect_loop",
                    f"redirect cycle detected via {current_url}"), None
            visited.append(current_url)
            try:
                response = self._doc_transport(current_url)
            except _TransportFailure as failure:
                return self._finish(
                    action_kind="http.doc.read", endpoint=url, body=b"",
                    status=0, error_code=failure.error_code,
                    provider_ref_override=url), None
            except (TimeoutError, OSError):
                return self._finish(
                    action_kind="http.doc.read", endpoint=url, body=b"",
                    status=0, error_code="timeout",
                    provider_ref_override=url), None
            if response.status not in _REDIRECT_STATUSES:
                break
            location = _header_value(response.headers, "Location")
            if not location:
                return self._doc_failure(
                    url, "redirect_missing_location",
                    f"redirect without Location at {current_url}"), None
            target = urllib.parse.urljoin(current_url, location.strip())
            violation = self._redirect_policy_violation(current_url, target)
            if violation is not None:
                return self._doc_failure(
                    url, "redirect_policy_rejected",
                    f"rejected redirect {current_url} -> {target}: "
                    f"{violation}"), None
            current_url = target
        else:
            return self._doc_failure(
                url, "redirect_loop",
                f"exceeded {_MAX_REDIRECTS} bounded redirects"), None

        body = response.body
        truncated = len(body) > _MAX_DOCUMENT_BYTES
        if truncated:
            body = body[:_MAX_DOCUMENT_BYTES]
        raw_sha256 = hashlib.sha256(body).hexdigest()
        sanitized_text = redact_text(body.decode("utf-8", errors="replace"))
        attempt = self._finish(
            action_kind="http.doc.read", endpoint=url,
            body=f"sanitized:{raw_sha256}".encode("utf-8"), status=200,
            provider_ref_override=current_url)
        attempt.after_state["bytes"] = len(body)
        attempt.after_state["truncated"] = truncated
        attempt.after_state["raw_sha256"] = raw_sha256
        attempt.after_state["redacted"] = sanitized_text != \
            body.decode("utf-8", errors="replace")
        content = DocumentContent(
            text=sanitized_text, raw_sha256=raw_sha256, bytes_len=len(body),
            final_url=current_url, redirects=tuple(visited[:-1]))
        return attempt, content

    def _doc_failure(
        self, url: str, error_code: str, detail: str,
    ) -> ConnectorAttempt:
        return self._finish(
            action_kind="http.doc.read", endpoint=url, body=b"",
            status=0, error_code=error_code, provider_ref_override=url)

    def _validate_document_url(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise GitHubResearchError("document URL must be HTTPS")
        if (self._allowed_doc_hosts
                and parsed.hostname.lower() not in self._allowed_doc_hosts):
            raise GitHubResearchError(
                f"host {parsed.hostname} is not allow-listed for doc reads")
        return url

    def _redirect_policy_violation(
        self, source: str, target: str,
    ) -> str | None:
        """None when the hop is policy-legal, else the violation reason."""
        t_parsed = urllib.parse.urlsplit(target)
        if t_parsed.scheme.lower() != "https":
            return "scheme downgrade refused"
        if not t_parsed.hostname:
            return "redirect target has no host"
        s_host = urllib.parse.urlsplit(source).hostname or ""
        allowed = self._allowed_doc_hosts
        if allowed:
            if t_parsed.hostname.lower() not in allowed:
                return (f"host {t_parsed.hostname} is not allow-listed")
        elif t_parsed.hostname.lower() != s_host.lower():
            return ("cross-host redirect outside same-host policy "
                    f"({s_host} -> {t_parsed.hostname})")
        return None

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
