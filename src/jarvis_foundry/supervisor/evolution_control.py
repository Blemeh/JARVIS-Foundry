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
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase
from ..events import Event
from ..evolution import canary as canary_mod
from ..evolution import cycle as cycle_mod
from ..evolution import detector as detector_mod
from ..evolution import discover as discover_mod
from ..evolution import promote as promote_mod
from ..evolution import records as records_mod
from ..evolution import registry as registry_mod
from ..evolution import restore as restore_mod
from .authority import SUPERVISOR_CONTROLLER

_MODES = ("observe", "active")
SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID = "v6-supervisor-intake"
_DISCOVERY_RETRY_DELAY = timedelta(minutes=5)


def recorded_evolution_mode(
    database: FoundryDatabase, *,
    source_type: str = "supervisor",
    source_id: str = SUPERVISOR_EVOLUTION_INTAKE_SOURCE_ID,
) -> str:
    """Restore only a valid mode transition from this control owner."""
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT source_type, source_id, payload_json "
            "FROM events WHERE event_type = 'evolution.mode_changed' "
            "AND source_type = ? AND source_id = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (source_type, source_id),
        ).fetchone()
    if row is None:
        return "observe"
    try:
        payload = json.loads(str(row["payload_json"]))
    except (TypeError, ValueError):
        return "observe"
    if not isinstance(payload, dict):
        return "observe"
    mode = payload.get("mode")
    if (
        row["source_type"] != source_type
        or row["source_id"] != source_id
        or payload.get("controller") != SUPERVISOR_CONTROLLER
        or mode not in _MODES
    ):
        return "observe"
    return str(mode)


def _utc_moment(now: datetime | str | None = None) -> datetime:
    if now is None:
        moment = utc_now()
    elif isinstance(now, str):
        moment = parse(now)
    elif isinstance(now, datetime):
        moment = now
    else:
        raise EvolutionControlError(
            "discovery time must be a datetime or ISO timestamp")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def daily_discovery_window(now: datetime | str | None = None) -> dict[str, str]:
    """Return the last completed UTC day as an explicit detector window."""
    end = _utc_moment(now).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    start = end - timedelta(days=1)
    return {"start": isoformat(start), "end": isoformat(end)}


