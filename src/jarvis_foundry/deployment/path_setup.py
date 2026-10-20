"""Install the global ``jarvis`` command and ensure it is on the user PATH."""
from __future__ import annotations

import ctypes
import os
import sys
import winreg
from pathlib import Path

from .layout import cli_cmd, install_root
from .release import (
    JARVIS_CLI_CMD_TEXT,
    JARVIS_CLI_CURRENT_SHA_VALIDATOR_TEXT,
)


def cli_launcher_destinations(root: Path | None = None) -> tuple[Path, ...]:
    """Locations for ``jarvis.cmd``; includes dirs already on typical PATH."""
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (
        cli_cmd(root),
        local / "Python" / "bin" / "jarvis.cmd",
    )


def _notify_environment_changed() -> None:
    if sys.platform != "win32":
        return
    ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
        0xFFFF,
        0x001A,
        0,
        "Environment",
        0,
        1000,
        None,
    )


def ensure_jarvis_bin_on_path(root: Path | None = None) -> bool:
    """Add ``%LOCALAPPDATA%\\JARVIS\\bin`` to the front of the user PATH."""
    if sys.platform != "win32":
        return False
    bin_dir = str((install_root(root) / "bin").resolve())
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    ) as key:
        try:
            path, _kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            path = ""
        parts = [part for part in str(path).split(";") if part]
        normalized = {Path(part).resolve() for part in parts}
        if Path(bin_dir) in normalized:
            return False
        updated = ";".join([bin_dir, *parts]) if parts else bin_dir
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, updated)
    os.environ["Path"] = f"{bin_dir};{os.environ.get('Path', '')}"
    _notify_environment_changed()
    return True


def install_cli_launchers(root: Path | None = None) -> Path:
    """Write ``jarvis.cmd`` and its CURRENT validator to known locations."""
    primary = cli_cmd(root)
    validator = install_root(root) / "bin" / "validate-current.ps1"
    validator.parent.mkdir(parents=True, exist_ok=True)
    validator.write_text(JARVIS_CLI_CURRENT_SHA_VALIDATOR_TEXT, encoding="utf-8")
    for destination in cli_launcher_destinations(root):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(JARVIS_CLI_CMD_TEXT, encoding="utf-8")
    return primary


def bootstrap_global_cli(root: Path | None = None) -> Path:
    """Write ``jarvis.cmd`` launchers and ensure the install bin dir is on PATH."""
    launcher = install_cli_launchers(root)
    ensure_jarvis_bin_on_path(root)
    return launcher


def cli_launcher_installed(root: Path | None = None) -> bool:
    return cli_cmd(root).is_file()
