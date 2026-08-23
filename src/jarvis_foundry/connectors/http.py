"""V5-7 bounded HTTP/service connector with explicit ambiguity handling."""

from __future__ import annotations

import hashlib
import json
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..events import canonical_json
from .contract import ConnectorAttempt, ConnectorSpec


class HttpServiceConnectorError(ValueError):
    """A service target or request shape is invalid."""


class _TransportUnknown(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class HttpServiceConnector:
    """A local-or-configured HTTP adapter; it never follows redirects blindly."""

    spec = ConnectorSpec(
        connector_id="conn-http-v5",
        kind="http_service",
        version="1.0.0",
        actions={"http.read": "read", "http.write": "write"},
        capabilities={"read": "READY", "write": "READY", "reconcile": "READY"},
        retry_policy={"max_attempts": 3, "backoff_s": [0, 0.02, 0.05]},
        target_kinds=("http_endpoint",),
        idempotency_strategy="provider-key",
        reconciliation_strategy="provider-query",
        supports_cancellation=False,
        cost_metadata={"unit": "request", "cost_known": True},
        evidence_fields=("attempt_id", "outcome", "provider_ref", "reconciled"),
    )

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 2.0,
        max_attempts: int | None = None,
        circuit_threshold: int = 3,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise HttpServiceConnectorError("base URL is required")
        parsed = urlsplit(base_url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise HttpServiceConnectorError("base URL must be HTTP(S) with a host")
        if parsed.username is not None or parsed.password is not None:
            raise HttpServiceConnectorError("base URL credentials are not allowed")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise HttpServiceConnectorError("timeout_s must be positive")
        if not isinstance(circuit_threshold, int) or circuit_threshold < 1:
            raise HttpServiceConnectorError("circuit_threshold must be positive")
        host = parsed.hostname.lower()
        netloc = host if parsed.port is None else f"{host}:{parsed.port}"
        self.base_url = urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/"), "", ""))
        self.timeout_s = float(timeout_s)
        self.max_attempts = max_attempts or int(self.spec.retry_policy["max_attempts"])
        self.backoff_s = tuple(float(value) for value in self.spec.retry_policy["backoff_s"])
        self.circuit_threshold = circuit_threshold
        self._consecutive_failures = 0
        self._circuit_open = False
        self._health = "UNKNOWN"
        self._pending: dict[str, dict[str, str]] = {}
        self._completed: dict[str, dict[str, str]] = {}

    @property
    def health(self) -> str:
        return self._health

    @property
    def circuit_state(self) -> str:
        return "OPEN" if self._circuit_open else "CLOSED"

    def _url(self, path: str) -> str:
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise HttpServiceConnectorError("target path must be absolute and local to the base URL")
        return urljoin(self.base_url + "/", path.lstrip("/"))

    @staticmethod
    def _digest(value: Any) -> str:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _attempt_id() -> str:
        return f"http-attempt-{uuid.uuid4().hex}"

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open = False
        self._health = "READY"

    def _record_transient_failure(self, *, ambiguous: bool) -> None:
        self._consecutive_failures += 1
        self._health = "UNKNOWN" if ambiguous else "DEGRADED"
        if self._consecutive_failures >= self.circuit_threshold:
            self._circuit_open = True
            self._health = "BLOCKED"

    def _guard_circuit(self) -> None:
        if self._circuit_open:
            raise _TransportUnknown("circuit_open", "connector circuit is open")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: bytes | None = None,
        headers: dict[str, str] | None = None,
        ignore_circuit: bool = False,
    ) -> tuple[int, bytes]:
        if not ignore_circuit:
            self._guard_circuit()
        request = urllib.request.Request(
            self._url(path), data=payload, method=method,
            headers={"Accept": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            try:
                body = error.read()
            except OSError:
                body = b""
            return int(error.code), body
        except (TimeoutError, socket.timeout) as error:
            raise _TransportUnknown("timeout", str(error) or "request timed out") from error
        except urllib.error.URLError as error:
            reason = error.reason
            code = "timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else "connection_lost"
            raise _TransportUnknown(code, str(reason)) from error
        except OSError as error:
            raise _TransportUnknown("connection_lost", str(error)) from error

    def _attempt(
        self,
        *,
        action_kind: str,
        target_ref: str,
        idempotency_key: str,
        request_hash: str,
        outcome: str,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        provider_ref: str | None = None,
        error_code: str | None = None,
        reconciled: bool = False,
        replayed: bool = False,
    ) -> ConnectorAttempt:
        return ConnectorAttempt(
            attempt_id=self._attempt_id(), connector_id=self.spec.connector_id,
            action_kind=action_kind, target_ref=target_ref,
            idempotency_key=idempotency_key, payload_hash=request_hash,
            outcome=outcome, before_state=before_state or {},
            after_state=after_state, provider_ref=provider_ref,
            error_code=error_code, evidence_refs=(f"http:{target_ref}",),
            reconciled=reconciled, replayed=replayed,
        )

    def _decode(self, body: bytes) -> dict[str, Any]:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HttpServiceConnectorError("response is not valid JSON") from error
        if not isinstance(value, dict):
            raise HttpServiceConnectorError("response JSON must be an object")
        return value

    def read(self, path: str = "/state") -> ConnectorAttempt:
        request_hash = self._digest({"method": "GET", "path": path})
        try:
            status, body = self._request("GET", path)
        except _TransportUnknown as error:
            if error.error_code != "circuit_open":
                self._record_transient_failure(ambiguous=True)
            return self._attempt(
                action_kind="http.read", target_ref=path,
                idempotency_key=f"read:{path}", request_hash=request_hash,
                outcome="UNKNOWN", error_code=error.error_code,
            )
        if status != 200:
            self._record_transient_failure(ambiguous=False)
            return self._attempt(
                action_kind="http.read", target_ref=path,
                idempotency_key=f"read:{path}", request_hash=request_hash,
                outcome="FAILED", error_code=f"http_{status}",
            )
        try:
            payload = self._decode(body)
        except HttpServiceConnectorError:
            self._record_transient_failure(ambiguous=True)
            return self._attempt(
                action_kind="http.read", target_ref=path,
                idempotency_key=f"read:{path}", request_hash=request_hash,
                outcome="UNKNOWN", error_code="malformed_response",
            )
        self._record_success()
        state = {"response_hash": self._digest(payload)}
        return self._attempt(
            action_kind="http.read", target_ref=path,
            idempotency_key=f"read:{path}", request_hash=request_hash,
            outcome="COMPLETED", after_state=state,
            provider_ref=f"http:{self.base_url}{path}", reconciled=True,
        )

    def read_document(self, path: str = "/state") -> tuple[ConnectorAttempt, dict[str, Any] | None]:
        """Read a JSON document for a higher-level adapter.

        The document is returned in memory for structural validation; the
        attempt still contains only its hash and provider reference.
        """
        request_hash = self._digest({"method": "GET", "path": path})
        try:
            status, body = self._request("GET", path)
        except _TransportUnknown as error:
            if error.error_code != "circuit_open":
                self._record_transient_failure(ambiguous=True)
            return self._attempt(
                action_kind="http.read", target_ref=path,
                idempotency_key=f"read:{path}", request_hash=request_hash,
                outcome="UNKNOWN", error_code=error.error_code,
            ), None
        if status != 200:
            self._record_transient_failure(ambiguous=False)
            return self._attempt(
                action_kind="http.read", target_ref=path,
                idempotency_key=f"read:{path}", request_hash=request_hash,
                outcome="FAILED", error_code=f"http_{status}",
            ), None
        try:
            payload = self._decode(body)
        except HttpServiceConnectorError:
            self._record_transient_failure(ambiguous=True)
            return self._attempt(
                action_kind="http.read", target_ref=path,
                idempotency_key=f"read:{path}", request_hash=request_hash,
                outcome="UNKNOWN", error_code="malformed_response",
            ), None
        self._record_success()
        attempt = self._attempt(
            action_kind="http.read", target_ref=path,
            idempotency_key=f"read:{path}", request_hash=request_hash,
            outcome="COMPLETED", after_state={"response_hash": self._digest(payload)},
            provider_ref=f"http:{self.base_url}{path}", reconciled=True,
        )
        return attempt, payload

    def reconcile(self, idempotency_key: str) -> ConnectorAttempt:
        pending = self._pending.get(idempotency_key)
        if pending is None:
            completed = self._completed.get(idempotency_key)
            if completed is not None:
                return self._attempt(
                    action_kind="http.write", target_ref=completed["target_ref"],
                    idempotency_key=idempotency_key, request_hash=completed["payload_hash"],
                    outcome="COMPLETED", after_state={"response_hash": completed["response_hash"]},
                    provider_ref=completed.get("provider_id"), reconciled=True, replayed=True,
                )
            return self._attempt(
                action_kind="http.write", target_ref="/state",
                idempotency_key=idempotency_key, request_hash=self._digest(idempotency_key),
                outcome="UNKNOWN", error_code="not_pending",
            )
        try:
            status, body = self._request("GET", "/state", ignore_circuit=True)
            payload = self._decode(body) if status == 200 else {}
        except (_TransportUnknown, HttpServiceConnectorError):
            return self._attempt(
                action_kind="http.write", target_ref=pending["target_ref"],
                idempotency_key=idempotency_key, request_hash=pending["payload_hash"],
                outcome="UNKNOWN", error_code="reconcile_unavailable",
            )
        if status == 200 and payload.get("last_key") == idempotency_key:
            response_hash = self._digest(payload)
            self._completed[idempotency_key] = {
                **pending, "response_hash": response_hash,
                "provider_id": f"http:{self.base_url}/state",
            }
            self._pending.pop(idempotency_key, None)
            self._record_success()
            return self._attempt(
                action_kind="http.write", target_ref=pending["target_ref"],
                idempotency_key=idempotency_key, request_hash=pending["payload_hash"],
                outcome="COMPLETED", after_state={"response_hash": response_hash},
                provider_ref=f"http:{self.base_url}/state", reconciled=True,
            )
        return self._attempt(
            action_kind="http.write", target_ref=pending["target_ref"],
            idempotency_key=idempotency_key, request_hash=pending["payload_hash"],
            outcome="UNKNOWN", error_code="not_observed",
        )

    def write(self, path: str, payload: dict[str, Any], *, idempotency_key: str) -> ConnectorAttempt:
        if not isinstance(payload, dict):
            raise HttpServiceConnectorError("HTTP write payload must be an object")
        if not idempotency_key or not idempotency_key.strip():
            raise HttpServiceConnectorError("idempotency_key is required")
        request_hash = self._digest(payload)
        previous = self._completed.get(idempotency_key)
        if previous is not None:
            if previous["payload_hash"] != request_hash or previous["target_ref"] != path:
                return self._attempt(
                    action_kind="http.write", target_ref=path,
                    idempotency_key=idempotency_key, request_hash=request_hash,
                    outcome="FAILED", error_code="idempotency_conflict",
                )
            return self._attempt(
                action_kind="http.write", target_ref=path,
                idempotency_key=idempotency_key, request_hash=request_hash,
                outcome="COMPLETED", after_state={"response_hash": previous["response_hash"]},
                provider_ref=previous.get("provider_id"), reconciled=True, replayed=True,
            )
        if idempotency_key in self._pending:
            resolved = self.reconcile(idempotency_key)
            if resolved.outcome == "COMPLETED":
                return self._attempt(
                    action_kind="http.write", target_ref=path,
                    idempotency_key=idempotency_key, request_hash=request_hash,
                    outcome="COMPLETED", after_state=resolved.after_state,
                    provider_ref=resolved.provider_ref, reconciled=True,
                    replayed=True,
                )
            return resolved
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._pending[idempotency_key] = {"target_ref": path, "payload_hash": request_hash}
        last_error = "server_error"
        for attempt_number in range(self.max_attempts):
            try:
                status, body = self._request(
                    "POST", path, payload=encoded,
                    headers={"Content-Type": "application/json", "Idempotency-Key": idempotency_key},
                )
            except _TransportUnknown as error:
                if error.error_code != "circuit_open":
                    self._record_transient_failure(ambiguous=True)
                return self._attempt(
                    action_kind="http.write", target_ref=path,
                    idempotency_key=idempotency_key, request_hash=request_hash,
                    outcome="UNKNOWN", error_code=error.error_code,
                )
            if status == 429:
                last_error = "rate_limited"
                self._record_transient_failure(ambiguous=False)
            elif 500 <= status <= 599:
                last_error = "server_error"
                self._record_transient_failure(ambiguous=True)
            elif not 200 <= status < 300:
                self._pending.pop(idempotency_key, None)
                self._record_transient_failure(ambiguous=False)
                return self._attempt(
                    action_kind="http.write", target_ref=path,
                    idempotency_key=idempotency_key, request_hash=request_hash,
                    outcome="FAILED", error_code=f"http_{status}",
                )
            else:
                try:
                    response = self._decode(body)
                except HttpServiceConnectorError:
                    self._record_transient_failure(ambiguous=True)
                    return self._attempt(
                        action_kind="http.write", target_ref=path,
                        idempotency_key=idempotency_key, request_hash=request_hash,
                        outcome="UNKNOWN", error_code="malformed_response",
                    )
                if response.get("idempotency_key") != idempotency_key:
                    self._record_transient_failure(ambiguous=True)
                    return self._attempt(
                        action_kind="http.write", target_ref=path,
                        idempotency_key=idempotency_key, request_hash=request_hash,
                        outcome="UNKNOWN", error_code="forged_idempotency_key",
                    )
                response_hash = self._digest(response)
                self._completed[idempotency_key] = {
                    "target_ref": path, "payload_hash": request_hash,
                    "response_hash": response_hash,
                    "provider_id": str(response.get("provider_id", "")),
                }
                self._pending.pop(idempotency_key, None)
                self._record_success()
                return self._attempt(
                    action_kind="http.write", target_ref=path,
                    idempotency_key=idempotency_key, request_hash=request_hash,
                    outcome="COMPLETED", after_state={"response_hash": response_hash},
                    provider_ref=str(response.get("provider_id", "")),
                )
            if attempt_number + 1 < self.max_attempts:
                time.sleep(min(self.backoff_s[min(attempt_number, len(self.backoff_s) - 1)], 0.25))
        # A 5xx response may have landed provider-side, so it remains UNKNOWN;
        # a 429 is a clean refusal and can be retried by the caller.
        if last_error == "rate_limited":
            self._pending.pop(idempotency_key, None)
            return self._attempt(
                action_kind="http.write", target_ref=path,
                idempotency_key=idempotency_key, request_hash=request_hash,
                outcome="FAILED", error_code=last_error,
            )
        return self._attempt(
            action_kind="http.write", target_ref=path,
            idempotency_key=idempotency_key, request_hash=request_hash,
            outcome="UNKNOWN", error_code=last_error,
        )


__all__ = ["HttpServiceConnector", "HttpServiceConnectorError"]
