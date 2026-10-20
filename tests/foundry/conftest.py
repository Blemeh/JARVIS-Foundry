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
def _hermetic_local_app_data(tmp_path_factory):
    """Keep the suite independent of the machine it runs on.

    The live install (``%LOCALAPPDATA%/JARVIS/config/provider.yaml``) and the
    live Foundry home (``%LOCALAPPDATA%/hermes/foundry``) both resolve from
    ``LOCALAPPDATA``.  Pointing it at an empty directory gives every test the
    deterministic defaults and guarantees the suite can never read or write
    the owner's live install.  Tests that need a specific location still set
    ``LOCALAPPDATA`` / ``JARVIS_INSTALL_ROOT`` themselves.
    """
    previous = os.environ.get("LOCALAPPDATA")
    vault_env = {k: os.environ.pop(k, None) for k in ("OBSIDIAN_VAULT", "JARVIS_OBSIDIAN_VAULT")}
    os.environ["LOCALAPPDATA"] = str(tmp_path_factory.mktemp("local-app-data"))
    try:
        yield
    finally:
        for key, value in vault_env.items():
            if value is not None:
                os.environ[key] = value
        if previous is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous

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

