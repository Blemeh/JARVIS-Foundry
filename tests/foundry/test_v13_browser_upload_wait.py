"""Real localhost proofs for bounded V13 browser upload and wait verbs."""

from __future__ import annotations

import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.connectors.web_research import (
    BrowserTargetError,
    PlaywrightBrowserConnector,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric.executors import ExecutorContext
from jarvis_foundry.fabric.loop import execute_action, seed_builtins
from jarvis_foundry.fabric.model import TaskIntent


UPLOAD_BYTES = b"account,total\nalpha,17\nbeta,29\nreviewed=true\n"


class _UploadHandler(BaseHTTPRequestHandler):
    server: "_UploadServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path == "/upload":
            with self.server.lock:
                uploaded = (
                    self.server.upload_count > 0
                    or self.server.precompleted
                )
            body = b"""<!doctype html>
<title>V13 upload</title>
<input id="upload" type="file" aria-label="Upload report">
<div id="complete" role="status" aria-label="Upload complete"__HIDDEN__>
  Upload complete
</div>
<script>
document.querySelector("#upload").addEventListener("change", async event => {
  const file = event.target.files[0];
  const response = await fetch("/uploaded", {
    method: "POST",
    headers: {"Content-Type": "application/octet-stream"},
    body: await file.arrayBuffer()
  });
  if (response.ok) {
    document.querySelector("#complete").hidden = false;
  }
});
</script>""".replace(
                b"__HIDDEN__", b"" if uploaded else b" hidden"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/wait":
            body = b"""<!doctype html>
<title>V13 wait</title>
<div id="ready" role="status" aria-label="Ready after delay" hidden>
  Ready after delay
</div>
<script>
setTimeout(() => {
  document.querySelector("#ready").hidden = false;
}, 250);
</script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path != "/uploaded":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        content = self.rfile.read(length)
        with self.server.lock:
            self.server.upload_count += 1
            self.server.upload_bytes = content
        if self.server.stop_path is not None:
            self.server.stop_path.write_text(
                "owner stop after upload\n", encoding="utf-8"
            )
        if self.server.upload_delay_s:
            time.sleep(self.server.upload_delay_s)
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


class _UploadServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _UploadHandler)
        self.lock = threading.Lock()
        self.upload_count = 0
        self.upload_bytes = b""
        self.upload_delay_s = 0.0
        self.stop_path: Path | None = None
        self.precompleted = False


@pytest.fixture()
def disposable_upload_site() -> Any:
    server = _UploadServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def browser() -> PlaywrightBrowserConnector:
    connector = PlaywrightBrowserConnector(
        timeout_ms=10_000,
        allowed_hosts=("127.0.0.1",),
        allow_insecure_localhost=True,
    )
    if not connector.health().get("available"):
        pytest.skip("Playwright browser binary is unavailable")
    return connector


def _upload_target(session: Any, site: str) -> Any:
    capture = session.navigate(f"{site}/upload")
    assert capture.success
    return session.discover_target(
        target_id="upload-report",
        role="button",
        name="Upload report",
        action="upload",
    )


def test_upload_requires_opt_in_and_refuses_escape_before_selection(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_upload_site
    source = tmp_path / "report.csv"
    source.write_bytes(UPLOAD_BYTES)
    with browser.session() as session:
        target = _upload_target(session, site)
        with pytest.raises(BrowserTargetError, match="not enabled"):
            session.execute(
                target,
                upload_source="report.csv",
                readback_target={
                    "role": "status",
                    "name": "Upload complete",
                    "wait_state": "visible",
                },
            )

    with browser.session(
        allow_uploads=True,
        upload_root=tmp_path,
    ) as session:
        target = _upload_target(session, site)
        with pytest.raises(BrowserTargetError, match="escapes"):
            session.execute(
                target,
                upload_source="../report.csv",
                readback_target={
                    "role": "status",
                    "name": "Upload complete",
                    "wait_state": "visible",
                },
            )

    assert server.upload_count == 0


def test_upload_requires_unsatisfied_acknowledgement_before_selection(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_upload_site
    source = tmp_path / "report.csv"
    source.write_bytes(UPLOAD_BYTES)
    with browser.session(
        allow_uploads=True,
        upload_root=tmp_path,
    ) as session:
        target = _upload_target(session, site)
        with pytest.raises(
            BrowserTargetError, match="acknowledgement postcondition"
        ):
            session.execute(target, upload_source="report.csv")

        server.precompleted = True
        target = _upload_target(session, site)
        attempted = session.execute(
            target,
            upload_source="report.csv",
            readback_target={
                "role": "status",
                "name": "Upload complete",
                "wait_state": "visible",
            },
        )

    assert attempted.outcome == "FAILED", attempted
    assert attempted.error_code == "upload_postcondition_already_satisfied"
    assert server.upload_count == 0


def test_upload_posts_exact_bytes_and_hash_bound_browser_readback(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_upload_site
    source = tmp_path / "uploads" / "report.csv"
    source.parent.mkdir()
    source.write_bytes(UPLOAD_BYTES)
    with browser.session(
        allow_uploads=True,
        upload_root=tmp_path,
        max_upload_bytes=1024,
    ) as session:
        target = _upload_target(session, site)
        attempt = session.execute(
            target,
            effect_id="effect:v13-upload",
            idempotency_key="idempotency:v13-upload",
            upload_source="uploads/report.csv",
            readback_target={
                "role": "status",
                "name": "Upload complete",
                "wait_state": "visible",
                "timeout_ms": 5_000,
            },
        )

    evidence = (attempt.after_state or {})["upload"]
    expected_hash = hashlib.sha256(UPLOAD_BYTES).hexdigest()
    assert attempt.outcome == "COMPLETED"
    assert attempt.reconciled is True
    assert evidence["source_ref"] == "uploads/report.csv"
    assert evidence["size_bytes"] == len(UPLOAD_BYTES)
    assert evidence["sha256"] == expected_hash
    assert evidence["readback"]["verified"] is True
    assert evidence["readback"]["files"][0]["sha256"] == expected_hash
    assert evidence["postcondition"]["verified"] is True
    assert server.upload_count == 1
    assert server.upload_bytes == UPLOAD_BYTES
    assert str(source) not in str(evidence)


def test_wait_observes_delayed_state_and_honors_stop(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
) -> None:
    _server, site = disposable_upload_site
    with browser.session() as session:
        assert session.navigate(f"{site}/wait").success
        observed = session.wait_for_target(
            target_id="ready-after-delay",
            role="status",
            name="Ready after delay",
            state="visible",
            timeout_ms=5_000,
        )
        stopped = session.wait_for_target(
            target_id="never-after-stop",
            role="status",
            name="Never appears",
            state="visible",
            timeout_ms=5_000,
            stop_check=lambda: True,
        )

    assert observed.outcome == "COMPLETED"
    assert observed.reconciled is True
    assert (observed.after_state or {})["readback"]["verified"] is True
    assert stopped.outcome == "CANCELLED"
    assert stopped.error_code == "stop_requested"


def test_wait_classifies_browser_crash_as_read_failure(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
) -> None:
    _server, site = disposable_upload_site
    with browser.session() as session:
        assert session.navigate(f"{site}/wait").success
        assert session._browser is not None
        session._browser.close()
        failed = session.wait_for_target(
            target_id="ready-after-crash",
            role="status",
            name="Ready after delay",
            state="visible",
            timeout_ms=5_000,
        )

    assert failed.outcome == "FAILED"
    assert failed.error_code == "browser_wait_failed"


def test_stop_after_upload_selection_is_unknown(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_upload_site
    server.upload_delay_s = 0.5
    source = tmp_path / "report.csv"
    source.write_bytes(UPLOAD_BYTES)
    with browser.session(
        allow_uploads=True,
        upload_root=tmp_path,
    ) as session:
        target = _upload_target(session, site)
        attempt = session.execute(
            target,
            upload_source="report.csv",
            stop_check=lambda: server.upload_count == 1,
            readback_target={
                "role": "status",
                "name": "Upload complete",
                "wait_state": "visible",
                "timeout_ms": 5_000,
            },
        )

    assert attempt.outcome == "UNKNOWN"
    assert attempt.error_code == "stop_after_effect"
    assert server.upload_count == 1


def test_fabric_upload_replays_without_a_second_post(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_upload_site
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    owned_root = tmp_path / "owned"
    owned_root.mkdir()
    source = owned_root / "uploads" / "report.csv"
    source.parent.mkdir()
    source.write_bytes(UPLOAD_BYTES)
    context = ExecutorContext(database=database, owned_root=owned_root)
    intent = TaskIntent(
        task_id="mission.v13.browser.upload",
        required_actions=("browser.web.execute",),
        unattended=False,
    )
    payload = {
        "url": f"{site}/upload",
        "target_ref": f"{site}/upload",
        "target_id": "upload-report",
        "role": "button",
        "name": "Upload report",
        "action": "upload",
        "allow_uploads": True,
        "upload_source": "uploads/report.csv",
        "max_upload_bytes": 1024,
        "readback_target": {
            "role": "status",
            "name": "Upload complete",
            "wait_state": "visible",
            "timeout_ms": 5_000,
        },
        "allowed_hosts": ("127.0.0.1",),
        "allow_insecure_localhost": True,
    }

    first = execute_action(
        database,
        context,
        intent,
        "browser.web.execute",
        payload,
        workflow_id="workflow.v13.upload",
        step_id="upload",
    )
    replay = execute_action(
        database,
        context,
        intent,
        "browser.web.execute",
        payload,
        workflow_id="workflow.v13.upload",
        step_id="upload",
    )

    assert first.success is True
    assert replay.success is True
    assert replay.observed["replayed"] is True
    assert replay.observed["performed"] is False
    assert server.upload_count == 1
    assert server.upload_bytes == UPLOAD_BYTES


def test_fabric_upload_reconciles_frozen_effect_under_stop_without_second_post(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_upload_site
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    owned_root = tmp_path / "owned"
    owned_root.mkdir()
    source = owned_root / "uploads" / "report.csv"
    source.parent.mkdir()
    source.write_bytes(UPLOAD_BYTES)
    stop_path = tmp_path / "EMERGENCY_STOP"
    server.stop_path = stop_path
    context = ExecutorContext(database=database, owned_root=owned_root)
    execute_intent = TaskIntent(
        task_id="mission.v13.browser.upload.ambiguous",
        required_actions=("browser.web.execute",),
        unattended=False,
    )
    payload = {
        "url": f"{site}/upload",
        "target_ref": f"{site}/upload",
        "target_id": "upload-report",
        "role": "button",
        "name": "Upload report",
        "action": "upload",
        "allow_uploads": True,
        "upload_source": "uploads/report.csv",
        "max_upload_bytes": 1024,
        "readback_target": {
            "role": "status",
            "name": "Upload complete",
            "wait_state": "visible",
            "timeout_ms": 5_000,
        },
        "allowed_hosts": ("127.0.0.1",),
        "allow_insecure_localhost": True,
        "stop_path": str(stop_path),
    }

    first = execute_action(
        database,
        context,
        execute_intent,
        "browser.web.execute",
        payload,
        workflow_id="workflow.v13.upload.ambiguous",
        step_id="upload",
    )
    action_key = str(first.observed["action_key"])

    assert first.claimed.value == "UNKNOWN"
    assert first.observed["journal_state"] == "FROZEN"
    assert server.upload_count == 1

    reconciled = execute_action(
        database,
        context,
        TaskIntent(
            task_id="mission.v13.browser.upload.reconcile",
            required_actions=("browser.web.reconcile",),
            unattended=True,
        ),
        "browser.web.reconcile",
        {
            "url": f"{site}/upload",
            "target_ref": f"{site}/upload",
            "original_effect_id": action_key,
            "original_action": "upload",
            "readback_target": payload["readback_target"],
            "allowed_hosts": ("127.0.0.1",),
            "allow_insecure_localhost": True,
            "stop_path": str(stop_path),
        },
        workflow_id="workflow.v13.upload.ambiguous",
        step_id="reconcile",
    )

    assert reconciled.success is True
    assert reconciled.observed["accepted"] is True
    assert reconciled.observed["terminal_truth"] is True
    assert reconciled.observed["recovered_from_ambiguity"] is True
    assert server.upload_count == 1

    server.stop_path = None
    stop_path.unlink()
    replay = execute_action(
        database,
        context,
        execute_intent,
        "browser.web.execute",
        payload,
        workflow_id="workflow.v13.upload.ambiguous",
        step_id="upload",
    )

    assert replay.success is True
    assert replay.observed["replayed"] is True
    assert replay.observed["performed"] is False
    assert server.upload_count == 1
    assert server.upload_bytes == UPLOAD_BYTES


def test_fabric_wait_is_read_only_and_verified(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
    tmp_path: Path,
) -> None:
    _server, site = disposable_upload_site
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
    )
    intent = TaskIntent(
        task_id="mission.v13.browser.wait",
        required_actions=("browser.web.wait",),
        unattended=True,
    )
    result = execute_action(
        database,
        context,
        intent,
        "browser.web.wait",
        {
            "url": f"{site}/wait",
            "target_ref": f"{site}/wait",
            "target_id": "ready-after-delay",
            "role": "status",
            "name": "Ready after delay",
            "wait_state": "visible",
            "timeout_ms": 5_000,
            "allowed_hosts": ("127.0.0.1",),
            "allow_insecure_localhost": True,
        },
        workflow_id="workflow.v13.wait",
        step_id="wait",
    )

    assert result.success is True
    assert result.verified.value == "MATCH"
    assert result.observed["action"] == "wait"
    assert result.observed["wait_state"] == "visible"
    with database.connect(readonly=True) as connection:
        action_events = connection.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE event_type LIKE 'system.action_%'"
        ).fetchone()[0]
    assert action_events == 0


def test_upload_over_limit_fails_before_page_or_network_effect(
    browser: PlaywrightBrowserConnector,
    disposable_upload_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_upload_site
    source = tmp_path / "too-large.bin"
    source.write_bytes(b"x" * 9)
    with browser.session(
        allow_uploads=True,
        upload_root=tmp_path,
        max_upload_bytes=8,
    ) as session:
        target = _upload_target(session, site)
        with pytest.raises(BrowserTargetError, match="byte limit"):
            session.execute(
                target,
                upload_source="too-large.bin",
                readback_target={
                    "role": "status",
                    "name": "Upload complete",
                    "wait_state": "visible",
                },
            )

    assert server.upload_count == 0
