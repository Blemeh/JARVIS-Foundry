"""Build the canonical hybrid worker stack from explicit runtime config.

Every real adapter is constructed with a run-scoped disposable state root;
the owner's live Hermes profile / ``~/.openhands`` are never valid targets
(the adapters themselves fail closed). Readiness is only marked READY when
the config carries positive containment evidence — never by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..deployment.layout import install_root
from ..database import FoundryDatabase
from ..paths import FoundryPaths
from ..supervisor.worker import WorkerController
from ..workers.adapters.hermes import HermesWorkerAdapter
from ..workers.adapters.openhands import OpenHandsWorkerAdapter
from ..workers.adapters.registry import WorkerRegistry
from ..workers.mission_bridge import HybridWorkerRoleAdapter
from ..workers.router import READY, NOT_READY, WorkerRouter
from .config import WorkerRuntimeConfig
from .authority import require_existing_executable


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


def _resolve_root(raw: str | None, run_id: str, *, kind: str, home: Path) -> Path:
    configured = Path(raw).expanduser() if raw else Path("var") / "worker-runtime"
    if configured.is_absolute():
        base = configured
    else:
        base = home / configured
        # The installed Foundry home normally lives below the live Hermes
        # profile.  A relative runtime root would therefore put disposable
        # Hermes state inside that profile, which the adapter correctly
        # refuses.  Keep relative config convenient for lab/test homes, but
        # redirect the production case to the JARVIS-owned install prefix.
        live_hermes = FoundryPaths.hermes_home().resolve()
        resolved_home = home.resolve()
        if resolved_home == live_hermes or live_hermes in resolved_home.parents:
            base = install_root() / "runtime" / "worker-runtime"
    root = base / f"{kind}-{run_id}"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _prepare_hermes_home(home: Path, hermes: dict[str, Any]) -> None:
    """Materialize explicit non-interactive model consent in the run home.

    Hermes intentionally fails closed for contributor-tier models unless the
    owner records the data-training acknowledgement.  Each JARVIS run uses a
    fresh isolated ``HERMES_HOME``; carrying the acknowledgement into that
    home is therefore part of the runtime contract, not a live-profile write.
    """
    if hermes.get("allow_data_training_tiers_noninteractive") is not True:
        return
    config_path = home / "config.yaml"
    if config_path.exists():
        # Never overwrite a pre-existing run profile: an explicit false or a
        # malformed file must remain fail-closed and observable to Hermes.
        return
    config_path.write_text(
        "security:\n"
        "  allow_data_training_tiers_noninteractive: true\n",
        encoding="utf-8",
    )


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
    run_root = _resolve_root(config.runtime_state_root, run_id, kind="run", home=paths.home)
    stop_path = config.resolve_stop_path(paths.home)
    pid_table = paths.home / "adapter_worker_pids.json"
    workers = WorkerController(database)
    router = WorkerRouter()
    registry = WorkerRegistry()
    enabled: list[str] = []

    if config.lab_scripted_hermes:
        from ..deployment.lab_hermes import LabHermesAdapter
        from ..workers.contracts import WORKER_HERMES
        registry.register(
            WORKER_HERMES,
            LabHermesAdapter(
                crash_after_write=config.lab_crash_after_write,
                crash_step_id=config.lab_crash_step_id,
                false_success_step_id=config.lab_false_success_step_id,
                workers=workers,
            ),
        )
        enabled.append(WORKER_HERMES)
        router.mark_ready(
            WORKER_HERMES, state=READY,
            note="LAB scripted Hermes adapter (no live provider)",
            evidence=("prompt2-lab-scripted-hermes",),
        )

    hermes = config.hermes
    if hermes.get("enabled", False) and not config.lab_scripted_hermes:
        home = run_root / "hermes-home"
        home.mkdir(parents=True, exist_ok=True)
        _prepare_hermes_home(home, hermes)
        root = Path(str(hermes.get("root", ""))).expanduser()
        if not root.is_absolute():
            from ..workers.adapters.base import AdapterConfigurationError
            raise AdapterConfigurationError("hermes root must be an absolute path")
        python_exe = require_existing_executable(
            hermes.get("python_exe"), kind="hermes python")
        registry.register(
            "hermes",
            HermesWorkerAdapter(
                hermes_root=root,
                hermes_home=home,
                enabled=True,
                timeout_seconds=int(hermes.get("timeout_seconds", 900)),
                python_exe=str(python_exe),
                stop_path=stop_path,
                pid_table=pid_table,
                workers=workers,
                provider_settings=config.provider_settings,
            ),
        )
        enabled.append("hermes")

    openhands = config.openhands
    if openhands.get("enabled", False) and not config.lab_scripted_hermes:
        data_dir = run_root / "openhands-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        exe = require_existing_executable(openhands.get("exe"), kind="openhands")
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
                provider_settings=config.provider_settings,
            ),
        )
        enabled.append("openhands")

    # Readiness is evidence-driven: only frameworks with positive recorded
    # containment evidence become READY; everything else stays NOT_READY.
    for framework in enabled:
        if config.lab_scripted_hermes and framework == "hermes":
            continue
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

    vault = paths.home / "connector-root"
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=registry,
        role_signals=dict(config.role_signals),
        stop_path=config.resolve_stop_path(paths.home),
        vault_root=vault if vault.is_dir() else None,
        use_fabric=not config.lab_scripted_hermes,
    )
    return WorkerStack(
        bridge=bridge, router=router, registry=registry,
        disposable_root=run_root, enabled_frameworks=tuple(enabled),
        stop_path=stop_path, pid_table=pid_table, workers=workers,
    )
