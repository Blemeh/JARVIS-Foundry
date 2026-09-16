"""V10_EXIT disposable authenticated-browser and recovery proofs."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import execute_fabric_effect
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    WorkflowStep,
    execute_action,
    run_workflow,
    seed_builtins,
)
from jarvis_foundry.connectors.web_research import (
    BrowserExecutionAuthorization,
    PlaywrightBrowserConnector,
)
from jarvis_foundry.events import Event


class _DisposableAuthHandler(BaseHTTPRequestHandler):
    server: "_DisposableAuthServer"

    def _cookie_authenticated(self) -> bool:
        return "jarvis_auth=1" in (self.headers.get("Cookie") or "")

    def _send(self, body: str, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, location: str, *, headers: dict[str, str] | None = None) -> None:
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
            self._redirect(
                "/login",
                headers={"Set-Cookie": "jarvis_auth=; Max-Age=0; Path=/"},
            )
            return
        if self.path == "/app":
            if not self._cookie_authenticated():
                self._redirect("/login")
                return
            label = (
                "Updated"
                if self.server.toggle_count or self.server.slow_count
                else "Ready"
            )
            self._send(
                "<!doctype html><h1>Signed in</h1>"
                f'<p role="status" aria-label="{label}">{label}</p>'
                '<label for="query">Query</label>'
                '<input id="query" aria-label="Query" name="query">'
                '<label for="choice">Choice</label>'
                '<select id="choice" aria-label="Choice">'
                '<option value="alpha">Alpha</option>'
                '<option value="beta">Beta</option>'
                '</select>'
                '<label for="flag">Flag</label>'
                '<input id="flag" aria-label="Flag" type="checkbox">'
                '<form method="post" action="/toggle">'
                '<button type="submit" aria-label="Toggle">Toggle</button></form>'
            )
            return
        if self.path == "/slow":
            if not self._cookie_authenticated():
                self._redirect("/login")
                return
            self._send(
                "<!doctype html><h1>Signed in</h1>"
                '<p role="status" aria-label="Ready">Ready</p>'
                '<form method="post" action="/slow">'
                '<button type="submit" aria-label="Slow">Slow</button></form>'
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path == "/login":
            self._redirect(
                "/app",
                headers={"Set-Cookie": "jarvis_auth=1; Path=/"},
            )
            return
        if self.path == "/toggle":
            if not self._cookie_authenticated():
                self._redirect("/login")
                return
            self.server.toggle_count += 1
            self._redirect("/app")
            return
        if self.path == "/slow":
            if not self._cookie_authenticated():
                self._redirect("/login")
                return
            self.server.slow_count += 1
            time.sleep(2.0)
            self._redirect("/app")
            return
        self.send_error(404)

    def log_message(self, *_args: object) -> None:
        return


class _DisposableAuthServer(ThreadingHTTPServer):
    toggle_count: int
    slow_count: int

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _DisposableAuthHandler)
        self.toggle_count = 0
        self.slow_count = 0


@pytest.fixture()
def disposable_site() -> Any:
    server = _DisposableAuthServer()
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
        pytest.skip("lane Playwright browser binary is unavailable")
    return connector


def _session_kwargs(site: str, state_path: Path) -> dict[str, Any]:
    return {
        "profile_id": "v10-disposable-profile",
        "site_origin": site,
        "owner_scope": "owner-local",
        "session_state_path": state_path,
        "auth_marker": {"role": "heading", "name": "Signed in"},
        "expired_url_prefixes": ("/login",),
        "persist_session": True,
    }


def _browser_grant(
    site: str,
    state_path: Path | None = None,
    **overrides: Any,
) -> BrowserExecutionAuthorization:
    values: dict[str, Any] = {
        "authorization_id": "grant:v10-disposable",
        "site_origin": site,
        "owner_authorized": True,
        "dispatch_enabled": True,
        "session_state_path": str(state_path) if state_path is not None else None,
        "profile_id": "v10-disposable-profile",
        "owner_scope": "owner-local",
        "auth_marker": ("heading", "Signed in"),
        "allow_insecure_localhost": True,
    }
    values.update(overrides)
    return BrowserExecutionAuthorization(**values)


def _login(session: Any) -> bool:
    target = session.discover_target(
        target_id="login-submit",
        role="button",
        name="Log in",
        action="submit",
    )
    attempt = session.execute(
        target,
        effect_id="effect:disposable-login",
        idempotency_key="idempotency:disposable-login",
        readback_target={"role": "heading", "name": "Signed in"},
    )
    return attempt.outcome == "COMPLETED"


def _create_persisted_session(
    browser: PlaywrightBrowserConnector,
    site: str,
    state_path: Path,
) -> None:
    with browser.session(
        reauthenticate=_login,
        **_session_kwargs(site, state_path),
    ) as session:
        capture = session.navigate(f"{site}/login")
        assert capture.success
        status = session.ensure_authenticated()
        assert status.state == "AUTHENTICATED"
        assert session.session_status().session_ref
    metadata = Path(f"{state_path}.meta.json")
    assert state_path.is_file()
    assert metadata.is_file()
    assert "jarvis_auth" not in metadata.read_text(encoding="utf-8")


def test_persisted_session_restores_and_bounded_interaction_is_read_back(
    browser: PlaywrightBrowserConnector,
    disposable_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_site
    state_path = tmp_path / "browser-state.json"
    _create_persisted_session(browser, site, state_path)

    with browser.session(**_session_kwargs(site, state_path)) as session:
        assert session.session_status(refresh=False).restored is True
        capture = session.navigate(f"{site}/app")
        assert capture.success
        assert session.check_session().state == "AUTHENTICATED"
        target = session.discover_target(
            target_id="toggle",
            role="button",
            name="Toggle",
            action="submit",
        )
        attempt = session.execute(
            target,
            effect_id="effect:disposable-toggle",
            idempotency_key="idempotency:disposable-toggle",
            readback_target={"role": "status", "name": "Updated"},
        )
        assert attempt.outcome == "COMPLETED"
        assert attempt.reconciled is True
        assert (attempt.after_state or {}).get("readback", {}).get("verified") is True
    assert server.toggle_count == 1

    with browser.session(**_session_kwargs(site, state_path)) as session:
        assert session.navigate(f"{site}/app").success
        status = session.check_session()
        assert status.restored is True
        assert status.state == "AUTHENTICATED"
        evidence = json.dumps(status.to_evidence(), sort_keys=True)
        assert "jarvis_auth" not in evidence


def test_fill_select_and_check_are_bounded_and_read_back(
    browser: PlaywrightBrowserConnector,
    disposable_site: Any,
    tmp_path: Path,
) -> None:
    _server, site = disposable_site
    state_path = tmp_path / "browser-state.json"
    _create_persisted_session(browser, site, state_path)

    with browser.session(**_session_kwargs(site, state_path)) as session:
        assert session.navigate(f"{site}/app").success
        fill_target = session.discover_target(
            target_id="query",
            role="textbox",
            name="Query",
            action="fill",
        )
        filled = session.execute(
            fill_target,
            value="bounded input",
            idempotency_key="idempotency:fill",
        )
        assert filled.outcome == "COMPLETED"
        assert (filled.after_state or {}).get("readback", {}).get("verified") is True

        select_target = session.discover_target(
            target_id="choice",
            role="combobox",
            name="Choice",
            action="select",
        )
        selected = session.execute(
            select_target,
            value="beta",
            idempotency_key="idempotency:select",
        )
        assert selected.outcome == "COMPLETED"
        assert (selected.after_state or {}).get("readback", {}).get("verified") is True

        check_target = session.discover_target(
            target_id="flag",
            role="checkbox",
            name="Flag",
            action="check",
        )
        checked = session.execute(
            check_target,
            value=True,
            idempotency_key="idempotency:check",
        )
        assert checked.outcome == "COMPLETED"
        assert (checked.after_state or {}).get("readback", {}).get("verified") is True


def test_stop_before_action_and_stop_after_effect_are_honest(
    browser: PlaywrightBrowserConnector,
    disposable_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_site
    state_path = tmp_path / "browser-state.json"
    _create_persisted_session(browser, site, state_path)

    stopped = False

    def stop_before() -> bool:
        return stopped

    with browser.session(
        stop_check=stop_before,
        **_session_kwargs(site, state_path),
    ) as session:
        assert session.navigate(f"{site}/app").success
        target = session.discover_target(
            target_id="toggle",
            role="button",
            name="Toggle",
            action="submit",
        )
        stopped = True
        attempt = session.execute(target, idempotency_key="idempotency:stop-before")
        assert attempt.outcome == "CANCELLED"
        assert attempt.error_code == "stop_requested"
    assert server.toggle_count == 0

    action_started = False
    action_checks = 0

    def stop_after_request() -> bool:
        nonlocal action_checks
        if not action_started:
            return False
        action_checks += 1
        # The execute preflight and the mutating request route each get a
        # chance to observe STOP.  The third observation is after dispatch.
        return action_checks >= 3

    with browser.session(
        stop_check=stop_after_request,
        **_session_kwargs(site, state_path),
    ) as session:
        assert session.navigate(f"{site}/app").success
        target = session.discover_target(
            target_id="toggle",
            role="button",
            name="Toggle",
            action="submit",
        )
        action_started = True
        attempt = session.execute(
            target,
            idempotency_key="idempotency:stop-after",
            readback_target={"role": "status", "name": "Updated"},
        )
        assert attempt.outcome == "UNKNOWN"
        assert attempt.error_code == "stop_after_effect"
    assert server.toggle_count == 1


def test_session_expiry_and_timeout_after_action_never_repeat(
    browser: PlaywrightBrowserConnector,
    disposable_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_site
    state_path = tmp_path / "browser-state.json"
    _create_persisted_session(browser, site, state_path)

    with browser.session(**_session_kwargs(site, state_path)) as session:
        assert session.navigate(f"{site}/app").success
        target = session.discover_target(
            target_id="toggle",
            role="button",
            name="Toggle",
            action="submit",
        )
        assert session.navigate(f"{site}/expire").success
        assert session.check_session().state == "EXPIRED"
        expired = session.execute(target, idempotency_key="idempotency:expired")
        assert expired.outcome == "BLOCKED"
        assert expired.error_code == "session_expired"
    assert server.toggle_count == 0

    timeout_connector = PlaywrightBrowserConnector(
        timeout_ms=400,
        allowed_hosts=("127.0.0.1",),
        allow_insecure_localhost=True,
    )
    with timeout_connector.session(**_session_kwargs(site, state_path)) as session:
        assert session.navigate(f"{site}/slow").success
        target = session.discover_target(
            target_id="slow",
            role="button",
            name="Slow",
            action="submit",
        )
        attempt = session.execute(
            target,
            effect_id="effect:slow",
            idempotency_key="idempotency:slow",
            readback_target={"role": "status", "name": "Updated"},
        )
        assert attempt.outcome == "UNKNOWN"
        assert attempt.error_code == "timeout_after_action"
        assert (attempt.after_state or {}).get("effect_id") == "effect:slow"
        time.sleep(2.2)
        assert session.navigate(f"{site}/app").success
        reconciled = session.reconcile(
            role="status",
            name="Updated",
            effect_id="effect:slow",
            idempotency_key="idempotency:slow",
        )
        assert reconciled.outcome == "COMPLETED"
        assert reconciled.reconciled is True
    assert server.slow_count == 1


def test_fabric_uses_one_effect_journal_and_replays_without_duplicate(
    browser: PlaywrightBrowserConnector,
    disposable_site: Any,
    tmp_path: Path,
) -> None:
    _server, site = disposable_site
    state_path = tmp_path / "browser-state.json"
    _create_persisted_session(browser, site, state_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    ctx = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        browser_execution_authorization=_browser_grant(site, state_path),
    )
    intent = TaskIntent(
        task_id="mission-browser-disposable",
        required_actions=("browser.web.execute",),
        unattended=False,
    )
    payload = {
        "url": f"{site}/app",
        "target_ref": f"{site}/app",
        "target_id": "toggle",
        "role": "button",
        "name": "Toggle",
        "action": "submit",
        "allowed_hosts": ("127.0.0.1",),
        "allow_insecure_localhost": True,
        **{
            **_session_kwargs(site, state_path),
            "session_state_path": str(state_path),
        },
        "readback_target": {"role": "status", "name": "Updated"},
    }
    first = execute_action(
        database, ctx, intent, "browser.web.execute", payload,
        workflow_id="workflow-browser", step_id="toggle",
    )
    second = execute_action(
        database, ctx, intent, "browser.web.execute", payload,
        workflow_id="workflow-browser", step_id="toggle",
    )
    assert first.success is True
    assert second.success is True
    assert first.receipt["effect_id"] == second.receipt["effect_id"]
    assert second.observed["replayed"] is True
    assert second.observed["performed"] is False
    with database.connect() as connection:
        counts = connection.execute(
            "SELECT event_type, COUNT(*) FROM events "
            "WHERE event_type IN ('system.action_started', 'system.action_completed') "
            "GROUP BY event_type"
        ).fetchall()
        started_payload = connection.execute(
            "SELECT payload_json FROM events "
            "WHERE event_type = 'system.action_started' "
            "ORDER BY sequence LIMIT 1"
        ).fetchone()[0]
    assert dict(counts) == {
        "system.action_completed": 1,
        "system.action_started": 1,
    }
    identity = json.loads(started_payload)["request_identity"]
    assert identity["mission_id"] == intent.task_id
    assert identity["workflow_id"] == "workflow-browser"
    assert identity["step_id"] == "toggle"
    assert identity["attempt"] == 1

    world_database = FoundryDatabase(tmp_path / "world.db")
    world_database.initialize()
    seed_builtins(world_database)
    world_result = run_workflow(
        world_database,
        ExecutorContext(
            database=world_database,
            owned_root=tmp_path / "world-owned",
            browser_execution_authorization=_browser_grant(site, state_path),
        ),
        TaskIntent(
            task_id="mission-browser-world",
            required_actions=("browser.web.execute",),
            unattended=False,
        ),
        [
            WorkflowStep(
                "toggle",
                "browser.web.execute",
                payload,
            ),
        ],
    )
    assert world_result["success"] is True
    assert world_result["world_report"]["outcome"] == "PASS"
    assert world_result["world_report"]["observation"]["observation_id"]
    with world_database.connect() as connection:
        world_events = connection.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE event_type = 'world.observation_recorded'"
        ).fetchone()[0]
    assert world_events == 1


def test_fabric_crash_reaps_started_browser_effect_without_dispatch(
    tmp_path: Path,
) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    action_key = "effect:crash-browser-proof"
    database.append(Event.create(
        "system.action_started",
        {
            "action_key": action_key,
            "system_id": "browser",
            "target_ref": "http://127.0.0.1:1/app",
            "payload_hash": "a" * 64,
            "kind": "browser.web.execute",
            "connector_id": "conn-browser-playwright-v10",
            "before_state_capture": {"target_id": "proof"},
            "expected_effects": ["bounded browser action"],
            "claim_token": "crash-proof",
            "claimed_at": 0,
            "claim_expires_at": 0,
        },
        source_type="supervisor",
        source_id="v10-exit-test",
        idempotency_key=f"{action_key}.started",
    ))
    dispatched = 0

    def perform() -> dict[str, Any]:
        nonlocal dispatched
        dispatched += 1
        return {"claimed": "COMPLETED"}

    result = execute_fabric_effect(
        database,
        action_key=action_key,
        system_id="browser",
        target_ref="http://127.0.0.1:1/app",
        payload_hash="a" * 64,
        kind="browser.web.execute",
        connector_id="conn-browser-playwright-v10",
        perform=perform,
        claim_ttl_s=1,
    )
    replay = execute_fabric_effect(
        database,
        action_key=action_key,
        system_id="browser",
        target_ref="http://127.0.0.1:1/app",
        payload_hash="a" * 64,
        kind="browser.web.execute",
        connector_id="conn-browser-playwright-v10",
        perform=perform,
        claim_ttl_s=1,
    )
    assert result["claimed"] == "UNKNOWN"
    assert result["journal_state"] == "FROZEN"
    assert result["reaped"] is True
    assert replay["journal_state"] == "FROZEN"
    assert replay["replayed"] is True
    assert dispatched == 0


def test_blocked_browser_outcome_never_writes_world_model(
    browser: PlaywrightBrowserConnector,
    disposable_site: Any,
    tmp_path: Path,
) -> None:
    server, site = disposable_site
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    context = ExecutorContext(
        database=database,
        owned_root=tmp_path / "owned",
        browser_execution_authorization=_browser_grant(
            site,
            profile_id="expired-profile",
            persist_session=False,
        ),
    )
    intent = TaskIntent(
        task_id="mission-browser-expired",
        required_actions=("browser.web.execute",),
        unattended=False,
    )
    result = run_workflow(
        database,
        context,
        intent,
        [
            WorkflowStep(
                "blocked-action",
                "browser.web.execute",
                {
                    "url": f"{site}/app",
                    "target_ref": f"{site}/app",
                    "target_id": "toggle",
                    "role": "button",
                    "name": "Toggle",
                    "action": "submit",
                    "allowed_hosts": ["127.0.0.1"],
                    "allow_insecure_localhost": True,
                    "site_origin": site,
                    "profile_id": "expired-profile",
                    "owner_scope": "owner-local",
                    "auth_marker": {"role": "heading", "name": "Signed in"},
                    "require_authenticated": True,
                    "persist_session": False,
                },
            ),
        ],
    )
    assert result["success"] is False
    assert result["stopped_at"] == "blocked-action"
    assert result["world_report"]["status"] == "NOT_EMITTED"
    assert server.toggle_count == 0
    with database.connect() as connection:
        world_events = connection.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE event_type = 'world.observation_recorded'"
        ).fetchone()[0]
    assert world_events == 0
