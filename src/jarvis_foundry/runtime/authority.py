"""Singular production configuration, execution, and test-suite truth.

This module is documentation-as-code for Goal 3 structural closure. It does
not add a second config system: it names the mechanism that already owns
runtime, marks ``config/foundry.yaml`` as a non-live template, and records
the one production mission-execution path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

from ..paths import FoundryPaths
from .provider_config import ProviderSettings, load_provider_settings

FOUNDRY_YAML_ROLE = "TEMPLATE"
FOUNDRY_YAML_RELATIVE = Path("config") / "foundry.yaml"

FAST_TEST_PATHS = ("tests/foundry",)
FULL_TEST_PATHS = ("tests/foundry", "tests/convergence")
FAST_TEST_COMMAND = "pytest tests/foundry"
FULL_TEST_COMMAND = "pytest tests/foundry tests/convergence"

PRODUCTION_EXECUTOR = "runtime.worker_loop -> MissionLoop -> HybridWorkerRoleAdapter"
PRODUCTION_EXECUTOR_COUNT = 1

ALLOWED_TRUST_STATES = (
    "LAB",
    "PROVEN",
    "TRUSTED_AUTONOMOUS",
    "DEGRADED",
    "SUSPENDED",
)

CONFIG_PRECEDENCE = (
    "cli",
    "environment",
    "installed_foundry_profile",
    "worker_runtime_yaml",
    "deterministic_defaults",
)

EXECUTION_CLASSIFICATION: Mapping[str, str] = {
    "MissionLoop": "PRODUCTION",
    "runtime.worker_loop": "PRODUCTION",
    "HybridWorkerRoleAdapter": "THIN_WRAPPER",
    "HermesWorkerAdapter": "PRODUCTION",
    "OpenHandsWorkerAdapter": "PRODUCTION",
    "HermesOneshotAdapter": "LEGACY_DISABLED",
    "HybridStepController": "TEST_ONLY",
    "v3_loop": "THIN_WRAPPER",
    "JARVIS-Foundry-Worker-Loop": "LEGACY_DISABLED",
}


@dataclass(frozen=True, slots=True)
class RuntimeAuthority:
    """Resolved production knobs. ``config/foundry.yaml`` is never a source."""

    home: Path
    stop_path: Path
    heartbeat_seconds: float
    worker_yaml: Path | None
    worker_enabled: bool
    spawn_worker_loop: bool
    python_executable: Path
    hermes_home: Path
    provider_settings: ProviderSettings
    foundry_yaml_role: str = FOUNDRY_YAML_ROLE
    precedence: tuple[str, ...] = CONFIG_PRECEDENCE


def foundry_yaml_is_live() -> bool:
    return False


def resolve_runtime_authority(
    *,
    home: str | Path | None = None,
    heartbeat_seconds: float | None = None,
    spawn_worker_loop: bool | None = None,
    worker_loop_config: str | Path | None = None,
    python_executable: str | Path | None = None,
) -> RuntimeAuthority:
    """Single precedence chain for production runtime.

    1. CLI / explicit arguments
    2. Environment (``JARVIS_HOME``, ``HERMES_HOME``, ``JARVIS_WORKER_LOOP_CONFIG``,
       ``JARVIS_PYTHON_EXE``, ``JARVIS_SPAWN_WORKER_LOOP``, ``JARVIS_HEARTBEAT_SECONDS``)
    3. Installed ``{home}/foundry-profile.yaml`` (installer-owned, not repo yaml)
    4. Worker runtime YAML (enablement + Hermes/OpenHands executables only)
    5. Deterministic defaults (``%LOCALAPPDATA%/hermes/foundry``, spawn OFF)

    ``config/foundry.yaml`` is a TEMPLATE and is not consulted.
    """
    from .config import WorkerRuntimeConfig

    paths = FoundryPaths.discover(home)
    provider_settings = load_provider_settings()
    profile = _read_profile(paths.home / "foundry-profile.yaml")

    interval = heartbeat_seconds
    if interval is None:
        env_interval = os.environ.get("JARVIS_HEARTBEAT_SECONDS")
        interval = float(env_interval) if env_interval else 5.0

    spawn = spawn_worker_loop
    if spawn is None:
        env_spawn = os.environ.get("JARVIS_SPAWN_WORKER_LOOP")
        if env_spawn is not None:
            spawn = env_spawn.lower() in {"1", "true", "yes"}
        elif str(profile.get("spawn_worker_loop", "")).lower() in {"1", "true", "yes"}:
            spawn = True
        else:
            spawn = False

    worker_yaml = worker_loop_config
    if worker_yaml is None and os.environ.get("JARVIS_WORKER_LOOP_CONFIG"):
        worker_yaml = os.environ["JARVIS_WORKER_LOOP_CONFIG"]
    if worker_yaml is None and profile.get("worker_config_path"):
        worker_yaml = profile["worker_config_path"]
    worker_path = Path(worker_yaml).expanduser().resolve() if worker_yaml else None
    worker_cfg = WorkerRuntimeConfig.from_yaml(
        worker_path, provider_settings=provider_settings)
    python = python_executable or os.environ.get("JARVIS_PYTHON_EXE") or sys.executable
    return RuntimeAuthority(
        home=paths.home,
        stop_path=paths.emergency_stop,
        heartbeat_seconds=float(interval),
        worker_yaml=worker_path,
        worker_enabled=bool(worker_cfg.enabled),
        spawn_worker_loop=bool(spawn),
        python_executable=Path(python).expanduser().resolve(),
        hermes_home=FoundryPaths.hermes_home(),
        provider_settings=provider_settings,
    )


def runtime_source_identity(python_exe: str | Path | None = None) -> dict[str, Any]:
    """Identify the committed/release source a Supervisor install would run."""
    exe = Path(python_exe or sys.executable).expanduser().resolve()
    import jarvis_foundry

    origin = Path(jarvis_foundry.__file__).resolve().parent
    identity: dict[str, Any] = {
        "package": "jarvis-foundry",
        "package_version": _package_version(),
        "code_origin": str(origin),
        "python_exe": str(exe),
        "working_directory": str(exe.parent),
        "source_sha": None,
        "source_kind": "installed_package",
        "repository": None,
    }
    repo = _git_toplevel(origin)
    if repo is not None:
        identity["repository"] = str(repo)
        identity["source_sha"] = _git_head(repo)
        identity["source_kind"] = "git_checkout"
    return identity


def require_existing_executable(raw: object, *, kind: str) -> Path:
    """Fail closed when a required runtime executable is missing."""
    from ..workers.adapters.base import AdapterConfigurationError

    if raw is None or not str(raw).strip():
        raise AdapterConfigurationError(f"{kind} executable is not configured")
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise AdapterConfigurationError(f"{kind} executable is missing: {path}")
    return path


def _package_version() -> str:
    try:
        return metadata.version("jarvis-foundry")
    except metadata.PackageNotFoundError:
        return "0.1.0"


def _git_toplevel(start: Path) -> Path | None:
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    return None


def _git_head(repo: Path) -> str | None:
    from ..platform.windows_subprocess import headless_run_kwargs

    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
            **headless_run_kwargs(),
        )
    except OSError:
        return None
    sha = result.stdout.strip()
    return sha or None


def _read_profile(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        values[key.strip()] = value.strip()
    return values
