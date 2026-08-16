"""R10-G06: real visual inspection, DOM accessibility audit, states, restart."""

from __future__ import annotations

import html
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    REPO_ROOT,
    safe_clear,
    sha256_file,
    write_json,
)

from jarvis_foundry.command_center.directives import DirectiveService
from jarvis_foundry.command_center.ledger import DirectiveLedger
from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.seeding import seed_proving_stores
from jarvis_foundry.command_center.server import serve
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

CHROME_CANDIDATES = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
)
G06_TEMP = Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder")
G06_MAIN_ROOT = G06_TEMP / "g06-main"
G06_EMPTY_ROOT = G06_TEMP / "g06-empty"


def _chrome() -> Path:
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    return Path("")


def _run(
    command: list[str],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "wall_seconds": round(time.monotonic() - started, 3),
        "output": (completed.stdout + completed.stderr)[-4000:],
    }


def _audit_from_dom(dom: str) -> dict[str, Any]:
    start = dom.find('<pre id="r10-a11y-audit">')
    end = dom.find("</pre>", start)
    if start < 0 or end < 0:
        return {"passed": False, "error": "audit pre not found in DOM dump"}
    raw = html.unescape(dom[start + len('<pre id="r10-a11y-audit">') : end])
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"passed": False, "error": f"audit JSON parse failed: {exc}", "raw": raw[:500]}
    payload["passed"] = bool(payload.get("passed"))
    return payload


def _http(base: str, path: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=15) as response:
            body = response.read()
            return {
                "status": response.status,
                "bytes": len(body),
                "sha256": sha256_file_bytes(body),
            }
    except (OSError, ValueError) as error:
        return {"status": -1, "error": str(error)[:300]}


