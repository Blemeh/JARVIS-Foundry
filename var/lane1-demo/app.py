"""Lane 1 demo application: a real local HTTP app with cookie sessions.

Stdlib only.  It exists so the browser operator can be proven against real
cookies, real redirects, real session expiry, real downloads, and a real
upload acknowledgement instead of a static file.  It is a toy, local-only
application; it has no passwords, no external network, and no persistence
beyond process memory.

Run standalone:

    python var/lane1-demo/app.py            # 127.0.0.1:8791 by default
    python var/lane1-demo/app.py --port 0   # ephemeral port (prints it)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


DOWNLOAD_BYTES = b"task,status\nalpha,done\nbeta,open\n"
LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Demo sign in</title></head>
<body>
<h1>Demo sign in</h1>
<form method="post" action="/login">
  <button type="submit" aria-label="Sign in">Sign in</button>
</form>
</body></html>
"""


def _dashboard_page(task: str | None) -> str:
    if task:
        status = (
            f'<p role="status" aria-label="Task accepted">'
            f"Task accepted: {html.escape(task)}</p>"
        )
    else:
        status = '<p role="status" aria-label="No task yet">No task yet</p>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Operator dashboard</title></head>
<body>
<p role="status" aria-label="Signed in">Signed in</p>
<h1>Operator dashboard</h1>
{status}
<form method="post" action="/submit-task">
  <label for="task">Task title</label>
  <input id="task" name="task" aria-label="Task title">
  <button type="submit" aria-label="Submit task">Submit task</button>
</form>
<form method="post" action="/submit-task">
  <button type="submit" name="task" value="Quick accepted"
          aria-label="Quick accept">Quick accept</button>
</form>
<form method="post" action="/submit-task">
  <button type="submit" name="task" value="Accepted review"
          aria-label="Accept review">Accept review</button>
</form>
<form method="post" action="/submit-task">
  <button type="submit" name="task" value="Accepted order"
          aria-label="Accept order">Accept order</button>
</form>
<a href="/download/report.csv" download aria-label="Download report">Download report</a>
<a href="/upload" aria-label="Upload evidence">Upload evidence</a>
<a href="/newtab" aria-label="Open notes tab">Open notes tab</a>
<a href="/expire" aria-label="Expire session">Expire session</a>
</body></html>
"""


UPLOAD_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Upload evidence</title></head>
<body>
<p role="status" aria-label="Signed in">Signed in</p>
<h1>Upload evidence</h1>
<p role="status" aria-label="Waiting for upload">Waiting for upload</p>
<input id="evidence" type="file" aria-label="Evidence file">
<div id="complete" role="status" aria-label="Upload complete" hidden>Upload complete</div>
<script>
document.querySelector("#evidence").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) { return; }
  const response = await fetch("/uploaded", {
    method: "POST",
    headers: {"Content-Type": "application/octet-stream", "X-File-Name": file.name},
    body: await file.arrayBuffer()
  });
  if (response.ok) {
    document.querySelector("#complete").hidden = false;
  }
});
</script>
</body></html>
"""


class DemoState:
    def __init__(self) -> None:
        self.auth_epoch = 1
        self.last_task: str | None = None
        self.uploads: list[dict[str, object]] = []
        self.lock = threading.Lock()


class DemoHandler(BaseHTTPRequestHandler):
    server: "DemoServer"

    @property
    def state(self) -> DemoState:
        return self.server.state

    def _authenticated(self) -> bool:
        cookie = self.headers.get("Cookie") or ""
        return f"jarvis_session={self.state.auth_epoch}" in cookie

    def _send(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._send(body.encode("utf-8"), status=status, headers=headers)

    def _redirect(self, location: str, *, headers: dict[str, str] | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path in {"/", "/login"}:
            if self.path == "/" and self._authenticated():
                self._redirect("/dashboard")
                return
            self._html(LOGIN_PAGE)
            return
        if self.path == "/expire":
            with self.state.lock:
                self.state.auth_epoch += 1
            self._redirect(
                "/login",
                headers={"Set-Cookie": "jarvis_session=; Max-Age=0; Path=/"},
            )
            return
        if self.path.startswith("/dashboard"):
            if not self._authenticated():
                self._redirect("/login")
                return
            self._html(_dashboard_page(self.state.last_task))
            return
        if self.path == "/download/report.csv":
            if not self._authenticated():
                self._redirect("/login")
                return
            self._send(
                DOWNLOAD_BYTES,
                content_type="text/csv",
                headers={
                    "Content-Disposition": 'attachment; filename="lane1-report.csv"'
                },
            )
            return
        if self.path == "/upload":
            if not self._authenticated():
                self._redirect("/login")
                return
            self._html(UPLOAD_PAGE)
            return
        if self.path == "/newtab":
            if not self._authenticated():
                self._redirect("/login")
                return
            self._html(
                "<!doctype html><title>New tab</title>"
                '<p role="status" aria-label="Signed in">Signed in</p>'
                "<h1>New tab</h1>"
                "<a href='/tab' target='_blank' aria-label='Open notes tab'>"
                "Open notes tab</a>"
            )
            return
        if self.path == "/tab":
            if not self._authenticated():
                self._redirect("/login")
                return
            self._html(
                "<!doctype html><title>Notes tab</title>"
                '<p role="status" aria-label="Signed in">Signed in</p>'
                "<h1>Notes tab</h1>"
                '<p role="status" aria-label="Tab ready">Tab ready</p>'
            )
            return
        self._send(b"not found", status=404, content_type="text/plain")

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path == "/login":
            self._redirect(
                "/dashboard",
                headers={
                    "Set-Cookie": (
                        f"jarvis_session={self.state.auth_epoch}; Path=/"
                    )
                },
            )
            return
        if self.path == "/submit-task":
            if not self._authenticated():
                self._redirect("/login")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            form = parse_qs(raw.decode("utf-8", "replace"))
            task = str((form.get("task") or [""])[0]).strip()
            with self.state.lock:
                self.state.last_task = task or None
            self._redirect("/dashboard")
            return
        if self.path == "/uploaded":
            if not self._authenticated():
                self._send(b"forbidden", status=403, content_type="text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            record = {
                "name": str(self.headers.get("X-File-Name") or ""),
                "size_bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            with self.state.lock:
                self.state.uploads.append(record)
            self._send(
                json.dumps({"accepted": True, **record}).encode("utf-8"),
                content_type="application/json",
            )
            return
        self._send(b"not found", status=404, content_type="text/plain")

    def log_message(self, _format: str, *_args: object) -> None:
        return


class DemoServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, DemoHandler)
        self.state = DemoState()


def serve(port: int = 8791) -> tuple[DemoServer, threading.Thread, str]:
    server = DemoServer(("127.0.0.1", port))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    return server, thread, base_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    server = DemoServer(("127.0.0.1", args.port))
    print(f"lane1 demo app listening on http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
