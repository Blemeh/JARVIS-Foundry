"""Windows toast adapter for owner-attention items. Not a new queue."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODE_IN_APP = "in-app"
MODE_IN_APP_TOAST = "in-app-toast"
MODE_OFF = "off"
VALID_MODES = frozenset({MODE_IN_APP, MODE_IN_APP_TOAST, MODE_OFF})


class ToastUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToastResult:
    status: str
    attention_id: str
    delivered: bool
    detail: str = ""


class WindowsToastAdapter:
    provider = "windows-toast"

    def show(self, *, title: str, body: str, attention_id: str) -> None:
        try:
            from winotify import Notification
        except ImportError as error:
            raise ToastUnavailable("winotify is not installed") from error
        toast = Notification(
            app_id="JARVIS Foundry",
            title=title[:120],
            msg=f"{body[:220]}\n{attention_id}" if attention_id else body[:240],
        )
        toast.show()


class AttentionToastRouter:
    """In-session plus optional JSON-file dedup keyed by attention identity."""

    def __init__(
        self,
        adapter: WindowsToastAdapter | None = None,
        *,
        mode: str = MODE_IN_APP_TOAST,
        store_path: str | Path | None = None,
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"unsupported notification mode: {mode}")
        self.adapter = adapter or WindowsToastAdapter()
        self.mode = mode
        self.store_path = Path(store_path) if store_path is not None else None
        self._emitted: dict[str, str] = {}
        if self.store_path is not None and self.store_path.is_file():
            raw = json.loads(self.store_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._emitted = {str(key): str(value) for key, value in raw.items()}

    def _persist(self) -> None:
        if self.store_path is None:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(json.dumps(self._emitted, indent=2), encoding="utf-8")

    def emit(self, item: dict[str, Any]) -> ToastResult:
        attention_id = str(
            item.get("queue_id")
            or item.get("attention_id")
            or item.get("dedupe_key")
            or ""
        ).strip()
        if not attention_id:
            return ToastResult("MALFORMED", "", False, "attention identity missing")
        if self.mode == MODE_OFF:
            return ToastResult("SUPPRESSED", attention_id, False, "notifications off")
        if attention_id in self._emitted:
            return ToastResult("DEDUPED", attention_id, False, "already emitted")
        title = str(item.get("classification") or "JARVIS").strip() or "JARVIS"
        body = str(item.get("summary") or item.get("consequence") or attention_id).strip()
        delivered = False
        detail = "in-app only"
        if self.mode == MODE_IN_APP_TOAST:
            self.adapter.show(title=title, body=body, attention_id=attention_id)
            delivered = True
            detail = "windows toast"
        self._emitted[attention_id] = datetime.now(timezone.utc).isoformat()
        self._persist()
        return ToastResult("EMITTED", attention_id, delivered, detail)
