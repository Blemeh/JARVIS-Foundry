"""Worker adapter protocol (framework-neutral boundary)."""
from __future__ import annotations

import subprocess
from typing import Callable, Protocol

from ..contracts import WorkerStepContract
from ..envelope import ResultEnvelope


class ProviderUnavailable(RuntimeError):
    """The requested provider cannot serve a worker session right now."""


class AdapterConfigurationError(RuntimeError):
    """The adapter was configured in a way that violates a safety invariant."""


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
) -> subprocess.CompletedProcess[str]:
    """Run a worker subprocess while heartbeating periodically.

    Real worker sessions (Hermes / OpenHands) can far exceed the role lease
    bound; without periodic heartbeats the supervisor's reaper reclaims the
    lease mid-flight and the durable run fails with heartbeat_timeout (live
    finding 2026-08-16). The subprocess runs in a daemon thread; the main
    thread heartbeats every ``interval_seconds`` until it finishes. The
    adapter's own timeout still bounds the session; a heartbeat that can no
    longer renew (the lease was reaped) propagates honestly.
    """
    import threading

    holder: dict[str, object] = {}

    def _run_blocking() -> None:
        try:
            holder["result"] = subprocess.run(
                command, cwd=cwd, env=env, capture_output=True, text=True,
                timeout=timeout_seconds,
                encoding=encoding, errors=errors,
            )
        except BaseException as error:  # noqa: BLE001 - surfaced by caller
            holder["error"] = error

    thread = threading.Thread(target=_run_blocking, daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(timeout=interval_seconds)
        heartbeat()
    thread.join(timeout=1)
    if "error" in holder:
        error = holder["error"]
        raise error  # type: ignore[misc]
    return holder["result"]  # type: ignore[return-value]
