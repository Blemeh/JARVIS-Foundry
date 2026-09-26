"""Build the canonical hybrid worker stack from explicit runtime config.

Every real adapter is constructed with a run-scoped disposable state root;
the owner's live Hermes profile / ``~/.openhands`` are never valid targets
(the adapters themselves fail closed). Readiness is only marked READY when
the config carries positive containment evidence — never by default.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import __version__
from ..deployment.layout import install_root
from ..database import FoundryDatabase
from ..paths import FoundryPaths
from ..supervisor.worker import WorkerController
from ..workers.adapters.hermes import (
    HermesWorkerAdapter,
    _delegation_request_overrides,
)
from ..workers.adapters.openhands import OpenHandsWorkerAdapter
from ..workers.adapters.registry import WorkerRegistry
from ..workers.mission_bridge import HybridWorkerRoleAdapter
from ..workers.router import READY, NOT_READY, WorkerRouter
from ..roles.router import ProviderRouteCandidate
from ..fabric.model import UnattendedMode
from ..fabric.providers import unattended_mode_for
from .provider_registry import (
    OPENCODE_FAMILY_ID,
    ProviderReadiness,
    family_for_provider,
    latest_provider_readiness,
    provider_profile,
    record_provider_profile,
    record_provider_readiness,
)
from .config import WorkerRuntimeConfig
from .authority import require_existing_executable
from .provider_config import ProviderSettings
from .provider_spend import AUTHORIZED_API_MODE


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
    provider_candidates: tuple[ProviderRouteCandidate, ...] = ()
    mcp_backend: Any | None = None

    def close(self) -> None:
        if self.mcp_backend is not None:
            self.mcp_backend.close(preserve_catalog_readiness=True)


def _provider_candidates(
    database: FoundryDatabase,
    config: WorkerRuntimeConfig,
) -> tuple[ProviderRouteCandidate, ...]:
    """Build routing observations from the existing registry + ledger."""

    if not config.provider_resilience_enabled:
        return ()
    raw = dict(config.provider_resilience or {})
    profiles = raw.get("profiles") or {}
    if not isinstance(profiles, dict):
        raise ValueError("provider_resilience.profiles must be a mapping")
    candidates: list[ProviderRouteCandidate] = []
    for settings in config.provider_settings_pool:
        override = profiles.get(settings.provider) or {}
        if not isinstance(override, dict):
            raise ValueError(
                f"provider profile {settings.provider!r} must be a mapping")
        static = provider_profile(settings.provider, settings.model)
        profile = record_provider_profile(
            database,
            provider_id=settings.provider,
            model=settings.model,
            profile_overrides=override,
            source_id="worker-runtime-provider-profile",
        )
        observed = latest_provider_readiness(
            database, settings.provider, settings.model)
        configured_state = str(override.get("readiness") or "UNKNOWN").upper()
        evidence = tuple(str(item) for item in override.get("evidence_refs") or ())
        if observed is None and configured_state in {
            state.value for state in ProviderReadiness
        }:
            # READY/DEGRADED require positive evidence.  Negative states may
            # be configured explicitly so an unavailable provider is never
            # probed merely because credentials exist.
            if configured_state not in {"READY", "DEGRADED"} or evidence:
                observed = record_provider_readiness(
                    database,
                    provider_id=settings.provider,
                    model=settings.model,
                    readiness=configured_state,
                    reason=str(override.get("readiness_reason") or "runtime configuration"),
                    evidence_refs=evidence,
                    notify_wakes=False,
                    source_id="worker-runtime-provider-profile",
                )
        readiness = str((observed or {}).get("readiness") or "UNKNOWN").upper()
        if static.get("known_model") is False:
            readiness = "INCOMPATIBLE"
        # One existing Hermes policy knob remains authoritative.  Provider
        # profile data cannot auto-enable training-tier noninteractive use.
        consent = config.hermes.get(
            "allow_data_training_tiers_noninteractive")
        unattended_mode = unattended_mode_for(
            settings,
            hermes_noninteractive_consent=(
                bool(consent) if consent is not None else None),
        )
        auth_state = str(
            override.get("auth_state")
            or ("READY" if settings.api_key else "UNAVAILABLE")
        ).upper()
        if readiness in {"READY", "DEGRADED"}:
            health = "READY" if readiness == "READY" else "DEGRADED"
            availability = "AVAILABLE"
        else:
            health = readiness
            availability = readiness
        data_class = str(
            profile.get("data_use_classification") or "UNKNOWN_POLICY")
        if data_class == "UNKNOWN":
            data_class = "UNKNOWN_POLICY"
        candidates.append(ProviderRouteCandidate(
            provider=settings.provider,
            model=settings.model,
            capabilities=tuple(str(item) for item in profile.get("capabilities") or ()),
            task_classes=tuple(str(item) for item in profile.get("task_classes") or ()),
            tool_support=tuple(str(item) for item in profile.get("tool_support") or ()),
            context_window_tokens=profile.get("context_window_tokens"),
            latency_ms=(
                float(override["latency_ms"])
                if override.get("latency_ms") is not None else None),
            reliability=(
                float(override["reliability"])
                if override.get("reliability") is not None else None),
            cost_usd=(
                float(profile["estimated_cost_usd"])
                if profile.get("estimated_cost_usd") is not None else None),
            health=health,
            auth_state=auth_state,
            availability=availability,
            readiness=readiness,
            data_use_classification=data_class,
            noninteractive_allowed=(unattended_mode is UnattendedMode.ALLOWED),
            owner_allowed=bool(override.get("owner_allowed", True)),
            limits=dict(profile.get("limits") or {}),
            evidence_refs=tuple(dict.fromkeys([
                *evidence,
                *(str(item) for item in (observed or {}).get("evidence_refs") or ()),
            ])),
        ))
    return tuple(candidates)


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
    root = base / f"{kind}-{_filesystem_component(run_id)}"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _filesystem_component(value: str) -> str:
    """Map a durable identity to one portable, collision-resistant name."""

    text = str(value)
    sanitized = "".join(
        character
        if character.isalnum() or character in {"-", "_", "."}
        else "-"
        for character in text
    ).strip(" .-_")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    unchanged = (
        sanitized == text
        and len(sanitized) <= 96
        and sanitized.split(".", 1)[0].upper() not in reserved
    )
    if unchanged:
        return sanitized
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    prefix = (sanitized or "identity")[:72].rstrip(" .-_") or "identity"
    return f"{prefix}-{digest}"


_OPENCODE_SESSION_HEADER = "x-opencode-session"


def _provider_request_headers(
    hermes: dict[str, Any],
    provider_settings: ProviderSettings | None,
    *,
    existing_headers: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Return the per-run provider headers Hermes must send on every request.

    Operators may declare additional headers in the worker routing file
    (``hermes.extra_headers``).  The OpenCode family additionally requires a
    stable ``x-opencode-session`` value per conversation: the console Go route
    refuses sessionless requests with HTTP 400 before they ever reach a model.
    One isolated run home equals one conversation, so the identifier is
    generated once here and reused for the whole run.
    """
    headers: dict[str, str] = {}
    if isinstance(existing_headers, dict):
        headers.update({
            str(key): str(value)
            for key, value in existing_headers.items()
            if value is not None
        })
    declared = hermes.get("extra_headers")
    if isinstance(declared, dict):
        headers.update({
            str(key): str(value)
            for key, value in declared.items()
            if value is not None
        })
    if provider_settings is None:
        return headers
    family = family_for_provider(provider_settings.provider)
    if family is not None and family.family_id == OPENCODE_FAMILY_ID:
        existing_session = next(
            (
                value
                for key, value in headers.items()
                if key.casefold() == _OPENCODE_SESSION_HEADER
            ),
            None,
        )
        if not existing_session:
            headers[_OPENCODE_SESSION_HEADER] = str(uuid.uuid4())
        headers.setdefault("User-Agent", f"jarvis-foundry/{__version__}")
    return headers


