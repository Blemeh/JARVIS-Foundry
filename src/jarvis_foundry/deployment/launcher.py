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
from .release import ReleaseError, validate_release


def _clear_import_collision_env() -> None:
    os.environ.pop("PYTHONPATH", None)
    os.environ.pop("PYTHONHOME", None)


def _release_root_from_prefix() -> Path:
    prefix = Path(sys.prefix).resolve()
    if prefix.name.lower() == ".venv":
        return prefix.parent
    return prefix


def validate_running_identity(home: Path) -> dict[str, object]:
    root = install_root()
    current = current_pointer().read_text(encoding="utf-8").strip().splitlines()[0].strip()
    observed_dir = _release_root_from_prefix()
    expected_dir = release_dir(current).resolve()
    if observed_dir != expected_dir:
        raise ReleaseError(
            f"sys.prefix release {observed_dir} != CURRENT {expected_dir}")
    identity = validate_release(current, foundry_home=home, require_schema_match=True)
    python_exe = Path(identity["python_exe"]).resolve()
    if Path(sys.executable).resolve() != python_exe:
        raise ReleaseError(
            f"sys.executable {sys.executable} != release python {python_exe}")
    origin = Path(__import__("jarvis_foundry").__file__).resolve()
    if str(expected_dir) not in str(origin):
        raise ReleaseError(f"jarvis_foundry imported from {origin}, not the release")
    return identity


def main(argv: list[str] | None = None) -> int:
    _clear_import_collision_env()
    parser = argparse.ArgumentParser(prog="jarvis-release-launcher")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--spawn-worker-loop", action="store_true")
    parser.add_argument("--worker-loop-config", type=Path, default=None)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser().resolve()
    try:
        identity = validate_running_identity(home)
    except (OSError, ReleaseError, ValueError) as error:
        print(json.dumps({"launcher": "FAIL_CLOSED", "error": str(error)}), flush=True)
        return 2
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
    from ..supervisor.run import main as supervisor_main
    return supervisor_main(supervisor_args)


if __name__ == "__main__":
    raise SystemExit(main())
