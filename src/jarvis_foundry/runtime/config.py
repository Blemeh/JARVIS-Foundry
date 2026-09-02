"""Explicit auditable configuration for the live worker loop."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .provider_config import (
    ProviderSettings,
    default_provider_settings,
    load_provider_settings,
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
    # V11 A2 initiative observation/judgment is an inert admission-boundary
    # probe.  It may observe, research, and rank, but it is mutually exclusive
    # with every execution-capable selector and remains OFF in production.
    initiative_a2_enabled: bool = False
    initiative_a2: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(
        cls,
        path: str | Path | None,
        *,
        provider_settings: ProviderSettings | None = None,
    ) -> "WorkerRuntimeConfig":
        settings = provider_settings or load_provider_settings()
        if path is None:
            return replace(
                cls(),
                provider=settings.provider,
                model=settings.model,
                provider_settings=settings,
            )
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"worker runtime config {path} must be a mapping")
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
            initiative_a2_enabled=bool(
                raw.get("initiative_a2", {}).get("enabled", False)),
            initiative_a2=dict(raw.get("initiative_a2") or {}),
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
        if self.initiative_a2_enabled:
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
                    "initiative_a2 is zero-execution and cannot be combined "
                    f"with execution-capable runtime flags: {', '.join(active)}")
