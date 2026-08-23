"""V5-S6: external-effect execution, idempotency, and reconciliation.

Retries never duplicate effects; the journal is monotonic
(STARTED -> COMPLETED/FAILED/FROZEN, never regresses); crashes between
start and completion are reaped deterministically (never a second
execution); ambiguous effects freeze with an append-only compensation
path; provider reconciliation is honest (NOT_TESTED without a real
provider); financial consequences are recorded.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from jarvis_foundry.connectors.contract import ConnectorSpec
from jarvis_foundry.connectors.registry import register_connector
from jarvis_foundry.control.broker import (PolicyBroker, record_decision)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionEnvelope, BrokerDecision
from jarvis_foundry.effects import (
    EffectStateError,
    compensate_effect,
    effect_identity,
    effect_state,
    execute_effect,
    freeze_effect,
    reconcile_effect,
)
from jarvis_foundry.events import Event


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _register_system(db: FoundryDatabase, system_id: str = "sys-browser") -> None:
    db.append(Event.create(
        "system.registered",
        {"system_id": system_id, "kind": "browser",
         "capabilities": {"write": "READY"}, "name": "Browser",
         "repository": None, "folder": None},
        source_type="supervisor", source_id="v5-test",
        idempotency_key=f"system.registered:{system_id}"))


def _register_connector(db: FoundryDatabase) -> None:
    register_connector(db, ConnectorSpec(
        connector_id="conn-fs", kind="filesystem", version="1.0.0",
        actions={"file.write": "write"},
        capabilities={"write": "READY"},
        retry_policy={"max_attempts": 3, "backoff_s": [1]}))


def _envelope(**overrides: object) -> ActionEnvelope:
    values: dict[str, object] = {
        "action_id": "act-s6-1",
        "mission_id": "mission-1",
        "resource_id": "res-1",
        "action_kind": "file.write",
        "target": None,
        "capability": "write",
        "expected_effects": ("create notes.txt",),
        "risk_class": "C1",
        "verification_required": True,
        "rollback": {"method": "remove"},
        "step_id": "step-1",
        "requested_by_agent_run_id": "run-1",
        "target_resources": ("sys-browser",),
        "capabilities_required": ("write",),
        "goal_alignment": "mission-1",
        "active_owner_vetoes_checked": (),
        "expected_effect": "create notes.txt",
        "reversibility": "reversible",
        "before_state_capture": {"ref": "snap-1", "hash": "h-before"},
        "verification_plan": {"kind": "deterministic"},
        "rollback_plan": {"strategy": "freeze",
                          "steps": ("record", "freeze for review")},
        "idempotency_key": "act-s6-1-key",
        "budget_estimate": {},
        "credential_requirements": (),
        "created_at": "2026-08-13T00:00:00Z",
    }
    values.update(overrides)
    return ActionEnvelope(**values)


def _allow(db: FoundryDatabase, *, action_id: str = "act-s6-1",
           **overrides: object) -> ActionEnvelope:
    envelope = _envelope(action_id=action_id, **overrides)
    outcome = PolicyBroker(db).decide(envelope)
    assert outcome.decision == BrokerDecision.ALLOW, outcome.reason
    record_decision(db, envelope, outcome)  # the ALLOW ticket
    return envelope


class _FakeStore:
    """Deterministic perform target: tracks writes; can fail or time
    out on demand; the after-state is its content hash."""

    def __init__(self) -> None:
        self.content: dict[str, str] = {}
        self.fail_with: BaseException | None = None
        self.calls = 0

    def perform(self, ref: str, payload: str) -> str:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.content[ref] = payload
        return f"hash-after:{ref}:{len(payload)}"


def _run(db: FoundryDatabase, store: _FakeStore, envelope: ActionEnvelope,
         *, connector_id: str = "conn-fs") -> dict[str, object]:
    return execute_effect(
        db, envelope=envelope, connector_id=connector_id,
        perform=store.perform)


# ---------------------------------------------------------------------------
# execution + idempotency
# ---------------------------------------------------------------------------

def test_execute_effect_started_then_completed(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    result = _run(db, store, envelope)
    assert result["outcome"] == "COMPLETED"
    assert store.calls == 1
    state = effect_state(db, result["action_key"])
    assert state["status"] == "COMPLETED"
    assert state["before_state"] == {"ref": "snap-1", "hash": "h-before"}
    assert state["after_state"].startswith("hash-after:")


def test_retry_never_duplicates_effect(db: FoundryDatabase) -> None:
    """The exit-evidence gate: a retried delivery of the SAME effect
    executes exactly once."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    first = _run(db, store, envelope)
    second = _run(db, store, envelope)  # identical re-delivery
    assert store.calls == 1  # performed once
    assert first["action_key"] == second["action_key"]
    assert second["outcome"] == "COMPLETED"
    assert second["performed"] is False  # idempotent retry, no re-run
    with db.connect() as connection:
        started = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'system.action_started'").fetchone()[0]
        completed = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'system.action_completed'").fetchone()[0]
    assert (started, completed) == (1, 1)