def _prepare_hermes_home(
    home: Path,
    hermes: dict[str, Any],
    *,
    provider_settings: ProviderSettings | None = None,
) -> None:
    """Materialize explicit non-interactive model consent in the run home.

    Hermes intentionally fails closed for contributor-tier models unless the
    owner records the data-training acknowledgement.  Each JARVIS run uses a
    fresh isolated ``HERMES_HOME``; carrying the acknowledgement into that
    home is therefore part of the runtime contract, not a live-profile write.

    The same home carries the per-run provider request headers (see
    :func:`_provider_request_headers`) so the OpenCode session identity stays
    stable for the whole run without any live-profile or environment write.
    """
    config_path = home / "config.yaml"
    document: dict[str, Any] = {}
    if config_path.exists():
        import yaml

        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            from ..workers.adapters.base import AdapterConfigurationError

            raise AdapterConfigurationError(
                "isolated Hermes config.yaml must be a valid mapping")
        document.update(parsed)
    if hermes.get("allow_data_training_tiers_noninteractive") is True:
        security = document.get("security")
        if not isinstance(security, dict):
            security = {}
        security["allow_data_training_tiers_noninteractive"] = True
        document["security"] = security
    existing_providers = document.get("providers")
    existing_provider = (
        existing_providers.get("opencode-go")
        if isinstance(existing_providers, dict)
        else None
    )
    existing_headers = (
        existing_provider.get("extra_headers")
        if isinstance(existing_provider, dict)
        else None
    )
    headers = _provider_request_headers(
        hermes,
        provider_settings,
        existing_headers=existing_headers if isinstance(existing_headers, dict) else None,
    )
    if headers and provider_settings is not None:
        # Hermes names this provider with a hyphen. JARVIS uses the internal
        # underscore id only until the adapter boundary.
        document["providers"] = {
            "opencode-go": {
                "api": provider_settings.base_url,
                "extra_headers": headers,
            },
        }
        document["model"] = {
            "provider": "opencode-go",
            "model": provider_settings.model,
        }
        document["fallback_providers"] = []
        document["fallback_model"] = []
        document["credential_pool_strategies"] = {}
        agent = document.get("agent")
        if not isinstance(agent, dict):
            agent = {}
        # Hermes owns bounded same-route resends; the request gateway meters
        # each one independently. Do not allow a hidden route-level retry pool.
        agent["api_max_retries"] = 1
        document["agent"] = agent
        auxiliary = document.get("auxiliary")
        if not isinstance(auxiliary, dict):
            auxiliary = {}
        auxiliary["transient_retries"] = 0
        task_names = (
            "vision", "compression", "skills_hub", "approval", "review",
            "mcp", "title_generation", "memory_query_rewrite", "tts_audio_tags",
            "triage_specifier", "kanban_decomposer", "profile_describer",
            "goal_judge", "curator", "monitor", "background_review",
            "moa_reference", "moa_aggregator", "web_extract",
        )
        for task_name in task_names:
            task = auxiliary.get(task_name)
            if not isinstance(task, dict):
                task = {}
            task["provider"] = "opencode-go"
            task["model"] = provider_settings.model
            task["base_url"] = provider_settings.base_url
            task["api_key"] = ""
            task["api_mode"] = AUTHORIZED_API_MODE
            task["fallback_chain"] = []
            auxiliary[task_name] = task
        document["auxiliary"] = auxiliary
        delegation = document.get("delegation")
        if not isinstance(delegation, dict):
            delegation = {}
        delegation["provider"] = "opencode-go"
        delegation["model"] = provider_settings.model
        delegation["base_url"] = provider_settings.base_url
        delegation["api_key"] = ""
        delegation["api_mode"] = AUTHORIZED_API_MODE
        delegation["fallback_providers"] = []
        delegation["request_overrides"] = _delegation_request_overrides(
            delegation.get("request_overrides"), headers)
        document["delegation"] = delegation
    import yaml

    if document:
        temporary = config_path.with_suffix(".yaml.tmp")
        temporary.write_text(
            yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        temporary.replace(config_path)


def build_worker_stack(
    database: FoundryDatabase,
    paths: FoundryPaths,
    config: WorkerRuntimeConfig,
    *,
    run_id: str,
    pid_table: str | Path | None = None,
) -> WorkerStack:
    """Construct router + registry + bridge from ``config`` (run-scoped).

    Disabled adapters are not registered, so the router can never select an
    unavailable worker (honest NOT_READY / no eligible worker).
    """
    run_root = _resolve_root(config.runtime_state_root, run_id, kind="run", home=paths.home)
    stop_path = config.resolve_stop_path(paths.home)
    pid_table_path = (
        Path(pid_table).expanduser().resolve()
        if pid_table is not None
        else (paths.home / "adapter_worker_pids.json").resolve()
    )
    workers = WorkerController(database, pid_table=pid_table_path)
    provider_candidates = _provider_candidates(database, config)
    provider_settings_by_id = {
        item.provider: item for item in config.provider_settings_pool
    }
    mcp_backend = None
    if not config.lab_scripted_hermes:
        try:
            from ..mcp_backend import restore_selected_mcp_backend

            mcp_backend = restore_selected_mcp_backend(database)
        except Exception:
            # A malformed or unavailable optional adoption cannot create a
            # second runtime or prevent non-MCP worker work from proceeding.
            mcp_backend = None
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
        _prepare_hermes_home(
            home, hermes, provider_settings=config.provider_settings)
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
                pid_table=pid_table_path,
                workers=workers,
                provider_settings=config.provider_settings,
                provider_settings_by_id=provider_settings_by_id,
                database=database,
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
                pid_table=pid_table_path,
                workers=workers,
                provider_settings=config.provider_settings,
                provider_settings_by_id=provider_settings_by_id,
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

    from ..vault_indexer import configured_vault_root

    vault = configured_vault_root(database)
    bridge = HybridWorkerRoleAdapter(
        database=database,
        router=router,
        registry=registry,
        role_signals=dict(config.role_signals),
        stop_path=config.resolve_stop_path(paths.home),
        vault_root=vault,
        use_fabric=not config.lab_scripted_hermes,
        mcp_backend=mcp_backend,
    )
    return WorkerStack(
        bridge=bridge, router=router, registry=registry,
        disposable_root=run_root, enabled_frameworks=tuple(enabled),
        stop_path=stop_path, pid_table=pid_table_path, workers=workers,
        provider_candidates=provider_candidates,
        mcp_backend=mcp_backend,
    )
