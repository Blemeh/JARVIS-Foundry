"""Install prefix independent of the developer checkout."""
from __future__ import annotations

import os
from pathlib import Path


def install_root(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser().resolve()
    env = os.environ.get("JARVIS_INSTALL_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (local / "JARVIS").resolve()


def default_live_foundry_home() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (local / "hermes" / "foundry").resolve()


def launcher_cmd(root: Path | None = None) -> Path:
    return install_root(root) / "bin" / "jarvis-supervisor.cmd"


def current_pointer(root: Path | None = None) -> Path:
    return install_root(root) / "CURRENT"


def previous_pointer(root: Path | None = None) -> Path:
    return install_root(root) / "PREVIOUS"


def release_dir(source_sha: str, root: Path | None = None) -> Path:
    return install_root(root) / "releases" / source_sha
