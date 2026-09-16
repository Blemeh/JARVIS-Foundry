"""Bounded GitHub write transport behind the existing ``github_write`` seam.

The read-only GitHub connector remains the authority for research. This module
owns the narrow issue/pull-request REST transport and extends it with an
owner-bound branch/push/PR driver over the canonical effect journal:

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
from pathlib import Path
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
from ..systems import external_action_key
from .contract import ConnectorSpec
from .git import GitConnector

_ENDPOINT = re.compile(r"^/repos/([^/\s]+)/([^/\s]+)/(issues|pulls)$")
_MAX_RESPONSE_BYTES = 256 * 1024
_WRITE_ACTIONS = frozenset({
    "github.issue.create",
    "github.pull_request.create",
})
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

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


def github_write_intent(
    action_kind: str, payload: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Map one canonical Fabric intent to its exact provider request.

    The mapping is shared by dispatch and recovery so a replayed intent can
    never drift from the provider payload that the original effect used.
    The returned provider payload is what crosses the wire; owner-supplied
    text is copied verbatim and never rewritten.
    """

    if not isinstance(payload, Mapping):
        raise GitHubWriteError("GitHub write payload must be an object")
    if action_kind == "github.issue.create":
        owner = str(payload["owner"])
        repo = str(payload["repo"])
        return f"/repos/{owner}/{repo}/issues", {
            "title": str(payload["title"]),
            "body": str(payload.get("body") or ""),
        }
    if action_kind == "github.pull_request.create":
        owner = str(payload["owner"])
        repo = str(payload["repo"])
        return f"/repos/{owner}/{repo}/pulls", {
            "title": str(payload["title"]),
            "head": str(payload["head"]),
            "base": str(payload.get("base") or "main"),
            "body": str(payload.get("body") or ""),
        }
    raise GitHubWriteError(f"undeclared GitHub write action: {action_kind!r}")


