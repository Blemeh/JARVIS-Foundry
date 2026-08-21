"""Bounded Hermes one-shot probe for ``jarvis setup`` connectivity tests."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..runtime.provider_config import (
    ProviderSettings,
    apply_hermes_provider_env,
)
from ..runtime.provider_registry import live_test_allowed
from ..workers.adapters.hermes import HermesWorkerAdapter
from ..workers.adapters.base import redact_secrets, configured_api_key

HERMES_PROBE_PROMPT = "Reply exactly: JARVIS_PROVIDER_TEST_OK"
HERMES_PROBE_EXPECTED = "JARVIS_PROVIDER_TEST_OK"
_PROBE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class HermesProbeRuntime:
    hermes_root: Path
    python_exe: str


@dataclass(frozen=True, slots=True)
class HermesProbeResult:
    success: bool
    returncode: int
    stdout: str
    stderr: str
    provider_cli: str
    model: str
    error: str | None = None


def _resolve_hermes_root(root_raw: str, yaml_path: Path) -> Path:
    root = Path(str(root_raw)).expanduser()
    if root.is_absolute():
        return root.resolve()
    search_roots = [yaml_path.parent, yaml_path.parent.parent, Path.cwd()]
    try:
        import jarvis_foundry

        search_roots.append(
            Path(jarvis_foundry.__file__).resolve().parent.parent.parent)
    except Exception:
        pass
    from ..deployment.layout import install_root

    search_roots.append(install_root())
    for base in dict.fromkeys(item.resolve() for item in search_roots):
        candidate = (base / root).resolve()
        if candidate.is_dir():
            return candidate
    return (yaml_path.parent / root).resolve()


def _valid_probe_root(root: Path) -> bool:
    live = HermesWorkerAdapter.LIVE_HERMES
    return not (root == live or live in root.parents)


def resolve_hermes_probe_runtime(
    *,
    worker_config: str | Path | None = None,
) -> HermesProbeRuntime | None:
    """Resolve the Hermes checkout + interpreter used for setup probes."""
    root_env = os.environ.get("JARVIS_HERMES_ROOT")
    python_env = os.environ.get("JARVIS_HERMES_PYTHON")
    if root_env and python_env:
        root = Path(root_env).expanduser().resolve()
        python = Path(python_env).expanduser().resolve()
        if root.is_dir() and python.is_file() and _valid_probe_root(root):
            return HermesProbeRuntime(hermes_root=root, python_exe=str(python))

    yaml_path: Path | None = None
    if worker_config is not None:
        yaml_path = Path(worker_config).expanduser()
    elif os.environ.get("JARVIS_WORKER_LOOP_CONFIG"):
        yaml_path = Path(os.environ["JARVIS_WORKER_LOOP_CONFIG"]).expanduser()
    else:
        from ..deployment.layout import install_root

        for candidate in (
            install_root() / "config" / "worker_routing.yaml",
            Path(__file__).resolve().parents[3] / "config" / "worker_routing.yaml",
        ):
            if candidate.is_file():
                yaml_path = candidate
                break

    if yaml_path is not None and yaml_path.is_file():
        from ..runtime.config import WorkerRuntimeConfig

        cfg = WorkerRuntimeConfig.from_yaml(yaml_path.resolve())
        hermes = cfg.hermes or {}
        root_raw = hermes.get("root")
        python_raw = hermes.get("python_exe")
        if root_raw and python_raw:
            root = _resolve_hermes_root(str(root_raw), yaml_path.resolve())
            python = Path(str(python_raw)).expanduser().resolve()
            if root.is_dir() and python.is_file() and _valid_probe_root(root):
                return HermesProbeRuntime(hermes_root=root, python_exe=str(python))
    return None


def build_hermes_probe_command(
    settings: ProviderSettings,
    *,
    python_exe: str,
    working_root: Path,
    prompt: str = HERMES_PROBE_PROMPT,
) -> list[str]:
    cli_provider = HermesWorkerAdapter.CLI_PROVIDER_IDS.get(
        settings.provider, settings.provider)
    return [
        python_exe,
        "-m",
        "hermes_cli.main",
        "-z",
        prompt,
        "--provider",
        cli_provider,
        "--model",
        settings.model,
        "--in",
        str(working_root),
        "--yolo",
        "--cli",
    ]


def run_hermes_live_probe(
    settings: ProviderSettings,
    runtime: HermesProbeRuntime,
    *,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    work_root: Path | None = None,
) -> HermesProbeResult:
    """Run the same bounded Hermes one-shot path JARVIS uses for workers."""
    if not live_test_allowed(settings.provider, settings.model):
        raise ValueError("live Hermes probe is not authorized for this provider/model")

    cli_provider = HermesWorkerAdapter.CLI_PROVIDER_IDS.get(
        settings.provider, settings.provider)
    temp_root = work_root
    created_temp = False
    if temp_root is None:
        temp_root = Path(tempfile.mkdtemp(prefix="jarvis-setup-probe-"))
        created_temp = True
    hermes_home = temp_root / "hermes-home"
    working_root = temp_root / "work"
    hermes_home.mkdir(parents=True, exist_ok=True)
    working_root.mkdir(parents=True, exist_ok=True)

    env = apply_hermes_provider_env(settings, os.environ)
    env["HERMES_HOME"] = str(hermes_home)
    env["PYTHONPATH"] = (
        str(runtime.hermes_root)
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    command = build_hermes_probe_command(
        settings,
        python_exe=runtime.python_exe,
        working_root=working_root,
    )
    runner = run_subprocess or (
        lambda cmd, **kwargs: subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            **kwargs,
        )
    )
    try:
        completed = runner(
            command,
            cwd=str(working_root),
            env=env,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        if created_temp:
            shutil.rmtree(temp_root, ignore_errors=True)
        return HermesProbeResult(
            success=False,
            returncode=-1,
            stdout="",
            stderr="",
            provider_cli=cli_provider,
            model=settings.model,
            error="Hermes probe timed out",
        )

    key = configured_api_key(settings)
    raw_stdout = completed.stdout or ""
    stdout = redact_secrets(raw_stdout, configured_key=key)
    stderr = redact_secrets(completed.stderr or "", configured_key=key)
    success = (
        completed.returncode == 0
        and HERMES_PROBE_EXPECTED in raw_stdout
        and _command_preserves_identity(command, cli_provider, settings.model)
    )
    error = None
    if not success:
        if completed.returncode != 0:
            error = f"Hermes probe exited {completed.returncode}"
        elif HERMES_PROBE_EXPECTED not in raw_stdout:
            error = "expected probe response not observed"
        else:
            error = "provider/model identity mismatch"
    if created_temp:
        shutil.rmtree(temp_root, ignore_errors=True)
    return HermesProbeResult(
        success=success,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        provider_cli=cli_provider,
        model=settings.model,
        error=error,
    )


def _command_preserves_identity(
    command: list[str],
    expected_provider: str,
    expected_model: str,
) -> bool:
    try:
        provider_idx = command.index("--provider") + 1
        model_idx = command.index("--model") + 1
    except ValueError:
        return False
    return (
        command[provider_idx] == expected_provider
        and command[model_idx] == expected_model
    )
