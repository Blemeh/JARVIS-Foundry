"""Start Supervisor from the immutable CURRENT release only."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..migrations import SCHEMA_VERSION
from ..paths import FoundryPaths
from .layout import current_pointer, install_root, release_dir
from .release import (
    ReleaseError,
    configure_release_browser_runtime,
    validate_release,
)


def _clear_import_collision_env() -> None:
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("PYTHONHOME", None)


def _release_root_from_prefix() -> Path:
    prefix = Path(sys.prefix).resolve()
    if prefix.name.lower() == ".venv":
        return prefix.parent
    return prefix


def _release_python_executables(python_exe: Path) -> set[Path]:
    """Console and GUI-subsystem interpreters for the same release venv."""
    resolved = python_exe.resolve()
    return {resolved, resolved.with_name("pythonw.exe").resolve()}


def validate_running_identity(home: Path) -> dict[str, object]:
    root = install_root()
    pointer = current_pointer(root)
    if not pointer.is_file():
        raise ReleaseError(f"CURRENT pointer missing: {pointer}")
    current = pointer.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    supplied_sha = (
        os.environ.get("JARVIS_RELEASE_SHA")
        or os.environ.get("JARVIS_RELEASE_ID")
    )
    if supplied_sha and supplied_sha.strip() != current:
        raise ReleaseError(
            f"launcher release identity {supplied_sha.strip()} != CURRENT {current}")
    # Direct validation/start invocations (outside the stable wrappers) still
    # carry the same immutable source identity into Supervisor events.
    os.environ.setdefault("JARVIS_RELEASE_SHA", current)
    os.environ.setdefault("JARVIS_RELEASE_ID", current)
    observed_dir = _release_root_from_prefix()
    expected_dir = release_dir(current).resolve()
    if observed_dir != expected_dir:
        raise ReleaseError(
            f"sys.prefix release {observed_dir} != CURRENT {expected_dir}")
    identity = validate_release(current, foundry_home=home, require_schema_match=True)
    python_exe = Path(identity["python_exe"]).resolve()
    running = Path(sys.executable).resolve()
    if running not in _release_python_executables(python_exe):
        raise ReleaseError(
            f"sys.executable {sys.executable} != release python {python_exe}")
    origin = Path(__import__("jarvis_foundry").__file__).resolve()
    if str(expected_dir) not in str(origin):
        raise ReleaseError(f"jarvis_foundry imported from {origin}, not the release")
    from .release import release_authority_report

    authority = release_authority_report(root=root, foundry_home=home)
    if (
        authority.get("status") != "CONSISTENT"
        or authority.get("database_authority_mode") != "DATABASE_LIVE_SLOT"
    ):
        raise ReleaseError(
            "database-backed release authority is not consistent: "
            f"{authority.get('status')}"
            f"/{authority.get('database_authority_mode')}"
        )
    configure_release_browser_runtime(identity)
    return identity


def main(argv: list[str] | None = None) -> int:
    _clear_import_collision_env()
    parser = argparse.ArgumentParser(prog="jarvis-release-launcher")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--spawn-worker-loop", action="store_true")
    parser.add_argument("--worker-loop-config", type=Path, default=None)
    parser.add_argument("--command-center-port", type=int, default=None)
    parser.add_argument("--owner-secret-path", type=Path, default=None)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser().resolve()
    try:
        identity = validate_running_identity(home)
    except (OSError, ReleaseError, ValueError) as error:
        print(json.dumps({"launcher": "FAIL_CLOSED", "error": str(error)}), flush=True)
        return 2
    manifest = identity.get("manifest")
    release_id = manifest.get("release_id") if isinstance(manifest, dict) else None
    if not isinstance(release_id, str) or not release_id.strip():
        print(json.dumps({
            "launcher": "FAIL_CLOSED",
            "error": "validated release manifest has no release_id",
        }), flush=True)
        return 2
    os.environ["JARVIS_RELEASE_ID"] = release_id.strip()
    if args.validate_only:
        print(json.dumps({"launcher": "OK", "identity": {
            "source_sha": identity["source_sha"],
            "python_exe": identity["python_exe"],
            "schema": SCHEMA_VERSION,
            "home": str(home),
        }}, indent=2), flush=True)
        return 0
    supervisor_args = ["--home", str(home), "--interval", str(args.interval)]
    if args.once:
        supervisor_args.append("--once")
    if args.spawn_worker_loop:
        supervisor_args.append("--spawn-worker-loop")
    if args.worker_loop_config is not None:
        supervisor_args.extend(["--worker-loop-config", str(args.worker_loop_config.resolve())])
    if args.command_center_port is not None:
        supervisor_args.extend(["--command-center-port", str(args.command_center_port)])
    if args.owner_secret_path is not None:
        supervisor_args.extend(["--owner-secret-path", str(args.owner_secret_path.resolve())])
    from ..supervisor.run import main as supervisor_main
    return supervisor_main(supervisor_args)


if __name__ == "__main__":
    raise SystemExit(main())
