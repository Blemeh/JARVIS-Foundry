from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.errors import BackpressureError, DuplicateLaunchError
from jarvis_foundry.kernel.events import EventKindRegistry
from jarvis_foundry.kernel.ids import DeterministicIdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.supervisor import SupervisorService


def supervisor_registry() -> EventKindRegistry:
    return (
        EventKindRegistry()
        .register("supervisor.started", required_keys=("instance_id",))
        .register("supervisor.heartbeat", required_keys=("instance_id",))
        .register("supervisor.lease", required_keys=("worker_id", "instance_id", "status"))
    )


def fresh_supervisor(tmp_path: Path, max_running: int = 2) -> SupervisorService:
    ledger = Ledger(tmp_path / "kernel.db", registry=supervisor_registry())
    return SupervisorService(
        store_path=tmp_path / "supervisor.db",
        ledger=ledger,
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
        max_running=max_running,
        lease_ttl_seconds=30,
    )


def test_start_heartbeat_and_durable_lifecycle(tmp_path: Path) -> None:
    service = fresh_supervisor(tmp_path)
    state = service.start(instance_id="instance-1")
    assert state.status == "RUNNING"
    assert service.heartbeat("instance-1").status == "RUNNING"
    restarted = fresh_supervisor(tmp_path)
    assert restarted.status("instance-1").status == "RUNNING"


def test_duplicate_launch_refused_by_lease(tmp_path: Path) -> None:
    service = fresh_supervisor(tmp_path)
    service.acquire_lease("worker-a", "instance-1")
    with pytest.raises(DuplicateLaunchError):
        service.acquire_lease("worker-a", "instance-2")


def test_lease_expiry_recovery_allows_relaunch(tmp_path: Path) -> None:
    service = fresh_supervisor(tmp_path)
    service.acquire_lease("worker-a", "instance-1")
    recovered = service.recover(at="2026-08-15T00:01:00Z")
    assert list(recovered.reclaimed_leases) == ["worker-a"]
    relaunched = service.acquire_lease("worker-a", "instance-2")
    assert relaunched.status == "ACTIVE"


def test_backpressure_returns_honest_waiting(tmp_path: Path) -> None:
    service = fresh_supervisor(tmp_path, max_running=1)
    service.acquire_lease("worker-a", "instance-1")
    waiting = service.acquire_lease("worker-b", "instance-1", allow_wait=True)
    assert waiting.status == "WAITING"
    assert waiting.reason == "BACKPRESSURE"
    with pytest.raises(BackpressureError):
        service.acquire_lease("worker-b", "instance-1")


def test_waiting_advances_after_lease_release(tmp_path: Path) -> None:
    service = fresh_supervisor(tmp_path, max_running=1)
    service.acquire_lease("worker-a", "instance-1")
    service.acquire_lease("worker-b", "instance-1", allow_wait=True)
    service.release_lease("worker-a", "instance-1")
    advanced = service.advance_waiting(at="2026-08-15T00:00:10Z")
    assert list(advanced.promoted) == ["worker-b"]
    assert service.lease("worker-b").status == "ACTIVE"


def test_restart_recovers_stale_leases_and_state(tmp_path: Path) -> None:
    service = fresh_supervisor(tmp_path)
    service.acquire_lease("worker-a", "instance-1")
    restarted = fresh_supervisor(tmp_path)
    recovered = restarted.recover(at="2026-08-15T00:02:00Z")
    assert list(recovered.reclaimed_leases) == ["worker-a"]