def sha256_file_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    chrome = _chrome()
    browser_available = bool(chrome)
    if G06_MAIN_ROOT.is_dir():
        safe_clear(G06_MAIN_ROOT)
    layout = ProvingLayout(G06_MAIN_ROOT)
    seed_proving_stores(layout, REPO_ROOT / "docs" / "remaster" / "evidence")
    running = serve(layout, evidence_root=REPO_ROOT / "docs" / "remaster" / "evidence", seed=False)
    base = running.base_url
    results: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(f"{base}/api/state", timeout=15) as response:
            state = json.loads(response.read())
        mission_id = state["missions"][0]["record_id"]
        http_checks = {
            "health": _http(base, "/api/health"),
            "state": _http(base, "/api/state"),
            "briefing_en": _http(base, "/api/briefings?lang=en"),
            "briefing_sq": _http(base, "/api/briefings?lang=sq"),
            "evidence_drilldown": _http(base, f"/api/evidence/{mission_id}"),
            "page_en": _http(base, "/"),
            "page_sq": _http(base, "/?lang=sq"),
        }
        with urllib.request.urlopen(f"{base}/", timeout=15) as response:
            page_html = response.read().decode("utf-8")
        if browser_available:
            user_data = Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder/chrome-profile")
            user_data.mkdir(parents=True, exist_ok=True)
            desktop_png = EVIDENCE_DIR / "R10_VISUAL_DESKTOP.png"
            mobile_png = EVIDENCE_DIR / "R10_VISUAL_MOBILE.png"
            desktop = _run(
                [
                    str(chrome),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--hide-scrollbars",
                    f"--user-data-dir={user_data}",
                    "--window-size=1440,1000",
                    f"--screenshot={desktop_png}",
                    f"{base}/",
                ],
                timeout_seconds=180,
            )
            mobile = _run(
                [
                    str(chrome),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--hide-scrollbars",
                    "--force-device-scale-factor=1",
                    f"--user-data-dir={user_data}",
                    "--window-size=390,844",
                    f"--screenshot={mobile_png}",
                    f"{base}/?lang=sq",
                ],
                timeout_seconds=180,
            )
            dom_run = _run(
                [
                    str(chrome),
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--user-data-dir={user_data}",
                    "--dump-dom",
                    f"{base}/?audit=1",
                ],
                timeout_seconds=180,
            )
            dom_path = EVIDENCE_DIR / "R10_VISUAL_DOM_DUMP.html"
            dom_path.write_text(dom_run["output"], encoding="utf-8")
            audit = _audit_from_dom(dom_run["output"])
            write_json(EVIDENCE_DIR / "R10_VISUAL_DOM_AUDIT.json", audit)
            results["browser"] = {
                "available": True,
                "path": str(chrome),
                "desktop": {
                    "exit_code": desktop["exit_code"],
                    "wall_seconds": desktop["wall_seconds"],
                    "screenshot": "R10_VISUAL_DESKTOP.png",
                    "sha256": sha256_file(desktop_png) if desktop_png.is_file() else None,
                    "size": desktop_png.stat().st_size if desktop_png.is_file() else 0,
                },
                "mobile": {
                    "exit_code": mobile["exit_code"],
                    "wall_seconds": mobile["wall_seconds"],
                    "screenshot": "R10_VISUAL_MOBILE.png",
                    "sha256": sha256_file(mobile_png) if mobile_png.is_file() else None,
                    "size": mobile_png.stat().st_size if mobile_png.is_file() else 0,
                },
                "dom_dump": {
                    "exit_code": dom_run["exit_code"],
                    "output": "R10_VISUAL_DOM_DUMP.html",
                    "sha256": sha256_file(dom_path),
                    "audit": audit,
                    "audit_output": "R10_VISUAL_DOM_AUDIT.json",
                },
            }
        else:
            results["browser"] = {
                "available": False,
                "path": None,
                "note": "no Chrome/Edge found; HTTP HTML capture only",
            }
            write_json(
                EVIDENCE_DIR / "R10_VISUAL_DOM_AUDIT.json",
                {"passed": False, "error": "browser not available; DOM audit NOT_TESTED"},
            )
    finally:
        running.close()

    # Restart/reconciliation against a durable store, not the closed HTTP server.
    second = DirectiveService(
        PortfolioStore(layout.portfolio),
        DirectiveLedger(layout.directive_ledger),
        clock=FixedClock(EVIDENCE_UTC),
    )
    second.apply(
        command_type="PAUSE",
        target="operators",
        reason="visual gate restart",
        nonce="g06-restart-2",
    )
    third = DirectiveService(
        PortfolioStore(layout.portfolio),
        DirectiveLedger(layout.directive_ledger),
        clock=FixedClock(EVIDENCE_UTC),
    )
    third.ledger.verify()
    restart_evidence = {
        "directive_applied": True,
        "ledger_verify_after_reopen": True,
        "state_after_reopen": third.state_dict(),
        "paused_after_reopen": "operators" in third.state().paused_projects,
    }

    if G06_EMPTY_ROOT.is_dir():
        safe_clear(G06_EMPTY_ROOT)
    empty_layout = ProvingLayout(G06_EMPTY_ROOT)
    empty_layout.ensure()
    empty_running = serve(
        empty_layout, evidence_root=REPO_ROOT / "docs" / "remaster" / "evidence", seed=False
    )
    try:
        empty_page = _http(empty_running.base_url, "/")
        with urllib.request.urlopen(f"{empty_running.base_url}/", timeout=15) as response:
            empty_html = response.read().decode("utf-8")
    finally:
        empty_running.close()
    empty_state = {
        "empty_page_status": empty_page["status"],
        "empty_banner_rendered": "banner-empty" in empty_html,
        "empty_text_rendered": "No durable records yet" in empty_html,
    }

    browser_ok = True
    if browser_available:
        browser_ok = (
            results["browser"]["desktop"]["exit_code"] == 0
            and results["browser"]["mobile"]["exit_code"] == 0
            and results["browser"]["desktop"]["size"] > 10_000
            and results["browser"]["mobile"]["size"] > 5_000
            and results["browser"]["dom_dump"]["audit"].get("passed") is True
        )
    checks = {
        "http_endpoints": all(item["status"] == 200 for item in http_checks.values()),
        "visual_inspected": browser_ok if browser_available else False,
        "dom_audit_passed": (
            results.get("browser", {}).get("dom_dump", {}).get("audit", {}).get("passed") is True
            if browser_available
            else False
        ),
        "provider_failure_banner_rendered": "banner-provider" in page_html,
        "empty_state_rendered": empty_state["empty_banner_rendered"]
        and empty_state["empty_text_rendered"],
        "restart_reconciliation": restart_evidence["paused_after_reopen"],
        "stale_and_degraded_flags_in_state": bool(state["data_quality"]["degraded"])
        and bool(state["data_quality"]["provider_failure"]),
    }
    passed = all(checks.values())
    payload = {
        "schema": "jarvis-r10-g06-visual-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "http_checks": http_checks,
        "visual": results,
        "empty_state": empty_state,
        "restart_reconciliation": restart_evidence,
        "evidence_files": [
            "R10_VISUAL_DESKTOP.png",
            "R10_VISUAL_MOBILE.png",
            "R10_VISUAL_DOM_DUMP.html",
            "R10_VISUAL_DOM_AUDIT.json",
        ],
    }
    write_json(EVIDENCE_DIR / "R10_G06_VISUAL.json", payload)
    print(
        f"g06 visual: passed={passed} browser={browser_available} "
        f"checks={sum(checks.values())}/{len(checks)} wall_seconds={payload['wall_seconds']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
