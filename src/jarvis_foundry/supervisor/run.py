"""WP7 — headless Supervisor daemon (runs as the scheduled task).

Independent of Hermes Desktop: initializes, reconciles, honors an existing
STOP sentinel (FROZEN state), heartbeats on an interval, reaps stale
workers, and freezes deterministically when the sentinel appears. Exits
cleanly on SIGTERM / Ctrl-Break (``--once`` runs a single cycle for tests
and gate probes).

Console-event resilience: the daemon installs a Windows console control
handler so Ctrl+C / Ctrl+Break / console close / logoff / shutdown all
produce a fast durable stop (lease release + ``supervisor.stopped``) instead
of an abrupt 0xC000013A death with a stale lease (live incident 2026-08-12).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from ..database import FoundryDatabase
from ..events import Event, EventConflict
from ..paths import FoundryPaths
from .console import ConsoleControlHandler
from .estop import EmergencyStop
from .leases import LeaseUnavailable
from .service import Supervisor

#: Bounded retry: after this many consecutive heartbeat failures the daemon
#: stops itself cleanly (lease released, ledger records the stop) so the
#: repeating scheduled trigger restarts a fresh instance (F1/F-test class).
MAX_CONSECUTIVE_FAILURES = 5


def _record_degraded(supervisor: Supervisor, cause: str,
                     details: dict[str, object], *,
                     throttle: float, last_recorded_at: float) -> bool:
    """Append a throttled ``supervisor.degraded`` event; never raises.

    The degraded append must not take the loop down with it — if the ledger
    itself is the problem, the failure is reported on stderr and the bounded
    retry path decides the daemon's fate.
    """
    import time as _time
    now = _time.monotonic()
    if now - last_recorded_at < throttle:
        return False
    try:
        supervisor.database.append(Event.create(
            "supervisor.degraded",
            {"instance_id": supervisor.instance_id,
             "trust_state": "LAB",
             "reason": cause,
             "details": details},
            source_type="supervisor", source_id=supervisor.instance_id,
            idempotency_key=(
                f"supervisor.degraded:{supervisor.instance_id}:{cause}"),
        ))
        return True
    except EventConflict:
        # Already recorded for this instance+cause (verifier OBS-1): the
        # ledger keeps one row per cause per instance; this is not a
        # failure — a fresh instance records its own events.
        return True
    except Exception as error:  # noqa: BLE001 — report, never propagate
        print(json.dumps({
            "supervisor": "DEGRADED_RECORD_FAILED",
            "cause": cause,
            "error_class": type(error).__name__,
            "error": str(error)[:300],
        }), flush=True)
        return False


def make_console_handler(supervisor: Supervisor) -> ConsoleControlHandler:
    """Console control handler: any control event performs a fast durable
    stop (lease release + ``supervisor.stopped``) and exits 0.

    Windows gives ~5 s after CTRL_CLOSE/LOGOFF/SHUTDOWN before forced
    termination, so the handler thread does the whole stop and then exits
    the process immediately (``os._exit`` skips remaining cleanup, which is
    already complete at that point).
    """

    def on_event(event_code: int) -> None:
        supervisor.stop()
        os._exit(0)

    return ConsoleControlHandler(on_event)


def _heartbeat_with_lock_retry(
        supervisor, *,
        backoffs_s: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0)) -> str:
    """V7-S5 promotion (cycle-r3b): retry a heartbeat on transient sqlite
    'database is locked' errors. The busy_timeout is 10 s, so any lock
    error that surfaces already outlasted it; the retry budget
    (0.5+1+2+4+8 = 15.5 s) covers locks that clear shortly after the busy
    window — such a heartbeat never counts a failure. Persistent errors
    raise after the bounded retries and are counted exactly as before."""
    import sqlite3 as _sqlite3
    last_error = None
    for backoff_s in backoffs_s:
        try:
            return supervisor.heartbeat()
        except _sqlite3.OperationalError as error:
            if "locked" not in str(error):
                raise
            last_error = error
            time.sleep(backoff_s)
    if last_error is not None:
        raise last_error
    return supervisor.heartbeat()


def _heartbeat_loop(supervisor: Supervisor, interval: float, *,
                    max_failures: int = MAX_CONSECUTIVE_FAILURES,
                    stop_after: int | None = None) -> int:
    """Exception-isolated heartbeat loop with cycle monitoring (step-5:
    FIND-2026-08-12-01 repair — a wedged or failing cycle must be recorded
    loudly and bounded, never persisted silently).

    - a healthy cycle that takes > 3x the interval records a throttled
      ``supervisor.degraded`` slow_cycle event (wedge class);
    - a raising heartbeat records a throttled ``supervisor.degraded``
      heartbeat_failure event and continues;
    - after ``max_failures`` consecutive failures the daemon stops itself
      cleanly (lease released, ``supervisor.stopped`` recorded) and returns
      1, so the repeating scheduled trigger restarts a fresh instance;
    - ``stop_after`` bounds the loop for tests (None = run forever).
    """
    failures = 0
    last_degraded_at = 0.0
    degraded_throttle = max(interval * 10, 120.0)
    iterations = 0
    while stop_after is None or iterations < stop_after:
        iterations += 1
        time.sleep(interval)
        cycle_start = time.monotonic()
        try:
            state = _heartbeat_with_lock_retry(supervisor)
            failures = 0
            cycle_seconds = time.monotonic() - cycle_start
            if cycle_seconds > 3 * interval:
                recorded = _record_degraded(
                    supervisor, "slow_cycle",
                    {"cycle_seconds": round(cycle_seconds, 1),
                     "interval_seconds": interval},
                    throttle=degraded_throttle,
                    last_recorded_at=last_degraded_at)
                if recorded:
                    last_degraded_at = time.monotonic()
        except Exception as error:  # noqa: BLE001 — the loop must survive
            failures += 1
            recorded = _record_degraded(
                supervisor, "heartbeat_failure",
                {"error_class": type(error).__name__,
                 "error": str(error)[:300],
                 "consecutive_failures": failures},
                throttle=degraded_throttle,
                last_recorded_at=last_degraded_at)
            if recorded:
                last_degraded_at = time.monotonic()
            print(json.dumps({
                "supervisor": "HEARTBEAT_FAILURE",
                "consecutive_failures": failures,
                "error_class": type(error).__name__,
            }), flush=True)
            if failures >= max_failures:
                # Bounded retry: stop cleanly so the repeating trigger
                # restarts a fresh instance (lease released, ledger records
                # the stop). Never die silently mid-state — and never let
                # the stop path itself mask the decision (verifier OBS-3).
                print(json.dumps({
                    "supervisor": "STOPPED_AFTER_FAILURES",
                    "consecutive_failures": failures,
                }), flush=True)
                try:
                    supervisor.stop()
                except Exception as stop_error:  # noqa: BLE001
                    print(json.dumps({
                        "supervisor": "STOP_FAILED",
                        "error_class": type(stop_error).__name__,
                        "error": str(stop_error)[:300],
                    }), flush=True)
                return 1
            continue
        if state == "STOPPED":
            return 0
        if state == "FROZEN":
            # The STOP sentinel exists: stay alive as the watchdog, keep
            # the frozen record fresh, and wait for resume/owner.
            pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis-supervisor")
    parser.add_argument("--home", type=Path, default=argparse.SUPPRESS,
                        help="override the Foundry state directory")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="heartbeat/STOP-sentinel poll interval in seconds "
                             "(gate 04 Sec 6 caps STOP detection latency at "
                             "10s; the 30s default previously made >10s "
                             "detection the norm — FIND-2026-08-12-06, "
                             "review deleg_acbc8ce5)")
    parser.add_argument("--once", action="store_true",
                        help="run a single start+heartbeat cycle and exit")
    parser.add_argument(
        "--spawn-worker-loop", action="store_true", default=False,
        help="Supervisor-owned worker_loop --once (default OFF)")
    parser.add_argument(
        "--worker-loop-config", type=Path, default=None,
        help="worker runtime YAML used only when --spawn-worker-loop is set")
    return parser


def _run_frozen_supervisor(
    paths: FoundryPaths,
    *,
    spawn_worker_loop: bool,
    worker_loop_config: Path | None,
    once: bool,
) -> int:
    """STOP-active entry: freeze without resolving provider configuration."""
    database = FoundryDatabase(paths.database)
    supervisor = Supervisor(
        database,
        paths.emergency_stop,
        spawn_worker_loop=spawn_worker_loop,
        worker_loop_config=worker_loop_config,
    )
    console_handler = make_console_handler(supervisor)
    console_handler.install()
    try:
        state = supervisor.start()
        print(json.dumps({"supervisor": state, "home": str(paths.home)}), flush=True)
        if state in {"STOPPED", "BLOCKED", "UNKNOWN"}:
            return 0 if state == "STOPPED" else 1
        if once:
            print(json.dumps({"heartbeat": supervisor.heartbeat()}), flush=True)
            return 0
    finally:
        supervisor.stop()
        console_handler.uninstall()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = FoundryPaths.discover(getattr(args, "home", None))
    from ..deployment.guard import refuse_developer_checkout_on_live_home
    from ..deployment.release import ReleaseError
    try:
        refuse_developer_checkout_on_live_home(paths.home)
    except ReleaseError as error:
        print(json.dumps({"supervisor": "FAIL_CLOSED", "error": str(error)}), flush=True)
        return 2
    if EmergencyStop(paths.emergency_stop).active():
        return _run_frozen_supervisor(
            paths,
            spawn_worker_loop=bool(args.spawn_worker_loop),
            worker_loop_config=getattr(args, "worker_loop_config", None),
            once=bool(args.once),
        )
    from ..runtime.authority import resolve_runtime_authority
    authority = resolve_runtime_authority(
        home=paths.home,
        heartbeat_seconds=args.interval,
        spawn_worker_loop=True if args.spawn_worker_loop else None,
        worker_loop_config=getattr(args, "worker_loop_config", None),
    )
    database = FoundryDatabase(paths.database)
    supervisor = Supervisor(
        database, paths.emergency_stop,
        spawn_worker_loop=bool(args.spawn_worker_loop or authority.spawn_worker_loop),
        worker_loop_config=getattr(args, "worker_loop_config", None) or authority.worker_yaml,
        provider_settings=authority.provider_settings,
    )
    console_handler = make_console_handler(supervisor)
    console_handler.install()

    exit_code = 0
    try:
        state = supervisor.start()
        print(json.dumps({"supervisor": state, "home": str(paths.home)}), flush=True)
        if state == "DEGRADED":
            # Fail loud: a supervisor that cannot reconcile must not hold the
            # lease or heartbeat (finding: DEGRADED was silently ignored).
            return 1
        if state in {"STOPPED", "BLOCKED", "UNKNOWN"}:
            return 0 if state == "STOPPED" else 1
        if args.once:
            print(json.dumps({"heartbeat": supervisor.heartbeat()}), flush=True)
            return 0
        exit_code = _heartbeat_loop(supervisor, args.interval)
    except LeaseUnavailable as error:
        print(json.dumps({
            "supervisor": "LEASE_UNAVAILABLE",
            "error": str(error),
        }), flush=True)
        return 2
    except KeyboardInterrupt:
        pass
    finally:
        supervisor.stop()
        console_handler.uninstall()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
