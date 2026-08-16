"""Ephemeral local HTTP service lifecycle adapter on an ephemeral test port."""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from jarvis_foundry.capabilities_live.adapters.base import (
    AfterState,
    BeforeState,
    CanaryAdapter,
    ExecutionResult,
    Outcome,
    Receipt,
)
from jarvis_foundry.capabilities_live.broker import ResolvedCredential

HEALTH_PATH = "/health"


class ServicesAdapter(CanaryAdapter):
    def __init__(self, proving_root: Path) -> None:
        super().__init__(
            proving_root,
            capability_id="capability.services",
            domain="services",
        )
        self._server: http.server.ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._fingerprint: str | None = None

    def _handler_class(
        self,
        expected_fingerprint: str,
    ) -> type[http.server.BaseHTTPRequestHandler]:
        class CanaryHandler(http.server.BaseHTTPRequestHandler):
            server_version = "R9Canary/1.0"

            def do_GET(self) -> None:
                if self.path != HEALTH_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                supplied = str(self.headers.get("X-Jarvis-Credential-Fingerprint", ""))
                if supplied == expected_fingerprint:
                    address = cast(tuple[str, int], self.server.server_address)
                    body = json.dumps(
                        {
                            "status": "ok",
                            "port": address[1],
                            "fingerprint": expected_fingerprint,
                        }
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = b'{"status":"denied"}'
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        return CanaryHandler

    def _health(self, fingerprint: str, port: int) -> tuple[int, str]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{HEALTH_PATH}",
            headers={"X-Jarvis-Credential-Fingerprint": fingerprint},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310 - localhost-only URL from an int port
                return int(response.status), response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return int(error.code), error.read().decode("utf-8")
        except Exception as error:  # noqa: BLE001 - network refusal is an honest failure
            return 0, str(error)

    def execute(
        self,
        operation: str,
        request: Mapping[str, Any],
        credential: ResolvedCredential,
    ) -> ExecutionResult:
        before = BeforeState(
            state_hash="running" if self._server is not None else "stopped",
            detail={"server_running": self._server is not None},
        )
        idempotency_key = str(request.get("idempotency_key", "n/a"))
        if self._stop_requested:
            return self._refusal(
                outcome=Outcome.STOPPED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason="adapter stop requested; lifecycle refused",
            )
        if operation != "lifecycle":
            return self._refusal(
                outcome=Outcome.REFUSED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason=f"unknown operation: {operation}",
            )
        entry = self._journal_entry("lifecycle", idempotency_key)
        if entry is not None:
            if self._server is None:
                return ExecutionResult(
                    outcome=Outcome.FAILED,
                    before=before,
                    after=None,
                    receipt=None,
                    reason="server stopped since idempotent receipt",
                    no_partial_effect=True,
                    reconciliation={"reconciled": False, "detail": "expected running service"},
                )
            server = self._server
            port = int(server.server_address[1])
            status, body = self._health(credential.fingerprint, port)
            if status == 200:
                return ExecutionResult(
                    outcome=Outcome.REPLAYED,
                    before=before,
                    after=AfterState(
                        state_hash="running",
                        detail={"port": port},
                    ),
                    receipt=Receipt(
                        operation="lifecycle",
                        idempotency_key=idempotency_key,
                        provider_reference=str(entry["receipt"]["provider_reference"]),
                        effect_hash=str(entry["receipt"]["effect_hash"]),
                        detail=dict(entry["receipt"]["detail"]),
                    ),
                    reason="idempotent replay; service already healthy",
                    no_partial_effect=True,
                    reconciliation={"reconciled": True, "detail": "health probe matched"},
                )
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=before,
                after=None,
                receipt=None,
                reason=f"health probe failed: {body[:200]}",
                no_partial_effect=True,
                reconciliation={"reconciled": False, "detail": "service not healthy"},
            )
        if self._fault:
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=before,
                after=AfterState(
                    state_hash=before.state_hash,
                    detail=dict(before.detail),
                ),
                receipt=None,
                reason="injected provider failure; no service started",
                no_partial_effect=True,
                reconciliation={"reconciled": True, "detail": "before and after state identical"},
            )
        requested_port = int(request.get("port", 0))
        if requested_port < 0 or requested_port > 65535:
            return self._refusal(
                outcome=Outcome.REFUSED,
                operation=operation,
                idempotency_key=idempotency_key,
                before=before,
                reason=f"invalid port: {requested_port}",
            )
        handler = self._handler_class(credential.fingerprint)
        try:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", requested_port), handler)
        except OSError as error:
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=before,
                after=AfterState(
                    state_hash=before.state_hash,
                    detail=dict(before.detail),
                ),
                receipt=None,
                reason=f"service provider refused to bind: {error}",
                no_partial_effect=True,
                reconciliation={"reconciled": True, "detail": "no service started"},
            )
        self._server = server
        self._fingerprint = credential.fingerprint
        self._server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._server_thread.start()
        port = int(server.server_address[1])
        status, body = self._health(credential.fingerprint, port)
        if status != 200:
            server.shutdown()
            server.server_close()
            self._server = None
            return ExecutionResult(
                outcome=Outcome.FAILED,
                before=before,
                after=AfterState(
                    state_hash="stopped",
                    detail={"server_running": False},
                ),
                receipt=None,
                reason=f"health check failed after start: {body[:200]}",
                no_partial_effect=True,
                reconciliation={"reconciled": True, "detail": "service stopped after failed probe"},
            )
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        after = AfterState(
            state_hash="running",
            detail={
                "port": port,
                "pid": os.getpid(),
                "health_body_hash": body_hash,
            },
        )
        receipt = Receipt(
            operation="lifecycle",
            idempotency_key=idempotency_key,
            provider_reference=f"http://127.0.0.1:{port}{HEALTH_PATH}",
            effect_hash=body_hash,
            detail={
                "port": port,
                "pid": os.getpid(),
                "status": status,
                "health_body_hash": body_hash,
                "fingerprint_prefix": credential.fingerprint[:12],
            },
        )
        second_status, second_body = self._health(credential.fingerprint, port)
        reconciled = (
            second_status == 200
            and hashlib.sha256(second_body.encode("utf-8")).hexdigest() == body_hash
        )
        self._append_journal(
            {
                "operation": "lifecycle",
                "idempotency_key": idempotency_key,
                "port": port,
                "receipt": receipt.to_dict(),
                "reconciliation": {
                    "reconciled": reconciled,
                    "detail": "second health probe matched first",
                },
            }
        )
        return ExecutionResult(
            outcome=Outcome.EXECUTED,
            before=before,
            after=after,
            receipt=receipt,
            reason="real ephemeral HTTP service lifecycle produced",
            no_partial_effect=not reconciled,
            reconciliation={
                "reconciled": reconciled,
                "detail": "second health probe matched first",
            },
        )

    def stop(self) -> dict[str, Any]:
        self._stop_requested = True
        if self._server is None:
            return {
                "capability_id": self.capability_id,
                "stopped": True,
                "port_released": True,
                "detail": "no server running",
            }
        port = int(self._server.server_address[1])
        self._server.shutdown()
        self._server.server_close()
        if self._server_thread is not None:
            self._server_thread.join(timeout=5)
        self._server = None
        self._server_thread = None
        return {
            "capability_id": self.capability_id,
            "stopped": True,
            "port_released": True,
            "port": port,
            "detail": "server shutdown and port released",
        }
