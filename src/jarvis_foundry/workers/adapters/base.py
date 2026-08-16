"""Worker adapter protocol (framework-neutral boundary)."""
from __future__ import annotations

import re
import subprocess
from typing import Callable, Protocol

from ..contracts import WorkerStepContract
from ..envelope import ResultEnvelope


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
