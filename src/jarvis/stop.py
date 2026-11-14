"""The STOP sentinel: a file in JARVIS_HOME. No LLM, no database needed."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path


def sentinel(home: Path) -> Path:
    return home / "STOP"


def active(home: Path) -> bool:
    return sentinel(home).exists()


def activate(home: Path, reason: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    record = {"reason": reason, "at": datetime.now(UTC).isoformat(timespec="seconds")}
    tmp = home / f".STOP.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(record) + "\n", encoding="utf-8")
    os.replace(tmp, sentinel(home))


def read(home: Path) -> dict | None:
    try:
        data = json.loads(sentinel(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"reason": "unreadable sentinel"}
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"reason": "unreadable sentinel"}


def clear(home: Path) -> bool:
    for attempt in range(8):
        try:
            sentinel(home).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:  # Windows may briefly hold the file open
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))
    return False
