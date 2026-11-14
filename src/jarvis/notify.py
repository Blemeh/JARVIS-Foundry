"""Optional generic webhook: POST a small JSON document."""

from __future__ import annotations

import json
import urllib.request


def send(url: str, event: str, payload: dict, timeout: float = 10) -> str | None:
    """Returns None on success, else the error text. Never raises."""
    if not url:
        return None
    data = json.dumps({"event": event, **payload}).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "jarvis"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return None if 200 <= response.status < 300 else f"HTTP {response.status}"
    except Exception as exc:  # noqa: BLE001 - a webhook must never break a task
        return str(exc)
