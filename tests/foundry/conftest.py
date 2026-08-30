from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Import the local helper after establishing this checkout's source path. A
# host-injected JARVIS checkout must not bind teardown to an unrelated module.
from scheduler_guard import (
    delete_scheduled_tasks,
    query_scheduled_task_names,
    select_abandoned_staging_tasks,
    select_created_staging_tasks,
)
# Child subprocesses (including ``python -m`` and scheduled-task probes)
# inherit an absolute candidate source path.  A relative ``PYTHONPATH=src``
# becomes unrelated when a test changes cwd to a disposable home and can
# silently import whichever globally installed JARVIS happens to be present.
_path_entries = [str(SRC)] + [
    entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
    if entry and Path(entry).resolve() != SRC.resolve()
]
os.environ["PYTHONPATH"] = os.pathsep.join(_path_entries)
try:
    from jarvis_foundry.platform.windows_subprocess import sanitized_env
    _fixed = sanitized_env()
    if "PSModulePath" in _fixed:
        os.environ["PSModulePath"] = _fixed["PSModulePath"]
except Exception:
    pass


@pytest.fixture(scope="session", autouse=True)
def _cleanup_session_created_staging_tasks():
    # An interrupted pytest process can leave a task behind; clean it only
    # when its XML proves it belongs to a pytest/temp staging home.
    startup_deadline = time.monotonic() + 30
    try:
        delete_scheduled_tasks(
            select_abandoned_staging_tasks(deadline=startup_deadline),
            deadline=startup_deadline,
        )
    except Exception:
        pass
    before = query_scheduled_task_names()
    yield
    deadline = time.monotonic() + 30
    try:
        remaining = max(0.0, deadline - time.monotonic())
        after = query_scheduled_task_names(timeout=min(10.0, remaining)) if remaining else None
    except Exception:
        after = None
    try:
        if after is not None:
            delete_scheduled_tasks(
                select_created_staging_tasks(before, after),
                deadline=deadline,
            )
    except Exception:
        pass

