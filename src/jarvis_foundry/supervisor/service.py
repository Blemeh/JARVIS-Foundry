from __future__ import annotations

import uuid
from pathlib import Path

from ..database import FoundryDatabase
from ..events import Event
from ..reconcile import reconcile
from .estop import EmergencyStop
from .leases import LeaseManager
from .worker import WorkerController


class Supervisor:
    LEASE_KEY = "supervisor:singleton"

    def __init__(
        self,
        database: FoundryDatabase,
        stop_path: str | Path,
        *,
        instance_id: str | None = None,
        lease_seconds: int = 90,
        workers: WorkerController | None = None,
    ) -> None:
        self.database = database
        self.estop = EmergencyStop(stop_path)
        self.instance_id = instance_id or f"supervisor-{uuid.uuid4()}"
        self.lease_seconds = lease_seconds
        self.leases = LeaseManager(database)
        self.workers = workers or WorkerController(database)
        self.started = False

    def start(self) -> str:
        self.database.initialize()
        report = reconcile(self.database)
        if not report.consistent:
            self.database.append(Event.create(
                "supervisor.degraded",
                {
                    "instance_id": self.instance_id,
                    "trust_state": "LAB",
                    "reason": "reconciliation failed at startup",
                    "mismatch_count": len(report.mismatches),
                    "integrity_failure_count": len(report.integrity_failures),
                },
                source_type="supervisor", source_id=self.instance_id,
                idempotency_key=f"supervisor.degraded:{self.instance_id}:startup",
            ))
            return "DEGRADED"
        self._reap_and_recover()
        self.leases.acquire(self.LEASE_KEY, self.instance_id, self.lease_seconds)
        stop = self.estop.read()
        event_type = "emergency_stop.frozen" if stop else "supervisor.started"
        payload = {
            "instance_id": self.instance_id,
            "trust_state": "SUSPENDED" if stop else "LAB",
            "stop_id": stop.get("stop_id") if stop else None,
        }
        self.database.append(Event.create(
            event_type, payload, source_type="supervisor", source_id=self.instance_id,
            idempotency_key=f"{event_type}:{self.instance_id}:{payload.get('stop_id') or 'start'}",
        ))
        self.started = True
        return "FROZEN" if stop else "RUNNING"

    def heartbeat(self) -> str:
        if not self.started:
            raise RuntimeError("supervisor has not started")
        self.leases.acquire(self.LEASE_KEY, self.instance_id, self.lease_seconds)
        reaped = self.workers.reap()
        stop = self.estop.read()
        if stop:
            self.workers.terminate_all()
            self.database.append(Event.create(
                "emergency_stop.frozen",
                {"instance_id": self.instance_id, "stop_id": stop["stop_id"], "reason": stop["reason"]},
                source_type="supervisor", source_id=self.instance_id,
                idempotency_key=f"emergency_stop.frozen:{stop['stop_id']}",
            ))
            return "FROZEN"
        self._reap_and_recover()
        payload = {"instance_id": self.instance_id, "trust_state": "LAB"}
        if reaped:
            payload["reaped_workers"] = reaped
        self.database.append(Event.create(
            "supervisor.heartbeat", payload,
            source_type="supervisor", source_id=self.instance_id,
        ))
        return "RUNNING"

    def stop(self) -> None:
        if self.started:
            self.workers.terminate_all()
            self.database.append(Event.create(
                "supervisor.stopped", {"instance_id": self.instance_id, "trust_state": "LAB"},
                source_type="supervisor", source_id=self.instance_id,
            ))
            self.leases.release(self.LEASE_KEY, self.instance_id)
            self.started = False

    def _reap_and_recover(self) -> None:
        """Deterministic stale-session hygiene (plan F3 wiring).

        - ``reap_stale_role_runs`` marks RUNNING role sessions whose lease
          expired/missing as FAILED (heartbeat_timeout) and releases the
          expired role leases (the ``role:*`` counterpart of
          ``workers.reap()``);
        - ``recover_stale_mission_state`` resumes missions left stuck in
          EXECUTING/VERIFYING by a killed session to ACTION_SELECTION so a
          fresh loop instance re-claims the step (partial work preserved).

        Both are pure ledger logic, idempotent, and append events only when
        something is actually stale. A maintenance failure is recorded as a
        throttled ``supervisor.degraded`` event and NEVER takes the daemon
        down (verifier finding F3-2: a raising recovery path must not crash
        the heartbeat; skipping one maintenance pass only delays recovery).
        """
        from ..roles.reaper import reap_stale_role_runs, recover_stale_mission_state
        try:
            reap_stale_role_runs(self.database)
            recover_stale_mission_state(self.database)
        except Exception as error:  # noqa: BLE001 — maintenance must not kill the daemon
            try:
                self.database.append(Event.create(
                    "supervisor.degraded",
                    {"instance_id": self.instance_id, "trust_state": "LAB",
                     "reason": "role_reaper_failure",
                     "error_class": type(error).__name__,
                     "error": str(error)[:300]},
                    source_type="supervisor", source_id=self.instance_id,
                    idempotency_key=(
                        f"supervisor.degraded:{self.instance_id}:role_reaper_failure"),
                ))
            except Exception:  # noqa: BLE001 — never let the record itself raise
                pass

    def doctor(self) -> dict[str, object]:
        self.database.initialize()
        return {
            "database": str(self.database.path),
            "database_quick_check": self.database.quick_check(),
            "emergency_stop": self.estop.active(),
            "event_count": len(self.database.events()),
            "hermes_required_for_supervisor": False,
            "reconciliation": reconcile(self.database).to_json(),
            "active_workers": self.workers.active_runs(),
        }
