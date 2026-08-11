from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FoundryPaths:
    home: Path

    @classmethod
    def discover(cls, override: str | Path | None = None) -> "FoundryPaths":
        if override:
            home = Path(override)
        elif os.environ.get("JARVIS_HOME"):
            home = Path(os.environ["JARVIS_HOME"])
        elif os.environ.get("HERMES_HOME"):
            home = Path(os.environ["HERMES_HOME"]) / "foundry"
        else:
            local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            home = local / "hermes" / "foundry"
        return cls(home.expanduser().resolve())

    @property
    def database(self) -> Path:
        return self.home / "foundry.db"

    @property
    def emergency_stop(self) -> Path:
        return self.home / "EMERGENCY_STOP"

    @property
    def evidence(self) -> Path:
        return self.home / "evidence"

    @classmethod
    def hermes_home(cls) -> Path:
        """Resolve the Hermes home for the scheduled task's HERMES_HOME env
        (explicit env override, else the local Hermes profile dir)."""
        env = os.environ.get("HERMES_HOME")
        if env:
            return Path(env).expanduser().resolve()
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local / "hermes"

