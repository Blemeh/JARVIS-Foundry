"""V5-7 local HTTP fault-injection connector proof."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from jarvis_foundry.connectors.http import HttpServiceConnector


class _FixtureState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.value = "initial"
        self.last_key: str | None = None
        self.write_count = 0
        self.keys: set[str] = set()
        self.mode = "ok"


class _Handler(BaseHTTPRequestHandler):
    server_version = "V5Fixture/1"

    def log_message(self, *_args) -> None:
        return

    @property
    def state(self) -> _FixtureState:
        return self.server.fixture_state  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            # The timeout-after-effect proof intentionally closes this client
            # socket before the delayed response is written.
            return

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/state":
            self._json(404, {"error": "missing"})
            return
        with self.state.lock:
            self._json(200, {
                "value": self.state.value,
                "last_key": self.state.last_key,
                "write_count": self.state.write_count,
            })

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path != "/write":
            self._json(404, {"error": "missing"})
            return
        mode = parse_qs(parsed.query).get("mode", [self.state.mode])[0]
        if mode == "429":
            self._json(429, {"error": "rate limited"})
            return
        if mode == "500":
            self._json(500, {"error": "fixture failure"})
            return
        if mode == "malformed":
            body_length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(body_length)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not-json")
            return
        body_length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(body_length) or b"{}")
        key = self.headers.get("Idempotency-Key")
        with self.state.lock:
            if key not in self.state.keys:
                self.state.keys.add(key)
                self.state.last_key = key
                self.state.value = str(body.get("value", ""))
                self.state.write_count += 1
            response = {
                "provider_id": f"msg-{key}",
                "idempotency_key": key,
                "value": self.state.value,
                "write_count": self.state.write_count,
            }
        if mode == "timeout-after-effect":
            time.sleep(0.25)
        self._json(200, response)


class _Fixture:
    def __init__(self) -> None:
        self.state = _FixtureState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.fixture_state = self.state  # type: ignore[attr-defined]
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
    service = _Fixture()
    try:
        yield service
    finally:
        service.close()


def test_read_write_timeout_reconcile_and_duplicate_prevention(fixture: _Fixture) -> None:
    connector = HttpServiceConnector(fixture.url, timeout_s=0.05)
    read = connector.read()
    assert read.outcome == "COMPLETED"
    assert connector.health == "READY"

    timed_out = connector.write("/write?mode=timeout-after-effect", {"value": "landed"}, idempotency_key="http-1")
    assert timed_out.outcome == "UNKNOWN"
    assert timed_out.error_code == "timeout"
    time.sleep(0.05)
    reconciled = connector.reconcile("http-1")
    assert reconciled.outcome == "COMPLETED"
    assert reconciled.reconciled is True
    retry = connector.write("/write?mode=timeout-after-effect", {"value": "landed"}, idempotency_key="http-1")
    assert retry.outcome == "COMPLETED"
    assert retry.replayed is True
    assert fixture.state.write_count == 1


def test_429_5xx_and_malformed_response_have_bounded_honest_states(fixture: _Fixture) -> None:
    connector = HttpServiceConnector(fixture.url, timeout_s=0.2, max_attempts=2)
    rate = connector.write("/write?mode=429", {"value": "nope"}, idempotency_key="rate")
    assert rate.outcome == "FAILED"
    assert rate.error_code == "rate_limited"
    assert connector.health == "DEGRADED"
    malformed = connector.write("/write?mode=malformed", {"value": "bad"}, idempotency_key="malformed")
    assert malformed.outcome == "UNKNOWN"
    assert malformed.error_code == "malformed_response"
    assert fixture.state.write_count == 0
    server_connector = HttpServiceConnector(fixture.url, timeout_s=0.2, max_attempts=2)
    failed = server_connector.write("/write?mode=500", {"value": "bad"}, idempotency_key="server")
    assert failed.outcome == "UNKNOWN"
    assert failed.error_code == "server_error"


def test_outage_opens_only_this_connector_circuit(fixture: _Fixture) -> None:
    unhealthy = HttpServiceConnector(fixture.url, timeout_s=0.05, circuit_threshold=2)
    healthy_fixture = _Fixture()
    healthy = HttpServiceConnector(healthy_fixture.url, timeout_s=0.2)
    try:
        fixture.close()
        first = unhealthy.read()
        second = unhealthy.read()
        assert first.outcome == "UNKNOWN"
        assert second.outcome == "UNKNOWN"
        assert unhealthy.health == "BLOCKED"
        blocked = unhealthy.read()
        assert blocked.error_code == "circuit_open"
        unaffected = healthy.write("/write", {"value": "ok"}, idempotency_key="healthy")
        assert unaffected.outcome == "COMPLETED"
    finally:
        healthy_fixture.close()
