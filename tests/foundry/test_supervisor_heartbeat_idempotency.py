"""Supervisor heartbeat idempotency must never conflict with itself.

Observed live defect (2026-09-14): the Supervisor wrote supervisor.degraded
heartbeat_failure carrying EventConflict for its OWN heartbeat key, five times
in a row, then stopped itself cleanly. The canonical trust-window evaluator
therefore excluded the whole UTC day as degraded (events #241076, #241079,
#241081, #241086).

_heartbeat_idempotency_key() coalesces a non-reap heartbeat onto the key of the
last durable heartbeat when the elapsed time is below ledger_heartbeat_seconds.
When that last heartbeat was a REAP heartbeat, the reused key still carries the
reap token (for example worker-loop) while the new payload omits
reaped_workers. events.append() then sees an identical key with a different
payload_hash and raises EventConflict: a self-inflicted failure that can
silently cost a proving-window day.
"""
from __future__ import annotations

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, EventConflict
from jarvis_foundry.supervisor.service import Supervisor


def _supervisor(tmp_path) -> Supervisor:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    supervisor = Supervisor(
        database, tmp_path / "EMERGENCY_STOP",
        instance_id="heartbeat-key-test",
        ledger_heartbeat_seconds=60,
    )
    # Live Supervisor generations start at 1 (Supervisor.start() bumps the
    # generation before the first heartbeat). The coalescing anchor compares
    # against this value, and generation 0 is falsy in that comparison, so a
    # test that left it at 0 would silently skip the anchor path entirely.
    supervisor._start_generation = 1
    return supervisor


def _payload(supervisor: Supervisor, *, reaped=None) -> dict:
    payload = {
        "instance_id": supervisor.instance_id,
        "generation": supervisor._start_generation,
        "pid": 0,
        "trust_state": "SUSPENDED",
        "release_id": "test",
    }
    if reaped:
        payload["reaped_workers"] = list(reaped)
    return payload


def _append(supervisor: Supervisor, key: str, payload: dict):
    return supervisor.database.append(Event.create(
        "supervisor.heartbeat", payload,
        source_type="supervisor", source_id=supervisor.instance_id,
        idempotency_key=key,
    ))


def test_reap_heartbeat_does_not_break_the_next_heartbeat(tmp_path):
    """A non-reap heartbeat after a reap heartbeat must persist, not self-conflict."""
    supervisor = _supervisor(tmp_path)

    first = supervisor._heartbeat_idempotency_key(None)
    _append(supervisor, first, _payload(supervisor))

    reap_key = supervisor._heartbeat_idempotency_key(["worker-loop"])
    assert reap_key != first, "a reap heartbeat must stay distinct from a plain one"
    _append(supervisor, reap_key, _payload(supervisor, reaped=["worker-loop"]))

    next_key = supervisor._heartbeat_idempotency_key(None)
    try:
        _append(supervisor, next_key, _payload(supervisor))
    except EventConflict as error:
        pytest.fail(
            "a heartbeat conflicted with the Supervisor own earlier heartbeat "
            + repr(next_key) + ": " + str(error))


def test_repeated_identical_heartbeat_still_coalesces(tmp_path):
    """Idempotent re-delivery of an unchanged heartbeat must remain a no-op."""
    supervisor = _supervisor(tmp_path)
    first = supervisor._heartbeat_idempotency_key(None)
    payload = _payload(supervisor)
    _sequence, created = _append(supervisor, first, payload)
    assert created is True

    same = supervisor._heartbeat_idempotency_key(None)
    assert same == first, "an unchanged heartbeat inside the window must coalesce"
    _sequence, created_again = _append(supervisor, same, payload)
    assert created_again is False, "an identical heartbeat must dedupe"


def test_reap_then_plain_keys_are_never_equal(tmp_path):
    """The key builder must not hand out the reap key for a plain heartbeat."""
    supervisor = _supervisor(tmp_path)
    reap_key = supervisor._heartbeat_idempotency_key(["worker-loop"])
    _append(supervisor, reap_key, _payload(supervisor, reaped=["worker-loop"]))
    plain_key = supervisor._heartbeat_idempotency_key(None)
    assert plain_key != reap_key, (
        "a plain heartbeat must not reuse the reap key: the payloads differ "
        "by reaped_workers, so reusing it raises EventConflict")
