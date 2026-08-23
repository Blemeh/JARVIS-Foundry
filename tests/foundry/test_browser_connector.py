"""V5-8 structural browser/procedure boundary over a local synthetic app."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from jarvis_foundry.connectors.browser import BrowserConnector, BrowserConnectorError
from jarvis_foundry.connectors.http import HttpServiceConnector


class _BrowserState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.version = 1
        self.drift = False
        self.count = 0
        self.last_key: str | None = None
        self.keys: set[str] = set()


class _BrowserHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    @property
    def state(self) -> _BrowserState:
        return self.server.browser_state  # type: ignore[attr-defined]

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            return

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        with self.state.lock:
            if path == "/ui":
                version = self.state.version + (1 if self.state.drift else 0)
                self.send_json(200, {
                    "target_id": "counter-form",
                    "version": version,
                    "controls": [{"role": "button", "name": "Apply", "action": "increment"}],
                })
            elif path == "/state":
                self.send_json(200, {
                    "count": self.state.count,
                    "last_key": self.state.last_key,
                    "write_count": len(self.state.keys),
                })
            else:
                self.send_json(404, {"error": "missing"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/action":
            self.send_json(404, {"error": "missing"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        with self.state.lock:
            if payload.get("target_id") != "counter-form" or payload.get("action") != "increment":
                self.send_json(409, {"error": "target mismatch"})
                return
            key = self.headers.get("Idempotency-Key")
            if key not in self.state.keys:
                self.state.keys.add(key)
                self.state.last_key = key
                self.state.count += 1
            response = {"provider_id": f"counter-{key}", "idempotency_key": key, "count": self.state.count}
        if parse_qs(urlsplit(self.path).query).get("mode", [""])[0] == "timeout-after-effect":
            time.sleep(0.25)
        self.send_json(200, response)


class _BrowserFixture:
    def __init__(self) -> None:
        self.state = _BrowserState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _BrowserHandler)
        self.server.browser_state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture()
def fixture():
    service = _BrowserFixture()
    try:
        yield service
    finally:
        service.close()


def test_structural_identity_parameterized_action_and_retry(fixture: _BrowserFixture) -> None:
    connector = BrowserConnector(HttpServiceConnector(fixture.url, timeout_s=0.2))
    target = connector.discover_target(target_id="counter-form", role="button", name="Apply")
    assert target.target_id == "counter-form"
    assert target.locator == {"role": "button", "name": "Apply"}
    assert target.structural_hash == connector.discover_target(
        target_id="counter-form", role="button", name="Apply"
    ).structural_hash
    result = connector.execute(target, action="increment", params={"task": "demo"}, idempotency_key="browser-1")
    assert result.outcome == "COMPLETED"
    replay = connector.execute(target, action="increment", params={"task": "demo"}, idempotency_key="browser-1")
    assert replay.replayed is True
    assert fixture.state.count == 1


def test_drift_geometry_only_and_stop_fail_closed(fixture: _BrowserFixture) -> None:
    connector = BrowserConnector(HttpServiceConnector(fixture.url, timeout_s=0.2))
    target = connector.discover_target(target_id="counter-form", role="button", name="Apply")
    fixture.state.drift = True
    drift = connector.execute(target, action="increment", params={}, idempotency_key="browser-drift")
    assert drift.outcome == "UNKNOWN"
    assert drift.error_code == "target_drift"
    assert fixture.state.count == 0
    with pytest.raises(BrowserConnectorError, match="geometry"):
        connector.discover_target(target_id="counter-form", geometry={"x": 10, "y": 20})
    cancelled = connector.execute(
        target, action="increment", params={}, idempotency_key="browser-stop",
        stop_check=lambda: True,
    )
    assert cancelled.outcome == "CANCELLED"
    assert fixture.state.count == 0


def test_timeout_after_effect_reconciles_without_second_action(fixture: _BrowserFixture) -> None:
    connector = BrowserConnector(HttpServiceConnector(fixture.url, timeout_s=0.05))
    target = connector.discover_target(target_id="counter-form", role="button", name="Apply")
    timed_out = connector.execute(
        target, action="increment", params={}, idempotency_key="browser-timeout",
        path="/action?mode=timeout-after-effect",
    )
    assert timed_out.outcome == "UNKNOWN"
    time.sleep(0.05)
    resolved = connector.reconcile("browser-timeout")
    assert resolved.outcome == "COMPLETED"
    assert resolved.reconciled is True
    assert fixture.state.count == 1

