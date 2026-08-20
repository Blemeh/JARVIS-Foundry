"""Production headless entry: log redirection then immutable-release Supervisor."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from .layout import install_root
from .launcher import _clear_import_collision_env, main as launcher_main


def _redirect_stdio_to_logs() -> Path:
    log_dir = install_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"supervisor-{date.today():%Y%m%d}.log"
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = log_file
    sys.stderr = log_file
    return log_path


def main(argv: list[str] | None = None) -> int:
    _clear_import_collision_env()
    log_path = _redirect_stdio_to_logs()
    print(f"headless_launcher: logging to {log_path}", flush=True)
    return launcher_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
