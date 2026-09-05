from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from ..clock import isoformat


class EmergencyStop:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def active(self) -> bool:
        return self.path.exists()

    def activate(self, reason: str, *, source: str = "local_cli") -> dict[str, str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "stop_id": str(uuid.uuid4()),
            "requested_at": isoformat(),
            "source": source,
            "reason": reason,
        }
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4()}.tmp")
        temporary.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        return record

    def read(self) -> dict[str, str] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "stop_id": "unparseable-sentinel",
                "requested_at": isoformat(),
                "source": "filesystem",
                "reason": "sentinel exists but could not be parsed",
            }
        if not isinstance(value, dict):
            return {
                "stop_id": "unparseable-sentinel",
                "requested_at": isoformat(),
                "source": "filesystem",
                "reason": "sentinel exists but is not a JSON object",
            }
        # Fail closed: any missing field receives a deterministic default so
        # every consumer sees a complete record and STOP always reaches the
        # freeze state machine.
        return {
            "stop_id": str(value.get("stop_id") or "sentinel-without-id"),
            "requested_at": str(value.get("requested_at") or isoformat()),
            "source": str(value.get("source") or "filesystem"),
            "reason": str(value.get("reason") or "sentinel exists but is incomplete"),
        }

    def clear(self) -> bool:
        for attempt in range(8):
            try:
                self.path.unlink()
                return True
            except FileNotFoundError:
                return False
            except OSError:
                if attempt == 7:
                    raise
                # Windows can briefly deny deletion while a concurrent
                # readback request closes its handle.
                time.sleep(0.01 * (attempt + 1))
        return False