def _valid_discovery_window(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    start_raw, end_raw = value.get("start"), value.get("end")
    if not isinstance(start_raw, str) or not isinstance(end_raw, str):
        return None
    try:
        start, end = _utc_moment(start_raw), _utc_moment(end_raw)
    except (EvolutionControlError, TypeError, ValueError, OverflowError):
        return None
    if (
        start >= end
        or start.hour != 0 or start.minute != 0 or start.second != 0
        or start.microsecond != 0
        or end - start != timedelta(days=1)
        or end.hour != 0 or end.minute != 0 or end.second != 0
        or end.microsecond != 0
    ):
        return None
    return {"start": isoformat(start), "end": isoformat(end)}


class EvolutionControlError(RuntimeError):
    """Refused by the Supervisor evolution control plane."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvolutionControlError(message)


class EvolutionControl:
    """Supervisor-owned gate in front of the evolution machinery."""

    def __init__(self, database: FoundryDatabase, *, mode: str = "observe",
                 source_type: str = "supervisor", source_id: str = "v7",
                 stop_path: str | Path | None = None) -> None:
        _require(mode in _MODES, f"mode must be one of {_MODES}")
        self._database = database
        self._mode = mode
        self._source_type = source_type
        self._source_id = source_id
        self._stop_path = (
            Path(stop_path) if stop_path is not None else
            Path(database.path).parent / "EMERGENCY_STOP"
        )

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
        return self._stop_path.exists()

    def _latest_discovery_attempt(self) -> tuple[str, dict[str, Any], str] | None:
        with self._database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT event_type, payload_json, recorded_at FROM events "
                "WHERE source_type = ? AND source_id = ? "
                "AND event_type IN ('discovery.observed', "
                "'evolution.discovery_failed') "
                "ORDER BY sequence DESC LIMIT 1",
                (self._source_type, self._source_id),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        recorded_at = str(row["recorded_at"])
        if str(row["event_type"]) == "evolution.discovery_failed":
            recorded_at = str(payload.get("attempted_at") or recorded_at)
        return str(row["event_type"]), payload, recorded_at

    def next_discovery_window(
        self, now: datetime | str | None = None,
    ) -> dict[str, str]:
        """Return the oldest unfinished day, or today's late-evidence scan.

        A single heartbeat advances at most one day. Persisted observations
        therefore act as a replay cursor after downtime; a failed day remains
        the retry target until it records successfully.
        """
        target = daily_discovery_window(now)
        target_end = _utc_moment(target["end"])
        latest = self._latest_discovery_attempt()
        if latest is None:
            return target
        event_type, payload, _ = latest
        previous = _valid_discovery_window(payload.get("window"))
        if previous is None:
            return target
        previous_end = _utc_moment(previous["end"])
        if previous_end > target_end:
            return target
        if event_type == "evolution.discovery_failed":
            return previous
        if previous_end >= target_end:
            return target
        next_end = previous_end + timedelta(days=1)
        if next_end > target_end:
            return target
        return {"start": isoformat(previous_end),
                "end": isoformat(next_end)}

    def refresh_recorded_mode(self) -> str:
        """Refresh this instance from its own durable mode stream."""
        self._mode = recorded_evolution_mode(
            self._database, source_type=self._source_type,
            source_id=self._source_id)
        return self._mode

    def run_discovery_window(
        self, *, window: dict[str, str], controller: str,
    ) -> dict[str, Any]:
        """Detect ledger-backed weaknesses and record them through V6 gates."""
        self.refresh_recorded_mode()
        self._gate(controller)
        now = _utc_moment()
        expected = self.next_discovery_window(now)
        _require(
            isinstance(window, dict)
            and window.get("start") == expected["start"]
            and window.get("end") == expected["end"],
            "discovery intake accepts only the Supervisor's next UTC day",
        )
        latest = self._latest_discovery_attempt()
        if latest is not None:
            event_type, payload, recorded_at = latest
            latest_window = payload.get("window")
            same_window = (
                isinstance(latest_window, dict)
                and latest_window.get("start") == window["start"]
                and latest_window.get("end") == window["end"]
            )
            if same_window and event_type == "evolution.discovery_failed":
                try:
                    if parse(recorded_at) + _DISCOVERY_RETRY_DELAY > now:
                        return {"status": "RETRY_BACKOFF", "window": dict(window)}
                except (TypeError, ValueError, OverflowError):
                    pass

        sources = detector_mod.discovery_sources(
            self._database,
            window_start=window["start"],
            window_end=window["end"],
        )
        sources_hash = discover_mod.sources_fingerprint(sources)
        if (
            latest is not None
            and latest[0] == "discovery.observed"
            and isinstance(latest[1].get("window"), dict)
            and latest[1]["window"].get("start") == window["start"]
            and latest[1]["window"].get("end") == window["end"]
            and latest[1].get("sources_hash") == sources_hash
        ):
            return {"status": "ALREADY_RECORDED", "window": dict(window)}
        candidates = discover_mod.candidate_improvements(
            self._database,
            sources=sources,
            controller=SUPERVISOR_CONTROLLER,
        )
        improvement_ids = discover_mod.record_discovered_improvements(
            self._database,
            candidates=candidates,
            sources=sources,
            controller=SUPERVISOR_CONTROLLER,
            source_type=self._source_type,
            source_id=self._source_id,
            window=window,
        )
        return {
            "status": "RECORDED",
            "window": dict(window),
            "observation_count": sum(len(items) for items in sources.values()),
            "improvement_ids": improvement_ids,
        }

    def record_discovery_failure(
        self, *, window: dict[str, str], error_class: str,
        controller: str,
    ) -> None:
        """Record a bounded failure class so retries remain visible and paced."""
        self.refresh_recorded_mode()
        self._gate(controller)
        _require(window == self.next_discovery_window(),
                 "discovery failure accepts only the Supervisor's next UTC day")
        safe_class = str(error_class or "UnknownError")[:80]
        _require(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", safe_class) is not None,
            "discovery failure error_class must be an exception class token",
        )
        attempted_at = _utc_moment()
        retry_bucket = isoformat(attempted_at.replace(second=0, microsecond=0))
        self._database.append(Event.create(
            "evolution.discovery_failed",
            {"window": dict(window), "error_class": safe_class,
             "controller": SUPERVISOR_CONTROLLER,
             "attempted_at": isoformat(attempted_at)},
            source_type=self._source_type,
            source_id=self._source_id,
            idempotency_key=(
                f"evolution.discovery_failed:{window['end']}:"
                f"{retry_bucket}:{safe_class}"),
        ))

    # -- mode transitions (Supervisor only, ledger-recorded) ------------------

    def _set_mode(self, mode: str, controller: str) -> None:
        self._require_supervisor(controller)
        _require(mode in _MODES, f"mode must be one of {_MODES}")
        self.refresh_recorded_mode()
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
