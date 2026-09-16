"""Owner UI preference file and attention-toast delivery. Not a new database."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .windows_toast import (
    MODE_IN_APP_TOAST,
    VALID_MODES,
    AttentionToastRouter,
    WindowsToastAdapter,
)

PREFS_NAME = "owner_ui_prefs.json"
TOAST_STORE_NAME = "attention_toast_seen.json"
VALID_LOCALES = frozenset({"en", "sq"})
DEFAULT_PREFS = {
    "locale": "en",
    "notification_routing": MODE_IN_APP_TOAST,
}


def prefs_path(runtime_home: str | Path) -> Path:
    return Path(runtime_home).expanduser().resolve() / PREFS_NAME


def toast_store_path(runtime_home: str | Path) -> Path:
    return Path(runtime_home).expanduser().resolve() / TOAST_STORE_NAME


def load_owner_ui_prefs(runtime_home: str | Path) -> dict[str, str]:
    prefs = dict(DEFAULT_PREFS)
    path = prefs_path(runtime_home)
    if not path.is_file():
        return prefs
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return prefs
    if not isinstance(raw, dict):
        return prefs
    locale = str(raw.get("locale") or prefs["locale"]).strip().lower()
    routing = str(raw.get("notification_routing") or prefs["notification_routing"]).strip().lower()
    if locale in VALID_LOCALES:
        prefs["locale"] = locale
    if routing in VALID_MODES:
        prefs["notification_routing"] = routing
    return prefs


def save_owner_ui_prefs(runtime_home: str | Path, payload: dict[str, Any]) -> dict[str, str]:
    current = load_owner_ui_prefs(runtime_home)
    locale = str(payload.get("locale") or current["locale"]).strip().lower()
    routing = str(
        payload.get("notification_routing")
        or payload.get("notificationRouting")
        or current["notification_routing"]
    ).strip().lower()
    if locale not in VALID_LOCALES:
        raise ValueError(f"unsupported locale: {locale}")
    if routing not in VALID_MODES:
        raise ValueError(f"unsupported notification routing: {routing}")
    prefs = {"locale": locale, "notification_routing": routing}
    path = prefs_path(runtime_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs


def toast_result_payload(result: Any) -> dict[str, Any]:
    return {
        "status": result.status,
        "attention_id": result.attention_id,
        "delivered": result.delivered,
        "detail": result.detail,
    }


def deliver_open_attention_toasts(
    items: list[dict[str, Any]] | None,
    *,
    adapter: WindowsToastAdapter | None = None,
    mode: str,
    store_path: str | Path | None,
) -> list[dict[str, Any]]:
    router = AttentionToastRouter(adapter, mode=mode, store_path=store_path)
    results: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        results.append(toast_result_payload(router.emit(item)))
    return results
