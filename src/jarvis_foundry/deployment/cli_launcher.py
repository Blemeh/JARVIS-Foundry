"""Run the owner CLI only from the immutable release selected by CURRENT."""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


_GIT_OBJECT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class CliLaunchError(RuntimeError):
    """The stable CLI could not prove its release identity."""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _parse_current_value(text: str) -> str:
    values = [line.strip() for line in text.splitlines() if line.strip()]
    sha = values[0] if len(values) == 1 else ""
    if not _GIT_OBJECT.fullmatch(sha):
        raise CliLaunchError("CURRENT must contain one full lowercase Git object id")
    return sha


def _current_release() -> tuple[Path, str]:
    # Keep the pointer as data until its entire value has passed the Git
    # object-id check. In particular, it is never used to execute a command.
    configured_root = os.environ.get("JARVIS_INSTALL_ROOT")
    if configured_root:
        root = Path(configured_root).expanduser().resolve()
    else:
        local = Path(os.environ.get(
            "LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        root = (local / "JARVIS").resolve()
    pointer = root / "CURRENT"
    if not pointer.is_file():
        raise CliLaunchError(f"CURRENT pointer missing: {pointer}")
    return root, _parse_current_value(pointer.read_text(encoding="utf-8"))


def _validated_cli_main() -> Any:
    root, sha = _current_release()
    release = (root / "releases" / sha).resolve()
    source_root = release / "source" / "src"
    entry = source_root / "jarvis_foundry" / "deployment" / "cli_launcher.py"
    try:
        actual_entry = Path(__file__).resolve(strict=True)
    except OSError as error:
        raise CliLaunchError(f"CLI bootstrap path is unavailable: {error}") from error
    if actual_entry != entry.resolve():
        raise CliLaunchError(
            f"CLI bootstrap {actual_entry} is not the CURRENT release entry {entry}")
    if not source_root.is_dir():
        raise CliLaunchError(f"CURRENT release source snapshot missing: {source_root}")

    # -I removes cwd and Python environment paths. This explicit first entry
    # pins imports to the source snapshot inside CURRENT, not site-packages or
    # an editable developer checkout.
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("PYTHONHOME", None)
    sys.path.insert(0, str(source_root))

    from jarvis_foundry.deployment.release import (
        ReleaseError,
        configure_release_browser_runtime,
        validate_release,
    )

    try:
        identity = validate_release(sha, root=root, require_schema_match=False)
    except (OSError, ReleaseError, ValueError) as error:
        raise CliLaunchError(f"CURRENT release validation failed: {error}") from error

    expected_python = Path(str(identity["python_exe"])).resolve()
    if Path(sys.executable).resolve() != expected_python:
        raise CliLaunchError(
            f"CLI interpreter {sys.executable} is not CURRENT release Python {expected_python}")
    expected_prefix = (release / ".venv").resolve()
    if Path(sys.prefix).resolve() != expected_prefix:
        raise CliLaunchError(
            f"CLI environment {sys.prefix} is not CURRENT release environment {expected_prefix}")

    package = importlib.import_module("jarvis_foundry")
    package_origin = Path(str(package.__file__)).resolve()
    if not _inside(package_origin, release):
        raise CliLaunchError(
            f"jarvis_foundry imported from {package_origin}, outside CURRENT")
    cli = importlib.import_module("jarvis_foundry.cli.main")
    cli_origin = Path(str(cli.__file__)).resolve()
    if not _inside(cli_origin, release):
        raise CliLaunchError(f"CLI imported from {cli_origin}, outside CURRENT")

    manifest = identity.get("manifest")
    release_id = manifest.get("release_id") if isinstance(manifest, dict) else None
    if not isinstance(release_id, str) or not release_id.strip():
        raise CliLaunchError("CURRENT release manifest has no release_id")
    os.environ["JARVIS_RELEASE_ID"] = release_id.strip()
    os.environ["JARVIS_RELEASE_SHA"] = sha
    configure_release_browser_runtime(identity)
    return cli.main


def main(argv: list[str] | None = None) -> int:
    try:
        cli_main = _validated_cli_main()
    except (CliLaunchError, OSError, ImportError, ValueError) as error:
        print(json.dumps({"launcher": "FAIL_CLOSED", "error": str(error)}), flush=True)
        return 2
    return int(cli_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
