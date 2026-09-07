"""Install prefix independent of the developer checkout."""
from __future__ import annotations

import os
from pathlib import Path

_SYSTEM_ROOT = Path(os.environ.get("SystemRoot", r"C:\Windows"))


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


def cli_cmd(root: Path | None = None) -> Path:
    return install_root(root) / "bin" / "jarvis.cmd"


def headless_launcher_cmd(root: Path | None = None) -> Path:
    return install_root(root) / "bin" / "jarvis-supervisor-headless.vbs"


def rollback_runtime_marker(root: Path | None = None) -> Path:
    """Durable marker for a schema-neutral frozen predecessor launch."""
    return install_root(root) / "ROLLBACK_RUNTIME.json"


def rollback_runtime_shim_dir(root: Path | None = None) -> Path:
    """Stable import-only directory used by the scheduled launcher."""
    return install_root(root) / "rollback-runtime" / "active"


def current_release_pythonw(root: Path | None = None) -> Path:
    """Resolve ``pythonw.exe`` for the immutable CURRENT release."""
    sha = current_pointer(root).read_text(encoding="utf-8").strip().splitlines()[0].strip()
    pythonw = release_dir(sha, root) / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        raise FileNotFoundError(f"release pythonw missing for CURRENT {sha}: {pythonw}")
    return pythonw


def headless_task_command(root: Path | None = None) -> tuple[str, str]:
    """Return Task Scheduler ``(command, argument_prefix)`` for production."""
    vbs = headless_launcher_cmd(root)
    wscript = _SYSTEM_ROOT / "System32" / "wscript.exe"
    return str(wscript), f'//B //Nologo "{vbs}"'


def current_pointer(root: Path | None = None) -> Path:
    return install_root(root) / "CURRENT"


def previous_pointer(root: Path | None = None) -> Path:
    return install_root(root) / "PREVIOUS"


def release_dir(source_sha: str, root: Path | None = None) -> Path:
    return install_root(root) / "releases" / source_sha
