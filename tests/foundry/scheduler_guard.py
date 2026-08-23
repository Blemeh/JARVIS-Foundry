"""Boundary for tests that create disposable Windows scheduled tasks."""
from __future__ import annotations

import csv
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