def github_write_input_hash(payload: Mapping[str, Any]) -> str:
    """Hash one canonical Fabric intent exactly as Fabric identity does.

    ``_EXECUTION_CONTROL_FIELDS`` are the runtime controls Fabric removes
    before deriving an effect identity; importing the canonical set keeps
    recovery bound to the same hash instead of a second, drifting copy.
    """

    from ..fabric.contracts import _EXECUTION_CONTROL_FIELDS

    filtered = {
        str(key): value
        for key, value in payload.items()
        if str(key) not in _EXECUTION_CONTROL_FIELDS
    }
    return hashlib.sha256(canonical_json(filtered).encode("utf-8")).hexdigest()


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
        code = _classify_cli_error(message)
        raise GitHubWriteTransportError(
            code,
            message,
            ambiguous=code == "transport_error" or code.startswith("http_5"),
        )
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
        dispatch_enabled: bool = False,
        authorized_repositories: tuple[str, ...] = (),
    ) -> None:
        self.transport = transport
        self.read_transport = read_transport
        self.dispatch_enabled = bool(dispatch_enabled)
        normalized: set[str] = set()
        for repository in authorized_repositories:
            candidate = str(repository).strip()
            if not _REPOSITORY.fullmatch(candidate):
                raise GitHubWriteError(
                    "authorized GitHub repositories must use exact owner/repo identities"
                )
            normalized.add(candidate.casefold())
        self.authorized_repositories = frozenset(normalized)
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
        repository = f"{request.owner}/{request.repo}".casefold()
        if not self.dispatch_enabled:
            return self._base_result(
                request,
                claimed="BLOCKED",
                status=None,
                error_code="github_writes_disabled",
            )
        if repository not in self.authorized_repositories:
            return self._base_result(
                request,
                claimed="BLOCKED",
                status=None,
                error_code="repository_not_authorized",
            )
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
            ambiguous = int(status) >= 500
            if not ambiguous:
                self._pending.pop(key, None)
            result = self._base_result(
                request,
                claimed="UNKNOWN" if ambiguous else "FAILED",
                status=int(status),
                error_code=f"http_{status}",
                response_hash=hashlib.sha256(bytes(raw)).hexdigest(),
            )
            result["reconciliation_required"] = ambiguous
            return result
        document = self._raw_document(bytes(raw))
        if not isinstance(document, Mapping):
            result = self._base_result(
                request,
                claimed="UNKNOWN",
                status=int(status),
                error_code="malformed_response",
                response_hash=hashlib.sha256(bytes(raw)).hexdigest(),
            )
            result["reconciliation_required"] = True
            return result
        self._pending.pop(key, None)
        result = self._from_document(request, document, status=int(status), raw=bytes(raw))
        if result.get("error_code") == "missing_provider_id":
            result["claimed"] = "UNKNOWN"
            result["reconciliation_required"] = True
            self._pending[key] = request
            return result
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

    def reconcile_request(
        self,
        action_kind: str,
        endpoint: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Reconcile a deterministic request after process-local state was lost.

        Reconciliation is read-only and remains available while dispatch is
        disabled or STOP is active.  The caller must still supply the exact
        canonical request, which reconstructs the same effect key.
        """

        request = GitHubWriteRequest.build(action_kind, endpoint, payload)
        repository = f"{request.owner}/{request.repo}".casefold()
        if repository not in self.authorized_repositories:
            result = self._base_result(
                request,
                claimed="BLOCKED",
                status=None,
                error_code="repository_not_authorized",
            )
            result["reconciliation_required"] = True
            return result
        self._requests[request.idempotency_key] = request
        return self._reconcile_collection(request)

    def observe_request(
        self,
        action_kind: str,
        endpoint: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Read provider truth for one exact request. Never writes.

        Unlike :meth:`reconcile_request` this read-only seam carries no write
        authorization requirement: recovery must stay possible after the
        original dispatch authorization was revoked or the process died.
        """

        request = GitHubWriteRequest.build(action_kind, endpoint, payload)
        return self._reconcile_collection(request)

    def __call__(self, endpoint: str, payload: Mapping[str, Any]) -> tuple[int, bytes]:
        """Expose the legacy callback shape for explicit seam injection."""

        return self.transport(endpoint, payload)


def _recovery_result(
    action_key: str,
    *,
    claimed: str,
    error_code: str | None,
    reconciliation_required: bool,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "claimed": claimed,
        "action_key": action_key,
        "original_effect_id": action_key,
        "performed": False,
        "replayed": False,
        "reconciled": False,
        "reconciliation_required": reconciliation_required,
        "error_code": error_code,
        **dict(extra or {}),
    }


def reconcile_github_write_effect(
    database: FoundryDatabase,
    *,
    action_key: str,
    adapter: GitHubWriteAdapter,
    action_kind: str,
    endpoint: str,
    request_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconcile one journaled GitHub write effect against provider truth.

    Recovery seam for ``execute_fabric_effect`` entries whose provider result
    was lost to a crash, timeout, or ambiguous transport.  The caller must
    re-supply the exact canonical Fabric intent; its input hash is checked
    against the durable journal identity before any provider read, and no
    provider write is ever issued here.

    Outcomes:

    * an accepted effect replays its durable terminal truth without any read;
    * a stale ``STARTED`` claim is frozen (never re-performed) and read back;
    * exactly one provider object matching the intent finalizes the original
      effect through :func:`finalize_verified_effect` with an independent
      MATCH readback and a durable provider receipt;
    * everything else stays ``UNKNOWN`` with ``reconciliation_required``.
    """

    if not isinstance(action_key, str) or not action_key.strip():
        raise GitHubWriteError("action_key is required for GitHub write reconciliation")
    if action_kind not in _WRITE_ACTIONS:
        return _recovery_result(
            action_key, claimed="BLOCKED",
            error_code="undeclared_write_action", reconciliation_required=False,
        )
    try:
        _, provider_payload = github_write_intent(action_kind, request_payload)
    except (KeyError, TypeError, GitHubWriteError):
        return _recovery_result(
            action_key, claimed="BLOCKED",
            error_code="invalid_request_payload", reconciliation_required=False,
        )
    GitHubWriteRequest.build(action_kind, endpoint, provider_payload)

    state = effect_state(database, action_key)
    status = state.get("status")
    if status == "UNKNOWN":
        return _recovery_result(
            action_key, claimed="UNKNOWN",
            error_code="effect_not_recorded", reconciliation_required=True,
        )
    if status == STARTED:
        if state.get("claim_active"):
            return _recovery_result(
                action_key, claimed="UNKNOWN",
                error_code="effect_claim_active", reconciliation_required=True,
            )
        freeze_effect(
            database, action_key,
            reason="interrupted GitHub write requires provider readback",
        )
        state = effect_state(database, action_key)
        status = state.get("status")
    if status not in {COMPLETED, FAILED, FROZEN}:
        return _recovery_result(
            action_key, claimed="UNKNOWN",
            error_code="effect_not_terminal", reconciliation_required=True,
        )

    identity = state.get("request_identity")
    identity = identity if isinstance(identity, Mapping) else {}
    if str(identity.get("action_kind") or "") != action_kind:
        return _recovery_result(
            action_key, claimed="BLOCKED",
            error_code="request_identity_mismatch", reconciliation_required=False,
        )
    try:
        observed_hash = github_write_input_hash(request_payload)
    except (TypeError, ValueError):
        return _recovery_result(
            action_key, claimed="BLOCKED",
            error_code="request_payload_not_serializable",
            reconciliation_required=False,
        )
    if observed_hash != identity.get("input_hash"):
        return _recovery_result(
            action_key, claimed="BLOCKED",
            error_code="request_identity_mismatch", reconciliation_required=False,
        )

    if status == FAILED:
        return _recovery_result(
            action_key, claimed="FAILED",
            error_code="effect_failed", reconciliation_required=False,
        )

    if status == COMPLETED and state.get("accepted") is True:
        # Durable terminal truth already exists; replay it without a provider
        # read so a later network failure cannot downgrade an accepted effect.
        return {
            "claimed": "COMPLETED",
            "action_key": action_key,
            "original_effect_id": action_key,
            "journal_state": COMPLETED,
            "accepted": True,
            "terminal_truth": True,
            "reconciled": True,
            "performed": False,
            "replayed": True,
            "reconciliation_required": False,
            "readback_status": "MATCH",
            "readback": {
                "status": "MATCH",
                "matches": True,
                "source": "durable accepted terminal truth",
            },
            "error_code": None,
        }

    observed = adapter.observe_request(action_kind, endpoint, provider_payload)
    readback = observed.get("readback")
    provider_id = observed.get("provider_id")
    provider_ref = observed.get("provider_ref")
    if (
        observed.get("claimed") != COMPLETED
        or observed.get("readback_status") != "MATCH"
        or not isinstance(readback, Mapping)
        or readback.get("matches") is not True
        or not provider_id
        or not provider_ref
    ):
        return {
            **dict(observed),
            "action_key": action_key,
            "original_effect_id": action_key,
            "journal_state": status,
            "performed": False,
            "replayed": False,
            "reconciled": False,
            "reconciliation_required": True,
        }

    receipt = {
        "provider_id": str(provider_id),
        "provider_ref": str(provider_ref),
        "number": observed.get("number"),
        "owner": observed.get("owner"),
        "repo": observed.get("repo"),
        "resource": observed.get("resource"),
        "html_url": observed.get("html_url"),
    }
    result = {
        **dict(observed),
        "action_key": action_key,
        "original_effect_id": action_key,
        "reconciled": True,
        "performed": False,
        "replayed": False,
    }
    return finalize_verified_effect(
        database,
        action_key,
        provider_receipt=receipt,
        readback=readback,
        result=result,
        acceptance_evidence={
            "criterion": "exactly one provider object matches the exact intent",
            "original_effect_id": action_key,
            "recovered_from_journal_state": status,
        },
        reconciled=True,
    )


@dataclass(frozen=True, slots=True)
class GitHubRepositoryAuthorization:
    """Exact owner grant for one repository and bounded PR bases."""

    authorization_id: str
    repository: str
    remote: str
    allowed_base_branches: tuple[str, ...] = ("main",)
    owner_authorized: bool = False
    dispatch_enabled: bool = False
    capability_trust: str = "ESTABLISHED"

    def __post_init__(self) -> None:
        if not isinstance(self.authorization_id, str) or not self.authorization_id.strip():
            raise GitHubWriteError("GitHub authorization_id is required")
        repository = str(self.repository).strip()
        if not _REPOSITORY.fullmatch(repository):
            raise GitHubWriteError("GitHub repository must use exact owner/repo identity")
        if not isinstance(self.remote, str) or not self.remote.strip():
            raise GitHubWriteError("GitHub authorized remote is required")
        bases = tuple(str(item).strip() for item in self.allowed_base_branches)
        if not bases or any(not _BRANCH.fullmatch(item) for item in bases):
            raise GitHubWriteError("GitHub allowed base branches are invalid")
        object.__setattr__(self, "authorization_id", self.authorization_id.strip())
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "remote", self.remote.strip())
        object.__setattr__(self, "allowed_base_branches", bases)
        object.__setattr__(self, "capability_trust", str(self.capability_trust).upper())

    def scope_refusal(
        self, *, repository: str, remote: str, base: str,
    ) -> str | None:
        if repository.casefold() != self.repository.casefold():
            return "repository_not_authorized"
        if str(remote) != self.remote:
            return "remote_not_authorized"
        if base not in self.allowed_base_branches:
            return "base_branch_not_authorized"
        return None

    def refusal(
        self, *, repository: str, remote: str, base: str,
    ) -> str | None:
        scope = self.scope_refusal(
            repository=repository, remote=remote, base=base,
        )
        if scope:
            return scope
        if self.capability_trust == "PROBATION":
            return "probation_capability_irreversible_effect_forbidden"
        if not self.owner_authorized:
            return "owner_authorization_required"
        if not self.dispatch_enabled:
            return "github_writes_disabled"
        return None


@dataclass(frozen=True, slots=True)
class GitHubPullRequestPlan:
    """Stable branch, push, and PR identities for one owner intent."""

    owner: str
    repo: str
    remote: str
    base: str
    head_sha: str
    title: str
    body: str
    branch: str
    intent_id: str
    branch_effect_id: str
    push_effect_id: str
    pull_request_effect_id: str
    authorization_id: str | None = None

    @classmethod
    def build(
        cls,
        *,
        owner: str,
        repo: str,
        remote: str,
        base: str,
        head_sha: str,
        title: str,
        body: str = "",
        effect_id: str | None = None,
        authorization_id: str | None = None,
    ) -> "GitHubPullRequestPlan":
        repository = f"{str(owner).strip()}/{str(repo).strip()}"
        if not _REPOSITORY.fullmatch(repository):
            raise GitHubWriteError("GitHub owner/repo identity is invalid")
        base_value = str(base).strip()
        if not _BRANCH.fullmatch(base_value):
            raise GitHubWriteError("GitHub base branch is invalid")
        sha = str(head_sha).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise GitHubWriteError("GitHub head_sha must be a full Git object id")
        title_value = str(title).strip()
        if not title_value or len(title_value) > 256:
            raise GitHubWriteError("GitHub pull request title is required and bounded")
        body_value = str(body)
        if len(body_value.encode("utf-8")) > 64 * 1024:
            raise GitHubWriteError("GitHub pull request body exceeds the bounded limit")
        identity_material = {
            "repository": repository.casefold(),
            "base": base_value,
            "head_sha": sha,
            "title": title_value,
            "body_hash": hashlib.sha256(body_value.encode("utf-8")).hexdigest(),
        }
        digest = hashlib.sha256(
            canonical_json(identity_material).encode("utf-8")
        ).hexdigest()
        branch = f"jarvis/effect-{digest[:16]}"
        root_effect = effect_id or fabric_effect_id(
            system_id="github",
            target_ref=repository,
            capability_id="cap.github.pull_request.publish",
            capability_version="1.0.0",
            action_kind="github.pull_request.publish",
            input_payload=identity_material,
        )
        branch_effect = fabric_effect_id(
            system_id="git",
            target_ref=branch,
            capability_id="cap.git.branch",
            capability_version="1.0.0",
            action_kind="git.branch",
            input_payload={"branch": branch, "head_sha": sha},
        )
        push_effect = fabric_effect_id(
            system_id="github",
            target_ref=f"{repository}:{branch}",
            capability_id="cap.git.push",
            capability_version="1.0.0",
            action_kind="git.push",
            input_payload={
                "repository": repository.casefold(),
                "remote": str(remote),
                "branch": branch,
                "head_sha": sha,
            },
        )
        return cls(
            owner=repository.split("/", 1)[0],
            repo=repository.split("/", 1)[1],
            remote=str(remote),
            base=base_value,
            head_sha=sha,
            title=title_value,
            body=body_value,
            branch=branch,
            intent_id=f"github-pr-intent:{digest}",
            branch_effect_id=branch_effect,
            push_effect_id=push_effect,
            pull_request_effect_id=str(root_effect),
            authorization_id=(
                str(authorization_id).strip() if authorization_id else None
            ),
        )


GitPushTransport = Callable[[Path, str, str, str], None]


class GitHubPullRequestEffectDriver:
    """Owner-bound branch/push/PR driver over the canonical effect journal."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        owned_root: str | Path,
        adapter: GitHubWriteAdapter,
        push_transport: GitPushTransport | None = None,
    ) -> None:
        self.database = database
        self.git = GitConnector(database, owned_root=owned_root)
        self.adapter = adapter
        self.push_transport = push_transport or self._default_push

    @staticmethod
    def _run_git(worktree: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise GitHubWriteTransportError(
                "git_error", redact_text(result.stderr.strip() or "git command failed")
            )
        return result.stdout.strip()

    @classmethod
    def _default_push(
        cls, worktree: Path, remote: str, branch: str, head_sha: str,
    ) -> None:
        try:
            result = subprocess.run(
                [
                    "git", "-C", str(worktree), "push", remote,
                    f"{head_sha}:refs/heads/{branch}",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("GitHub branch push timed out") from error
        if result.returncode != 0:
            message = redact_text(result.stderr.strip() or "git push failed")
            lowered = message.lower()
            definite_rejection = any(marker in lowered for marker in (
                "[rejected]", "non-fast-forward", "permission denied",
                "authentication failed", "repository not found",
            ))
            if definite_rejection:
                raise GitHubWriteTransportError("push_rejected", message)
            raise TimeoutError(
                "GitHub branch push ended without a definitive provider response"
            )

    @staticmethod
    def _blocked(plan: GitHubPullRequestPlan, code: str) -> dict[str, Any]:
        return {
            "claimed": "BLOCKED",
            "performed": False,
            "intent_id": plan.intent_id,
            "branch": plan.branch,
            "effect_id": plan.pull_request_effect_id,
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

    def _ensure_branch(
        self, plan: GitHubPullRequestPlan, worktree: Path,
        *, stop_path: str | None, mission_id: str | None,
    ) -> dict[str, Any]:
        def perform() -> Mapping[str, Any]:
            current = self._run_git(
                worktree, "show-ref", "--verify", "--hash",
                f"refs/heads/{plan.branch}",
            ) if self._branch_exists(worktree, plan.branch) else None
            if current is not None and current != plan.head_sha:
                raise GitHubWriteError(
                    "deterministic GitHub effect branch exists at a different head"
                )
            if current is None:
                self._run_git(worktree, "branch", plan.branch, plan.head_sha)
            observed = self._run_git(worktree, "rev-parse", plan.branch)
            return {
                "claimed": "COMPLETED" if observed == plan.head_sha else "FAILED",
                "performed": current is None,
                "branch": plan.branch,
                "head_sha": observed,
            }

        return execute_fabric_effect(
            self.database,
            action_key=plan.branch_effect_id,
            system_id="git",
            target_ref=str(worktree),
            payload_hash=plan.branch_effect_id.removeprefix("effect:"),
            kind="git.branch",
            connector_id="conn-git-v5",
            expected_effects=(f"local branch {plan.branch} at {plan.head_sha}",),
            request_identity={
                "intent_id": plan.intent_id,
                "branch": plan.branch,
                "head_sha": plan.head_sha,
                "authorization_id": plan.authorization_id,
            },
            perform=perform,
            stop_check=lambda: self._stopped(stop_path, mission_id),
        )

    @classmethod
    def _branch_exists(cls, worktree: Path, branch: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(worktree), "show-ref", "--verify", "--quiet",
             f"refs/heads/{branch}"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0

    @staticmethod
    def _push_receipt(plan: GitHubPullRequestPlan) -> dict[str, Any]:
        return {
            "provider_id": f"github:ref:{plan.owner}/{plan.repo}:{plan.branch}",
            "provider_ref": (
                f"https://github.com/{plan.owner}/{plan.repo}/tree/{plan.branch}"
            ),
            "repository": f"{plan.owner}/{plan.repo}",
            "branch": plan.branch,
            "head_sha": plan.head_sha,
        }

    def _push_readback(self, plan: GitHubPullRequestPlan) -> dict[str, Any]:
        try:
            observed = self.git.read_ref(plan.remote, plan.branch)
        except Exception as error:  # noqa: BLE001 - readback remains UNKNOWN
            return {
                "status": "UNKNOWN", "matches": False,
                "reason": type(error).__name__,
            }
        return {
            "status": "MATCH" if observed == plan.head_sha else "NOT_OBSERVED",
            "matches": observed == plan.head_sha,
            "observed_head": observed,
            "expected_head": plan.head_sha,
            "source": "git ls-remote",
        }

    def _finalize_push(
        self, plan: GitHubPullRequestPlan, *, reconciled: bool,
    ) -> dict[str, Any] | None:
        readback = self._push_readback(plan)
        if readback.get("matches") is not True:
            return None
        receipt = self._push_receipt(plan)
        result = {
            "claimed": "COMPLETED",
            "performed": not reconciled,
            "intent_id": plan.intent_id,
            "repository": f"{plan.owner}/{plan.repo}",
            "branch": plan.branch,
            "remote_head": plan.head_sha,
            "provider_id": receipt["provider_id"],
            "provider_ref": receipt["provider_ref"],
            "readback": readback,
            "readback_status": "MATCH",
        }
        return finalize_verified_effect(
            self.database,
            plan.push_effect_id,
            provider_receipt=receipt,
            readback=readback,
            result=result,
            acceptance_evidence={
                "intent_id": plan.intent_id,
                "criterion": "remote branch resolves to exact head SHA",
                "authorization_id": plan.authorization_id,
            },
            reconciled=reconciled,
        )

    def _push(
        self, plan: GitHubPullRequestPlan, worktree: Path,
        *, stop_path: str | None, mission_id: str | None,
    ) -> dict[str, Any]:
        state = effect_state(self.database, plan.push_effect_id)
        if state.get("status") == STARTED and state.get("claim_active"):
            return {
                "claimed": "UNKNOWN",
                "performed": False,
                "action_key": plan.push_effect_id,
                "journal_state": STARTED,
                "intent_id": plan.intent_id,
                "branch": plan.branch,
                "reconciliation_required": True,
                "error_code": "effect_claim_active",
            }
        if state.get("status") == STARTED:
            freeze_effect(
                self.database, plan.push_effect_id,
                reason="interrupted GitHub branch push requires provider readback",
            )
            state = effect_state(self.database, plan.push_effect_id)
        if state.get("status") in {COMPLETED, FROZEN}:
            reconciled = self._finalize_push(plan, reconciled=True)
            if reconciled is not None:
                reconciled.update({"performed": False, "replayed": True})
                return reconciled
            return {
                "claimed": "UNKNOWN",
                "performed": False,
                "action_key": plan.push_effect_id,
                "journal_state": state["status"],
                "intent_id": plan.intent_id,
                "branch": plan.branch,
                "reconciliation_required": True,
                "error_code": "remote_branch_not_observed",
            }
        if state.get("status") == FAILED:
            return execute_fabric_effect(
                self.database,
                action_key=plan.push_effect_id,
                system_id="github",
                target_ref=f"{plan.owner}/{plan.repo}:{plan.branch}",
                payload_hash=plan.push_effect_id.removeprefix("effect:"),
                kind="git.push",
                connector_id="conn-git-v5",
                perform=lambda: {},
            )

        def perform() -> Mapping[str, Any]:
            self.push_transport(worktree, plan.remote, plan.branch, plan.head_sha)
            readback = self._push_readback(plan)
            receipt = self._push_receipt(plan)
            if readback.get("matches") is not True:
                return {
                    "claimed": "UNKNOWN",
                    "performed": True,
                    "intent_id": plan.intent_id,
                    "provider_id": receipt["provider_id"],
                    "provider_ref": receipt["provider_ref"],
                    "readback": readback,
                    "readback_status": readback.get("status"),
                    "reconciliation_required": True,
                    "error_code": "remote_branch_not_observed",
                }
            return {
                "claimed": "COMPLETED",
                "performed": True,
                "intent_id": plan.intent_id,
                "provider_id": receipt["provider_id"],
                "provider_ref": receipt["provider_ref"],
                "branch": plan.branch,
                "remote_head": plan.head_sha,
                "readback": readback,
                "readback_status": "MATCH",
            }

        result = execute_fabric_effect(
            self.database,
            action_key=plan.push_effect_id,
            system_id="github",
            target_ref=f"{plan.owner}/{plan.repo}:{plan.branch}",
            payload_hash=plan.push_effect_id.removeprefix("effect:"),
            kind="git.push",
            connector_id="conn-git-v5",
            expected_effects=(f"remote branch {plan.branch} at {plan.head_sha}",),
            request_identity={
                "intent_id": plan.intent_id,
                "repository": f"{plan.owner}/{plan.repo}",
                "branch": plan.branch,
                "head_sha": plan.head_sha,
                "authorization_id": plan.authorization_id,
            },
            perform=perform,
            stop_check=lambda: self._stopped(stop_path, mission_id),
        )
        if result.get("claimed") == COMPLETED:
            finalized = self._finalize_push(plan, reconciled=False)
            if finalized is not None:
                finalized["performed"] = bool(result.get("performed"))
                return finalized
        return result

    @staticmethod
    def _pr_payload(plan: GitHubPullRequestPlan) -> dict[str, Any]:
        return {
            "title": plan.title,
            "head": plan.branch,
            "base": plan.base,
            "body": plan.body,
        }

    def _finalize_pr(
        self,
        plan: GitHubPullRequestPlan,
        result: Mapping[str, Any],
        *,
        reconciled: bool,
    ) -> dict[str, Any] | None:
        readback = result.get("readback")
        if not isinstance(readback, Mapping) or readback.get("matches") is not True:
            return None
        provider_id = result.get("provider_id")
        provider_ref = result.get("provider_ref")
        if not provider_id or not provider_ref:
            return None
        receipt = {
            "provider_id": provider_id,
            "provider_ref": provider_ref,
            "number": result.get("number"),
            "repository": f"{plan.owner}/{plan.repo}",
            "branch": plan.branch,
        }
        safe_result = {
            **dict(result),
            "intent_id": plan.intent_id,
            "effect_id": plan.pull_request_effect_id,
        }
        return finalize_verified_effect(
            self.database,
            plan.pull_request_effect_id,
            provider_receipt=receipt,
            readback=readback,
            result=safe_result,
            acceptance_evidence={
                "intent_id": plan.intent_id,
                "criterion": "pull request fields independently match intent",
                "authorization_id": plan.authorization_id,
            },
            reconciled=reconciled,
        )

    def _pull_request(
        self, plan: GitHubPullRequestPlan,
        *, stop_path: str | None, mission_id: str | None,
    ) -> dict[str, Any]:
        endpoint = f"/repos/{plan.owner}/{plan.repo}/pulls"
        payload = self._pr_payload(plan)
        state = effect_state(self.database, plan.pull_request_effect_id)
        if state.get("status") == STARTED and state.get("claim_active"):
            return {
                "claimed": "UNKNOWN",
                "performed": False,
                "action_key": plan.pull_request_effect_id,
                "journal_state": STARTED,
                "intent_id": plan.intent_id,
                "reconciliation_required": True,
                "error_code": "effect_claim_active",
            }
        if state.get("status") == STARTED:
            freeze_effect(
                self.database, plan.pull_request_effect_id,
                reason="interrupted GitHub PR dispatch requires provider readback",
            )
            state = effect_state(self.database, plan.pull_request_effect_id)
        if state.get("status") in {COMPLETED, FROZEN}:
            observed = self.adapter.reconcile_request(
                "github.pull_request.create", endpoint, payload,
            )
            finalized = self._finalize_pr(
                plan, observed, reconciled=True,
            )
            if finalized is not None:
                finalized.update({"performed": False, "replayed": True})
                return finalized
            return {
                **dict(observed),
                "claimed": "UNKNOWN",
                "performed": False,
                "action_key": plan.pull_request_effect_id,
                "journal_state": state["status"],
                "intent_id": plan.intent_id,
                "reconciliation_required": True,
            }

        def perform() -> Mapping[str, Any]:
            result = self.adapter.write(
                "github.pull_request.create", endpoint, payload,
            )
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
            action_key=plan.pull_request_effect_id,
            system_id="github",
            target_ref=f"{plan.owner}/{plan.repo}",
            payload_hash=plan.pull_request_effect_id.removeprefix("effect:"),
            kind="github.pull_request.create",
            connector_id=self.adapter.spec.connector_id,
            expected_effects=(
                f"pull request from {plan.branch} to {plan.base}",
            ),
            request_identity={
                "intent_id": plan.intent_id,
                "repository": f"{plan.owner}/{plan.repo}",
                "branch": plan.branch,
                "base": plan.base,
                "head_sha": plan.head_sha,
            },
            perform=perform,
            stop_check=lambda: self._stopped(stop_path, mission_id),
        )
        if result.get("claimed") == COMPLETED:
            finalized = self._finalize_pr(plan, result, reconciled=False)
            if finalized is not None:
                finalized["performed"] = bool(result.get("performed"))
                return finalized
        return result

    def publish(
        self,
        *,
        authorization: GitHubRepositoryAuthorization,
        worktree: str | Path,
        owner: str,
        repo: str,
        remote: str,
        base: str,
        head_sha: str,
        title: str,
        body: str = "",
        effect_id: str | None = None,
        mission_id: str | None = None,
        stop_path: str | None = None,
    ) -> dict[str, Any]:
        """Create the deterministic branch, push once, and open one PR."""

        plan = GitHubPullRequestPlan.build(
            owner=owner,
            repo=repo,
            remote=remote,
            base=base,
            head_sha=head_sha,
            title=title,
            body=body,
            effect_id=effect_id,
            authorization_id=authorization.authorization_id,
        )
        push_state = effect_state(self.database, plan.push_effect_id)
        pr_state = effect_state(self.database, plan.pull_request_effect_id)
        pending_external = any(
            state.get("status") in {STARTED, COMPLETED, FROZEN}
            for state in (push_state, pr_state)
        )
        refusal = (
            authorization.scope_refusal(
                repository=f"{plan.owner}/{plan.repo}",
                remote=plan.remote,
                base=plan.base,
            )
            if pending_external
            else authorization.refusal(
                repository=f"{plan.owner}/{plan.repo}",
                remote=plan.remote,
                base=plan.base,
            )
        )
        if refusal:
            return self._blocked(plan, refusal)
        repository_key = f"{plan.owner}/{plan.repo}".casefold()
        if not pending_external and not self.adapter.dispatch_enabled:
            return self._blocked(plan, "github_writes_disabled")
        if repository_key not in self.adapter.authorized_repositories:
            return self._blocked(plan, "repository_not_authorized")
        if self._stopped(stop_path, mission_id) and not pending_external:
            return self._blocked(plan, "stop_requested")

        owned = self.git._owned_worktree(worktree)
        actual_head = self._run_git(owned, "rev-parse", plan.head_sha)
        if actual_head != plan.head_sha:
            raise GitHubWriteError("GitHub effect head is not present in the worktree")
        stopped_now = self._stopped(stop_path, mission_id)
        branch_state = effect_state(self.database, plan.branch_effect_id)
        if stopped_now and pending_external:
            if branch_state.get("status") != COMPLETED:
                return self._blocked(plan, "stop_requested")
            branch = {
                "claimed": COMPLETED,
                "performed": False,
                "replayed": True,
                "action_key": plan.branch_effect_id,
                "journal_state": COMPLETED,
                "branch": plan.branch,
                "head_sha": plan.head_sha,
            }
        else:
            branch = self._ensure_branch(
                plan, owned, stop_path=stop_path, mission_id=mission_id,
            )
        if branch.get("claimed") != COMPLETED:
            return {**branch, "stage": "branch", "intent_id": plan.intent_id}
        if self._stopped(stop_path, mission_id):
            push_state = effect_state(self.database, plan.push_effect_id)
            if push_state.get("status") in {STARTED, COMPLETED, FROZEN}:
                pushed = self._push(
                    plan, owned, stop_path=stop_path, mission_id=mission_id,
                )
            else:
                return {
                    **self._blocked(plan, "stop_requested"),
                    "stage": "before_push",
                    "branch_effect": branch,
                }
        else:
            pushed = self._push(
                plan, owned, stop_path=stop_path, mission_id=mission_id,
            )
        if pushed.get("claimed") != COMPLETED:
            return {
                **pushed,
                "stage": "push",
                "branch_effect": branch,
                "push_effect": pushed,
            }
        pr_state = effect_state(self.database, plan.pull_request_effect_id)
        if pr_state.get("status") not in {STARTED, COMPLETED, FROZEN}:
            dispatch_refusal = authorization.refusal(
                repository=f"{plan.owner}/{plan.repo}",
                remote=plan.remote,
                base=plan.base,
            )
            if dispatch_refusal:
                return {
                    **self._blocked(plan, dispatch_refusal),
                    "stage": "before_pull_request",
                    "external_effects": 1,
                    "branch_effect": branch,
                    "push_effect": pushed,
                }
            if not self.adapter.dispatch_enabled:
                return {
                    **self._blocked(plan, "github_writes_disabled"),
                    "stage": "before_pull_request",
                    "external_effects": 1,
                    "branch_effect": branch,
                    "push_effect": pushed,
                }
        if self._stopped(stop_path, mission_id):
            if pr_state.get("status") in {STARTED, COMPLETED, FROZEN}:
                reconciled_pr = self._pull_request(
                    plan, stop_path=stop_path, mission_id=mission_id,
                )
                return {
                    **reconciled_pr,
                    "stage": "pull_request_reconciliation_under_stop",
                    "intent_id": plan.intent_id,
                    "effect_id": plan.pull_request_effect_id,
                    "branch": plan.branch,
                    "head_sha": plan.head_sha,
                    "branch_effect": branch,
                    "push_effect": pushed,
                    "pull_request_effect": reconciled_pr,
                    "external_effects": (
                        2 if reconciled_pr.get("claimed") == COMPLETED else 1
                    ),
                }
            return {
                **self._blocked(plan, "stop_requested"),
                "stage": "before_pull_request",
                "external_effects": 1,
                "branch_effect": branch,
                "push_effect": pushed,
            }
        pull_request = self._pull_request(
            plan, stop_path=stop_path, mission_id=mission_id,
        )
        return {
            **pull_request,
            "stage": "complete" if pull_request.get("claimed") == COMPLETED else "pull_request",
            "intent_id": plan.intent_id,
            "effect_id": plan.pull_request_effect_id,
            "branch": plan.branch,
            "head_sha": plan.head_sha,
            "branch_effect": branch,
            "push_effect": pushed,
            "pull_request_effect": pull_request,
            "external_effects": 2 if pull_request.get("claimed") == COMPLETED else 1,
        }


__all__ = [
    "GitHubPullRequestEffectDriver",
    "GitHubPullRequestPlan",
    "GitHubRepositoryAuthorization",
    "GitPushTransport",
    "GitHubWriteAdapter",
    "GitHubWriteError",
    "GitHubWriteRequest",
    "GitHubWriteTransportError",
    "GITHUB_WRITE_SPEC",
    "gh_api_write_transport",
    "github_effect_key",
    "github_write_input_hash",
    "github_write_intent",
    "reconcile_github_write_effect",
]
