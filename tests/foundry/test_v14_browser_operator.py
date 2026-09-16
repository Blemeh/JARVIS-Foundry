"""V14 unattended browser operator proofs.

Covers: unattended capability admission, the exact owner execution grant and
its classified refusals, payload override refusal, read-only page inspection,
and one-session ordered steps over a real local HTTP application.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.connectors.web_research import (
    BrowserExecutionAuthorization,
    PlaywrightBrowserConnector,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric.executors import ExecutorContext
from jarvis_foundry.fabric.loop import (
    execute_action,
    seed_builtins,
    select_capability,
)
from jarvis_foundry.fabric.model import TaskIntent


class _OperatorHandler(BaseHTTPRequestHandler):
    server: "_OperatorServer"

    def _authenticated(self) -> bool:
        cookie = self.headers.get("Cookie") or ""
        return f"jarvis_auth={self.server.auth_epoch}" in cookie

    def _send(
        self,
        body: str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(
        self,
        location: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path == "/login":
            self._send(
                "<!doctype html><h1>Login</h1>"
                '<form method="post" action="/login">'
                '<button type="submit" aria-label="Log in">Log in</button></form>'
            )
            return
        if self.path == "/expire":
            self.server.auth_epoch += 1
            self._redirect(
                "/login",
                headers={"Set-Cookie": "jarvis_auth=; Max-Age=0; Path=/"},
            )
            return
        if self.path == "/app":
            if not self._authenticated():
                self._redirect("/login")
                return
            label = "Updated" if self.server.toggle_count else "Ready"
            self._send(
                "<!doctype html><h1>Signed in</h1>"
                '<p role="status" aria-label="Signed in">Signed in</p>'
                f'<p role="status" aria-label="{label}">{label}</p>'
                '<label for="query">Query</label>'
                '<input id="query" aria-label="Query" name="query">'
                '<form method="post" action="/toggle">'
                '<button type="submit" aria-label="Toggle">Toggle</button></form>'
                '<a href="/tab" target="_blank" aria-label="Open tab">Open tab</a>'
            )
            return
        if self.path == "/tab":
            if not self._authenticated():
                self._redirect("/login")
                return
            self.server.newtab_count += 1
            self._send(
                "<!doctype html><h1>Tab</h1>"
                '<p role="status" aria-label="Signed in">Signed in</p>'
                "<p role='status' aria-label='Tab ready'>Tab ready</p>"
            )
            return
        if self.path == "/frame":
            if not self._authenticated():
                self._redirect("/login")
                return
            self._send(
                "<!doctype html><h1>Signed in</h1>"
                '<p role="status" aria-label="Signed in">Signed in</p>'
                "<iframe name='embedded' src='/frame-inner' "
                "title='Embedded form'></iframe>"
            )
            return
        if self.path.startswith("/frame-inner"):
            body = (
                "<!doctype html>"
                '<p role="status" aria-label="Frame ready">Frame ready</p>'
                "<form method='post' action='/frame-submit'>"
                "<label>Frame note</label>"
                "<input aria-label='Frame note' name='note'>"
                "<button type='submit' aria-label='Frame send'>Frame send</button>"
                "</form>"
            )
            if "done=1" in self.path:
                body += (
                    "<p role='status' aria-label='Frame accepted'>"
                    "Frame accepted</p>"
                )
            self._send(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path == "/login":
            self.server.login_count += 1
            self._redirect(
                "/app",
                headers={
                    "Set-Cookie": f"jarvis_auth={self.server.auth_epoch}; Path=/"
                },
            )
            return
        if self.path == "/toggle":
            if not self._authenticated():
                self._redirect("/login")
                return
            self.server.toggle_count += 1
            self._redirect("/app")
            return
        if self.path == "/frame-submit":
            if not self._authenticated():
                self._redirect("/login")
                return
            self.server.frame_submissions += 1
            self._redirect("/frame-inner?done=1")
            return
        self.send_error(404)

    def log_message(self, *_args: object) -> None:
        return


class _OperatorServer(ThreadingHTTPServer):
    toggle_count: int
    newtab_count: int
    login_count: int
    frame_submissions: int
    auth_epoch: int

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _OperatorHandler)
        self.toggle_count = 0
        self.newtab_count = 0
        self.login_count = 0
        self.frame_submissions = 0
        self.auth_epoch = 1


@pytest.fixture()
def operator_site() -> Any:
    server = _OperatorServer()
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


def _grant(
    site: str,
    state_path: Path | None = None,
    **overrides: Any,
) -> BrowserExecutionAuthorization:
    values: dict[str, Any] = {
        "authorization_id": "grant:v14-operator",
        "site_origin": site,
        "owner_authorized": True,
        "dispatch_enabled": True,
        "session_state_path": str(state_path) if state_path is not None else None,
        "profile_id": "v14-operator-profile",
        "auth_marker": ("status", "Signed in"),
        "allow_insecure_localhost": True,
    }
    values.update(overrides)
    return BrowserExecutionAuthorization(**values)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    return database


def _login(session: Any) -> bool:
    target = session.discover_target(
        target_id="login-submit",
        role="button",
        name="Log in",
        action="submit",
    )
    attempt = session.execute(
        target,
        effect_id="effect:v14-login",
        idempotency_key="idempotency:v14-login",
        readback_target={"role": "heading", "name": "Signed in"},
    )
    return attempt.outcome == "COMPLETED"


def _persisted_session(
    browser: PlaywrightBrowserConnector,
    site: str,
    state_path: Path,
) -> None:
    with browser.session(
        reauthenticate=_login,
        profile_id="v14-operator-profile",
        site_origin=site,
        session_state_path=state_path,
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        capture = session.navigate(f"{site}/login")
        assert capture.success
        status = session.ensure_authenticated()
        assert status.state == "AUTHENTICATED"
    assert state_path.is_file()
    assert Path(f"{state_path}.meta.json").is_file()


def _mutation_payload(site: str, state_path: Path) -> dict[str, Any]:
    return {
        "url": f"{site}/app",
        "target_ref": f"{site}/app",
        "target_id": "toggle",
        "role": "button",
        "name": "Toggle",
        "action": "submit",
        "readback_target": {"role": "status", "name": "Updated"},
        "profile_id": "v14-operator-profile",
        "site_origin": site,
        "session_state_path": str(state_path),
        "auth_marker": {"role": "status", "name": "Signed in"},
        "persist_session": True,
        "allowed_hosts": ("127.0.0.1",),
        "allow_insecure_localhost": True,
    }


def test_unattended_selection_admits_browser_mutation_and_inspect(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    intent = TaskIntent(
        task_id="mission.browser.unattended",
        required_actions=("browser.web.execute",),
        unattended=True,
    )
    selection = select_capability(database, intent, "browser.web.execute")
    assert selection.available is True
    assert selection.capability_id == "cap.browser.web.execute"
    inspect_selection = select_capability(
        database, intent, "browser.web.inspect"
    )
    assert inspect_selection.available is True
    assert inspect_selection.capability_id == "cap.browser.web.inspect"


def test_mutation_without_owner_grant_is_blocked_before_dispatch(
    operator_site: Any,
    tmp_path: Path,
) -> None:
    server, site = operator_site
    database = _database(tmp_path)
    context = ExecutorContext(database=database, owned_root=tmp_path / "owned")
    intent = TaskIntent(
        task_id="mission.browser.ungranted",
        required_actions=("browser.web.execute",),
        unattended=True,
    )
    result = execute_action(
        database,
        context,
        intent,
        "browser.web.execute",
        _mutation_payload(site, tmp_path / "never.json"),
        workflow_id="workflow.browser.ungranted",
        step_id="toggle",
    )
    assert result.claimed.value == "BLOCKED"
    assert result.observed["error_code"] == "owner_browser_authorization_required"
    assert server.toggle_count == 0
    with database.connect(readonly=True) as connection:
        action_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type LIKE 'system.action_%'"
        ).fetchone()[0]
    assert action_events == 0


def test_grant_refusals_are_classified(operator_site: Any, tmp_path: Path) -> None:
    _server, site = operator_site
    payload = {
        "url": f"{site}/app",
        "target_ref": f"{site}/app",
        "target_id": "toggle",
        "role": "button",
        "name": "Toggle",
        "action": "submit",
    }
    cases = {
        "owner_authorization_required": _grant(
            site, owner_authorized=False
        ),
        "browser_execution_disabled": _grant(site, dispatch_enabled=False),
    }
    for expected_code, authorization in cases.items():
        database = _database(tmp_path / expected_code)
        context = ExecutorContext(
            database=database,
            owned_root=tmp_path / f"owned-{expected_code}",
            browser_execution_authorization=authorization,
        )
        result = execute_action(
            database,
            context,
            TaskIntent(
                task_id=f"mission.{expected_code}",
                required_actions=("browser.web.execute",),
                unattended=True,
            ),
            "browser.web.execute",
            payload,
            workflow_id=f"workflow.{expected_code}",
            step_id="toggle",
        )
        assert result.claimed.value == "BLOCKED"
        assert result.observed["error_code"] == expected_code

    cross_origin = dict(payload)
    cross_origin["url"] = "http://127.0.0.1:9/app"
    cross_origin["target_ref"] = "http://127.0.0.1:9/app"
    database = _database(tmp_path / "cross-origin")
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned-origin",
        browser_execution_authorization=_grant(site),
    )
    result = execute_action(
        database,
        context,
        TaskIntent(
            task_id="mission.browser.origin",
            required_actions=("browser.web.execute",),
            unattended=True,
        ),
        "browser.web.execute",
        cross_origin,
        workflow_id="workflow.browser.origin",
        step_id="toggle",
    )
    assert result.observed["error_code"] == "browser_origin_not_authorized"

    download_payload = dict(payload)
    download_payload.update({
        "action": "download",
        "allow_downloads": True,
        "download_target": "downloads/report.csv",
    })
    database = _database(tmp_path / "download-refusal")
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned-download",
        browser_execution_authorization=_grant(site),
    )
    result = execute_action(
        database,
        context,
        TaskIntent(
            task_id="mission.browser.download",
            required_actions=("browser.web.execute",),
            unattended=True,
        ),
        "browser.web.execute",
        download_payload,
        workflow_id="workflow.browser.download",
        step_id="download",
    )
    assert result.observed["error_code"] == "browser_action_not_authorized"


def test_payload_cannot_override_the_grant_session_binding(
    operator_site: Any,
    tmp_path: Path,
) -> None:
    _server, site = operator_site
    database = _database(tmp_path)
    grant_state = tmp_path / "grant-state.json"
    authorization = _grant(site, grant_state)
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        browser_execution_authorization=authorization,
    )
    payload = _mutation_payload(site, grant_state)
    payload["session_state_path"] = str(tmp_path / "other-state.json")
    result = execute_action(
        database,
        context,
        TaskIntent(
            task_id="mission.browser.override",
            required_actions=("browser.web.execute",),
            unattended=True,
        ),
        "browser.web.execute",
        payload,
        workflow_id="workflow.browser.override",
        step_id="toggle",
    )
    assert result.claimed.value == "BLOCKED"
    assert result.observed["error_code"] == "browser_authorization_override_refused"


def test_unattended_mutation_and_inspect_over_a_real_session(
    browser: PlaywrightBrowserConnector,
    operator_site: Any,
    tmp_path: Path,
) -> None:
    server, site = operator_site
    state_path = tmp_path / "operator-state.json"
    _persisted_session(browser, site, state_path)
    database = _database(tmp_path)
    authorization = _grant(site, state_path)
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        browser_execution_authorization=authorization,
    )
    intent = TaskIntent(
        task_id="mission.browser.real",
        required_actions=("browser.web.execute",),
        unattended=True,
    )
    result = execute_action(
        database,
        context,
        intent,
        "browser.web.execute",
        _mutation_payload(site, state_path),
        workflow_id="workflow.browser.real",
        step_id="toggle",
    )
    assert result.success is True
    assert result.verified.value == "MATCH"
    assert result.observed["authorization_id"] == authorization.authorization_id
    assert result.observed["authorization_require_authenticated"] is True
    assert result.observed["session"]["state"] == "AUTHENTICATED"
    assert server.toggle_count == 1
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT system_id, status FROM system_actions WHERE action_key = ?",
            (result.receipt["effect_id"],),
        ).fetchone()
    assert tuple(row) == ("browser", "COMPLETED")

    inspect_result = execute_action(
        database,
        context,
        intent,
        "browser.web.inspect",
        {
            "url": f"{site}/app",
            "target_ref": f"{site}/app",
            "profile_id": "v14-operator-profile",
            "site_origin": site,
            "session_state_path": str(state_path),
            "auth_marker": {"role": "status", "name": "Signed in"},
            "persist_session": True,
            "allowed_hosts": ("127.0.0.1",),
            "allow_insecure_localhost": True,
        },
        workflow_id="workflow.browser.real",
        step_id="inspect",
    )
    assert inspect_result.success is True
    assert inspect_result.verified.value == "MATCH"
    names = {
        element["name"]
        for element in inspect_result.observed["elements"]
        if isinstance(element, dict)
    }
    assert {"Query", "Toggle", "Open tab"} <= names
    encoded = json.dumps(
        {"observed": inspect_result.observed, "receipt": inspect_result.receipt},
        default=str,
    )
    assert "jarvis_auth" not in encoded


def test_run_steps_executes_an_ordered_flow_on_one_session(
    browser: PlaywrightBrowserConnector,
    operator_site: Any,
    tmp_path: Path,
) -> None:
    server, site = operator_site
    with browser.session(
        reauthenticate=_login,
        profile_id="v14-operator-profile",
        site_origin=site,
        session_state_path=tmp_path / "steps-state.json",
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        result = session.run_steps(
            [
                {"step_id": "open", "action": "navigate", "url": f"{site}/login"},
                {"step_id": "login", "action": "authenticate"},
                {"step_id": "inspect", "action": "inspect"},
                {
                    "step_id": "fill", "action": "fill",
                    "target_id": "query", "role": "textbox", "name": "Query",
                    "value": "alpha",
                },
                {
                    "step_id": "toggle", "action": "submit",
                    "target_id": "toggle", "role": "button", "name": "Toggle",
                    "readback_target": {"role": "status", "name": "Updated"},
                },
                {
                    "step_id": "readback", "action": "readback",
                    "role": "status", "name": "Updated",
                },
            ],
            step_scope="grant:v14-steps",
        )
    assert result["success"] is True
    assert result["stopped_at"] is None
    assert [row["outcome"] for row in result["steps"]] == ["COMPLETED"] * 6
    inspect_row = result["steps"][2]
    assert {"Query", "Toggle"} <= {
        element["name"] for element in inspect_row["elements"]
    }
    assert result["final"]["session"]["state"] == "AUTHENTICATED"
    assert result["final"]["final_url"].endswith("/app")
    assert server.toggle_count == 1


def test_run_steps_stops_at_the_first_missing_target(
    browser: PlaywrightBrowserConnector,
    operator_site: Any,
) -> None:
    server, site = operator_site
    with browser.session() as session:
        result = session.run_steps(
            [
                {"step_id": "open", "action": "navigate", "url": f"{site}/login"},
                {
                    "step_id": "missing", "action": "click",
                    "target_id": "missing", "role": "button", "name": "Never",
                },
                {"step_id": "never", "action": "inspect"},
            ],
            step_scope="grant:v14-steps-missing",
        )
    assert result["success"] is False
    assert result["stopped_at"] == "missing"
    assert result["step_count"] == 2
    assert result["steps"][1]["error_code"] == "browser_target_error"
    assert server.toggle_count == 0


def test_expired_session_blocks_then_the_reauth_provider_recovers(
    browser: PlaywrightBrowserConnector,
    operator_site: Any,
    tmp_path: Path,
) -> None:
    server, site = operator_site
    state_path = tmp_path / "reauth-state.json"
    _persisted_session(browser, site, state_path)
    with browser.session(
        profile_id="v14-operator-profile",
        site_origin=site,
        session_state_path=state_path,
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        session.navigate(f"{site}/expire")
    assert server.auth_epoch == 2

    intent = TaskIntent(
        task_id="mission.browser.reauth",
        required_actions=("browser.web.execute",),
        unattended=True,
    )
    payload = _mutation_payload(site, state_path)
    blocked_database = _database(tmp_path / "blocked")
    blocked = execute_action(
        blocked_database,
        ExecutorContext(
            database=blocked_database,
            owned_root=tmp_path / "blocked-owned",
            browser_execution_authorization=_grant(site, state_path),
        ),
        intent,
        "browser.web.execute",
        payload,
        workflow_id="workflow.browser.reauth",
        step_id="toggle",
    )
    assert blocked.claimed.value == "BLOCKED"
    assert blocked.observed["error_code"] == "session_expired"
    assert server.toggle_count == 0

    recovered_database = _database(tmp_path / "recovered")
    recovered = execute_action(
        recovered_database,
        ExecutorContext(
            database=recovered_database,
            owned_root=tmp_path / "recovered-owned",
            browser_execution_authorization=_grant(site, state_path),
            browser_reauth_provider=_login,
        ),
        intent,
        "browser.web.execute",
        payload,
        workflow_id="workflow.browser.reauth",
        step_id="toggle",
    )
    assert recovered.success is True
    assert recovered.verified.value == "MATCH"
    assert recovered.observed["session"]["state"] == "AUTHENTICATED"
    assert server.toggle_count == 1
    assert server.login_count >= 2
    metadata = json.loads(
        Path(f"{state_path}.meta.json").read_text(encoding="utf-8")
    )
    assert metadata["auth_state"] == "AUTHENTICATED"

    with browser.session(
        profile_id="v14-operator-profile",
        site_origin=site,
        session_state_path=state_path,
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        session.navigate(f"{site}/app")
        assert session.session_status().authenticated


def test_execute_can_open_a_popup_and_switch_between_tabs(
    browser: PlaywrightBrowserConnector,
    operator_site: Any,
    tmp_path: Path,
) -> None:
    server, site = operator_site
    with browser.session(
        reauthenticate=_login,
        profile_id="v14-operator-profile",
        site_origin=site,
        session_state_path=tmp_path / "tab-state.json",
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        assert session.navigate(f"{site}/login").success
        assert session.ensure_authenticated().authenticated
        session.navigate(f"{site}/app")
        target = session.discover_target(
            target_id="open-tab",
            role="link",
            name="Open tab",
            action="click",
        )
        attempt = session.execute(target, expect_new_page=True)
        assert attempt.outcome == "COMPLETED", attempt.error_code
        assert server.newtab_count == 1
        new_page = (attempt.after_state or {}).get("new_page")
        assert isinstance(new_page, dict)
        assert new_page["url"].endswith("/tab")
        assert new_page["adopted"] is False
        urls = session.page_urls()
        assert len(urls) == 2
        assert session.active_page_index() == 0
        switched = session.switch_page(index=1)
        assert switched["index"] == 1
        assert session.active_page_index() == 1
        back = session.switch_page(index=0)
        assert back["navigated"] is True
        assert session.active_page_index() == 0
        assert session.session_status().authenticated


def test_run_steps_can_target_an_iframe(
    browser: PlaywrightBrowserConnector,
    operator_site: Any,
    tmp_path: Path,
) -> None:
    server, site = operator_site
    with browser.session(
        reauthenticate=_login,
        profile_id="v14-operator-profile",
        site_origin=site,
        session_state_path=tmp_path / "frame-state.json",
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        assert session.navigate(f"{site}/login").success
        assert session.ensure_authenticated().authenticated
        session.navigate(f"{site}/frame")
        frames = session.frame_targets()
        assert len(frames) == 2
        assert frames[1]["index"] == 1
        inspected = session.inspect()
        assert len(inspected["frames"]) == 2
        framed = session.inspect(frame_index=1)
        assert framed["frame_index"] == 1
        assert {
            element["name"] for element in framed["elements"]
        } >= {"Frame note", "Frame send"}
        result = session.run_steps(
            [
                {
                    "step_id": "wait", "action": "wait", "frame_index": 1,
                    "target_id": "frame-send", "role": "button",
                    "name": "Frame send", "wait_state": "visible",
                },
                {
                    "step_id": "fill", "action": "fill", "frame_index": 1,
                    "target_id": "frame-note", "role": "textbox",
                    "name": "Frame note", "value": "inside frame",
                },
                {
                    "step_id": "send", "action": "submit", "frame_index": 1,
                    "target_id": "frame-send", "role": "button",
                    "name": "Frame send",
                    "readback_target": {
                        "role": "status", "name": "Frame accepted",
                    },
                },
                {
                    "step_id": "readback", "action": "readback",
                    "frame_index": 1, "role": "status",
                    "name": "Frame accepted",
                },
            ],
            step_scope="grant:v14-frame",
        )
    assert result["success"] is True, result["stopped_at"]
    assert [row["outcome"] for row in result["steps"]] == ["COMPLETED"] * 4
    assert server.frame_submissions == 1