def test_two_workers_claim_same_effect_at_most_once(
        db: FoundryDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent deliveries must not both cross the external boundary."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    barrier = threading.Barrier(2)
    original_effect_state = effect_state

    def gated_effect_state(database: FoundryDatabase,
                           action_key: str) -> dict[str, object]:
        state = original_effect_state(database, action_key)
        if state["status"] == "UNKNOWN":
            barrier.wait(timeout=10)
        return state

    monkeypatch.setattr("jarvis_foundry.effects.effect_state",
                        gated_effect_state)
    results: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(_run(db, store, envelope))
        except BaseException as exc:  # pragma: no cover - assertion below
            errors.append(exc)

    workers = [threading.Thread(target=worker) for _ in range(2)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(timeout=15)

    assert not errors
    assert len(results) == 2
    assert store.calls == 1
    assert {result["outcome"] for result in results} <= {
        "COMPLETED", "UNKNOWN"}


def test_active_claim_is_not_reaped_by_competing_worker(
        db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db, action_id="act-active-claim")
    store = _FakeStore()
    key, system_id, target_ref, payload_hash = effect_identity(db, envelope)
    now = time.time()
    db.append(Event.create(
        "system.action_started",
        {"action_key": key, "system_id": system_id,
         "target_ref": target_ref, "payload_hash": payload_hash,
         "kind": envelope.action_kind, "connector_id": "conn-fs",
         "before_state_capture": envelope.before_state_capture,
         "expected_effects": list(envelope.expected_effects),
         "action_id": envelope.action_id, "claim_token": "worker-a",
         "claimed_at": now, "claim_expires_at": now + 60},
        source_type="supervisor", source_id="v5-effect-driver",
        idempotency_key=f"{key}.started"))

    result = _run(db, store, envelope)

    assert result["outcome"] == "UNKNOWN"
    assert store.calls == 0
    assert effect_state(db, key)["status"] == "STARTED"


def test_conflicting_payload_is_a_different_effect(db: FoundryDatabase) -> None:
    """Same system+target with a DIFFERENT payload -> different key:
    both effects execute independently."""
    _register_system(db)
    _register_connector(db)
    store = _FakeStore()
    envelope_a = _allow(db, action_id="act-a",
                        expected_effect="create notes.txt")
    envelope_b = _allow(db, action_id="act-b",
                        expected_effect="create other.txt")
    first = _run(db, store, envelope_a)
    second = _run(db, store, envelope_b)
    assert first["action_key"] != second["action_key"]
    assert store.calls == 2


def test_effect_requires_allow_ticket(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _envelope()  # never decided -> no ALLOW ticket
    store = _FakeStore()
    with pytest.raises(PermissionError):
        _run(db, store, envelope)
    assert store.calls == 0


def test_budget_exceeded_never_executes(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _envelope(budget_estimate={"USD": 15})
    outcome = PolicyBroker(db).decide(envelope, budget={"USD": 10})
    assert outcome.decision == BrokerDecision.BUDGET_EXCEEDED
    store = _FakeStore()
    with pytest.raises(PermissionError):
        _run(db, store, envelope)
    assert store.calls == 0


# ---------------------------------------------------------------------------
# crash window + monotonicity
# ---------------------------------------------------------------------------

def test_crash_between_start_and_completion_reaped_once(
        db: FoundryDatabase) -> None:
    """A STARTED effect with no terminal event is reaped deterministically:
    NEVER re-executed (a second attempt would duplicate the effect)."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    # simulate the crash: record STARTED only (the S1 journal API)
    from jarvis_foundry.systems import record_external_action
    key, system_id, target_ref, effect_hash = effect_identity(db, envelope)
    record_external_action(db, system_id, target_ref, effect_hash,
                           kind="file.write", started=True)
    result = _run(db, store, envelope)  # the driver reaps the crash
    assert store.calls == 0  # no second execution, no duplicate effect
    assert result["outcome"] == "FROZEN"
    assert result["reaped"] is True
    state = effect_state(db, result["action_key"])
    assert state["status"] == "FROZEN"
    # a third attempt is an idempotent no-op: the recorded FROZEN
    # outcome returns, the effect is NEVER re-performed
    third = _run(db, store, envelope)
    assert third["outcome"] == "FROZEN"
    assert third["performed"] is False
    assert store.calls == 0


def test_terminal_state_never_regresses(db: FoundryDatabase) -> None:
    """Monotonic journal: a late FAILED event cannot un-complete a
    completed effect, and a late COMPLETED cannot un-fail a failed one."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    key = _run(db, store, envelope)["action_key"]
    _, _, _, effect_hash = effect_identity(db, envelope)
    # late failed event (crash report arriving after completion)
    db.append(Event.create(
        "system.action_failed",
        {"action_key": key, "system_id": "sys-browser",
         "target_ref": "sys-browser", "payload_hash": effect_hash,
         "kind": "file.write"},
        source_type="connector", source_id="conn-fs",
        idempotency_key=f"{key}.failed.late"))
    assert effect_state(db, key)["status"] == "COMPLETED"
    # and a FAILED effect stays failed on a late completed event
    store.fail_with = RuntimeError("boom")
    envelope_fail = _allow(db, action_id="act-fail",
                           expected_effect="create fail.txt")
    key_fail = _run(db, store, envelope_fail)["action_key"]
    _, _, _, fail_hash = effect_identity(db, envelope_fail)
    assert effect_state(db, key_fail)["status"] == "FAILED"
    db.append(Event.create(
        "system.action_completed",
        {"action_key": key_fail, "system_id": "sys-browser",
         "target_ref": "sys-browser",
         "payload_hash": fail_hash,
         "kind": "file.write"},
        source_type="connector", source_id="conn-fs",
        idempotency_key=f"{key_fail}.completed.late"))
    assert effect_state(db, key_fail)["status"] == "FAILED"


# ---------------------------------------------------------------------------
# failure, timeout, partial success
# ---------------------------------------------------------------------------

def test_failure_records_reason_and_no_completed(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db, action_id="act-boom",
                      expected_effect="create boom.txt")
    store = _FakeStore()
    store.fail_with = RuntimeError("connection refused")
    result = _run(db, store, envelope)
    assert result["outcome"] == "FAILED"
    state = effect_state(db, result["action_key"])
    assert state["status"] == "FAILED"
    assert "connection refused" in state["failure_reason"]


def test_timeout_is_ambiguous_until_reconciled(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db, action_id="act-slow",
                      expected_effect="create slow.txt")
    store = _FakeStore()
    store.fail_with = TimeoutError("deadline exceeded")
    result = _run(db, store, envelope)
    assert result["outcome"] == "FROZEN"
    state = effect_state(db, result["action_key"])
    assert state["status"] == "FROZEN"
    assert "deadline" in str(state["frozen_reason"])
    assert db.event_count(event_type="effect.reconcile_pending") == 1
    assert db.event_count(event_type="system.action_failed") == 0


def test_crash_after_external_call_freezes_on_expired_claim(
        db: FoundryDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db, action_id="act-crash-after",
                      expected_effect="create crash-after.txt")
    store = _FakeStore()
    original_append = __import__("jarvis_foundry.effects",
                                 fromlist=["_append"])._append

    def crash_at_completion(database: FoundryDatabase, event_type: str,
                            payload: dict[str, object], key_suffix: str,
                            **kwargs: object) -> None:
        if event_type == "system.action_completed":
            raise RuntimeError("simulated process crash")
        original_append(database, event_type, payload, key_suffix, **kwargs)

    monkeypatch.setattr("jarvis_foundry.effects._append", crash_at_completion)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        execute_effect(db, envelope=envelope, connector_id="conn-fs",
                       perform=store.perform, claim_ttl_s=0.01)
    assert store.calls == 1

    monkeypatch.setattr("jarvis_foundry.effects._append", original_append)
    time.sleep(0.03)
    retry = execute_effect(db, envelope=envelope, connector_id="conn-fs",
                           perform=store.perform, claim_ttl_s=0.01)
    assert retry["outcome"] == "FROZEN"
    assert retry["reaped"] is True
    assert store.calls == 1


def test_partial_success_captures_after_state(db: FoundryDatabase) -> None:
    """A perform that mutates then raises: the effect FAILED and the
    journal records NO fabricated after-state (the driver can only
    capture what the perform returns). The partial change stays
    observable provider-side — which is exactly what reconciliation
    exists to catch."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db, action_id="act-partial",
                      expected_effect="create partial.txt")
    store = _FakeStore()

    def partial(ref: str, payload: str) -> str:
        store.content[ref] = payload  # side effect happened
        raise RuntimeError("crashed mid-write")

    result = execute_effect(
        db, envelope=envelope, connector_id="conn-fs", perform=partial)
    assert result["outcome"] == "FAILED"
    state = effect_state(db, result["action_key"])
    assert state["status"] == "FAILED"
    assert state["after_state"] is None  # honest — nothing captured
    assert "crashed mid-write" in state["failure_reason"]
    assert "sys-browser" in store.content  # visible provider-side only


# ---------------------------------------------------------------------------
# freeze + compensation (append-only correction)
# ---------------------------------------------------------------------------

def test_freeze_and_compensation_append_only(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    key = _run(db, store, envelope)["action_key"]
    with pytest.raises(EffectStateError):
        # a COMPLETED effect cannot be frozen by request
        freeze_effect(db, key, reason="too late")
    assert effect_state(db, key)["status"] == "COMPLETED"


def test_freeze_started_effect_and_compensate(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    from jarvis_foundry.systems import record_external_action
    key, system_id, target_ref, effect_hash = effect_identity(db, envelope)
    record_external_action(db, system_id, target_ref, effect_hash,
                           kind="file.write", started=True)
    freeze_effect(db, key, reason="provider unreachable — ambiguous")
    assert effect_state(db, key)["status"] == "FROZEN"
    compensate_effect(db, key, compensation_ref="undo-1")
    # the journal stays FROZEN — the correction is recorded, never a rewrite
    assert effect_state(db, key)["status"] == "FROZEN"
    with db.connect() as connection:
        frozen = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'effect.frozen'"
        ).fetchone()[0]
        compensated = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'effect.compensated'").fetchone()[0]
    assert (frozen, compensated) == (1, 1)


# ---------------------------------------------------------------------------
# provider reconciliation (honest NOT_TESTED without a real provider)
# ---------------------------------------------------------------------------

def test_reconcile_matching_provider_state(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    key = _run(db, store, envelope)["action_key"]
    after_state = effect_state(db, key)["after_state"]
    provider_state = {"target": "sys-browser", "exists": True,
                      "content_hash": after_state}
    result = reconcile_effect(db, key, provider_state=provider_state)
    assert result["reconciled"] is True
    assert effect_state(db, key)["status"] == "COMPLETED"


def test_reconcile_mismatch_freezes(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    key = _run(db, store, envelope)["action_key"]
    provider_state = {"target": "sys-browser", "exists": False}
    result = reconcile_effect(db, key, provider_state=provider_state)
    assert result["reconciled"] is False
    assert effect_state(db, key)["status"] == "FROZEN"


def test_reconcile_requires_terminal_effect(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    from jarvis_foundry.systems import record_external_action
    key, system_id, target_ref, effect_hash = effect_identity(db, envelope)
    record_external_action(db, system_id, target_ref, effect_hash,
                           kind="file.write", started=True)
    with pytest.raises(EffectStateError):
        reconcile_effect(db, key, provider_state={"exists": True})
    with pytest.raises(EffectStateError):
        reconcile_effect(db, "nope", provider_state={"exists": True})


# ---------------------------------------------------------------------------
# S6 round-1 verifier (deleg_2d03fb7f) regressions: M1/M2/M3 + L1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("veto_target", [
    "system:sys-browser",  # resolved spelling
    "GLOBAL",
    "mission-1",           # mission id
])
def test_fresh_veto_blocks_execution(db: FoundryDatabase,
                                     veto_target: str) -> None:
    """M1: a fresh VETO_ACTION between ticket and execution must block
    on EVERY identity the broker honors — resolved spelling, GLOBAL,
    and mission — not just the raw target resources."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-fresh", "command_type": "VETO_ACTION",
         "target": veto_target, "exact_text": f"veto {veto_target}",
         "reason": "t"},
        source_type="owner", source_id="test",
        idempotency_key=f"owner_directive.recorded:dir-fresh-{veto_target}"))
    store = _FakeStore()
    with pytest.raises(PermissionError) as exc:
        _run(db, store, envelope)
    assert "veto" in str(exc.value)
    assert store.calls == 0


def test_missing_journal_row_still_reaps_crash(db: FoundryDatabase) -> None:
    """M2: with a STARTED event in the ledger but the journal row
    missing (rebuild-only state), the driver must NOT re-perform — the
    event is the durable truth and the crash-reap engages."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    from jarvis_foundry.systems import record_external_action
    key, system_id, target_ref, effect_hash = effect_identity(db, envelope)
    record_external_action(db, system_id, target_ref, effect_hash,
                           kind="file.write", started=True)
    # simulate the rebuild-only window: journal row missing, event kept
    with db.connect() as connection:
        connection.execute("DELETE FROM system_actions WHERE action_key = ?",
                           (key,))
    result = _run(db, store, envelope)
    assert store.calls == 0  # never re-performed (R-IDM-01)
    assert result["outcome"] == "FROZEN"
    state = effect_state(db, key)
    assert state["status"] == "FROZEN"
    # the projection catches up on rebuild and stays FROZEN
    db.rebuild_projections()
    with db.connect() as connection:
        row = connection.execute(
            "SELECT status FROM system_actions WHERE action_key = ?",
            (key,)).fetchone()
    assert row is not None and row[0] == "FROZEN"


def test_cross_mission_ticket_reuse_refused(db: FoundryDatabase) -> None:
    """M3: an envelope from ANOTHER mission cannot execute under an
    existing ticket — ticket scope is never silently widened."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)  # ticket for mission-1
    foreign = _envelope(action_id="act-s6-1", mission_id="mission-999",
                        expected_effect="create notes.txt")
    store = _FakeStore()
    with pytest.raises(PermissionError) as exc:
        _run(db, store, foreign)
    assert "scope mismatch" in str(exc.value)
    assert store.calls == 0


def test_changed_observations_record_history(db: FoundryDatabase) -> None:
    """L1: a changed reconcile claim or compensation ref under the SAME
    effect key records NEW append-only history instead of crashing with
    EventConflict; identical re-delivery still dedupes."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    key = _run(db, store, envelope)["action_key"]
    # two different mismatching provider observations -> both recorded
    first = reconcile_effect(db, key, provider_state={"exists": False})
    second = reconcile_effect(db, key,
                              provider_state={"exists": False,
                                              "content_hash": "h-x"})
    assert first["reconciled"] is False and second["reconciled"] is False
    with db.connect() as connection:
        mismatches = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'effect.reconcile_mismatch'").fetchone()[0]
    assert mismatches == 2  # both observations are history
    # two different compensation refs -> both recorded
    compensate_effect(db, key, compensation_ref="undo-1")
    compensate_effect(db, key, compensation_ref="undo-2")
    with db.connect() as connection:
        compensated = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'effect.compensated'").fetchone()[0]
    assert compensated == 2


@pytest.mark.parametrize("override", [
    {"expected_effect": "DELETE everything.txt"},   # foreign payload
    {"before_state_capture": {"ref": "snap-9", "hash": "h-other"}},
    {"expected_effects": ("delete everything.txt",)},
])
def test_foreign_payload_under_ticket_refused(db: FoundryDatabase,
                                              override: dict[str, object]) -> None:
    """M3 round-2 (deleg_fd714bbc): an envelope that differs ONLY in
    the effect PAYLOAD identity cannot execute under an existing
    ticket — the payload is exactly what the action key hashes."""
    _register_system(db)
    _register_connector(db)
    _allow(db)  # ticket for the default payload
    foreign = _envelope(action_id="act-s6-1", **override)
    store = _FakeStore()
    with pytest.raises(PermissionError) as exc:
        _run(db, store, foreign)
    assert "scope mismatch" in str(exc.value)
    assert store.calls == 0


def test_derived_terminal_matches_projection_when_both_terminal(
        db: FoundryDatabase) -> None:
    """INFO round-2 (deleg_fd714bbc): with BOTH terminal events in the
    ledger and the journal row missing, the event-derived state must
    match what the projection replays — the FIRST terminal event in
    sequence order wins (later terminal transitions no-op on a
    non-STARTED row)."""
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db, action_id="act-ord",
                      expected_effect="create ordered.txt")
    from jarvis_foundry.systems import record_external_action
    key, system_id, target_ref, effect_hash = effect_identity(db, envelope)
    record_external_action(db, system_id, target_ref, effect_hash,
                           kind="file.write", started=True)
    # completed first, failed LAST (the projection: completed locks the
    # row; the late failed no-ops -> FAILED would be wrong)
    db.append(Event.create(
        "system.action_completed",
        {"action_key": key, "system_id": system_id, "target_ref": target_ref,
         "payload_hash": effect_hash, "kind": "file.write"},
        source_type="connector", source_id="conn-fs",
        idempotency_key=f"{key}.completed.before"))
    db.append(Event.create(
        "system.action_failed",
        {"action_key": key, "system_id": system_id, "target_ref": target_ref,
         "payload_hash": effect_hash, "kind": "file.write"},
        source_type="connector", source_id="conn-fs",
        idempotency_key=f"{key}.failed.after"))
    with db.connect() as connection:
        connection.execute("DELETE FROM system_actions WHERE action_key = ?",
                           (key,))
    derived = effect_state(db, key)
    db.rebuild_projections()
    with db.connect() as connection:
        row = connection.execute(
            "SELECT status FROM system_actions WHERE action_key = ?",
            (key,)).fetchone()
    assert derived["status"] == row[0] == "COMPLETED"
    # and the mirror case: failed first, completed last -> FAILED
    store = _FakeStore()
    envelope_b = _allow(db, action_id="act-ord2",
                        expected_effect="create ordered2.txt")
    key_b, system_b, target_b, hash_b = effect_identity(db, envelope_b)
    record_external_action(db, system_b, target_b, hash_b,
                           kind="file.write", started=True)
    db.append(Event.create(
        "system.action_failed",
        {"action_key": key_b, "system_id": system_b, "target_ref": target_b,
         "payload_hash": hash_b, "kind": "file.write"},
        source_type="connector", source_id="conn-fs",
        idempotency_key=f"{key_b}.failed.before"))
    db.append(Event.create(
        "system.action_completed",
        {"action_key": key_b, "system_id": system_b, "target_ref": target_b,
         "payload_hash": hash_b, "kind": "file.write"},
        source_type="connector", source_id="conn-fs",
        idempotency_key=f"{key_b}.completed.after"))
    with db.connect() as connection:
        connection.execute("DELETE FROM system_actions WHERE action_key = ?",
                           (key_b,))
    derived_b = effect_state(db, key_b)
    db.rebuild_projections()
    with db.connect() as connection:
        row_b = connection.execute(
            "SELECT status FROM system_actions WHERE action_key = ?",
            (key_b,)).fetchone()
    assert derived_b["status"] == row_b[0] == "FAILED"


# ---------------------------------------------------------------------------
# durability: migration v20 + rebuild
# ---------------------------------------------------------------------------

def test_v20_schema_accepts_frozen(db: FoundryDatabase) -> None:
    """Migration v20 rebuilds system_actions with FROZEN in the CHECK
    (the freeze path needs the derived journal state)."""
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND "
            "name='system_actions'").fetchone()[0]
    assert version >= 20  # v20's rebuild effect must persist at any later schema
    assert "FROZEN" in ddl


def test_effect_state_survives_rebuild(db: FoundryDatabase) -> None:
    _register_system(db)
    _register_connector(db)
    envelope = _allow(db)
    store = _FakeStore()
    key = _run(db, store, envelope)["action_key"]
    before = effect_state(db, key)
    db.rebuild_projections()
    after = effect_state(db, key)
    assert before == after
