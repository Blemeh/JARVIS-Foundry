"""Windows console control-event handling for the headless Supervisor daemon.

Why this exists (live incident, 2026-08-12): the V1 scheduled task ran as an
interactive console process. When its console was closed (console host
teardown / session transition), Windows delivered CTRL_CLOSE_EVENT; the
process died with 0xC000013A (STATUS_CONTROL_C_EXIT) without running
Python's finally blocks — no ``supervisor.stopped`` event, singleton lease
left to expire, stale RUNNING projection.

A console control handler converts EVERY control event (Ctrl+C, Ctrl+Break,
console close, logoff, shutdown) into a fast durable stop: release the lease
and append ``supervisor.stopped`` within the ~5 s grace period Windows gives
before forced termination. Recovery then stays deterministic and idempotent
on every termination path.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Callable

# wincon.h control event codes
CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2
CTRL_LOGOFF_EVENT = 5
CTRL_SHUTDOWN_EVENT = 6

# Windows event-handler return values
_HANDLED = 1
_NOT_HANDLED = 0

if sys.platform == "win32":  # pragma: no cover - exercised on Windows hosts
    _HandlerType = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
else:
    # Non-Windows hosts (e.g. CI): the module must remain importable and the
    # handler a safe no-op. SetConsoleCtrlHandler does not exist there.
    _HandlerType = None  # type: ignore[assignment]


class ConsoleControlHandler:
    """Installs a ``SetConsoleCtrlHandler`` callback.

    The callback runs on a dedicated handler thread and must be quick and
    never raise: after CTRL_CLOSE / CTRL_LOGOFF / CTRL_SHUTDOWN the system
    force-terminates the process within ~5 seconds. Return ``TRUE`` so the
    system grants the grace period instead of killing us immediately.
    """

    def __init__(self, on_event: Callable[[int], None]) -> None:
        self._on_event = on_event
        self._callback = _HandlerType(self._handle) if _HandlerType is not None else None
        self._installed = False
        if sys.platform == "win32":
            self._set_console_ctrl_handler = (
                ctypes.windll.kernel32.SetConsoleCtrlHandler
            )
            self._set_console_ctrl_handler.argtypes = [_HandlerType, ctypes.c_int]
            self._set_console_ctrl_handler.restype = ctypes.c_int

    def _handle(self, event: int) -> int:
        try:
            self._on_event(event)
        except Exception:
            # A failed stop must never block the system's termination or
            # crash the handler thread; the lease expiry is the fallback.
            pass
        return _HANDLED

    def install(self) -> bool:
        if sys.platform != "win32":
            return False
        if not self._installed:
            if self._set_console_ctrl_handler(self._callback, 1):
                self._installed = True
        return self._installed

    def uninstall(self) -> None:
        if sys.platform == "win32" and self._installed:
            self._set_console_ctrl_handler(self._callback, 0)
            self._installed = False
