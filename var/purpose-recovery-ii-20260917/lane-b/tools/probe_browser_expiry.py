"""Probe: does session expiry actually fail the canonical browser action?

Lane B diagnostic (not evidence): verifies the injected failure is real and
shows exactly what the runtime records for expired-session vs reauthenticated
execution.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _repo_root(start: Path) -> Path:
    current = start
    for _ in range(10):
        if (current / "src" / "jarvis_foundry").is_dir() and (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise RuntimeError(f"could not locate JARVIS repo from {start}")


REPO = _repo_root(HERE)
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

app_spec = importlib.util.spec_from_file_location("probe_lane1_app", REPO / "var" / "lane1-demo" / "app.py")
app = importlib.util.module_from_spec(app_spec)
app_spec.loader.exec_module(app)

from jarvis_foundry.connectors.web_research import (  # noqa: E402
    BrowserExecutionAuthorization,
    PlaywrightBrowserConnector,
)
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.fabric.executors import ExecutorContext  # noqa: E402
from jarvis_foundry.fabric.loop import run_intent, seed_builtins  # noqa: E402
from jarvis_foundry.fabric.model import TaskIntent  # noqa: E402

RUN = HERE / "browser-run"


def _login(session: Any) -> bool:
    target = session.discover_target(
        target_id="login-submit", role="button", name="Sign in", action="submit",
    )
    attempt = session.execute(
        target,
        effect_id="effect:probe-login",
        idempotency_key="idempotency:probe-login",
        readback_target={"role": "heading", "name": "Operator dashboard"},
    )
    return attempt.outcome == "COMPLETED"


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=lambda fn, path, err: (os.chmod(path, 0o700), fn(path)))
    RUN.mkdir(parents=True)
    state_path = RUN / "state.json"
    server, _thread, site = app.serve(0)
    out: dict[str, Any] = {}
    try:
        browser = PlaywrightBrowserConnector(
            timeout_ms=10_000, allowed_hosts=("127.0.0.1",), allow_insecure_localhost=True,
        )
        with browser.session(
            reauthenticate=_login, profile_id="probe-profile", site_origin=site,
            session_state_path=state_path, owner_scope="probe", 
            credential_reference="cred-disposable",
            auth_marker={"role": "status", "name": "Signed in"}, persist_session=True,
        ) as session:
            session.navigate(f"{site}/login")
            status = session.ensure_authenticated()
            out["initial_login"] = {"authenticated": status.authenticated, "state": status.state}

        grant = BrowserExecutionAuthorization(
            authorization_id="grant:probe", site_origin=site, owner_authorized=True,
            dispatch_enabled=True, allowed_actions=("click", "fill", "submit"),
            session_state_path=str(state_path), profile_id="probe-profile",
            owner_scope="probe", credential_reference="cred-disposable",
            auth_marker=("status", "Signed in"), require_authenticated=True,
            persist_session=True, allow_insecure_localhost=True,
        )
        payload = {
            "url": f"{site}/dashboard", "site_origin": site,
            "session_state_path": str(state_path), "profile_id": "probe-profile",
            "owner_scope": "probe", "credential_reference": "cred-disposable",
            "auth_marker": {"role": "status", "name": "Signed in"},
            "require_authenticated": True, "persist_session": True,
            "allowed_hosts": ["127.0.0.1"], "allow_insecure_localhost": True,
            "idempotency_key": "probe-read-1",
        }
        db = FoundryDatabase(RUN / "probe.db")
        db.initialize()
        seed_builtins(db)
        owned_root = RUN / "root"
        owned_root.mkdir(parents=True, exist_ok=True)

        def run_read(*, reauth: bool, key: str) -> dict[str, Any]:
            ctx = ExecutorContext(
                database=db, owned_root=owned_root,
                browser_execution_authorization=grant,
                browser_reauth_provider=_login if reauth else None,
            )
            body = dict(payload)
            body["idempotency_key"] = key
            result = run_intent(
                db, ctx,
                TaskIntent(task_id=f"probe-{key}", required_actions=("browser.web.read",)),
                {"browser.web.read": body},
            )
            return result

        out["read_while_authenticated"] = run_read(reauth=True, key="probe-authenticated")
        # Expire the session server-side (bumps the auth epoch and clears the cookie).
        with browser.session(
            reauthenticate=_login, profile_id="probe-profile", site_origin=site,
            session_state_path=state_path, owner_scope="probe",
            credential_reference="cred-disposable",
            auth_marker={"role": "status", "name": "Signed in"}, persist_session=True,
        ) as expiry:
            expiry.navigate(f"{site}/expire")
            out["expire_navigation"] = {"state": str(getattr(expiry, "state", "") or "")}
        # Drop the persisted storage state so the next context restores nothing.
        out["state_file_before"] = state_path.is_file()
        if state_path.is_file():
            state_path.write_text("{}", encoding="utf-8")

        out["read_after_expiry_without_reauth"] = run_read(reauth=False, key="probe-expired")
        out["read_after_expiry_with_reauth"] = run_read(reauth=True, key="probe-recovered")
    finally:
        server.shutdown()
        server.server_close()

    def _trim(value: Any) -> Any:
        text = json.dumps(value, default=str)
        return text[:700]

    print(json.dumps({
        "initial_login": out.get("initial_login"),
        "expire_navigation": out.get("expire_navigation"),
        "read_while_authenticated": _trim(out.get("read_while_authenticated")),
        "read_after_expiry_without_reauth": _trim(out.get("read_after_expiry_without_reauth")),
        "read_after_expiry_with_reauth": _trim(out.get("read_after_expiry_with_reauth")),
    }, indent=2))
    (RUN / "PROBE_RESULTS.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
