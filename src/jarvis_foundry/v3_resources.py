"""Bounded, local V3 resource/counter observations.

The hard gate uses this module for honest envelope evidence.  It measures the
disposable process and ledger only; provider credentials and live deployments
are deliberately outside its scope.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .database import FoundryDatabase


@dataclass(frozen=True)
class ResourceLimits:
    max_event_count: int = 100_000
    max_db_bytes: int = 64 * 1024 * 1024
    max_candidates: int = 1_000
    max_missions: int = 500
    max_agent_runs: int = 5_000
    max_verification_runs: int = 5_000
    max_retries: int = 10_000
    max_processes: int = 512
    max_workers: int = 64


def _rss_bytes() -> int | None:
    """Return current-process RSS on Windows when the OS API is available."""
    if os.name != "nt":
        return None
    try:
        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        if psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
    except (AttributeError, OSError, TypeError):
        return None
    return None


def _process_count() -> int | None:
    """Count visible processes without requiring a third-party package."""
    if os.name != "nt":
        return 1
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0:
            return len([line for line in result.stdout.splitlines() if line.strip()])
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def _count(database: FoundryDatabase, sql: str, args: tuple[Any, ...] = ()) -> int:
    with database.connect() as connection:
        return int(connection.execute(sql, args).fetchone()[0])


def capture_resource_snapshot(
    database: FoundryDatabase,
    *,
    provider_calls: int = 0,
    queue_depth: int | None = None,
    worker_count: int | None = None,
    retry_count: int | None = None,
) -> dict[str, Any]:
    """Capture one bounded ledger/process snapshot with no state mutation."""
    db_path = Path(database.path)
    event_count = _count(database, "SELECT COUNT(*) FROM events")
    candidates = _count(database, "SELECT COUNT(*) FROM candidates")
    missions = _count(database, "SELECT COUNT(*) FROM missions")
    agent_runs = _count(database, "SELECT COUNT(*) FROM agent_runs")
    active_agents = _count(
        database, "SELECT COUNT(*) FROM agent_runs WHERE status = 'RUNNING'")
    verification_runs = _count(database, "SELECT COUNT(*) FROM verification_verdicts")
    if queue_depth is None:
        queue_depth = _count(
            database,
            "SELECT COUNT(*) FROM candidates WHERE status IN ('NEW', 'ELIGIBLE')")
    if retry_count is None:
        retry_count = _count(
            database,
            "SELECT COUNT(*) FROM events WHERE event_type IN "
            "('agent_run.failed', 'verification.outcome_recorded') "
            "AND (json_extract(payload_json, '$.status') = 'FAIL' "
            "OR json_extract(payload_json, '$.failure_class') IS NOT NULL)")
    return {
        "captured_at_epoch": time.time(),
        "pid": os.getpid(),
        "cpu_time_seconds": time.process_time(),
        "rss_bytes": _rss_bytes(),
        "process_count": _process_count(),
        "worker_count": worker_count if worker_count is not None else active_agents,
        "active_agent_runs": active_agents,
        "event_count": event_count,
        "db_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "candidate_count": candidates,
        "mission_count": missions,
        "agent_run_count": agent_runs,
        "verification_run_count": verification_runs,
        "retry_count": retry_count,
        "queue_depth": queue_depth,
        "provider_calls": provider_calls,
    }


def envelope_violations(snapshot: dict[str, Any],
                        limits: ResourceLimits | None = None) -> list[str]:
    """Return explicit limit violations; missing OS metrics are NOT failures."""
    limits = limits or ResourceLimits()
    checks = {
        "event_count": limits.max_event_count,
        "db_bytes": limits.max_db_bytes,
        "candidate_count": limits.max_candidates,
        "mission_count": limits.max_missions,
        "agent_run_count": limits.max_agent_runs,
        "verification_run_count": limits.max_verification_runs,
        "retry_count": limits.max_retries,
        "process_count": limits.max_processes,
        "worker_count": limits.max_workers,
    }
    return [
        f"{name}={snapshot[name]} exceeds {limit}"
        for name, limit in checks.items()
        if snapshot.get(name) is not None and int(snapshot[name]) > limit
    ]


def limits_payload(limits: ResourceLimits | None = None) -> dict[str, Any]:
    return asdict(limits or ResourceLimits())
