"""V5-S4: local deterministic fake connectors (test + offline substrate).

Deterministic, in-memory fakes used by the test suite and any local
proving: read returns stored content, write stores, execute increments
a counter, and a deliberate ``fail_next`` flag simulates an outage for
the isolation/reroute probes. They are NOT claims about real online
connectors — those stay honestly NOT_READY until S8.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .contract import (ConnectorAttempt, ConnectorRequest, ConnectorSpec,
                       validate_connector_attempt,
                       validate_connector_request)


class ConnectorFailure(Exception):
    """Deterministic simulated outage (outage-isolation probes)."""


class FakeConnector:
    """Deterministic fake with the read/write/execute surface.

    ``fail_next`` makes the NEXT operation raise ConnectorFailure (and
    clears itself) — a deliberate, replayable outage.
    """

    def __init__(self, connector_id: str, kind: str = "fake") -> None:
        self.connector_id = connector_id
        self.kind = kind
        self._store: dict[str, str] = {}
        self._executions: dict[str, int] = {}
        self.fail_next = False

    def read(self, ref: str) -> str:
        self._maybe_fail()
        return self._store.get(ref, "")

    def write(self, ref: str, content: str) -> str:
        self._maybe_fail()
        self._store[ref] = content
        return ref

    def execute(self, ref: str) -> int:
        self._maybe_fail()
        self._executions[ref] = self._executions.get(ref, 0) + 1
        return self._executions[ref]

    def snapshot(self) -> dict[str, Any]:
        return {"connector_id": self.connector_id, "kind": self.kind,
                "store": dict(self._store),
                "executions": dict(self._executions)}

    def _maybe_fail(self) -> None:
        if self.fail_next:
            self.fail_next = False
            raise ConnectorFailure(
                f"fake connector {self.connector_id} simulated outage")


class ContractFakeConnector:
    """A disposable adapter for the normalized V5-2 contract.

    The adapter is deliberately in-memory. It proves request validation,
    idempotent replay, timeout ambiguity, reconciliation, and cancellation
    without claiming an online provider or bypassing the effect journal.
    """

    def __init__(self, spec: ConnectorSpec) -> None:
        self.spec = spec
        self.fail_next = False
        self.invocation_count = 0
        self._attempts: dict[str, ConnectorAttempt] = {}
        self._records: dict[str, str] = {}

    def execute(self, request: ConnectorRequest) -> ConnectorAttempt:
        error = validate_connector_request(self.spec, request)
        if error:
            raise ValueError(error)
        previous = self._attempts.get(request.idempotency_key)
        if previous is not None:
            if previous.payload_hash != request.payload_hash:
                raise ValueError(
                    "idempotency key conflict: payload hash changed")
            return replace(previous, replayed=True)

        self.invocation_count += 1
        if self.fail_next:
            self.fail_next = False
            attempt = ConnectorAttempt(
                attempt_id=f"attempt-{self.invocation_count}",
                connector_id=request.connector_id,
                action_kind=request.action_kind,
                target_ref=request.target_ref,
                idempotency_key=request.idempotency_key,
                payload_hash=request.payload_hash,
                outcome="UNKNOWN",
                before_state=request.before_state,
                error_code="timeout",
                evidence_refs=("fake:timeout",))
        else:
            self._records[request.target_ref] = request.payload_hash
            attempt = ConnectorAttempt(
                attempt_id=f"attempt-{self.invocation_count}",
                connector_id=request.connector_id,
                action_kind=request.action_kind,
                target_ref=request.target_ref,
                idempotency_key=request.idempotency_key,
                payload_hash=request.payload_hash,
                outcome="COMPLETED",
                before_state=request.before_state,
                after_state={"payload_hash": request.payload_hash},
                evidence_refs=(f"fake:record:{request.target_ref}",))
        self._store_attempt(request, attempt)
        return attempt

    def reconcile(self, request: ConnectorRequest,
                  attempt: ConnectorAttempt) -> ConnectorAttempt:
        error = validate_connector_request(self.spec, request)
        if error:
            raise ValueError(error)
        if attempt.outcome != "UNKNOWN":
            return attempt
        self._records[request.target_ref] = request.payload_hash
        reconciled = replace(
            attempt,
            outcome="COMPLETED",
            after_state={"payload_hash": request.payload_hash},
            error_code=None,
            evidence_refs=attempt.evidence_refs +
            (f"fake:reconcile:{request.target_ref}",),
            reconciled=True)
        self._store_attempt(request, reconciled)
        return reconciled

    def cancel(self, request: ConnectorRequest) -> ConnectorAttempt:
        error = validate_connector_request(self.spec, request)
        if error:
            raise ValueError(error)
        attempt = ConnectorAttempt(
            attempt_id=f"cancel-{request.idempotency_key}",
            connector_id=request.connector_id,
            action_kind=request.action_kind,
            target_ref=request.target_ref,
            idempotency_key=request.idempotency_key,
            payload_hash=request.payload_hash,
            outcome="CANCELLED",
            before_state=request.before_state,
            evidence_refs=(f"fake:cancel:{request.target_ref}",))
        self._store_attempt(request, attempt)
        return attempt

    def _store_attempt(self, request: ConnectorRequest,
                       attempt: ConnectorAttempt) -> None:
        error = validate_connector_attempt(self.spec, request, attempt)
        if error:
            raise ValueError(error)
        self._attempts[request.idempotency_key] = attempt
