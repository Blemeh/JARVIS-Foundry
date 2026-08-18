"""Bounded Screenpipe sidecar adapter.

Source order (contract section 12): the supported local API is used for
health (``/health`` is whitelisted/unauthenticated per Phase-1 evidence);
record reads use read-only SQLite because Phase-1 proved there is NO
dedicated activity-summary endpoint and ``/search`` requires a Bearer token
(403 without it). The SQLite dependency is isolated HERE, schema-guarded and
fail-closed: an unknown schema raises ``SourceFailure(DATA_CORRUPT)`` and
JARVIS never depends on Screenpipe internals anywhere else.

Raw text/frames are never read or carried by default (under-capture).
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SourceFailure(RuntimeError):
    """Deterministic source failure classification (fail closed)."""

    def __init__(self, category: str, detail: str = "") -> None:
        super().__init__(f"{category}: {detail}")
        self.category = category
        self.detail = detail


@dataclass(frozen=True, slots=True)
class SourceHealth:
    status: str  # NOT_RUNNING | STARTING | HEALTHY | STALLED | QUERY_FAILURE
    #           | DATA_CORRUPT | OFFLINE_OK
    version: str = ""
    audio_status: str = "disabled"
    last_frame_seconds_ago: float | None = None
    detail: str = ""

    @property
    def readable(self) -> bool:
        """Historical reads are allowed only when the source DB is usable."""
        return self.status in {"HEALTHY", "OFFLINE_OK"}


_FRAMES_COLUMNS = {
    "id", "timestamp", "app_name", "window_name", "focused",
    "capture_trigger", "content_hash",
}
_UI_EVENTS_COLUMNS = {
    "id", "timestamp", "event_type", "app_name", "window_title", "frame_id",
}


class ScreenpipeClient:
    def __init__(
        self,
        *,
        api_url: str = "http://127.0.0.1:3030",
        data_dir: str | Path | None = None,
        db_path: str | Path | None = None,
        api_token: str | None = None,
        timeout_s: float = 15.0,
        max_records: int = 500,
        stale_threshold_s: float = 600.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.data_dir = Path(data_dir).expanduser() if data_dir else None
        self.db_path = Path(db_path).expanduser() if db_path else None
        self.api_token = api_token
        self.timeout_s = timeout_s
        self.max_records = max_records
        self.stale_threshold_s = stale_threshold_s

    def _resolve_db(self) -> Path:
        if self.db_path is not None:
            path = self.db_path
        elif self.data_dir is not None:
            path = self.data_dir / "db.sqlite"
        else:
            raise SourceFailure("NOT_RUNNING", "no data_dir or db_path")
        if not path.is_file():
            raise SourceFailure("NOT_RUNNING", f"db not found at {path}")
        return path

    def _quick_check(self, path: Path) -> bool:
        try:
            connection = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
            try:
                result = connection.execute("PRAGMA quick_check").fetchone()
                return bool(result) and str(result[0]) == "ok"
            finally:
                connection.close()
        except sqlite3.Error:
            return False

    def _last_frame_age(self, path: Path) -> float | None:
        try:
            from ..clock import parse as parse_ts
            connection = sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
            try:
                row = connection.execute(
                    "SELECT timestamp FROM frames ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                connection.close()
            if row is None or not row[0]:
                return None
            captured = parse_ts(str(row[0]))
            return max(0.0, (time.time() - captured.timestamp()))
        except Exception:
            return None

    def health(self) -> SourceHealth:
        """Classify source state. API failure never masks DB corruption."""
        try:
            path = self._resolve_db()
        except SourceFailure:
            return SourceHealth("NOT_RUNNING", detail="no local history")
        if not self._quick_check(path):
            return SourceHealth("DATA_CORRUPT", detail="PRAGMA quick_check failed")

        api = self._api_health()
        last_age = self._last_frame_age(path)
        if api is not None:
            if last_age is not None and last_age > self.stale_threshold_s:
                return SourceHealth(
                    "STALLED", version=api.get("version", ""),
                    audio_status=str(api.get("audio_status") or "disabled"),
                    last_frame_seconds_ago=last_age,
                    detail="API healthy but no recent frames")
            return SourceHealth(
                "HEALTHY", version=api.get("version", ""),
                audio_status=str(api.get("audio_status") or "disabled"),
                last_frame_seconds_ago=last_age,
                detail="local API healthy")
        if last_age is None:
            return SourceHealth(
                "OFFLINE_OK", detail="history readable; no capture yet")
        if last_age > self.stale_threshold_s:
            return SourceHealth(
                "STALLED", last_frame_seconds_ago=last_age,
                detail="history readable; capture stale")
        return SourceHealth(
            "OFFLINE_OK", last_frame_seconds_ago=last_age,
            detail="history readable (API not reachable)")

    def _api_health(self) -> dict[str, Any] | None:
        request = urllib.request.Request(
            f"{self.api_url}/health", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                if response.status != 200:
                    return None
                body = json.loads(response.read().decode("utf-8", "replace"))
                return body if isinstance(body, dict) else {}
        except (urllib.error.URLError, urllib.error.HTTPError,
                OSError, ValueError):
            return None

    def _guard_schema(self, connection: sqlite3.Connection) -> None:
        frames = {row[1] for row in connection.execute(
            "PRAGMA table_info(frames)").fetchall()}
        ui_events = {row[1] for row in connection.execute(
            "PRAGMA table_info(ui_events)").fetchall()}
        missing_frames = sorted(_FRAMES_COLUMNS - frames)
        missing_ui = sorted(_UI_EVENTS_COLUMNS - ui_events)
        if missing_frames or missing_ui:
            raise SourceFailure(
                "UNKNOWN_SCHEMA",
                f"missing columns: frames={missing_frames} "
                f"ui_events={missing_ui}")

    def read_records(self, after_id: int, limit: int | None = None) -> list[dict[str, Any]]:
        """Bounded read-only fetch of frames (plus their latest ui_event).

        Only metadata columns are read; raw text/frames stay external.
        Returns records ordered by source id.
        """
        path = self._resolve_db()
        if not self._quick_check(path):
            raise SourceFailure("DATA_CORRUPT", "PRAGMA quick_check failed")
        limit = max(1, min(limit or self.max_records, self.max_records))
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            self._guard_schema(connection)
            rows = connection.execute(
                "SELECT id, timestamp, app_name, window_name, "
                "capture_trigger, content_hash FROM frames "
                "WHERE id > ? ORDER BY id LIMIT ?",
                (int(after_id), limit)).fetchall()
            records: list[dict[str, Any]] = []
            records = [
                {
                    "application": str(row["app_name"] or "unknown"),
                    "window": str(
                        (lambda event: (
                            event["window_title"] if event else
                            row["window_name"]))(
                                self._latest_event(connection, row["id"]))
                        or ""),
                    "event_type": (lambda event: (
                        event["event_type"] if event else None))(
                            self._latest_event(connection, row["id"])),
                    "capture_trigger": row["capture_trigger"],
                    "captured_at": str(row["timestamp"] or ""),
                    "source_ids": [int(row["id"])],
                    "content_hash": str(row["content_hash"] or ""),
                    "text_fragments": (),
                }
                for row in rows
            ]
            return records
        except SourceFailure:
            raise
        except sqlite3.Error as error:
            raise SourceFailure(
                "QUERY_FAILURE", f"sqlite error: {error}") from error
        finally:
            connection.close()

    @staticmethod
    def _latest_event(connection: sqlite3.Connection,
                      frame_id: int) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT event_type, window_title FROM ui_events "
            "WHERE frame_id = ? ORDER BY id DESC LIMIT 1",
            (frame_id,)).fetchone()
