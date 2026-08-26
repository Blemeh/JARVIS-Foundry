"""Boundary for tests that create disposable Windows scheduled tasks."""
from __future__ import annotations

import csv
import re
import subprocess


STAGING_TASK_PREFIX = "\\JF-WP7Gate-"


def query_scheduled_task_names() -> set[str] | None:
    result = subprocess.run(
        ["schtasks", "/Query", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    if result.returncode != 0:
        return None
    try:
        rows = list(csv.reader(result.stdout.splitlines()))
    except csv.Error:
        return None
    return {row[0].strip() for row in rows if row and row[0].strip()}


def select_created_staging_tasks(
    before: set[str] | None, after: set[str] | None
) -> list[str]:
    """Select only names absent before the session; never pre-existing tasks."""
    if before is None or after is None:
        return []
    return sorted(
        name for name in after - before
        if name.casefold().startswith(STAGING_TASK_PREFIX.casefold())
    )


def task_xml(name: str) -> str | None:
    """Read one task definition without relying on PowerShell CIM modules."""
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", name, "/XML"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    return result.stdout if result.returncode == 0 else None


def select_abandoned_staging_tasks(
    names: set[str] | None = None,
) -> list[str]:
    """Select only leaked staging tasks whose XML proves pytest/temp ownership.

    The production task and any unrelated JARVIS task are excluded by both
    name and command/path checks.  This is intentionally a separate operation
    from the before/after diff so a later test run can clean an interrupted
    earlier run without guessing at ownership.
    """
    inventory = names if names is not None else query_scheduled_task_names()
    if not inventory:
        return []
    abandoned: list[str] = []
    for name in sorted(inventory):
        if not name.casefold().startswith(STAGING_TASK_PREFIX.casefold()):
            continue
        xml = task_xml(name)
        if not xml:
            continue
        lowered = xml.casefold()
        if not any(module in lowered for module in (
                "jarvis_foundry.supervisor.run",
                "jarvis_foundry.deployment.launcher",
                "jarvis_foundry.deployment.headless_launcher")):
            continue
        if not re.search(r"pytest(?:-of-[^\\\"]+)?|\\temp\\|\\appdata\\local\\temp\\", lowered):
            continue
        # Never classify the real production home/authority as a test task.
        if "hermes\\foundry" in lowered or "jarvis-foundry-supervisor" in lowered:
            continue
        abandoned.append(name)
    return abandoned


def delete_scheduled_tasks(names: list[str]) -> list[str]:
    removed: list[str] = []
    for name in names:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )
        if result.returncode == 0:
            removed.append(name)
    return removed
