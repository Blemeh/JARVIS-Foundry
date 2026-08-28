from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from ..clock import parse, utc_now
from ..database import FoundryDatabase
from ..events import Event
from ..deployment.release import release_authority_report
from ..reconcile import inspect as inspect_ledger
from ..reconcile import reconcile
from ..runtime.authority import ALLOWED_TRUST_STATES
from ..runtime.provider_config import ProviderConfigError, ProviderSettings
from .control import (
    RuntimeControlRequest,
    current_process_identity,
    latest_request,
    latest_process_identity,
    latest_generation,
    latest_resolution,
    record_claim,
    record_lease_reclaimed,
    record_resolution,
    inspect_control,
    request_is_stale,
)
from .estop import EmergencyStop
from .leases import LeaseManager, LeaseUnavailable
from .worker import WorkerController, pid_alive, process_creation_identity

if TYPE_CHECKING:
    from ..resilience import ResilienceCoordinator

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
        resilience: ResilienceCoordinator | None = None,
        command_center_port: int | None = None,
        owner_secret_path: str | Path | None = None,
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
        self.resilience = resilience
        self._start_generation = 0
        self.command_center_port = command_center_port
        self.owner_secret_path = (
            Path(owner_secret_path).resolve()
            if owner_secret_path is not None else
            Path(self.database.path).resolve().parent / "owner-control.key")
        self._command_center = None

    def _release_id(self) -> str | None:
        """Return only an explicitly supplied release identity.

        A missing identity is reported as ``None`` and never replaced with a
        guessed source or package value.
        """

        value = (
            os.environ.get("JARVIS_RELEASE_SHA")
            or os.environ.get("JARVIS_RELEASE_ID")
        )
        return value.strip() if value and value.strip() else None

    def _record_resolution(
        self,
        request: RuntimeControlRequest | None,
        *,
        status: str,
        phase: str,
        reason: str | None = None,
        attempt: int = 0,
        request_status: str | None = None,
    ) -> None:
        if request is None:
            return
        pid, identity = current_process_identity()
        record_resolution(
            self.database,
            request,
            status=status,
            phase=phase,
            reason=reason,
            instance_id=self.instance_id,
            pid=pid,
            creation_identity=identity,
            release_id=self._release_id(),
            attempt=attempt,
            request_status=request_status,
        )

    def _reclaim_stale_supervisor_lease(self) -> None:
        """Release only a lease whose recorded process identity is dead."""

        connection = self.database.connect(readonly=True)
        try:
            row = connection.execute(
                "SELECT owner_id FROM leases WHERE lease_key = ?",
                (self.LEASE_KEY,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return
        owner_id = str(row["owner_id"])
        identity_payload = latest_process_identity(self.database, owner_id)
        if identity_payload is None:
            return
        try:
            pid = int(identity_payload["pid"])
        except (KeyError, TypeError, ValueError):
            return
        if pid <= 0 or pid_alive(pid):
            expected = identity_payload.get("creation_identity")
            if not isinstance(expected, dict) or expected.get("create_time") is None:
                return
            observed = process_creation_identity(pid)
            if observed.get("create_time") == expected.get("create_time"):
                return
            reason = "supervisor PID creation identity was reused"
        else:
            reason = "supervisor process is no longer alive"
        if self.leases.release(self.LEASE_KEY, owner_id):
            record_lease_reclaimed(
                self.database, owner_id=owner_id, pid=pid, reason=reason)

    def start(self) -> str:
        if self.started:
            return "FROZEN" if self.estop.active() else "RUNNING"
        self.database.initialize()
        self._reclaim_stale_supervisor_lease()
        request = latest_request(self.database)
        if request is not None and request_is_stale(request):
            if latest_resolution(self.database, request.request_id) is not None:
                # An earlier attempt already resolved this expired request.
                # It is consumed: treating it as still-latest would wedge
                # every future startup outside the lease path (live incident
                # 2026-08-24: one expired START request permanently blocked
                # supervisor recovery with UNKNOWN exits).
                request = None
            else:
                state = inspect_control(self.database, stop_path=str(self.estop.path))
                stale_status = state.status if state.status in {
                    "STOPPED", "RUNNING", "BLOCKED", "DEGRADED", "UNKNOWN"
                } else "UNKNOWN"
                if self.estop.active():
                    stale_status = "BLOCKED"
                self._record_resolution(
                    request,
                    status=stale_status,
                    phase="stale_request",
                    request_status="STALE",
                    reason="owner request expired before application",
                )
                return stale_status
        if request is not None and request.action == "STOP":
            # A duplicate scheduler trigger must not record STOPPED while a
            # different live Supervisor still owns the singleton lease. The
            # owner heartbeat will consume the durable request.
            connection = self.database.connect(readonly=True)
            try:
                lease = connection.execute(
                    "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?",
                    (self.LEASE_KEY,),
                ).fetchone()
            finally:
                connection.close()
            live_lease = False
            if lease is not None:
                try:
                    live_lease = parse(str(lease["expires_at"])) > utc_now()
                except (TypeError, ValueError, OverflowError):
                    live_lease = True
            if live_lease:
                # Continue through the normal acquire path, which fails
                # closed with LeaseUnavailable and leaves the request pending.
                request = None
        if request is not None and request.action == "STOP":
            if self.estop.active():
                self._record_resolution(
                    request, status="BLOCKED", phase="blocked_at_start",
                    reason="emergency STOP sentinel is active")
                return "BLOCKED"
            self._record_resolution(
                request, status="STOPPED", phase="already_stopped",
                reason="start request observed after an owner STOP")
            return "STOPPED"
        report = reconcile(self.database)
        if not report.consistent:
            self._record_resolution(
                request, status="DEGRADED", phase="reconciliation_failed",
                reason="ledger reconciliation failed at startup")
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
        reaped_workers = self.workers.reap()
        self._start_generation = max(
            self._start_generation + 1,
            latest_generation(self.database, self.instance_id) + 1,
        )
        connection = self.database.connect(readonly=True)
        try:
            lease = connection.execute(
                "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?",
                (self.LEASE_KEY,),
            ).fetchone()
        finally:
            connection.close()
        if lease is not None and str(lease["owner_id"]) == self.instance_id:
            try:
                live = parse(str(lease["expires_at"])) > utc_now()
            except (TypeError, ValueError, OverflowError):
                live = True
            if live:
                raise LeaseUnavailable(
                    f"lease {self.LEASE_KEY} is already held by instance {self.instance_id}")
        pid, identity = current_process_identity()
        record_claim(
            self.database,
            instance_id=self.instance_id,
            pid=pid,
            creation_identity=identity,
            request_id=request.request_id if request else None,
            release_id=self._release_id(),
            generation=self._start_generation,
        )
        self.leases.acquire(self.LEASE_KEY, self.instance_id, self.lease_seconds)
        stop = self.estop.read()
        event_type = "emergency_stop.frozen" if stop else "supervisor.started"
        payload = {
            "instance_id": self.instance_id,
            # Startup is not a trust promotion.  Preserve the previously
            # certified/demoted state across a restart; an active STOP still
            # forces SUSPENDED until an explicit owner-authorized transition.
            "trust_state": "SUSPENDED" if stop else self._preserved_trust(),
            "stop_id": stop.get("stop_id") if stop else None,
            "reaped_workers": reaped_workers,
            "pid": pid,
            "creation_identity": identity,
            "release_id": self._release_id(),
            "control_request_id": request.request_id if request else None,
            "generation": self._start_generation,
        }
        self.database.append(Event.create(
            event_type, payload, source_type="supervisor", source_id=self.instance_id,
            idempotency_key=(
                f"{event_type}:{self.instance_id}:"
                f"{payload.get('stop_id') or 'start'}:{self._start_generation}"),
        ))
        self.started = True
        if self.command_center_port is not None:
            try:
                self._start_command_center()
            except Exception:
                # Command Center ownership is part of the requested production
                # service.  A bind/auth failure must not leave a seemingly
                # healthy Supervisor holding the singleton lease.
                self.stop()
                raise
        if request is not None and request.action in {"START", "RESTART"}:
            self._record_resolution(request, status="RUNNING", phase="started")
        elif request is not None and request.action == "STOP":
            self._record_resolution(
                request, status="BLOCKED", phase="blocked_at_start",
                reason="owner STOP request arrived during startup")
        return "FROZEN" if stop else "RUNNING"

    def heartbeat(self, *, resilience_metrics: Mapping[str, Any] | None = None) -> str:
        if not self.started:
            raise RuntimeError("supervisor has not started")
        try:
            self.leases.renew(self.LEASE_KEY, self.instance_id, self.lease_seconds)
        except LeaseUnavailable:
            # A heartbeat may never reacquire a lease that another owner has
            # taken. Preserve an explicit degraded record and let the bounded
            # headless retry path decide when to stop this process.
            self.database.append(Event.create(
                "supervisor.degraded",
                {
                    "instance_id": self.instance_id,
                    "trust_state": "LAB",
                    "reason": "supervisor lease lost during heartbeat",
                    "generation": self._start_generation,
                },
                source_type="supervisor", source_id=self.instance_id,
                idempotency_key=(
                    f"supervisor.degraded:{self.instance_id}:lease_lost:{self._start_generation}"),
            ))
            raise
        reaped = self.workers.reap()
        request = latest_request(self.database)
        if request is not None and request.action in {"STOP", "RESTART"}:
            # A RESTART request is intentionally durable across the stopped
            # process boundary.  Once the replacement instance has recorded
            # its own ``started`` resolution, that instance has adopted the
            # request; re-applying it on the first heartbeat would make the
            # repeating scheduled trigger stop every replacement forever.
            resolution = latest_resolution(self.database, request.request_id)
            adopted_restart = (
                request.action == "RESTART"
                and resolution is not None
                and resolution.payload.get("status") == "RUNNING"
                and resolution.payload.get("phase") in {"started", "recovered"}
                and resolution.payload.get("instance_id") == self.instance_id
            )
            if adopted_restart:
                request = None
        if request is not None and request.action in {"STOP", "RESTART"}:
            if request_is_stale(request):
                self._record_resolution(
                    request,
                    status="RUNNING",
                    phase="stale_request",
                    request_status="STALE",
                    reason="owner request expired before application",
                )
            elif self.estop.active():
                self._record_resolution(
                    request, status="BLOCKED", phase="blocked_during_stop",
                    reason="emergency STOP sentinel is active")
            else:
                self.stop(control_request=request)
                return "STOPPED"
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
            "pid": os.getpid(),
            "creation_identity": process_creation_identity(os.getpid()),
            "release_id": self._release_id(),
            "generation": self._start_generation,
        }
        if reaped:
            payload["reaped_workers"] = reaped
        key = self._heartbeat_idempotency_key(reaped)
        self.database.append(Event.create(
            "supervisor.heartbeat", payload,
            source_type="supervisor", source_id=self.instance_id,
            idempotency_key=key,
        ))
        self.resilience_tick(metrics=resilience_metrics)
        return "RUNNING"

    def resilience_tick(self, *, metrics: Mapping[str, Any] | None = None) -> Any:
        """Run the bounded resilience coordinator without widening heartbeat failure.

        Scoped anomalies are owned by the coordinator and remain incident
        events; only a coordinator integration failure is recorded as a
        Supervisor degradation. The emergency STOP branch returns before this
        hook is reached, and the coordinator itself rechecks STOP before any
        repair callback.
        """
        if self.resilience is None or self.estop.active():
            return None
        try:
            return self.resilience.run_once(metrics=metrics)
        except Exception as error:  # noqa: BLE001 — resilience must not kill heartbeat
            try:
                self.database.append(Event.create(
                    "supervisor.degraded",
                    {
                        "instance_id": self.instance_id,
                        "trust_state": "LAB",
                        "reason": "resilience_tick_failure",
                        "error_class": type(error).__name__,
                        "error": str(error)[:300],
                    },
                    source_type="supervisor", source_id=self.instance_id,
                    idempotency_key=(
                        f"supervisor.degraded:{self.instance_id}:resilience_tick_failure"),
                ))
            except Exception:  # noqa: BLE001 — the heartbeat remains bounded
                pass
            return None

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
                if trust == "PROVEN":
                    trust = "PROVING"
        except Exception:  # noqa: BLE001 — fail closed to LAB
            trust = "LAB"
        return trust

    def _heartbeat_idempotency_key(self, reaped: list | tuple | None) -> str:
        """Coalesce non-material heartbeats; keep reap events distinct."""
        if self.ledger_heartbeat_seconds <= 0:
            return str(uuid.uuid4())
        bucket = int(utc_now().timestamp() // self.ledger_heartbeat_seconds)
        reap_token = ",".join(str(item) for item in (reaped or ()))
        return (
            f"supervisor.heartbeat:{self.instance_id}:{self._start_generation}:"
            f"{bucket}:{reap_token or 'ok'}")

    def stop(self, *, control_request: RuntimeControlRequest | None = None) -> None:
        if not self.started:
            if control_request is not None and control_request.action == "STOP":
                self._record_resolution(
                    control_request, status="STOPPED", phase="already_stopped",
                    reason="supervisor was already stopped")
            return
        if self._command_center is not None:
            self._command_center.close()
            self._command_center = None
        self.workers.terminate_all()
        pid, identity = current_process_identity()
        self.database.append(Event.create(
            "supervisor.stopped",
            {
                "instance_id": self.instance_id,
                "trust_state": self._preserved_trust(),
                "pid": pid,
                "creation_identity": identity,
                "release_id": self._release_id(),
                "control_request_id": control_request.request_id if control_request else None,
                "generation": self._start_generation,
            },
            source_type="supervisor", source_id=self.instance_id,
            idempotency_key=(
                f"supervisor.stopped:{self.instance_id}:{self._start_generation}"),
        ))
        self.leases.release(self.LEASE_KEY, self.instance_id)
        self.started = False
        if control_request is not None and control_request.action in {"STOP", "RESTART"}:
            self._record_resolution(
                control_request,
                status="STOPPED",
                phase="stopped_for_restart" if control_request.action == "RESTART" else "stopped",
                reason="owner control request applied",
            )

    def _start_command_center(self) -> None:
        if self._command_center is not None:
            return
        from ..command_center import CommandCenterServer
        from ..owner_controls import load_or_create_owner_secret

        secret = load_or_create_owner_secret(self.owner_secret_path)
        discovery_cycle_enabled = None
        static_root = None
        release_id = self._release_id()
        if release_id:
            from ..deployment.release import release_dir
            candidate_static_root = release_dir(release_id) / "command-center"
            if (candidate_static_root / "index.html").is_file():
                static_root = candidate_static_root
        if self.worker_loop_config is not None:
            try:
                from ..runtime.config import WorkerRuntimeConfig
                discovery_cycle_enabled = WorkerRuntimeConfig.from_yaml(
                    self.worker_loop_config,
                    provider_settings=self._provider_settings,
                ).discovery_cycle_enabled
            except Exception:  # noqa: BLE001 - projection stays honest/unregistered
                # The worker-loop activation path records the durable fault.
                # Command Center remains available and reports UNREGISTERED.
                pass
        server = CommandCenterServer(
            self.database,
            stop_path=self.estop.path,
            host="127.0.0.1",
            port=int(self.command_center_port or 0),
            discovery_cycle_enabled=discovery_cycle_enabled,
            static_root=static_root,
            runtime_home=Path(self.database.path).resolve().parent,
            source_repo=os.environ.get("JARVIS_SOURCE_REPO"),
        )
        server.configure_owner(secret)
        server.start()
        self._command_center = server

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
            # A live one-shot worker-loop process legitimately has tiny gaps
            # between a completed builder lease, VERIFYING, and acquisition of
            # the fresh verifier lease.  Treating that in-process handoff as a
            # crash races the child and corrupts the state machine.  Once the
            # owned child exits (normally or by crash), the next heartbeat
            # performs the same durable recovery immediately.
            worker_loop = self.workers.status(WORKER_LOOP_RUN_ID)
            if not worker_loop.alive:
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
        except Exception as error:  # noqa: BLE001 - persist all activation faults
            self._record_worker_loop_activation_blocked(
                phase="configuration", error=error)
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
        except (LeaseUnavailable, OSError) as error:
            self._record_worker_loop_activation_blocked(
                phase="launch", error=error)
            return

    def _record_worker_loop_activation_blocked(
        self, *, phase: str, error: BaseException,
    ) -> None:
        """Persist loss of autonomous dispatch instead of hiding it."""
        try:
            self.database.append(Event.create(
                "worker_loop.activation_blocked",
                {
                    "instance_id": self.instance_id,
                    "phase": phase,
                    "error_class": type(error).__name__,
                    "error": str(error)[:300],
                    "trust_state": "UNKNOWN",
                },
                source_type="supervisor",
                source_id=self.instance_id,
                idempotency_key=(
                    f"worker_loop.activation_blocked:{self.instance_id}:{phase}"
                ),
            ))
        except Exception:  # noqa: BLE001 - heartbeat must remain alive
            pass

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
                "runtime_control": {
                    "status": "UNKNOWN",
                    "reason": "database missing",
                },
                "health": {
                    "status": "UNKNOWN",
                    "reason": "database missing",
                },
                "release_authority": release_authority_report(
                    foundry_home=Path(self.database.path).parent),
            }
        from ..diagnostics.runtime import diagnostic_snapshot, derive_health
        release_authority = release_authority_report(
            foundry_home=Path(self.database.path).parent)
        reconciliation = inspect_ledger(self.database).to_json()
        diagnostic = diagnostic_snapshot(
            self.database, stop_path=str(self.estop.path))
        runtime_control = diagnostic["runtime_control"]
        health = derive_health(
            runtime_status=str(runtime_control.get("status") or "UNKNOWN"),
            reconciliation_consistent=bool(reconciliation.get("consistent")),
            release_status=("VALID" if release_authority.get("status") == "CONSISTENT"
                            else str(release_authority.get("status") or "UNKNOWN")),
            stop_active=self.estop.active(),
        )
        return {
            "database": str(self.database.path),
            "database_quick_check": self.database.quick_check(readonly=True),
            "emergency_stop": self.estop.active(),
            "event_count": self.database.event_count(readonly=True),
            "heartbeat_count": self.database.event_count(
                readonly=True, event_type="supervisor.heartbeat"),
            "hermes_required_for_supervisor": False,
            "reconciliation": reconciliation,
            "active_workers": self.workers.active_runs(readonly=True),
            "runtime_control": runtime_control,
            "health": health,
            "diagnostic": diagnostic,
            "release_authority": release_authority,
        }
