"""Supervisor-owned evolution control plane (V7-S3 adoption wiring).

A thin deterministic adapter over the V6 evolution machinery. Its job is
identity pinning, mode gating, and STOP gating — ALL semantics live in the
evolution modules (records, registry, builder, lab, verify, shadow, canary,
promote, restore, discover, cycle, acceptance).

Rules enforced here:
- the only accepted controller identity is ``supervisor`` (the durable
  control plane); workers, candidates, and retrieved content can never
  present another identity;
- evolution defaults to OBSERVE mode (no effectful operation runs until the
  Supervisor explicitly enables it and records the transition in the ledger);
- an active STOP sentinel blocks every effectful operation;
- the control plane is plain local files + the ledger: no Hermes, UI, voice,
  provider, or network is required to drive or to disable it.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from ..evolution import canary as canary_mod
from ..evolution import cycle as cycle_mod
from ..evolution import discover as discover_mod
from ..evolution import promote as promote_mod
from ..evolution import records as records_mod
from ..evolution import registry as registry_mod
from ..evolution import restore as restore_mod

SUPERVISOR_CONTROLLER = "supervisor"
_MODES = ("observe", "active")


class EvolutionControlError(RuntimeError):
    """Refused by the Supervisor evolution control plane."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvolutionControlError(message)


