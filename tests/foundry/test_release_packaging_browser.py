"""Fresh immutable-release proof for the V10 browser dependency closure."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.release import build_release, set_current
from jarvis_foundry.migrations import SCHEMA_VERSION


PAGE = b"""<!doctype html>
<html><head><title>V10 packaging fixture</title></head>
<body><main><h1>Fresh release browser fixture</h1>
<p>Bounded read-only navigation.</p></main></body></html>"""


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path != "/page":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args: object) -> None:
        return


def _clean_release_env(*, root: Path, home: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
        "PLAYWRIGHT_BROWSERS_PATH",
    ):
        environment.pop(name, None)
    environment.update({
        "PYTHONNOUSERSITE": "1",
        "JARVIS_INSTALL_ROOT": str(root),
        "JARVIS_HOME": str(home),
    })
    return environment


def _release_browser_processes(browser_root: Path) -> list[dict[str, object]]:
    """Inspect only Chromium executables owned by this disposable release."""
    if os.name != "nt":
        return []
    environment = dict(os.environ)
    environment["JARVIS_TEST_BROWSER_ROOT"] = str(browser_root.resolve())
    script = (
        "$root=[Environment]::GetEnvironmentVariable('JARVIS_TEST_BROWSER_ROOT','Process'); "
        "$rows=@(Get-CimInstance Win32_Process | "
        "Where-Object { $_.ExecutablePath -and "
        "$_.ExecutablePath.StartsWith($root,[System.StringComparison]::OrdinalIgnoreCase) } | "
        "Select-Object ProcessId,ExecutablePath,CommandLine); "
        "if($rows.Count -gt 0){$rows | ConvertTo-Json -Compress}"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    if not result.stdout.strip():
        return []
    payload = json.loads(result.stdout)
    if isinstance(payload, dict):
        payload = [payload]
    return payload


def test_fresh_immutable_release_contains_and_runs_v10_web_runtime(tmp_path: Path) -> None:
    """Build from a clean SHA; the pre-repair base install fails these assertions."""
    source_repo = Path(__file__).resolve().parents[2]
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tracked_dirty = [
        line for line in subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=source_repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if line and not line.startswith("??")
    ]
    assert not tracked_dirty

    install_root = tmp_path / "JARVIS"
    foundry_home = tmp_path / "foundry"
    built = build_release(
        source_repo=source_repo,
        source_sha=sha,
        python_exe=sys.executable,
        root=install_root,
    )
    release_dir = Path(built["release_dir"])
    release_python = Path(built["python_exe"])
    browser_root = release_dir / ".pw"
    manifest = json.loads((release_dir / "RELEASE.json").read_text(encoding="utf-8"))
    runtime = manifest["web_runtime"]
    assert manifest["source_sha"] == sha
    assert manifest["git_commit"] == sha
    assert manifest["schema_compatibility"]["package_schema"] == SCHEMA_VERSION
    assert runtime["contract"] == "release-local"
    assert runtime["browsers_path"] == ".pw"
    assert browser_root.is_dir()

    database = FoundryDatabase(foundry_home / "foundry.db")
    database.initialize()
    set_current(sha, root=install_root, database=database)
    environment = _clean_release_env(root=install_root, home=foundry_home)

    validated = subprocess.run(
        [
            str(release_python), "-m", "jarvis_foundry.deployment.launcher",
            "--home", str(foundry_home), "--validate-only",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    assert json.loads(validated.stdout)["launcher"] == "OK"

    ordinary_start = subprocess.run(
        [
            str(release_python), "-m", "jarvis_foundry.deployment.launcher",
            "--home", str(foundry_home), "--once",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    assert ordinary_start.returncode == 0

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/page"
        browser_before = _release_browser_processes(browser_root)
        probe_code = """
import importlib.metadata
import json
import sys
from pathlib import Path
import jarvis_foundry
import playwright
import trafilatura
from jarvis_foundry.connectors.web_research import (
    PlaywrightBrowserConnector,
    TrafilaturaExtractor,
)

url = sys.argv[1]
release_root = Path(sys.prefix).resolve().parent
assert Path(jarvis_foundry.__file__).resolve().is_relative_to(release_root)
assert importlib.metadata.version("playwright") == "1.62.0"
assert importlib.metadata.version("trafilatura") == "2.2.0"
browser = PlaywrightBrowserConnector(
    headless=True,
    timeout_ms=10000,
    allowed_hosts=("127.0.0.1",),
    allow_insecure_localhost=True,
)
health = browser.health()
assert health["status"] == "READY", health
assert health["available"] is True
assert health["browser_version"]
browser_executable = Path(health["browser_executable"]).resolve()
browser_root = (release_root / ".pw").resolve()
assert browser_executable.is_relative_to(browser_root)
with browser.session(
    allowed_hosts=("127.0.0.1",),
    persist_session=False,
) as session:
    capture = session.navigate(url)
assert capture.success
assert capture.page is not None
assert capture.page.status == 200
extracted = TrafilaturaExtractor().extract(capture)
assert extracted.success
print(json.dumps({
    "python": sys.executable,
    "package_origin": jarvis_foundry.__file__,
    "playwright_module": playwright.__file__,
    "trafilatura_module": trafilatura.__file__,
    "playwright": importlib.metadata.version("playwright"),
    "trafilatura": importlib.metadata.version("trafilatura"),
    "browser_health": health,
    "navigation_status": capture.page.status,
    "extraction": extracted.success,
}, sort_keys=True, default=str))
"""
        browser_probe = subprocess.run(
            [str(release_python), "-c", probe_code, url],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            env=environment,
        )
        report = json.loads(browser_probe.stdout.strip().splitlines()[-1])
        assert report["browser_health"]["status"] == "READY"
        assert report["browser_health"]["browser_version"]
        assert report["navigation_status"] == 200
        assert report["extraction"] is True
        assert _release_browser_processes(browser_root) == browser_before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
