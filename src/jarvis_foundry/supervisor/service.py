from __future__ import annotations

import sys
import uuid
from pathlib import Path

from ..clock import utc_now
from ..database import FoundryDatabase
from ..events import Event
from ..reconcile import inspect as inspect_ledger
from ..reconcile import reconcile
from ..runtime.authority import ALLOWED_TRUST_STATES
from ..runtime.provider_config import ProviderConfigError, ProviderSettings
from .estop import EmergencyStop
from .leases import LeaseManager, LeaseUnavailable
from .worker import WorkerController

WORKER_LOOP_RUN_ID = "worker-loop"


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
        spawn_worker_loop: bool = False,
        worker_loop_config: str | Path | None = None,
        ledger_heartbeat_seconds: int = 60,
        provider_settings: ProviderSettings | None = None,
    ) -> None:
        self.database = database
        self.estop = EmergencyStop(stop_path)
        self.instance_id = instance_id or f"supervisor-{uuid.uuid4()}"
        self.lease_seconds = lease_seconds
        self.leases = LeaseManager(database)
        self.workers = workers or WorkerController(database)
        self.started = False
        self.spawn_worker_loop = spawn_worker_loop
        self.worker_loop_config = (
            Path(worker_loop_config) if worker_loop_config is not None else None)
        self.ledger_heartbeat_seconds = ledger_heartbeat_seconds
        self._provider_settings = provider_settings

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
                idempotency_key=f"emergency_stop.frozen:{stop['stop_id']}:{self.instance_id}",
            ))
            return "FROZEN"
        self._maybe_spawn_worker_loop()
        self._reap_and_recover()
        payload = {
            "instance_id": self.instance_id,
            "trust_state": self._preserved_trust(),
        }
        if reaped:
            payload["reaped_workers"] = reaped
        key = self._heartbeat_idempotency_key(reaped)
        self.database.append(Event.create(
            "supervisor.heartbeat", payload,
            source_type="supervisor", source_id=self.instance_id,
            idempotency_key=key,
        ))
        return "RUNNING"

    def _preserved_trust(self) -> str:
        """Heartbeats are not a trust gate. Never promote to TRUSTED_AUTONOMOUS."""
        trust = "LAB"
        try:
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT trust_state FROM supervisor_state WHERE singleton = 1"
                ).fetchone()
            if row is not None and row["trust_state"] in ALLOWED_TRUST_STATES:
                trust = str(row["trust_state"])
        except Exception:  # noqa: BLE001 — fail closed to LAB
            trust = "LAB"
        return trust

    def _heartbeat_idempotency_key(self, reaped: list | tuple | None) -> str:
        """Coalesce non-material heartbeats; keep reap events distinct."""
        if self.ledger_heartbeat_seconds <= 0:
            return str(uuid.uuid4())
        bucket = int(utc_now().timestamp() // self.ledger_heartbeat_seconds)
        reap_token = ",".join(str(item) for item in (reaped or ()))
        return f"supervisor.heartbeat:{self.instance_id}:{bucket}:{reap_token or 'ok'}"

    def stop(self) -> None:
        if self.started:
            self.workers.terminate_all()
            self.database.append(Event.create(
                "supervisor.stopped",
                {"instance_id": self.instance_id, "trust_state": self._preserved_trust()},
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

    def _maybe_spawn_worker_loop(self) -> None:
        """Optional fail-closed companion: spawn ``worker_loop --once``.

        Default OFF so the live watchdog-only Supervisor does not start
        dispatching missions. No-ops when worker YAML ``enabled`` is false.
        The child is registered with WorkerController so STOP/terminate_all
        applies. Heartbeat never waits on the child. Loop logic stays in
        ``runtime.worker_loop`` / ``v3_loop`` — not inside ``heartbeat()``.
        """
        if not self.spawn_worker_loop:
            return
        from ..runtime.config import WorkerRuntimeConfig

        try:
            config = WorkerRuntimeConfig.from_yaml(
                self.worker_loop_config,
                provider_settings=self._provider_settings,
            )
        except (OSError, ValueError, TypeError, ProviderConfigError):
            return
        if not config.enabled:
            return
        status = self.workers.status(WORKER_LOOP_RUN_ID)
        if status.alive:
            return
        home = Path(self.database.path).resolve().parent
        from ..platform.windows_subprocess import background_python_executable
        command = [
            background_python_executable(), "-m", "jarvis_foundry.runtime.worker_loop",
            "--once", "--home", str(home),
        ]
        if self.worker_loop_config is not None:
            command.extend(["--config", str(self.worker_loop_config)])
        try:
            self.workers.spawn(
                WORKER_LOOP_RUN_ID, command, cwd=home,
                worker_type="worker_loop",
                stop_path=self.estop.path,
            )
        except (LeaseUnavailable, OSError):
            return

    def doctor(self) -> dict[str, object]:
        """Inspect without migrating. Live homes are already at schema;
        ``initialize()`` here would take a write lock and could mutate
        the Supervisor's database.
        """
        if not Path(self.database.path).is_file():
            return {
                "database": str(self.database.path),
                "database_quick_check": "missing",
                "emergency_stop": self.estop.active(),
                "event_count": 0,
                "hermes_required_for_supervisor": False,
                "reconciliation": {"consistent": False, "reason": "database missing"},
                "active_workers": [],
            }
        return {
            "database": str(self.database.path),
            "database_quick_check": self.database.quick_check(readonly=True),
            "emergency_stop": self.estop.active(),
            "event_count": self.database.event_count(readonly=True),
            "heartbeat_count": self.database.event_count(
                readonly=True, event_type="supervisor.heartbeat"),
            "hermes_required_for_supervisor": False,
            "reconciliation": inspect_ledger(self.database).to_json(),
            "active_workers": self.workers.active_runs(readonly=True),
        }
