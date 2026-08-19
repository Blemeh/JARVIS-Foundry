"""Explicit auditable configuration for the live worker loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkerRuntimeConfig:
    """Explicit, auditable activation configuration for the worker loop.

    Defaults fail closed: the driver refuses to run cycles unless
    ``enabled`` is true. Provider/model are fixed by the owner mandate.
    """

    enabled: bool = False
    status: str = "INACTIVE"
    mission_id: str = "mission-wfm-optimizer"
    resource_id: str = "resource-wfm-optimizer"
    capability: str = "write_registered_worktree_files"
    provider: str = "opencode_go"
    model: str = "deepseek-v4-flash"
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
    executive_selection_enabled: bool = False
    executive_min_action_score: float = 0.40
    executive_wait_margin: float = 0.05
    executive_activation_epoch: str | None = None
    executive_candidate_limit: int = 20

    @classmethod
    def from_yaml(cls, path: str | Path | None) -> "WorkerRuntimeConfig":
        if path is None:
            return cls()  # fail closed: disabled
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
            provider=str(raw.get("provider", "opencode_go")),
            model=str(raw.get("model", "deepseek-v4-flash")),
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
            executive_selection_enabled=bool(
                raw.get("executive", {}).get("selection_enabled", False)),
            executive_min_action_score=float(
                raw.get("executive", {}).get("min_action_score", 0.40)),
            executive_wait_margin=float(
                raw.get("executive", {}).get("wait_margin", 0.05)),
            executive_activation_epoch=raw.get("executive", {}).get("activation_epoch"),
            executive_candidate_limit=int(
                raw.get("executive", {}).get("candidate_limit", 20)),
        )
        cfg.validate()
        return cfg

    def resolve_stop_path(self, home: Path) -> Path:
        """Sentinel the worker loop and bridge must watch.

        Historical yaml used ``foundry/EMERGENCY_STOP`` while Foundry home
        is already ``.../foundry``, which nested a second foundry directory
        the CLI never writes. A leading segment equal to the home name is
        stripped so STOP and ``jarvis stop`` share one file.
        """
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
        """Fail closed on any deviation from the owner model mandate."""
        from ..roles.router import DIRECTIVE_MODEL, DIRECTIVE_PROVIDER

        if self.provider != DIRECTIVE_PROVIDER or self.model != DIRECTIVE_MODEL:
            raise ValueError(
                f"worker runtime must route to {DIRECTIVE_PROVIDER!r} + "
                f"{DIRECTIVE_MODEL!r} only, got {self.provider!r} + "
                f"{self.model!r}")
