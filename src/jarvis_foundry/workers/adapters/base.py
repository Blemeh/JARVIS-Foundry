"""Worker adapter protocol (framework-neutral boundary)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from ..contracts import WorkerStepContract
from ..envelope import ResultEnvelope

from ...runtime.provider_config import ProviderSettings

if TYPE_CHECKING:
    from ...supervisor.worker import WorkerController

#: STOP is polled independently of lease heartbeat. A 30s heartbeat
#: interval must not become a 30s STOP detection delay.
STOP_POLL_SECONDS = 1.0


class ProviderUnavailable(RuntimeError):
    """The requested provider cannot serve a worker session right now."""


class AdapterConfigurationError(RuntimeError):
    """The adapter was configured in a way that violates a safety invariant."""


#: Owner API credential shape (never the value). Worker sessions can dump
#: their process env into captured logs; every adapter output that becomes
#: durable evidence must be scrubbed of secret-shaped tokens at intake.
_SECRET_TOKEN = re.compile(r"sk-[A-Za-z0-9]{16,}")


def redact_secrets(text: str, *, configured_key: str | None = None) -> str:
    """Remove secret-shaped tokens (and the configured key when known) from
    worker-captured output before it enters envelopes or retained evidence."""
    if not text:
        return text
    out = _SECRET_TOKEN.sub("<REDACTED>", text)
    if configured_key:
        out = out.replace(configured_key, "<REDACTED>")
    return out


def configured_api_key(settings: ProviderSettings) -> str | None:
    """Return the resolved credential without re-reading configuration."""
    return settings.api_key


def configured_base_url(settings: ProviderSettings) -> str:
    """Return the resolved base URL without re-reading configuration."""
    return settings.base_url


class WorkerAdapter(Protocol):
    """One framework adapter. ``dispatch`` returns a normalized envelope."""

    framework: str

    def dispatch(
        self,
        contract: WorkerStepContract,
        *,
        heartbeat: Callable[[], None],
    ) -> ResultEnvelope:
        """Run one isolated worker session; call ``heartbeat`` periodically."""
        ...


def run_subprocess_with_heartbeat(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout_seconds: int,
    heartbeat: Callable[[], None],
    interval_seconds: int = 30,
    encoding: str | None = None,
    errors: str | None = None,
    run_id: str | None = None,
    workers: "WorkerController | None" = None,
    stop_path: str | Path | None = None,
    pid_table: str | Path | None = None,
    mission_id: str | None = None,
    step_id: str | None = None,
    worker_type: str | None = None,
    stop_poll_seconds: float = STOP_POLL_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run a worker subprocess while heartbeating and watching STOP.

    The child is started with ``Popen`` so its exact PID can be registered
    with ``WorkerController`` / the durable PID table BEFORE the wait loop.
    Lease heartbeats may remain coarse; STOP is polled at
    ``stop_poll_seconds`` so local detection stays within the 10s contract.
    """
    import os
    import threading
    import time

    from ...supervisor.worker import (
        default_adapter_pid_table,
        register_adapter_pid,
        terminate_popen,
        unregister_adapter_pid,
    )

    from ...platform.windows_subprocess import headless_popen_kwargs, sanitized_env
    popen_kwargs: dict[str, object] = headless_popen_kwargs(
        cwd=cwd,
        env=sanitized_env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if encoding is not None:
        popen_kwargs["encoding"] = encoding
    if errors is not None:
        popen_kwargs["errors"] = errors

    process = subprocess.Popen(command, **popen_kwargs)  # type: ignore[arg-type]
    key = run_id or f"adapter-{process.pid}"
    table_path = Path(pid_table) if pid_table is not None else default_adapter_pid_table()
    if workers is not None:
        workers.attach(
            key, process, acquire_lease=False,
            mission_id=mission_id, step_id=step_id, worker_type=worker_type,
            working_directory=cwd, stop_path=stop_path,
        )
    elif table_path is not None:
        register_adapter_pid(
            table_path, key, process.pid,
            mission_id=mission_id, step_id=step_id, worker_type=worker_type,
            working_directory=cwd, stop_path=stop_path,
        )

    resolved_stop = stop_path if stop_path is not None else os.environ.get("JARVIS_STOP_PATH")
    stop = Path(resolved_stop) if resolved_stop else None

    holder: dict[str, object] = {}
    stopped = threading.Event()

    def _run_blocking() -> None:
        try:
            stdout, stderr = process.communicate()
            holder["result"] = subprocess.CompletedProcess(
                command, process.returncode if process.returncode is not None else -1,
                stdout, stderr,
            )
        except BaseException as error:  # noqa: BLE001 - surfaced by caller
            holder["error"] = error

    thread = threading.Thread(target=_run_blocking, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    last_beat = time.monotonic()
    poll = max(0.05, float(stop_poll_seconds))
    try:
        while thread.is_alive():
            if stop is not None and stop.exists() and not stopped.is_set():
                stopped.set()
                terminate_popen(process, timeout=5)
            if deadline is not None and time.monotonic() >= deadline:
                terminate_popen(process, timeout=5)
                thread.join(timeout=5)
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            thread.join(timeout=poll)
            if time.monotonic() - last_beat >= interval_seconds:
                heartbeat()
                last_beat = time.monotonic()
        thread.join(timeout=1)
        if thread.is_alive():
            # A child that ignored termination must not leave pipe-drain work
            # holding the test/session teardown open indefinitely.
            try:
                process.kill()
            except OSError:
                pass
            thread.join(timeout=1)
        if "error" in holder:
            error = holder["error"]
            raise error  # type: ignore[misc]
        result = holder.get("result")
        if result is None:
            result = subprocess.CompletedProcess(command, -1, "", "adapter child ended without a result")
        if stopped.is_set() and isinstance(result, subprocess.CompletedProcess):
            if result.returncode == 0:
                result = subprocess.CompletedProcess(
                    command, -15, result.stdout,
                    (result.stderr or "") + "\nSTOP: adapter child terminated\n",
                )
        return result  # type: ignore[return-value]
    finally:
        if workers is not None:
            workers.detach(key)
        elif table_path is not None:
            unregister_adapter_pid(table_path, key)
