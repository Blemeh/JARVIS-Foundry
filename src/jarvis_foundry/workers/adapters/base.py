"""Worker adapter protocol (framework-neutral boundary)."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from ..contracts import WorkerStepContract
from ..envelope import ResultEnvelope

if TYPE_CHECKING:
    from ...supervisor.worker import WorkerController


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


def configured_api_key() -> str | None:
    """The OpenCode Go credential the worker needs (never printed)."""
    import os

    return (os.environ.get("JARVIS_OPENCODE_API_KEY")
            or os.environ.get("OPENCODE_GO_API_KEY")
            or os.environ.get("OPENCODE_API_KEY") or None)


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
) -> subprocess.CompletedProcess[str]:
    """Run a worker subprocess while heartbeating periodically.

    Real worker sessions (Hermes / OpenHands) can far exceed the role lease
    bound; without periodic heartbeats the supervisor's reaper reclaims the
    lease mid-flight and the durable run fails with heartbeat_timeout (live
    finding 2026-08-16). The subprocess is started with ``Popen`` so its PID
    can be registered with ``WorkerController`` / the durable PID table
    before the wait loop; the main thread heartbeats every
    ``interval_seconds``, polls STOP, and can kill the tree. The adapter's
    own timeout still bounds the session; a heartbeat that can no longer
    renew (the lease was reaped) propagates honestly.

    When no ``workers`` / PID table / JARVIS_HOME is available, behavior
    stays watchdog-only (no live-home registration).
    """
    import threading
    import time

    from ...supervisor.worker import (
        default_adapter_pid_table,
        kill_pid,
        register_adapter_pid,
        terminate_popen,
        unregister_adapter_pid,
    )

    popen_kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if encoding is not None:
        popen_kwargs["encoding"] = encoding
    if errors is not None:
        popen_kwargs["errors"] = errors

    process = subprocess.Popen(command, **popen_kwargs)  # type: ignore[arg-type]
    key = run_id or f"adapter-{process.pid}"
    table_path = Path(pid_table) if pid_table is not None else default_adapter_pid_table()
    if workers is not None:
        workers.attach(key, process, acquire_lease=False)
    elif table_path is not None:
        register_adapter_pid(table_path, key, process.pid)

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
    try:
        while thread.is_alive():
            if stop is not None and stop.exists():
                stopped.set()
                terminate_popen(process, timeout=5)
                kill_pid(process.pid)
            if deadline is not None and time.monotonic() >= deadline:
                terminate_popen(process, timeout=5)
                kill_pid(process.pid)
                thread.join(timeout=5)
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            thread.join(timeout=interval_seconds)
            heartbeat()
        thread.join(timeout=1)
        if "error" in holder:
            error = holder["error"]
            raise error  # type: ignore[misc]
        result = holder["result"]  # type: ignore[assignment]
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
