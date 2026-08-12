"""Step-5 regression tests: the exception-isolated, cycle-monitored
heartbeat loop (repair for FIND-2026-08-12-01 — a 21-minute silent
heartbeat-event stall on the live runtime)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.supervisor.run import (
    MAX_CONSECUTIVE_FAILURES,
    _heartbeat_loop,
)
from jarvis_foundry.supervisor.service import Supervisor


class FakeSupervisor:
    """Minimal stand-in: real Supervisor shape, scripted heartbeat."""

    def __init__(self, database: FoundryDatabase, instance_id: str,
                 script: list[object]) -> None:
        self.database = database
        self.instance_id = instance_id
        self.script = list(script)  # items: "RUNNING"/"FROZEN"/Exception/sleep-seconds
        self.heartbeat_calls = 0
        self.stop_calls = 0
        self.started = True

    def heartbeat(self) -> str:
        step = self.script[self.heartbeat_calls % len(self.script)]
        self.heartbeat_calls += 1
        if isinstance(step, Exception):
            raise step
        if isinstance(step, (int, float)):
            import time
            time.sleep(step)
            return "RUNNING"
        return step

    def stop(self) -> None:
        self.stop_calls += 1
        # Mirror the real Supervisor.stop(): the ledger records the stop.
        from jarvis_foundry.events import Event
        self.database.append(Event.create(
            "supervisor.stopped",
            {"instance_id": self.instance_id, "trust_state": "LAB"},
            source_type="supervisor", source_id=self.instance_id,
            idempotency_key=f"supervisor.stopped:{self.instance_id}"))


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _event_types(database: FoundryDatabase) -> list[str]:
    return [event.event_type for event in database.events()]


def _degraded_payloads(database: FoundryDatabase) -> list[dict]:
    return [event.payload for event in database.events()
            if event.event_type == "supervisor.degraded"]


def test_healthy_loop_emits_no_degraded_events(db: FoundryDatabase) -> None:
    supervisor = FakeSupervisor(db, "supervisor-test-1", ["RUNNING"])
    code = _heartbeat_loop(supervisor, interval=0.01, stop_after=3)
    assert code == 0
    assert supervisor.heartbeat_calls == 3
    assert _event_types(db) == []


def test_heartbeat_failure_survives_and_records_degraded(
        db: FoundryDatabase) -> None:
    supervisor = FakeSupervisor(
        db, "supervisor-test-2",
        [RuntimeError("boom"), "RUNNING", "RUNNING"])
    code = _heartbeat_loop(supervisor, interval=0.01, stop_after=3)
    assert code == 0  # the loop survives a failing cycle
    assert supervisor.heartbeat_calls == 3
    degraded = _degraded_payloads(db)
    assert len(degraded) == 1
    assert degraded[0]["reason"] == "heartbeat_failure"
    assert degraded[0]["details"]["error_class"] == "RuntimeError"
    assert degraded[0]["details"]["consecutive_failures"] == 1
    assert degraded[0]["instance_id"] == "supervisor-test-2"


def test_degraded_events_are_throttled(db: FoundryDatabase) -> None:
    # Every cycle fails; the degraded event must be throttled (one per
    # throttle window), not spammed.
    supervisor = FakeSupervisor(
        db, "supervisor-test-3", [RuntimeError("boom")])
    code = _heartbeat_loop(supervisor, interval=0.01, stop_after=4,
                           max_failures=10)
    assert code == 0
    assert len(_degraded_payloads(db)) == 1


def test_bounded_retry_stops_cleanly_after_max_failures(
        db: FoundryDatabase) -> None:
    supervisor = FakeSupervisor(
        db, "supervisor-test-4", [RuntimeError("boom")])
    code = _heartbeat_loop(supervisor, interval=0.01,
                           max_failures=MAX_CONSECUTIVE_FAILURES,
                           stop_after=MAX_CONSECUTIVE_FAILURES + 3)
    assert code == 1  # bounded: the daemon gives up and returns 1
    assert supervisor.stop_calls == 1  # clean stop (lease release + event)
    assert supervisor.heartbeat_calls == MAX_CONSECUTIVE_FAILURES
    assert "supervisor.stopped" in _event_types(db)


def test_recovery_resets_the_failure_counter(db: FoundryDatabase) -> None:
    # Fail, recover, fail twice more: no self-stop (not consecutive). The
    # counter reset is proven by the outcome (code 0, no stop) — the
    # degraded events themselves are throttle-limited to one per window.
    supervisor = FakeSupervisor(
        db, "supervisor-test-5",
        [RuntimeError("boom"), "RUNNING", RuntimeError("boom"),
         RuntimeError("boom")])
    code = _heartbeat_loop(supervisor, interval=0.01, stop_after=4,
                           max_failures=3)
    assert code == 0
    assert supervisor.stop_calls == 0
    assert len(_degraded_payloads(db)) == 1


def test_slow_cycle_records_degraded(db: FoundryDatabase) -> None:
    # A cycle taking longer than 3x the interval is a wedge signal.
    supervisor = FakeSupervisor(db, "supervisor-test-6", [0.05])
    code = _heartbeat_loop(supervisor, interval=0.01, stop_after=1)
    assert code == 0
    degraded = _degraded_payloads(db)
    assert len(degraded) == 1
    assert degraded[0]["reason"] == "slow_cycle"
    assert degraded[0]["details"]["cycle_seconds"] >= 0.03


def test_degraded_record_failure_never_kills_the_loop(
        db: FoundryDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    # If the ledger itself is the problem, the loop reports on stderr and
    # keeps going — the degraded record must not take the daemon down.
    supervisor = FakeSupervisor(
        db, "supervisor-test-7", [RuntimeError("boom"), "RUNNING"])
    monkeypatch.setattr(supervisor.database, "append",
                        lambda event: (_ for _ in ()).throw(
                            RuntimeError("ledger is wedged")))
    code = _heartbeat_loop(supervisor, interval=0.01, stop_after=2)
    assert code == 0
    assert supervisor.heartbeat_calls == 2
    assert supervisor.stop_calls == 0


def test_frozen_state_keeps_the_loop_alive(db: FoundryDatabase) -> None:
    supervisor = FakeSupervisor(db, "supervisor-test-8", ["FROZEN"])
    code = _heartbeat_loop(supervisor, interval=0.01, stop_after=2)
    assert code == 0
    assert supervisor.heartbeat_calls == 2
    assert supervisor.stop_calls == 0


def test_json_diagnostics_are_parseable(db: FoundryDatabase,
                                        capsys: pytest.CaptureFixture) -> None:
    supervisor = FakeSupervisor(db, "supervisor-test-9", [RuntimeError("x")])
    _heartbeat_loop(supervisor, interval=0.01, stop_after=1, max_failures=2)
    for line in capsys.readouterr().out.strip().splitlines():
        payload = json.loads(line)
        assert payload["supervisor"] == "HEARTBEAT_FAILURE"


def test_verifier_obs1_eventconflict_is_not_a_failure_label(
        db: FoundryDatabase, capsys: pytest.CaptureFixture) -> None:
    # Verifier OBS-1: a same-cause degraded event after the throttle window
    # hits EventConflict (payload differs on consecutive_failures). The
    # conflict means "already recorded" — the loop must not print
    # DEGRADED_RECORD_FAILED for it.
    from jarvis_foundry.events import Event, EventConflict
    from jarvis_foundry.supervisor.run import _record_degraded

    class Supervisor:
        instance_id = "supervisor-test-obs1"
        database = db

    # First record lands.
    assert _record_degraded(Supervisor(), "heartbeat_failure",
                            {"consecutive_failures": 1},
                            throttle=0.0, last_recorded_at=-1.0) is True
    # Same cause, different payload -> EventConflict -> absorbed as True.
    assert _record_degraded(Supervisor(), "heartbeat_failure",
                            {"consecutive_failures": 2},
                            throttle=0.0, last_recorded_at=-1.0) is True
    out = capsys.readouterr().out
    assert "DEGRADED_RECORD_FAILED" not in out
    assert len([e for e in db.events()
                if e.event_type == "supervisor.degraded"]) == 1


def test_verifier_obs3_stop_failure_never_masks_the_decision(
        db: FoundryDatabase, capsys: pytest.CaptureFixture) -> None:
    # Verifier OBS-3: a raising stop() path must not escape the loop — the
    # bounded-retry decision (exit 1) is reported and preserved.
    supervisor = FakeSupervisor(db, "supervisor-test-obs3", [RuntimeError("x")])

    def failing_stop() -> None:
        raise RuntimeError("stop path is wedged")
    supervisor.stop = failing_stop  # type: ignore[method-assign]

    code = _heartbeat_loop(supervisor, interval=0.01, max_failures=2,
                           stop_after=2)
    assert code == 1
    out = capsys.readouterr().out
    assert '"supervisor": "STOP_FAILED"' in out
    assert '"supervisor": "STOPPED_AFTER_FAILURES"' in out
