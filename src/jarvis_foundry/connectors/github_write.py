"""Bounded GitHub write transport behind the existing ``github_write`` seam.

The read-only GitHub connector remains the authority for research.  This
module owns only the narrow issue/pull-request REST transport and the
in-memory request lifecycle needed to make an ambiguous response safe:

* the effect key is derived from action, target, and canonical payload;
* the provider id is retained separately from the human URL;
* a timeout is ``UNKNOWN`` and remains pending for explicit reconciliation;
* a repeated request never sends a second write from the same adapter; and
* readback is optional at construction time but is ``NOT_TESTED`` when absent.

The default transport uses an already authenticated GitHub CLI session.  It
is never installed into a runtime context implicitly; callers must explicitly
construct/inject it, preserving the no-silent-live-mutation boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..control.redaction import redact_text
from ..events import canonical_json
from ..systems import external_action_key
from .contract import ConnectorSpec

_ENDPOINT = re.compile(r"^/repos/([^/\s]+)/([^/\s]+)/(issues|pulls)$")
_MAX_RESPONSE_BYTES = 256 * 1024
_WRITE_ACTIONS = frozenset({
    "github.issue.create",
    "github.pull_request.create",
})

GITHUB_WRITE_SPEC = ConnectorSpec(
    connector_id="conn-github-write-v1",
    kind="github_write",
    version="1.0.0",
    actions={
        "github.issue.create": "external_effect",
        "github.pull_request.create": "external_effect",
    },
    capabilities={
        "issue_create": "DISCOVERED",
        "pull_request_create": "DISCOVERED",
    },
    retry_policy={"max_attempts": 1, "backoff_s": []},
    target_kinds=("git_remote",),
    idempotency_strategy="effect-key",
    reconciliation_strategy="provider-query",
    supports_cancellation=False,
    cost_metadata={"unit": "github_api_write", "cost_known": True},
    evidence_fields=("idempotency_key", "provider_id", "provider_ref", "readback_status", "claimed"),
)


class GitHubWriteError(ValueError):
    """A write request is outside the bounded GitHub adapter contract."""


class GitHubWriteTransportError(RuntimeError):
    """A transport failed before the provider result was known."""

    def __init__(self, code: str, message: str = "", *, ambiguous: bool = False) -> None:
        super().__init__(message or code)
        self.code = code
        self.ambiguous = ambiguous


def _endpoint_parts(endpoint: str) -> tuple[str, str, str]:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise GitHubWriteError("GitHub write endpoint is required")
    match = _ENDPOINT.fullmatch(endpoint.strip())
    if match is None:
        raise GitHubWriteError(
            "GitHub writes are limited to exact /repos/{owner}/{repo}/issues or /pulls endpoints"
        )
    return match.group(1), match.group(2), match.group(3)


def _payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(payload)).encode("utf-8")).hexdigest()


def github_effect_key(
    action_kind: str, endpoint: str, payload: Mapping[str, Any]
) -> str:
    """Return the canonical external-effect key for one GitHub write."""

    if action_kind not in _WRITE_ACTIONS:
        raise GitHubWriteError(f"undeclared GitHub write action: {action_kind!r}")
    _endpoint_parts(endpoint)
    return external_action_key(
        "github",
        f"{action_kind}:{endpoint}",
        _payload_hash(payload),
    )


def _safe_transport_message(raw: bytes | str) -> str:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return redact_text(text).strip()[:400]


def _classify_cli_error(message: str) -> str:
    lowered = message.lower()
    if "rate limit" in lowered:
        return "rate_limited"
    if "bad credentials" in lowered or "authentication" in lowered or "auth" in lowered:
        return "auth_unavailable"
    if "http 404" in lowered or "not found" in lowered:
        return "http_404"
    match = re.search(r"http\s+(4\d{2}|5\d{2})", lowered)
    if match:
        return f"http_{match.group(1)}"
    return "transport_error"


def gh_api_write_transport(
    endpoint: str,
    payload: Mapping[str, Any],
    *,
    executable: str = "gh",
    timeout_s: float = 30.0,
) -> tuple[int, bytes]:
    """POST JSON through an existing ``gh`` authentication session.

    ``gh api`` does not expose the REST status on its normal successful
    output, so a successful response is represented as status 200; the
    response document still carries the provider id.  The caller validates
    the document before claiming completion.  Private ``_jarvis_*`` metadata
    is deliberately omitted from the provider JSON body.
    """

    _endpoint_parts(endpoint)
    if not isinstance(payload, Mapping):
        raise GitHubWriteError("GitHub write payload must be an object")
    body = {
        str(key): value
        for key, value in payload.items()
        if not str(key).startswith("_jarvis_")
    }
    try:
        json_body = json.dumps(body, ensure_ascii=True, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise GitHubWriteError("GitHub write payload is not JSON serializable") from error
    if len(json_body) > _MAX_RESPONSE_BYTES:
        raise GitHubWriteError("GitHub write payload exceeds the bounded request limit")
    if not (shutil.which(executable) or shutil.which(str(executable))):
        raise GitHubWriteTransportError("gh_unavailable", "GitHub CLI is not available")
    args = [
        executable,
        "api",
        endpoint,
        "--method",
        "POST",
        "--header",
        "Accept: application/vnd.github+json",
        "--header",
        "X-GitHub-Api-Version: 2022-11-28",
        "--input",
        "-",
    ]
    try:
        result = subprocess.run(
            args,
            input=json_body,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GitHubWriteTransportError(
            "timeout", "GitHub write response timed out", ambiguous=True
        ) from error
    except OSError as error:
        raise GitHubWriteTransportError("transport_error", str(error)) from error
    stderr = getattr(result, "stderr", b"") or b""
    if result.returncode != 0:
        message = _safe_transport_message(stderr)
        raise GitHubWriteTransportError(_classify_cli_error(message), message)
    stdout = getattr(result, "stdout", b"") or b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8")
    if len(stdout) > _MAX_RESPONSE_BYTES:
        raise GitHubWriteTransportError("response_too_large", "GitHub response exceeded the bounded limit")
    return 200, bytes(stdout)


@dataclass(frozen=True, slots=True)
class GitHubWriteRequest:
    """Canonical request identity without credential material or raw secrets."""

    action_kind: str
    endpoint: str
    payload: Mapping[str, Any]
    idempotency_key: str
    payload_hash: str
    owner: str
    repo: str
    resource: str

    @classmethod
    def build(
        cls, action_kind: str, endpoint: str, payload: Mapping[str, Any]
    ) -> "GitHubWriteRequest":
        if action_kind not in _WRITE_ACTIONS:
            raise GitHubWriteError(f"undeclared GitHub write action: {action_kind!r}")
        owner, repo, resource = _endpoint_parts(endpoint)
        if not isinstance(payload, Mapping):
            raise GitHubWriteError("GitHub write payload must be an object")
        body = dict(payload)
        digest = _payload_hash(body)
        return cls(
            action_kind=action_kind,
            endpoint=endpoint,
            payload=body,
            idempotency_key=github_effect_key(action_kind, endpoint, body),
            payload_hash=digest,
            owner=owner,
            repo=repo,
            resource=resource,
        )


def _provider_identity(request: GitHubWriteRequest, document: Mapping[str, Any]) -> tuple[str, str, int] | None:
    number = document.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        return None
    provider_id = f"github:{request.resource}:{request.owner}/{request.repo}#{number}"
    candidate = document.get("html_url")
    provider_ref = (
        str(candidate)
        if isinstance(candidate, str) and candidate.startswith("https://github.com/")
        else f"https://github.com/{request.owner}/{request.repo}/{request.resource}/{number}"
    )
    return provider_id, provider_ref, number


def _field_ref(document: Mapping[str, Any], field: str) -> str | None:
    value = document.get(field)
    if isinstance(value, Mapping):
        ref = value.get("ref")
        return str(ref) if isinstance(ref, str) else None
    return str(value) if isinstance(value, str) else None


def _matches_request(request: GitHubWriteRequest, document: Mapping[str, Any]) -> bool:
    identity = _provider_identity(request, document)
    if identity is None:
        return False
    if document.get("title") != request.payload.get("title"):
        return False
    if request.resource == "pulls":
        if _field_ref(document, "head") not in {None, str(request.payload.get("head"))}:
            return False
        if _field_ref(document, "base") not in {None, str(request.payload.get("base"))}:
            return False
    if "body" in document and document.get("body") is not None:
        return document.get("body") == request.payload.get("body", "")
    return True


class GitHubWriteAdapter:
    """Lifecycle adapter for explicit issue/PR writes.

    The adapter is deliberately process-scoped.  Durable effect state remains
    owned by the existing effects/ledger spine; this object only prevents a
    caller from issuing a second provider request while an ambiguous result
    is awaiting explicit readback reconciliation.
    """

    spec = GITHUB_WRITE_SPEC

    def __init__(
        self,
        *,
        transport: Callable[[str, Mapping[str, Any]], tuple[int, bytes]] = gh_api_write_transport,
        read_transport: Callable[[str], tuple[int, bytes]] | None = None,
    ) -> None:
        self.transport = transport
        self.read_transport = read_transport
        self._pending: dict[str, GitHubWriteRequest] = {}
        self._requests: dict[str, GitHubWriteRequest] = {}
        self._completed: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _raw_document(raw: bytes) -> Any:
        if not isinstance(raw, (bytes, bytearray)) or len(raw) > _MAX_RESPONSE_BYTES:
            return None
        try:
            return json.loads(bytes(raw).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _readback(
        self, request: GitHubWriteRequest, document: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self.read_transport is None:
            return {
                "status": "NOT_TESTED",
                "matches": False,
                "reason": "GitHub readback transport is not configured",
            }
        identity = _provider_identity(request, document)
        if identity is None:
            return {"status": "UNKNOWN", "matches": False, "reason": "provider id is missing"}
        endpoint = f"{request.endpoint}/{identity[2]}"
        try:
            status, raw = self.read_transport(endpoint)
        except (TimeoutError, subprocess.TimeoutExpired, OSError) as error:
            return {"status": "UNKNOWN", "matches": False, "reason": type(error).__name__}
        if status != 200:
            return {"status": "UNKNOWN", "matches": False, "reason": f"http_{status}"}
        observed = self._raw_document(raw)
        if not isinstance(observed, Mapping):
            return {"status": "UNKNOWN", "matches": False, "reason": "malformed_response"}
        matches = _matches_request(request, observed)
        return {
            "status": "MATCH" if matches else "MISMATCH",
            "matches": matches,
            "provider_id": identity[0],
            "provider_ref": identity[1],
        }

    @staticmethod
    def _base_result(
        request: GitHubWriteRequest,
        *,
        claimed: str,
        status: int | None,
        error_code: str | None = None,
        response_hash: str | None = None,
        reconciled: bool = False,
        replayed: bool = False,
        duplicate_prevented: bool = False,
    ) -> dict[str, Any]:
        return {
            "claimed": claimed,
            "action_kind": request.action_kind,
            "endpoint": request.endpoint,
            "owner": request.owner,
            "repo": request.repo,
            "resource": request.resource,
            "idempotency_key": request.idempotency_key,
            "payload_hash": request.payload_hash,
            "response_hash": response_hash,
            "status": status,
            "number": None,
            "provider_id": None,
            "provider_ref": None,
            "html_url": None,
            "readback_status": "NOT_TESTED",
            "readback": None,
            "reconciliation_required": False,
            "reconciled": reconciled,
            "replayed": replayed,
            "duplicate_prevented": duplicate_prevented,
            "error_code": error_code,
            "title": request.payload.get("title"),
        }

    def _from_document(
        self,
        request: GitHubWriteRequest,
        document: Mapping[str, Any],
        *,
        status: int,
        raw: bytes,
        reconciled: bool = False,
    ) -> dict[str, Any]:
        identity = _provider_identity(request, document)
        if identity is None:
            result = self._base_result(
                request,
                claimed="FAILED",
                status=status,
                error_code="missing_provider_id",
                response_hash=hashlib.sha256(raw).hexdigest(),
            )
            return result
        result = self._base_result(
            request,
            claimed="COMPLETED",
            status=status,
            response_hash=hashlib.sha256(raw).hexdigest(),
            reconciled=reconciled,
        )
        result.update({
            "number": identity[2],
            "provider_id": identity[0],
            "provider_ref": identity[1],
            "html_url": identity[1],
            "head": request.payload.get("head"),
            "base": request.payload.get("base"),
        })
        readback = self._readback(request, document)
        result["readback"] = readback
        result["readback_status"] = readback["status"]
        result["reconciliation_required"] = readback["status"] != "MATCH"
        return result

    def write(
        self,
        action_kind: str,
        endpoint: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request = GitHubWriteRequest.build(action_kind, endpoint, payload)
        key = request.idempotency_key
        self._requests[key] = request
        prior = self._completed.get(key)
        if prior is not None:
            return {
                **prior,
                "replayed": True,
                "duplicate_prevented": True,
            }
        if key in self._pending:
            result = self._base_result(
                request,
                claimed="UNKNOWN",
                status=None,
                error_code="pending_reconciliation",
                duplicate_prevented=True,
            )
            result["reconciliation_required"] = True
            return result
        self._pending[key] = request
        try:
            status, raw = self.transport(request.endpoint, dict(request.payload))
        except GitHubWriteTransportError as error:
            if not error.ambiguous:
                self._pending.pop(key, None)
            result = self._base_result(
                request,
                claimed="UNKNOWN" if error.ambiguous else "FAILED",
                status=None,
                error_code=error.code,
            )
            result["reconciliation_required"] = error.ambiguous
            return result
        except (TimeoutError, subprocess.TimeoutExpired, OSError):
            result = self._base_result(
                request,
                claimed="UNKNOWN",
                status=None,
                error_code="timeout",
            )
            result["reconciliation_required"] = True
            return result
        if not isinstance(raw, (bytes, bytearray)):
            self._pending.pop(key, None)
            return self._base_result(
                request,
                claimed="FAILED",
                status=status,
                error_code="malformed_response",
            )
        if not 200 <= int(status) < 300:
            self._pending.pop(key, None)
            return self._base_result(
                request,
                claimed="FAILED",
                status=int(status),
                error_code=f"http_{status}",
                response_hash=hashlib.sha256(bytes(raw)).hexdigest(),
            )
        document = self._raw_document(bytes(raw))
        if not isinstance(document, Mapping):
            self._pending.pop(key, None)
            return self._base_result(
                request,
                claimed="FAILED",
                status=int(status),
                error_code="malformed_response",
                response_hash=hashlib.sha256(bytes(raw)).hexdigest(),
            )
        self._pending.pop(key, None)
        result = self._from_document(request, document, status=int(status), raw=bytes(raw))
        if result["claimed"] == "COMPLETED":
            self._completed[key] = dict(result)
        return result

    def _reconcile_collection(self, request: GitHubWriteRequest) -> dict[str, Any]:
        if self.read_transport is None:
            result = self._base_result(
                request,
                claimed="UNKNOWN",
                status=None,
                error_code="readback_not_configured",
            )
            result["reconciliation_required"] = True
            return result
        endpoint = f"{request.endpoint}?state=all&per_page=100"
        try:
            status, raw = self.read_transport(endpoint)
        except (TimeoutError, OSError):
            result = self._base_result(
                request, claimed="UNKNOWN", status=None, error_code="timeout"
            )
            result["reconciliation_required"] = True
            return result
        if status != 200:
            result = self._base_result(
                request, claimed="UNKNOWN", status=status, error_code=f"http_{status}"
            )
            result["reconciliation_required"] = True
            return result
        value = self._raw_document(raw)
        if not isinstance(value, list):
            result = self._base_result(
                request, claimed="UNKNOWN", status=status, error_code="malformed_response"
            )
            result["reconciliation_required"] = True
            return result
        matches = [item for item in value if isinstance(item, Mapping) and _matches_request(request, item)]
        if len(matches) != 1:
            result = self._base_result(
                request,
                claimed="UNKNOWN" if not matches else "FAILED",
                status=status,
                error_code="not_observed" if not matches else "duplicate_provider_matches",
                response_hash=hashlib.sha256(bytes(raw)).hexdigest(),
                reconciled=False,
                duplicate_prevented=bool(matches),
            )
            result["reconciliation_required"] = True
            return result
        result = self._from_document(
            request,
            matches[0],
            status=status,
            raw=canonical_json(matches[0]).encode("utf-8"),
            reconciled=True,
        )
        result["reconciliation_required"] = result["readback_status"] != "MATCH"
        if result["claimed"] == "COMPLETED":
            self._pending.pop(request.idempotency_key, None)
            self._completed[request.idempotency_key] = dict(result)
        return result

    def reconcile(self, idempotency_key: str) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise GitHubWriteError("idempotency_key is required for reconciliation")
        prior = self._completed.get(idempotency_key)
        request = self._requests.get(idempotency_key)
        if request is None:
            return {
                "claimed": "UNKNOWN",
                "idempotency_key": idempotency_key,
                "error_code": "unknown_effect_key",
                "reconciliation_required": True,
            }
        if prior is not None and prior.get("readback_status") == "MATCH":
            return {**prior, "reconciled": True, "replayed": True}
        if prior is not None and prior.get("number"):
            # A provider id is known, so item readback is safer than a broad
            # collection query and cannot accidentally select a duplicate.
            if self.read_transport is None:
                result = dict(prior)
                result.update({
                    "claimed": "UNKNOWN",
                    "error_code": "readback_not_configured",
                    "reconciliation_required": True,
                })
                return result
            endpoint = f"{request.endpoint}/{prior['number']}"
            try:
                status, raw = self.read_transport(endpoint)
            except (TimeoutError, subprocess.TimeoutExpired, OSError):
                result = dict(prior)
                result.update({
                    "claimed": "UNKNOWN",
                    "error_code": "timeout",
                    "reconciliation_required": True,
                })
                return result
            document = self._raw_document(raw) if status == 200 else None
            if document is None:
                result = dict(prior)
                result.update({
                    "claimed": "UNKNOWN",
                    "error_code": f"http_{status}" if status != 200 else "malformed_response",
                    "reconciliation_required": True,
                })
                return result
            readback = self._readback(request, document)
            result = dict(prior)
            result.update({
                "claimed": "COMPLETED" if readback["status"] == "MATCH" else "UNKNOWN",
                "readback": readback,
                "readback_status": readback["status"],
                "reconciled": True,
                "reconciliation_required": readback["status"] != "MATCH",
                "error_code": None if readback["status"] == "MATCH" else "readback_mismatch",
            })
            if result["readback_status"] == "MATCH":
                self._completed[idempotency_key] = dict(result)
            return result
        return self._reconcile_collection(request)

    def __call__(self, endpoint: str, payload: Mapping[str, Any]) -> tuple[int, bytes]:
        """Expose the legacy callback shape for explicit seam injection."""

        return self.transport(endpoint, payload)


__all__ = [
    "GitHubWriteAdapter",
    "GitHubWriteError",
    "GitHubWriteRequest",
    "GitHubWriteTransportError",
    "GITHUB_WRITE_SPEC",
    "gh_api_write_transport",
    "github_effect_key",
]