class EvolutionControl:
    """Supervisor-owned gate in front of the evolution machinery."""

    def __init__(self, database: FoundryDatabase, *, mode: str = "observe",
                 source_type: str = "supervisor", source_id: str = "v7") -> None:
        _require(mode in _MODES, f"mode must be one of {_MODES}")
        self._database = database
        self._mode = mode
        self._source_type = source_type
        self._source_id = source_id

    # -- state ---------------------------------------------------------------

    def mode(self) -> str:
        return self._mode

    def is_frozen(self) -> bool:
        return promote_mod.is_frozen(self._database)

    def slot_pointers(self) -> dict[str, str | None]:
        return registry_mod.slot_pointers(self._database)

    def canary_status(self, canary_id: str) -> str:
        return canary_mod.canary_status(self._database, canary_id)

    def _stop_active(self) -> bool:
        return (Path(self._database.path).parent / "EMERGENCY_STOP").exists()

    # -- mode transitions (Supervisor only, ledger-recorded) ------------------

    def _set_mode(self, mode: str, controller: str) -> None:
        self._require_supervisor(controller)
        _require(mode in _MODES, f"mode must be one of {_MODES}")
        if self._mode == mode:
            return
        self._database.append(Event.create(
            "evolution.mode_changed",
            {"mode": mode, "controller": SUPERVISOR_CONTROLLER,
             "previous_mode": self._mode},
            source_type=self._source_type, source_id=self._source_id,
            idempotency_key=(
                f"evolution.mode_changed:{uuid.uuid4().hex[:12]}")))
        self._mode = mode

    def enable(self, *, controller: str) -> None:
        """Switch evolution to ACTIVE. Observe-only until this is called."""
        self._set_mode("active", controller)

    def disable(self, *, controller: str) -> None:
        """Switch evolution back to OBSERVE (effectful ops refused)."""
        self._set_mode("observe", controller)

    # -- gates ----------------------------------------------------------------

    def _require_supervisor(self, controller: str) -> None:
        _require(
            isinstance(controller, str)
            and controller == SUPERVISOR_CONTROLLER,
            "only the Supervisor (controller) can drive the evolution "
            "control plane")

    def _gate(self, controller: str, *, effectful: bool = True) -> None:
        self._require_supervisor(controller)
        if effectful:
            _require(self._mode == "active",
                     "observe mode — effectful evolution operations are "
                     "disabled (Supervisor must enable() first)")
            _require(not self._stop_active(),
                     "STOP active — evolution operations are blocked")

    # -- effectful operations (Supervisor-pinned) -----------------------------

    def run_cycle(self, *, controller: str, **kwargs: Any) -> dict[str, Any]:
        self._gate(controller)
        return cycle_mod.run_cycle(
            self._database, controller=SUPERVISOR_CONTROLLER, **kwargs)

    def start_canary(self, *, controller: str, **kwargs: Any) -> str:
        self._gate(controller)
        return canary_mod.start_canary(
            self._database, controller=SUPERVISOR_CONTROLLER, **kwargs)

    def canary_tick(self, *, controller: str, **kwargs: Any) -> dict[str, Any]:
        self._gate(controller)
        return canary_mod.canary_tick(
            self._database, controller=SUPERVISOR_CONTROLLER, **kwargs)

    def terminate_canary(self, *, controller: str, **kwargs: Any) -> None:
        self._gate(controller)
        return canary_mod.terminate_canary(
            self._database, controller=SUPERVISOR_CONTROLLER, **kwargs)

    def reap_stale_canaries(self, *, controller: str,
                            current_cycle: int,
                            max_cycles_without_tick: int) -> list[str]:
        self._gate(controller)
        return canary_mod.reap_stale_canaries(
            self._database, current_cycle=current_cycle,
            max_cycles_without_tick=max_cycles_without_tick,
            controller=SUPERVISOR_CONTROLLER)

    def freeze(self, *, reason: str, controller: str) -> None:
        self._gate(controller)
        promote_mod.freeze_active(
            self._database, reason=reason, controller=SUPERVISOR_CONTROLLER)

    def unfreeze(self, *, controller: str) -> None:
        self._gate(controller)
        promote_mod.unfreeze_active(
            self._database, controller=SUPERVISOR_CONTROLLER)

    def promote(self, *, controller: str, **kwargs: Any) -> str:
        self._gate(controller)
        return promote_mod.promote_release(
            self._database, controller=SUPERVISOR_CONTROLLER, **kwargs)

    def rollback(self, *, trigger: str, reason: str,
                 controller: str) -> str:
        self._gate(controller)
        return promote_mod.rollback_release(
            self._database, trigger=trigger, reason=reason,
            controller=SUPERVISOR_CONTROLLER)

    def set_slot(self, slot: str, release_id: str | None, *, reason: str,
                 controller: str, transition_id: str | None = None) -> None:
        self._gate(controller)
        registry_mod.set_slot(
            self._database, slot, release_id, reason=reason,
            controller=SUPERVISOR_CONTROLLER, transition_id=transition_id)

    def create_restore_point(self, *, label: str,
                             controller: str) -> str:
        self._gate(controller)
        return restore_mod.create_restore_point(
            self._database, label=label, controller=SUPERVISOR_CONTROLLER,
            source_type=self._source_type, source_id=self._source_id)

    def restore(self, *, point_id: str, controller: str) -> int:
        self._gate(controller)
        return restore_mod.restore_from_point(
            self._database, point_id=point_id,
            controller=SUPERVISOR_CONTROLLER,
            source_type=self._source_type, source_id=self._source_id)

    def record_discovered(self, *, candidates: list[dict[str, Any]],
                          sources: dict[str, Any],
                          controller: str) -> list[str]:
        self._gate(controller)
        return discover_mod.record_discovered_improvements(
            self._database, candidates=candidates, sources=sources,
            controller=SUPERVISOR_CONTROLLER,
            source_type=self._source_type, source_id=self._source_id)

    def record_improvement(self, *, controller: str,
                           **kwargs: Any) -> str:
        # recording an improvement freezes a hypothesis — it is effectful
        # (append-only ledger write) and Supervisor-pinned
        self._gate(controller)
        return records_mod.record_improvement(
            self._database, source_type=self._source_type,
            source_id=self._source_id, **kwargs)

    def register_release(self, *, controller: str, **kwargs: Any) -> str:
        self._gate(controller)
        return registry_mod.register_release(
            self._database, controller=SUPERVISOR_CONTROLLER,
            source_type=self._source_type, source_id=self._source_id,
            **kwargs)
