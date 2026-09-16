"""Real disposable-browser proofs for bounded V13 downloads."""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.connectors.web_research import (
    BrowserExecutionAuthorization,
    BrowserTargetError,
    PlaywrightBrowserConnector,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric.executors import ExecutorContext
from jarvis_foundry.fabric.loop import execute_action, seed_builtins
from jarvis_foundry.fabric.model import TaskIntent


DOWNLOAD_BYTES = b"account,total\nalpha,17\nbeta,29\n"


class _DownloadHandler(BaseHTTPRequestHandler):
    server: "_DownloadServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path == "/":
            body = (
                b"<!doctype html><title>V13 download</title>"
                b'<a href="/artifact" download '
                b'aria-label="Download report">Download report</a>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/artifact":
            self.server.download_count += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="server-report.csv"',
            )
            self.send_header("Content-Length", str(len(DOWNLOAD_BYTES)))
            self.end_headers()
            self.wfile.write(DOWNLOAD_BYTES)
            return
        self.send_error(404)

    def log_message(self, *_args: object) -> None:
        return


class _DownloadServer(ThreadingHTTPServer):
    download_count: int

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _DownloadHandler)
        self.download_count = 0


@pytest.fixture()
def disposable_download_site() -> Any:
    server = _DownloadServer()
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


def _target(session: Any, site: str) -> Any:
    capture = session.navigate(f"{site}/")
    assert capture.success
    return session.discover_target(
        target_id="download-report",
        role="link",
        name="Download report",
        action="download",
    )


def _download_grant(site: str) -> BrowserExecutionAuthorization:
    return BrowserExecutionAuthorization(
        authorization_id="grant:v13-download",
        site_origin=site,
        owner_authorized=True,
        dispatch_enabled=True,
        allowed_actions=("download",),
        allow_downloads=True,
        require_authenticated=False,
        allow_insecure_localhost=True,
    )


def test_download_requires_opt_in_and_refuses_escape_before_click(
    browser: PlaywrightBrowserConnector,
    disposable_download_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_download_site
    with browser.session() as session:
        target = _target(session, site)
        with pytest.raises(BrowserTargetError, match="not enabled"):
            session.execute(
                target,
                download_target="downloads/report.csv",
            )
    assert server.download_count == 0

    with browser.session(
        allow_downloads=True,
        download_root=tmp_path,
    ) as session:
        target = _target(session, site)
        with pytest.raises(BrowserTargetError, match="escapes"):
            session.execute(target, download_target="../escape.csv")
    assert server.download_count == 0
    assert not (tmp_path.parent / "escape.csv").exists()


def test_download_is_atomically_saved_and_read_back(
    browser: PlaywrightBrowserConnector,
    disposable_download_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_download_site
    with browser.session(
        allow_downloads=True,
        download_root=tmp_path,
        max_download_bytes=1024,
    ) as session:
        target = _target(session, site)
        attempt = session.execute(
            target,
            effect_id="effect:v13-download",
            idempotency_key="idempotency:v13-download",
            download_target="downloads/report.csv",
        )

    destination = tmp_path / "downloads" / "report.csv"
    evidence = (attempt.after_state or {})["download"]
    assert attempt.outcome == "COMPLETED"
    assert attempt.reconciled is True
    assert destination.read_bytes() == DOWNLOAD_BYTES
    assert evidence["target_ref"] == "downloads/report.csv"
    assert evidence["size_bytes"] == len(DOWNLOAD_BYTES)
    assert evidence["sha256"] == hashlib.sha256(DOWNLOAD_BYTES).hexdigest()
    assert evidence["readback"]["status"] == "MATCH"
    assert "server-report.csv" not in str(evidence)
    assert server.download_count == 1

    with browser.session(
        allow_downloads=True,
        download_root=tmp_path,
    ) as session:
        target = _target(session, site)
        with pytest.raises(BrowserTargetError, match="already exists"):
            session.execute(
                target,
                download_target="downloads/report.csv",
            )
    assert server.download_count == 1


def test_fabric_download_replays_without_a_second_request(
    browser: PlaywrightBrowserConnector,
    disposable_download_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_download_site
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    owned_root = tmp_path / "owned"
    context = ExecutorContext(
        database=database,
        owned_root=owned_root,
        browser_execution_authorization=_download_grant(site),
    )
    intent = TaskIntent(
        task_id="mission.v13.browser.download",
        required_actions=("browser.web.execute",),
        unattended=False,
    )
    payload = {
        "url": f"{site}/",
        "target_ref": f"{site}/",
        "target_id": "download-report",
        "role": "link",
        "name": "Download report",
        "action": "download",
        "allow_downloads": True,
        "download_target": "downloads/report.csv",
        "max_download_bytes": 1024,
        "allowed_hosts": ("127.0.0.1",),
        "allow_insecure_localhost": True,
    }

    first = execute_action(
        database,
        context,
        intent,
        "browser.web.execute",
        payload,
        workflow_id="workflow.v13.download",
        step_id="download",
    )
    replay = execute_action(
        database,
        context,
        intent,
        "browser.web.execute",
        payload,
        workflow_id="workflow.v13.download",
        step_id="download",
    )

    assert first.success is True
    assert replay.success is True
    assert replay.observed["replayed"] is True
    assert replay.observed["performed"] is False
    assert server.download_count == 1
    assert (owned_root / "downloads" / "report.csv").read_bytes() == DOWNLOAD_BYTES
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT system_id, status FROM system_actions "
            "WHERE action_key = ?",
            (first.receipt["effect_id"],),
        ).fetchone()
    assert tuple(row) == ("browser", "COMPLETED")


def test_download_over_limit_is_unknown_after_request_and_not_published(
    browser: PlaywrightBrowserConnector,
    disposable_download_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_download_site
    with browser.session(
        allow_downloads=True,
        download_root=tmp_path,
        max_download_bytes=8,
    ) as session:
        target = _target(session, site)
        attempt = session.execute(
            target,
            download_target="downloads/too-large.csv",
        )

    assert attempt.outcome == "UNKNOWN"
    assert attempt.error_code == "download_ambiguous"
    assert (attempt.after_state or {})["action_request_seen"] is True
    assert server.download_count == 1
    assert not (tmp_path / "downloads" / "too-large.csv").exists()
