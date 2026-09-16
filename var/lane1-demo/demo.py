r"""Lane 1 real demo: durable unattended browser operator.

Proves the full loop against the local demo app in ``var/lane1-demo/app.py``:

  1. local app starts; Playwright health is real
  2. login, persisted session state saved
  3. read-only inspect through the canonical Fabric executor
  4. a mission-style browser mutation without an owner grant is BLOCKED
  5. granted, unattended submit through Fabric, readback verified
  6. download through Fabric, hash verified from disk
  7. upload through Fabric with a real acknowledgement
  8. browser closed; a fresh browser restores the session
  9. session expiry is classified; reauth provider recovers and continues
 10. one-session ordered steps (fill -> submit -> readback -> download)
 11. popup tab opened and switched deterministically

Run:

    $env:PYTHONPATH="$PWD\src"
    python var/lane1-demo/demo.py

Receipts are written under ``var/lane1-demo/receipts``.  They never contain
cookies, storage-state contents, or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_app_spec = importlib.util.spec_from_file_location("lane1_demo_app", HERE / "app.py")
assert _app_spec is not None and _app_spec.loader is not None
app = importlib.util.module_from_spec(_app_spec)
_app_spec.loader.exec_module(app)

from jarvis_foundry.connectors.web_research import (  # noqa: E402
    BrowserExecutionAuthorization,
    PlaywrightBrowserConnector,
)
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.fabric.executors import ExecutorContext  # noqa: E402
from jarvis_foundry.fabric.loop import (  # noqa: E402
    execute_action,
    seed_builtins,
)
from jarvis_foundry.fabric.model import TaskIntent  # noqa: E402

WORK = HERE / "work"
RECEIPTS = HERE / "receipts"


class Checks:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def check(self, label: str, condition: bool, detail: Any = None) -> None:
        row = {"label": label, "passed": bool(condition)}
        if detail is not None:
            row["detail"] = detail
        self.results.append(row)
        marker = "PASS" if condition else "FAIL"
        print(f"  [{marker}] {label}")
        if not condition and detail is not None:
            print(f"         {detail}")

    @property
    def passed(self) -> bool:
        return all(row["passed"] for row in self.results)


def _write_receipt(name: str, payload: Any) -> None:
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    (RECEIPTS / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _login(session: Any) -> bool:
    target = session.discover_target(
        target_id="login-submit",
        role="button",
        name="Sign in",
        action="submit",
    )
    attempt = session.execute(
        target,
        effect_id="effect:lane1-demo-login",
        idempotency_key="idempotency:lane1-demo-login",
        readback_target={"role": "heading", "name": "Operator dashboard"},
    )
    return attempt.outcome == "COMPLETED"


def _grant(site: str, state_path: Path) -> BrowserExecutionAuthorization:
    return BrowserExecutionAuthorization(
        authorization_id="grant:lane1-demo",
        site_origin=site,
        owner_authorized=True,
        dispatch_enabled=True,
        allowed_actions=("click", "fill", "select", "check", "submit", "download", "upload"),
        allow_downloads=True,
        allow_uploads=True,
        session_state_path=str(state_path),
        profile_id="lane1-demo-profile",
        auth_marker=("status", "Signed in"),
        require_authenticated=True,
        persist_session=True,
        allow_insecure_localhost=True,
    )


def _run(
    database: FoundryDatabase,
    context: ExecutorContext,
    intent: TaskIntent,
    action: str,
    payload: dict[str, Any],
    step: str,
) -> dict[str, Any]:
    result = execute_action(
        database,
        context,
        intent,
        action,
        payload,
        workflow_id="workflow.lane1-demo",
        step_id=step,
    )
    return {
        "action": action,
        "step": step,
        "claimed": result.claimed.value,
        "verified": result.verified.value,
        "note": result.note,
        "observed": result.observed,
        "receipt": result.receipt,
    }


def _session_payload(
    base: str,
    state_path: Path,
    *,
    path: str = "/dashboard",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target_ref": f"{base}{path}",
        "url": f"{base}{path}",
        "profile_id": "lane1-demo-profile",
        "site_origin": base,
        "session_state_path": str(state_path),
        "auth_marker": {"role": "status", "name": "Signed in"},
        "persist_session": True,
        "allowed_hosts": ("127.0.0.1",),
        "allow_insecure_localhost": True,
    }
    payload.update(extra)
    return payload


def run_demo() -> int:
    checks = Checks()
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    owned_root = WORK / "owned"
    owned_root.mkdir()
    (WORK / "steps-downloads").mkdir()
    state_path = WORK / "session" / "demo-state.json"
    state_path.parent.mkdir(parents=True)

    print("1. start local demo app + Playwright health")
    server, _thread, site = app.serve(0)
    connector = PlaywrightBrowserConnector(
        timeout_ms=10_000,
        allowed_hosts=("127.0.0.1",),
        allow_insecure_localhost=True,
    )
    health = connector.health()
    checks.check("playwright available", bool(health.get("available")), health.get("error_code"))
    checks.check("local demo app started", site.startswith("http://127.0.0.1:"))
    if not checks.passed:
        server.shutdown()
        server.server_close()
        return 2

    print("2. login and persist session state")
    with connector.session(
        reauthenticate=_login,
        profile_id="lane1-demo-profile",
        site_origin=site,
        session_state_path=state_path,
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        capture = session.navigate(f"{site}/login")
        checks.check("login page read", capture.success)
        status = session.ensure_authenticated()
        checks.check("session authenticated", status.authenticated, status.state)
        await_state = status.to_evidence()
    checks.check("storage state saved", state_path.is_file())
    checks.check(
        "storage state metadata saved",
        Path(f"{state_path}.meta.json").is_file(),
    )
    _write_receipt("01-session.json", {
        "site": site,
        "session": await_state,
        "state_file_present": state_path.is_file(),
        "state_sha256": await_state.get("state_sha256"),
        "note": "storage-state contents are never serialized into receipts",
    })

    print("3. unattended Fabric surface")
    database = FoundryDatabase(WORK / "foundry.db")
    database.initialize()
    seed_builtins(database)
    intent = TaskIntent(
        task_id="mission.lane1.browser.demo",
        required_actions=("browser.web.inspect", "browser.web.execute"),
        unattended=True,
    )
    grant = _grant(site, state_path)

    ungranted_context = ExecutorContext(
        database=database,
        owned_root=owned_root,
        mcp_backend=None,
    )
    ungranted = _run(
        database,
        ungranted_context,
        intent,
        "browser.web.execute",
        _session_payload(
            site,
            state_path,
            target_id="quick-accept",
            role="button",
            name="Quick accept",
            action="submit",
            readback_target={"role": "status", "name": "Task accepted"},
        ),
        "ungranted",
    )
    checks.check(
        "ungranted mutation is BLOCKED",
        ungranted["claimed"] == "BLOCKED"
        and ungranted["observed"].get("error_code")
        == "owner_browser_authorization_required",
        ungranted["observed"].get("error_code"),
    )
    _write_receipt("02-ungranted-refusal.json", ungranted)

    inspect_payload = _session_payload(site, state_path)
    inspected = _run(
        database,
        ExecutorContext(
            database=database,
            owned_root=owned_root,
            browser_execution_authorization=grant,
        ),
        intent,
        "browser.web.inspect",
        inspect_payload,
        "inspect",
    )
    names = {
        element["name"]
        for element in inspected["observed"].get("elements", [])
        if isinstance(element, dict)
    }
    checks.check("inspect verified MATCH", inspected["verified"] == "MATCH")
    checks.check(
        "inspect exposes role/name targets",
        {"Submit task", "Quick accept", "Download report"} <= names,
        sorted(names),
    )
    checks.check(
        "inspect receipt carries no cookie material",
        "jarvis_session" not in json.dumps(inspected, default=str),
    )
    _write_receipt("03-inspect.json", inspected)

    print("4. granted unattended mutation through Fabric")
    granted_context = ExecutorContext(
        database=database,
        owned_root=owned_root,
        browser_execution_authorization=grant,
    )
    submitted = _run(
        database,
        granted_context,
        intent,
        "browser.web.execute",
        _session_payload(
            site,
            state_path,
            target_id="quick-accept",
            role="button",
            name="Quick accept",
            action="submit",
            readback_target={"role": "status", "name": "Task accepted"},
        ),
        "submit",
    )
    checks.check("granted submit verified MATCH", submitted["verified"] == "MATCH")
    checks.check(
        "application-level readback observed",
        server.state.last_task == "Quick accepted",
        server.state.last_task,
    )
    checks.check(
        "effect journal is COMPLETED",
        submitted["observed"].get("claimed") == "COMPLETED",
    )
    _write_receipt("04-fabric-submit.json", submitted)

    print("5. download through Fabric with hash proof")
    downloaded = _run(
        database,
        granted_context,
        intent,
        "browser.web.execute",
        _session_payload(
            site,
            state_path,
            target_id="download-report",
            role="link",
            name="Download report",
            action="download",
            allow_downloads=True,
            download_target="downloads/report.csv",
        ),
        "download",
    )
    download_path = Path(downloaded["observed"].get("download", {}).get("path", ""))
    expected = app.DOWNLOAD_BYTES
    checks.check("download verified MATCH", downloaded["verified"] == "MATCH")
    checks.check("download file exists in owned root", download_path.is_file())
    if download_path.is_file():
        content = download_path.read_bytes()
        checks.check("download bytes match the app", content == expected)
        checks.check(
            "download sha256 matches receipt",
            hashlib.sha256(content).hexdigest()
            == downloaded["observed"]["download"]["sha256"],
        )
    _write_receipt("05-fabric-download.json", downloaded)

    print("6. upload through Fabric with real acknowledgement")
    evidence = owned_root / "uploads" / "evidence.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(b"lane1 evidence\nbounded upload proof\n")
    uploaded = _run(
        database,
        granted_context,
        intent,
        "browser.web.execute",
        _session_payload(
            site,
            state_path,
            path="/upload",
            target_id="evidence-file",
            role="button",
            name="Evidence file",
            action="upload",
            allow_uploads=True,
            upload_source="uploads/evidence.txt",
            readback_target={
                "role": "status",
                "name": "Upload complete",
                "wait_state": "visible",
                "timeout_ms": 5_000,
            },
        ),
        "upload",
    )
    checks.check("upload verified MATCH", uploaded["verified"] == "MATCH")
    checks.check("upload acknowledgement observed", bool(server.state.uploads))
    if server.state.uploads:
        checks.check(
            "uploaded bytes match on the server",
            server.state.uploads[-1]["sha256"]
            == hashlib.sha256(evidence.read_bytes()).hexdigest(),
        )
    checks.check(
        "upload receipt distinguishes selection and acknowledgement",
        uploaded["observed"].get("upload", {}).get("readback", {}).get("verified") is True
        and uploaded["observed"].get("upload", {}).get("postcondition", {}).get("matched") is True,
    )
    _write_receipt("06-fabric-upload.json", uploaded)

    print("7. close browser and restore the session")
    with connector.session(
        profile_id="lane1-demo-profile",
        site_origin=site,
        session_state_path=state_path,
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        capture = session.navigate(f"{site}/dashboard")
        restored = session.session_status()
    checks.check("session restored after browser restart", capture.success and restored.authenticated, restored.state)
    _write_receipt("07-session-restore.json", {
        "restored": restored.to_evidence(),
        "final_url": capture.page.final_url if capture.page else None,
    })

    print("8. session expiry and the reauth seam")
    with connector.session(
        profile_id="lane1-demo-profile",
        site_origin=site,
        session_state_path=state_path,
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        session.navigate(f"{site}/expire")
    checks.check("app session expired", server.state.auth_epoch == 2)

    expired = _run(
        database,
        ExecutorContext(
            database=database,
            owned_root=owned_root,
            browser_execution_authorization=grant,
        ),
        intent,
        "browser.web.execute",
        _session_payload(
            site,
            state_path,
            target_id="accept-review",
            role="button",
            name="Accept review",
            action="submit",
            readback_target={"role": "status", "name": "Task accepted"},
        ),
        "expired",
    )
    checks.check(
        "expired session is classified, not retried blindly",
        expired["claimed"] == "BLOCKED"
        and expired["observed"].get("error_code") == "session_expired",
        expired["observed"].get("error_code"),
    )
    _write_receipt("08-expired-blocked.json", expired)

    recovered_database = FoundryDatabase(WORK / "foundry-recovered.db")
    recovered_database.initialize()
    seed_builtins(recovered_database)
    recovered = _run(
        recovered_database,
        ExecutorContext(
            database=recovered_database,
            owned_root=owned_root,
            browser_execution_authorization=grant,
            browser_reauth_provider=_login,
        ),
        intent,
        "browser.web.execute",
        _session_payload(
            site,
            state_path,
            target_id="accept-order",
            role="button",
            name="Accept order",
            action="submit",
            readback_target={"role": "status", "name": "Task accepted"},
        ),
        "recovered",
    )
    checks.check("reauth recovered the mission", recovered["verified"] == "MATCH", recovered["note"])
    checks.check(
        "reauth observed AUTHENTICATED",
        recovered["observed"].get("session", {}).get("state") == "AUTHENTICATED",
    )
    checks.check(
        "fresh session state persisted",
        json.loads(Path(f"{state_path}.meta.json").read_text(encoding="utf-8")).get("auth_state")
        == "AUTHENTICATED",
    )
    _write_receipt("09-reauth-recovered.json", recovered)

    print("9. one-session ordered steps")
    with connector.session(
        reauthenticate=_login,
        profile_id="lane1-demo-profile",
        site_origin=site,
        session_state_path=state_path,
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
        allow_downloads=True,
        download_root=WORK / "steps-downloads",
    ) as session:
        steps = session.run_steps(
            [
                {"step_id": "open", "action": "navigate", "url": f"{site}/login"},
                {"step_id": "login", "action": "authenticate"},
                {"step_id": "inspect", "action": "inspect"},
                {
                    "step_id": "fill", "action": "fill",
                    "target_id": "task-title", "role": "textbox",
                    "name": "Task title", "value": "Ordering review",
                },
                {
                    "step_id": "submit", "action": "submit",
                    "target_id": "submit-task", "role": "button",
                    "name": "Submit task",
                    "readback_target": {"role": "status", "name": "Task accepted"},
                },
                {
                    "step_id": "readback", "action": "readback",
                    "role": "status", "name": "Task accepted",
                },
                {
                    "step_id": "download", "action": "download",
                    "target_id": "download-report", "role": "link",
                    "name": "Download report",
                    "download_target": "steps/report.csv",
                },
            ],
            step_scope="lane1-demo-steps",
        )
    checks.check("ordered steps succeeded", steps["success"] is True, steps["stopped_at"])
    checks.check(
        "every step completed",
        [row["outcome"] for row in steps["steps"]] == ["COMPLETED"] * 7,
        [row["outcome"] for row in steps["steps"]],
    )
    checks.check(
        "application received the filled task",
        server.state.last_task == "Ordering review",
        server.state.last_task,
    )
    checks.check(
        "step download is inside the caller-owned root",
        (WORK / "steps-downloads" / "steps" / "report.csv").is_file(),
    )
    _write_receipt("10-run-steps.json", steps)

    print("10. popup tab handling")
    with connector.session(
        profile_id="lane1-demo-profile",
        site_origin=site,
        session_state_path=state_path,
        auth_marker={"role": "status", "name": "Signed in"},
        persist_session=True,
    ) as session:
        session.navigate(f"{site}/newtab")
        tab_target = session.discover_target(
            target_id="open-notes-tab",
            role="link",
            name="Open notes tab",
            action="click",
        )
        opened = session.execute(tab_target, expect_new_page=True)
        page_urls = session.page_urls()
        switched = (
            session.switch_page(index=1) if len(page_urls) == 2 else {}
        )
    checks.check("popup observed COMPLETED", opened.outcome == "COMPLETED", opened.error_code)
    checks.check("two tabs are open", len(page_urls) == 2, page_urls)
    checks.check(
        "switch_page selected the popup",
        switched.get("index") == 1,
        switched.get("index"),
    )
    _write_receipt("11-tabs.json", {
        "attempt_outcome": opened.outcome,
        "after_state": dict(opened.after_state or {}),
        "page_urls": page_urls,
        "switch": switched,
    })

    summary = {
        "verdict": "DEMO_PASS" if checks.passed else "DEMO_FAIL",
        "applied": "playwright-unattended-browser-operator",
        "site": site,
        "checks": checks.results,
        "receipts": sorted(path.name for path in RECEIPTS.glob("*.json")),
    }
    _write_receipt("12-summary.json", summary)
    print(json.dumps(summary["checks"], indent=1)[:2000])
    print(f"\n{summary['verdict']}: {sum(1 for row in checks.results if row['passed'])}/{len(checks.results)} checks passed")
    server.shutdown()
    server.server_close()
    return 0 if checks.passed else 1


def main() -> int:
    argparse.ArgumentParser(
        description="Lane 1 real browser-operator demo",
    ).parse_args()
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())
