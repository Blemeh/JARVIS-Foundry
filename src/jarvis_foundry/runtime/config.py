"""Explicit auditable configuration for the live worker loop."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .provider_config import (
    ProviderSettings,
    default_provider_settings,
    load_provider_settings,
    load_provider_settings_pool,
)


@dataclass(frozen=True, slots=True)
class WorkerRuntimeConfig:
    """Explicit, auditable activation configuration for the worker loop.

    Defaults fail closed: the driver refuses to run cycles unless
    ``enabled`` is true. Provider/model are owned by ``provider_settings``.
    """

    enabled: bool = False
    status: str = "INACTIVE"
    mission_id: str = "mission-wfm-optimizer"
    resource_id: str = "resource-wfm-optimizer"
    capability: str = "write_registered_worktree_files"
    provider: str = "opencode_go"
    model: str = "muse-spark-1.2-contributor"
    runtime_state_root: str = "var/worker-runtime"
    hermes: dict[str, Any] = field(default_factory=dict)
    openhands: dict[str, Any] = field(default_factory=dict)
    readiness: dict[str, dict[str, Any]] = field(default_factory=dict)
    role_signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_repairs: int = 2
    stop_sentinel_path: str | None = None
    pause_directives: bool = True
    lab_scripted_hermes: bool = False
    lab_crash_after_write: bool = False
    lab_crash_step_id: str = "step-a"
    lab_false_success_step_id: str | None = None
    provider_settings: ProviderSettings = field(default_factory=default_provider_settings)
    provider_settings_pool: tuple[ProviderSettings, ...] = field(default_factory=tuple)
    # V12 provider routing is opt-in.  The pool is still resolved at the
    # runtime boundary when disabled, but no fallback behavior changes.
    provider_resilience_enabled: bool = False
    provider_resilience: dict[str, Any] = field(default_factory=dict)
    executive_selection_enabled: bool = False
    # V8 Executive Autonomy: opt-in orchestration above the existing V7
    # portfolio selector.  It only plans/advises through canonical seams.
    executive_autonomy_enabled: bool = False
    executive_min_action_score: float = 0.40
    executive_wait_margin: float = 0.05
    executive_activation_epoch: str | None = None
    executive_candidate_limit: int = 20
    # V7 full-autonomy: portfolio-level selection above candidate selection.
    # OFF by default; when ON the loop consults select_next_work after the
    # candidate path yields nothing, and WAIT/wake semantics govern idling.
    portfolio_selection_enabled: bool = False
    # V3 Discovery and Building: the unified cycle stays OFF by default;
    # production keeps the v3_loop WAIT + executive selection split.
    discovery_cycle_enabled: bool = False
    # V9 Continuous Executive: durable wake/condition system above autonomy.
    # OFF by default; when ON the loop uses run_continuous_cycle as the single wake-aware path.
    continuous_enabled: bool = False
    # Discovery inside the Continuous Executive: observing registered projects
    # and admitting the Executive's selected work is part of the canonical
    # next-work authority, not a separate selector branch.  DEFAULT ON so a
    # candidate that enables the Continuous Executive actually observes.
    continuous_discovery_enabled: bool = True
    # V11 A2 initiative observation/judgment is an inert admission-boundary
    # probe.  It may observe, research, and rank, but it is mutually exclusive
    # with every execution-capable selector and remains OFF in production.
    initiative_a2_enabled: bool = False
    initiative_a2: dict[str, Any] = field(default_factory=dict)
    # Durable due-driven productionization of A2. It reuses monitor rows,
    # ResearchQueue, Executive judgment, and Executive time wakes, and remains
    # admission-boundary-only while A3 owns objective creation.
    continuous_observation_enabled: bool = False
    continuous_observation: dict[str, Any] = field(default_factory=dict)
    # V11 A3 consumes the exact A2 contract and may admit one small,
    # reversible owner-repository objective through the existing Executive.
    # It remains opt-in and isolated from every legacy selector flag.
    initiative_a3_enabled: bool = False
    initiative_a3: dict[str, Any] = field(default_factory=dict)
    # V12 objective-scale campaign recovery.  Opt-in so existing V11
    # configurations retain their exact execution path by default.
    campaign_runtime_enabled: bool = False
    campaign_runtime: dict[str, Any] = field(default_factory=dict)

    @property
    def campaign_enabled(self) -> bool:
        """Canonical opt-in bit, accepting the nested YAML form as well."""

        section = self.campaign_runtime if isinstance(self.campaign_runtime, dict) else {}
        return bool(self.campaign_runtime_enabled or section.get("enabled", False))

    @classmethod
    def from_yaml(
        cls,
        path: str | Path | None,
        *,
        provider_settings: ProviderSettings | None = None,
        provider_settings_pool: tuple[ProviderSettings, ...] | None = None,
    ) -> "WorkerRuntimeConfig":
        if provider_settings is None and provider_settings_pool is None:
            # Preserve the single, patchable primary-resolution boundary used
            # by the existing runtime contract.  The pool loader receives that
            # already-resolved identity and therefore never resolves it twice.
            settings = load_provider_settings()
            resolved_pool = load_provider_settings_pool(
                primary_settings=settings)
            settings_pool = resolved_pool.providers
        else:
            settings = provider_settings or load_provider_settings()
            settings_pool = tuple(provider_settings_pool or (settings,))
        if not settings_pool:
            settings_pool = (settings,)
        if path is None:
            return replace(
                cls(),
                provider=settings.provider,
                model=settings.model,
                provider_settings=settings,
                provider_settings_pool=settings_pool,
            )
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"worker runtime config {path} must be a mapping")
        campaign_raw = raw.get("campaign_runtime")
        campaign_section = dict(campaign_raw) if isinstance(campaign_raw, dict) else {}
        cfg = cls(
            enabled=bool(raw.get("enabled", False)),
            status=str(raw.get("status", "INACTIVE")),
            mission_id=str(raw.get("mission_id", "mission-wfm-optimizer")),
            resource_id=str(raw.get("resource_id", "resource-wfm-optimizer")),
            capability=str(
                raw.get("capability", "write_registered_worktree_files")),
            provider=settings.provider,
            model=settings.model,
            runtime_state_root=str(
                raw.get("runtime_state_root", "var/worker-runtime")),
            hermes=dict(raw.get("hermes") or {}),
            openhands=dict(raw.get("openhands") or {}),
            readiness=dict(raw.get("readiness") or {}),
            role_signals=dict(raw.get("role_signals") or {}),
            max_repairs=int(raw.get("verification", {}).get("max_repairs", 2)),
            stop_sentinel_path=raw.get(
                "stop_integration", {}).get("sentinel_path"),
            pause_directives=bool(
                raw.get("stop_integration", {}).get("pause_directives", True)),
            lab_scripted_hermes=bool(raw.get("lab_scripted_hermes", False)),
            lab_crash_after_write=bool(raw.get("lab_crash_after_write", False)),
            lab_crash_step_id=str(raw.get("lab_crash_step_id", "step-a")),
            lab_false_success_step_id=(
                str(raw["lab_false_success_step_id"])
                if raw.get("lab_false_success_step_id") else None),
            provider_settings=settings,
            provider_settings_pool=settings_pool,
            provider_resilience_enabled=bool(
                raw.get("provider_resilience", {}).get("enabled", False)),
            provider_resilience=dict(raw.get("provider_resilience") or {}),
            executive_selection_enabled=bool(
                raw.get("executive", {}).get("selection_enabled", False)),
            executive_autonomy_enabled=bool(
                raw.get("executive", {}).get("autonomy_enabled", False)),
            executive_min_action_score=float(
                raw.get("executive", {}).get("min_action_score", 0.40)),
            executive_wait_margin=float(
                raw.get("executive", {}).get("wait_margin", 0.05)),
            executive_activation_epoch=raw.get("executive", {}).get("activation_epoch"),
            executive_candidate_limit=int(
                raw.get("executive", {}).get("candidate_limit", 20)),
            portfolio_selection_enabled=bool(
                raw.get("executive", {}).get("portfolio_selection_enabled",
                                             False)),
            discovery_cycle_enabled=bool(
                raw.get("discovery", {}).get("cycle_enabled", False)),
            continuous_enabled=bool(
                raw.get("executive", {}).get("continuous_enabled", False) or raw.get("continuous", {}).get("enabled", False)),
            continuous_discovery_enabled=bool(
                raw.get("executive", {}).get(
                    "continuous_discovery_enabled",
                    raw.get("continuous", {}).get("discovery_enabled", True),
                )),
            initiative_a2_enabled=bool(
                raw.get("initiative_a2", {}).get("enabled", False)),
            initiative_a2=dict(raw.get("initiative_a2") or {}),
            continuous_observation_enabled=bool(
                raw.get("continuous_observation", {}).get("enabled", False)),
            continuous_observation=dict(raw.get("continuous_observation") or {}),
            initiative_a3_enabled=bool(
                raw.get("initiative_a3", {}).get("enabled", False)),
            initiative_a3=dict(raw.get("initiative_a3") or {}),
            campaign_runtime_enabled=bool(
                raw.get(
                    "campaign_runtime_enabled",
                    campaign_section.get("enabled", False),
                )),
            campaign_runtime=campaign_section,
        )
        cfg.validate()
        return cfg

    def resolve_stop_path(self, home: Path) -> Path:
        """Sentinel the worker loop and bridge must watch."""
        canonical = (home / "EMERGENCY_STOP").resolve()
        if not self.stop_sentinel_path:
            return canonical
        configured = Path(self.stop_sentinel_path)
        if configured.is_absolute():
            return configured.resolve()
        parts = configured.parts
        if len(parts) >= 2 and parts[0] == home.name:
            configured = Path(*parts[1:]) if len(parts) > 1 else Path("EMERGENCY_STOP")
        return (home / configured).resolve()

    def validate(self) -> None:
        """Fail closed on any deviation from the resolved provider mandate."""
        if (self.provider != self.provider_settings.provider
                or self.model != self.provider_settings.model):
            raise ValueError(
                f"worker runtime must route to "
                f"{self.provider_settings.provider!r} + "
                f"{self.provider_settings.model!r} only, got "
                f"{self.provider!r} + {self.model!r}")
        if self.provider_resilience_enabled:
            identities = [(item.provider, item.model) for item in self.provider_settings_pool]
            if not identities or identities[0] != (
                self.provider_settings.provider, self.provider_settings.model,
            ):
                raise ValueError(
                    "provider resilience pool must start with the resolved primary identity")
            if len({provider for provider, _model in identities}) != len(identities):
                raise ValueError(
                    "provider resilience requires one configured model per provider")
            bounds = dict(self.provider_resilience.get("retry") or {})
            max_total = int(bounds.get("max_total_attempts", 4))
            max_same = int(bounds.get("max_retries_per_provider", 0))
            if max_total < 1 or max_total > 12:
                raise ValueError("provider max_total_attempts must be between 1 and 12")
            if max_same < 0 or max_same > 2:
                raise ValueError("provider max_retries_per_provider must be between 0 and 2")
            max_cost = float(self.provider_resilience.get("max_cost_usd") or 0.0)
            if max_cost < 0:
                raise ValueError("provider max_cost_usd must be non-negative")
            constraints = self.provider_resilience.get("constraints") or {}
            if not isinstance(constraints, dict):
                raise ValueError("provider resilience constraints must be a mapping")
        if self.initiative_a2_enabled and self.continuous_observation_enabled:
            raise ValueError(
                "initiative_a2 and continuous_observation are alternate "
                "admission-boundary drivers and cannot both be enabled")
        if self.initiative_a2_enabled or self.continuous_observation_enabled:
            execution_flags = {
                "discovery.cycle_enabled": self.discovery_cycle_enabled,
                "executive.selection_enabled": self.executive_selection_enabled,
                "executive.autonomy_enabled": self.executive_autonomy_enabled,
                "executive.portfolio_selection_enabled": (
                    self.portfolio_selection_enabled),
                "executive.continuous_enabled": self.continuous_enabled,
            }
            active = [name for name, enabled in execution_flags.items() if enabled]
            if active:
                raise ValueError(
                    "observation/judgment is zero-execution and cannot be combined "
                    f"with execution-capable runtime flags: {', '.join(active)}")
        if self.initiative_a2_enabled and self.initiative_a3_enabled:
            raise ValueError("initiative_a2 and initiative_a3 are mutually exclusive")
        if self.initiative_a3_enabled and self.continuous_observation_enabled:
            raise ValueError(
                "initiative_a3 and continuous_observation are mutually exclusive "
                "admission-boundary drivers")
        if self.initiative_a3_enabled:
            competing_flags = {
                "discovery.cycle_enabled": self.discovery_cycle_enabled,
                "executive.selection_enabled": self.executive_selection_enabled,
                "executive.autonomy_enabled": self.executive_autonomy_enabled,
                "executive.portfolio_selection_enabled": self.portfolio_selection_enabled,
                "executive.continuous_enabled": self.continuous_enabled,
            }
            active = [name for name, enabled in competing_flags.items() if enabled]
            if active:
                raise ValueError(
                    "initiative_a3 owns the bounded admission call into the existing "
                    "Executive and cannot be combined with competing runtime entry "
                    f"flags: {', '.join(active)}")
        if self.campaign_enabled and (
                self.initiative_a2_enabled or self.continuous_observation_enabled):
            raise ValueError(
                "campaign_runtime is execution-capable and cannot be combined "
                "with zero-execution observation modes")
        if self.continuous_enabled:
            # The Continuous Executive is the single canonical next-work
            # authority.  Any YAML that lets a second factory create a mission
            # in the same cycle is rejected here, including the
            # continuous+portfolio pair that used to validate successfully.
            competing_factories = {
                "discovery.cycle_enabled": self.discovery_cycle_enabled,
                "executive.selection_enabled": self.executive_selection_enabled,
                "executive.autonomy_enabled": self.executive_autonomy_enabled,
                "executive.portfolio_selection_enabled": (
                    self.portfolio_selection_enabled),
                "initiative_a3.enabled": self.initiative_a3_enabled,
            }
            active = [
                name for name, enabled in competing_factories.items() if enabled
            ]
            if active:
                raise ValueError(
                    "executive.continuous_enabled is the single next-work "
                    "authority and cannot be combined with a competing mission "
                    f"factory: {', '.join(active)}")
