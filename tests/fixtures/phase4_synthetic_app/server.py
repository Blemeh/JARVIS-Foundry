"""Harmless local Phase-4 system-of-record fixture.

It accepts only local HTTP requests and persists synthetic records in the
database named by ``PHASE4_RECORDS_DB``.  It is never a customer or public
service and is used only by the bounded browser gate.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("PHASE4_RECORDS_DB", str(ROOT / "records.sqlite")))


def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "task TEXT NOT NULL, option TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    return conn


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        if self.path in {"/", "/index.html"}:
            body = (ROOT / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if self.path == "/state":
            with connection() as conn:
                count = int(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])
                row = conn.execute(
                    "SELECT id, task, option, created_at FROM records ORDER BY id DESC LIMIT 1"
                ).fetchone()
            self._json(200, {"count": count, "prev": (
                {"id": row[0], "task": row[1], "option": row[2], "created_at": row[3]}
                if row else None)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        if self.path != "/tasks":
            self._json(404, {"error": "not found"})
            return
        payload = self._body()
        task = str(payload.get("task", "")).strip()
        option = str(payload.get("option", "")).strip()
        if not task or not option:
            self._json(400, {"error": "missing task or option"})
            return
        with connection() as conn:
            cursor = conn.execute(
                "INSERT INTO records(task, option, created_at) VALUES (?, ?, ?)",
                (task, option, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            conn.commit()
        self._json(200, {"accepted": True, "id": cursor.lastrowid,
                         "task": task, "option": option})

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8797"))
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()

