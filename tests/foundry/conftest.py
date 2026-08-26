from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

from scheduler_guard import (
    delete_scheduled_tasks,
    query_scheduled_task_names,
    select_abandoned_staging_tasks,
    select_created_staging_tasks,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
# Child subprocesses (including ``python -m`` and scheduled-task probes)
# inherit an absolute candidate source path.  A relative ``PYTHONPATH=src``
# becomes unrelated when a test changes cwd to a disposable home and can
# silently import whichever globally installed JARVIS happens to be present.
_path_entries = [str(SRC)] + [
    entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep)
    if entry and Path(entry).resolve() != SRC.resolve()
]
os.environ["PYTHONPATH"] = os.pathsep.join(_path_entries)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_session_created_staging_tasks():
    # An interrupted pytest process can leave a task behind; clean it only
    # when its XML proves it belongs to a pytest/temp staging home.
    delete_scheduled_tasks(select_abandoned_staging_tasks())
    before = query_scheduled_task_names()
    yield
    after = query_scheduled_task_names()
    delete_scheduled_tasks(select_created_staging_tasks(before, after))

