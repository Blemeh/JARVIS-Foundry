"""V5-8 structural browser/procedure boundary over a harmless local app."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from ..control.redaction import contains_secret_like
from ..events import canonical_json
from .contract import ConnectorAttempt, ConnectorSpec
from .http import HttpServiceConnector


class BrowserConnectorError(ValueError):
    """A browser target or procedure is not structurally safe to run."""


@dataclass(frozen=True, slots=True)
class BrowserTarget:
    target_id: str
    locator: dict[str, str]
    action: str
    version: int
    structural_hash: str


class BrowserConnector:
    """Use structural target identity; geometry is never an authority anchor."""

    spec = ConnectorSpec(
        connector_id="conn-browser-v5",
        kind="browser",
        version="1.0.0",
        actions={"browser.execute": "execute", "browser.read": "read"},
        capabilities={"read": "READY", "execute": "READY", "desktop": "PROBING"},
        retry_policy={"max_attempts": 2, "backoff_s": [0, 0.05]},
        target_kinds=("browser_target",),
        idempotency_strategy="provider-key",
        reconciliation_strategy="provider-query",
        supports_cancellation=True,
        cost_metadata={"unit": "local_browser_action", "cost_known": True},
        evidence_fields=("attempt_id", "target_id", "outcome", "reconciled"),
    )

    def __init__(self, service: HttpServiceConnector, *, ui_path: str = "/ui") -> None:
        self.service = service
        self.ui_path = ui_path

    @staticmethod
    def _attempt_id() -> str:
        return f"browser-attempt-{uuid.uuid4().hex}"

    @staticmethod
    def _hash(value: Any) -> str:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    def _attempt(
        self,
        *,
        target_id: str,
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
            action_kind="browser.execute", target_ref=target_id,
            idempotency_key=idempotency_key, payload_hash=request_hash,
            outcome=outcome, before_state=before_state or {}, after_state=after_state,
            provider_ref=provider_ref, error_code=error_code,
            evidence_refs=(f"browser-target:{target_id}",),
            reconciled=reconciled, replayed=replayed,
        )

    def _fetch_target(
        self, *, target_id: str, role: str | None, name: str | None,
    ) -> tuple[BrowserTarget | None, ConnectorAttempt | None, str | None]:
        attempt, document = self.service.read_document(self.ui_path)
        if attempt.outcome != "COMPLETED" or document is None:
            return None, attempt, attempt.error_code or "ui_unavailable"
        if document.get("target_id") != target_id:
            return None, attempt, "target_not_found"
        controls = document.get("controls")
        if not isinstance(controls, list):
            return None, attempt, "malformed_ui"
        candidates = [
            item for item in controls if isinstance(item, dict)
            and (role is None or item.get("role") == role)
            and (name is None or item.get("name") == name)
        ]
        if len(candidates) != 1:
            return None, attempt, "target_ambiguous"
        control = candidates[0]
        if not isinstance(control.get("action"), str) or not control.get("action"):
            return None, attempt, "target_action_missing"
        version = int(document.get("version", 0))
        identity = {
            "target_id": target_id, "version": version,
            "role": str(control.get("role", "")),
            "name": str(control.get("name", "")),
            "action": control["action"],
        }
        target = BrowserTarget(
            target_id=target_id,
            locator={"role": identity["role"], "name": identity["name"]},
            action=str(control["action"]), version=version,
            structural_hash=self._hash(identity),
        )
        return target, attempt, None

    def discover_target(
        self,
        *,
        target_id: str,
        role: str | None = None,
        name: str | None = None,
        geometry: dict[str, Any] | None = None,
    ) -> BrowserTarget:
        if geometry is not None and role is None and name is None:
            raise BrowserConnectorError("geometry-only browser target is unsafe")
        if not target_id or not target_id.strip():
            raise BrowserConnectorError("target_id is required")
        target, _attempt, reason = self._fetch_target(
            target_id=target_id, role=role, name=name,
        )
        if target is None:
            raise BrowserConnectorError(reason or "target discovery failed")
        return target

    def execute(
        self,
        target: BrowserTarget,
        *,
        action: str,
        params: dict[str, Any],
        idempotency_key: str,
        path: str = "/action",
        stop_check: Callable[[], bool] | None = None,
    ) -> ConnectorAttempt:
        if stop_check is not None and stop_check():
            return self._attempt(
                target_id=target.target_id, idempotency_key=idempotency_key,
                request_hash=self._hash({"target": target.target_id, "action": action, "params": params}),
                outcome="CANCELLED", before_state={"structural_hash": target.structural_hash},
                error_code="stop_requested",
            )
        if action != target.action:
            return self._attempt(
                target_id=target.target_id, idempotency_key=idempotency_key,
                request_hash=self._hash({"target": target.target_id, "action": action, "params": params}),
                outcome="FAILED", error_code="action_not_declared",
            )
        if not isinstance(params, dict) or contains_secret_like(canonical_json(params)):
            raise BrowserConnectorError("browser parameters are invalid or secret-like")
        current, _ui_attempt, reason = self._fetch_target(
            target_id=target.target_id,
            role=target.locator.get("role"), name=target.locator.get("name"),
        )
        request_hash = self._hash({"target": target.target_id, "action": action, "params": params})
        if current is None:
            return self._attempt(
                target_id=target.target_id, idempotency_key=idempotency_key,
                request_hash=request_hash, outcome="UNKNOWN", error_code=reason or "target_unavailable",
                before_state={"structural_hash": target.structural_hash},
            )
        if current.structural_hash != target.structural_hash:
            return self._attempt(
                target_id=target.target_id, idempotency_key=idempotency_key,
                request_hash=request_hash, outcome="UNKNOWN", error_code="target_drift",
                before_state={"structural_hash": target.structural_hash},
            )
        payload = {
            "target_id": target.target_id,
            "action": action,
            "params": {str(key): str(value) for key, value in sorted(params.items())},
            "expected_structure": target.structural_hash,
        }
        http_attempt = self.service.write(path, payload, idempotency_key=idempotency_key)
        return self._attempt(
            target_id=target.target_id, idempotency_key=idempotency_key,
            request_hash=request_hash, outcome=http_attempt.outcome,
            before_state={"structural_hash": target.structural_hash},
            after_state=http_attempt.after_state, provider_ref=http_attempt.provider_ref,
            error_code=http_attempt.error_code, reconciled=http_attempt.reconciled,
            replayed=http_attempt.replayed,
        )

    def reconcile(self, idempotency_key: str) -> ConnectorAttempt:
        http_attempt = self.service.reconcile(idempotency_key)
        return self._attempt(
            target_id=http_attempt.target_ref, idempotency_key=idempotency_key,
            request_hash=http_attempt.payload_hash, outcome=http_attempt.outcome,
            after_state=http_attempt.after_state, provider_ref=http_attempt.provider_ref,
            error_code=http_attempt.error_code, reconciled=http_attempt.reconciled,
            replayed=http_attempt.replayed,
        )


__all__ = ["BrowserConnector", "BrowserConnectorError", "BrowserTarget"]
