"""Build the canonical hybrid worker stack from explicit runtime config.

Every real adapter is constructed with a run-scoped disposable state root;
the owner's live Hermes profile / ``~/.openhands`` are never valid targets
(the adapters themselves fail closed). Readiness is only marked READY when
the config carries positive containment evidence — never by default.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..paths import FoundryPaths
from ..supervisor.worker import WorkerController
from ..workers.adapters.hermes import HermesWorkerAdapter
from ..workers.adapters.openhands import OpenHandsWorkerAdapter
from ..workers.adapters.registry import WorkerRegistry
from ..workers.mission_bridge import HybridWorkerRoleAdapter
from ..workers.router import READY, NOT_READY, WorkerRouter
from .config import WorkerRuntimeConfig


@dataclass(frozen=True, slots=True)
class WorkerStack:
    bridge: HybridWorkerRoleAdapter
    router: WorkerRouter
    registry: WorkerRegistry
    disposable_root: Path
    enabled_frameworks: tuple[str, ...] = ()
    stop_path: Path | None = None
    pid_table: Path | None = None
    workers: WorkerController | None = None


def _resolve_root(raw: str | None, run_id: str, *, kind: str) -> Path:
    base = Path(raw).expanduser() if raw else Path(tempfile.gettempdir())
    if not base.is_absolute():
        base = Path.cwd() / base
    root = base / f"{kind}-{run_id}"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def build_worker_stack(
    database: FoundryDatabase,
    paths: FoundryPaths,
    config: WorkerRuntimeConfig,
    *,
    run_id: str,
) -> WorkerStack:
    """Construct router + registry + bridge from ``config`` (run-scoped).

    Disabled adapters are not registered, so the router can never select an
    unavailable worker (honest NOT_READY / no eligible worker).
    """
    run_root = _resolve_root(config.runtime_state_root, run_id, kind="run")
    stop_path = config.resolve_stop_path(paths.home)
    pid_table = paths.home / "adapter_worker_pids.json"
    workers = WorkerController(database)
    router = WorkerRouter()
    registry = WorkerRegistry()
    enabled: list[str] = []

    hermes = config.hermes
    if hermes.get("enabled", False):
        home = run_root / "hermes-home"
        home.mkdir(parents=True, exist_ok=True)
        root = Path(str(hermes.get("root", ".h"))).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        registry.register(
            "hermes",
            HermesWorkerAdapter(
                hermes_root=root,
                hermes_home=home,
                enabled=True,
                timeout_seconds=int(hermes.get("timeout_seconds", 900)),
                python_exe=str(hermes.get(
                    "python_exe",
                    r"C:\Users\b\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe")),
                stop_path=stop_path,
                pid_table=pid_table,
                workers=workers,
            ),
        )
        enabled.append("hermes")

    openhands = config.openhands
    if openhands.get("enabled", False):
        data_dir = run_root / "openhands-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        exe = Path(str(openhands.get("exe", ""))).expanduser()
        if not exe.is_absolute():
            exe = Path.cwd() / exe
        registry.register(
            "openhands",
            OpenHandsWorkerAdapter(
                openhands_exe=exe,
                data_dir=data_dir,
                enabled=True,
                timeout_seconds=int(openhands.get("timeout_seconds", 900)),
                stop_path=stop_path,
                pid_table=pid_table,
                workers=workers,
            ),
        )
        enabled.append("openhands")

    # Readiness is evidence-driven: only frameworks with positive recorded
    # containment evidence become READY; everything else stays NOT_READY.
    for framework in enabled:
        spec = dict(config.readiness.get(framework) or {})
        state = str(spec.get("state") or NOT_READY)
        if state != READY or not spec.get("evidence"):
            router.mark_ready(
                framework, state=NOT_READY,
                note="no positive containment evidence in runtime config",
                evidence=tuple(spec.get("evidence") or ()),
            )
        else:
            router.mark_ready(
                framework, state=READY,
                note=str(spec.get("note") or "readiness from runtime config"),
                evidence=tuple(str(item) for item in spec.get("evidence") or ()),
            )
    # A framework without a registered adapter can never be selected: force
    # NOT_READY so the router excludes it (default Hermes baseline must not
    # leak into the live path when Hermes is disabled).
    from ..workers.contracts import VALID_WORKERS
    for framework in VALID_WORKERS:
        if framework not in enabled:
            router.mark_ready(
                framework, state=NOT_READY,
                note=f"adapter not enabled in runtime config",
                evidence=(),
            )

    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=registry,
        role_signals=dict(config.role_signals),
        stop_path=config.resolve_stop_path(paths.home),
    )
    return WorkerStack(
        bridge=bridge, router=router, registry=registry,
        disposable_root=run_root, enabled_frameworks=tuple(enabled),
        stop_path=stop_path, pid_table=pid_table, workers=workers,
    )
