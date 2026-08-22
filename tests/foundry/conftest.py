from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scheduler_guard import (
    delete_scheduled_tasks,
    query_scheduled_task_names,
    select_created_staging_tasks,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session", autouse=True)
def _cleanup_session_created_staging_tasks():
    before = query_scheduled_task_names()
    yield
    after = query_scheduled_task_names()
    delete_scheduled_tasks(select_created_staging_tasks(before, after))

