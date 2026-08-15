"""Focused edge tests closing remaining kernel branch coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_ledger import registry, valid_event
from test_r4_effects import CrashOnExecuteAdapter

from jarvis_foundry.kernel.canonical import canonical_bytes, sha256_hex
from jarvis_foundry.kernel.capabilities import CapabilityBroker
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.effects import EffectEngine, RecordingSystemAdapter
from jarvis_foundry.kernel.errors import (
    AuthorityError,
    CapabilityError,
    EventIntegrityError,
    FinancialActionDeniedError,
    ProjectionError,
    ReleaseError,
    SupervisorError,
)
from jarvis_foundry.kernel.events import EventKindRegistry, LedgerEvent
from jarvis_foundry.kernel.evidence import bind_artifacts, issue_verdict, verify_verdict
from jarvis_foundry.kernel.ids import DeterministicIdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.owner_control import FileStopGate
from jarvis_foundry.kernel.projections import (
    EffectProjectionHandler,
    ProjectionDispatcher,
    ProjectionRegistry,
    SupervisorProjectionHandler,
)
from jarvis_foundry.kernel.records import EffectIntent, VerifierVerdict
from jarvis_foundry.kernel.releases import ReleaseController, ReleaseFailureAdapter
from jarvis_foundry.kernel.timeutil import add_seconds_to_iso, iso_compare


def test_canonical_bytes_normalizes_bytes_and_sequences() -> None:
    assert canonical_bytes(b"abc") == canonical_bytes("616263")
    assert canonical_bytes([1, 2]) == canonical_bytes((1, 2))
    assert canonical_bytes(set()) == canonical_bytes("set()")
    assert sha256_hex("abc") == sha256_hex(b"abc")


def test_capabilities_grantee_filter_and_missing_grant(tmp_path: Path) -> None:
    service = CapabilityBroker(
        tmp_path / "caps.db",
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
    )
    service.issue(
        grantee="worker-1",
        capability="file.read",
        target="C:/Work",
        scope={},
        ttl_seconds=60,
    )
    assert len(service.list_grants(grantee="worker-1")) == 1
    assert len(service.list_grants()) == 1
    assert service.list_grants(grantee="worker-2") == []
    with pytest.raises(CapabilityError):
        service.verify("grant-missing", requested_target="C:/Work", at="2026-08-15T00:00:00Z")
    with pytest.raises(CapabilityError):
        service.revoke("grant-missing", reason="test")


def test_effect_reuses_prepared_phase_after_crash(tmp_path: Path) -> None:
    adapter = CrashOnExecuteAdapter()
    ledger = Ledger(
        tmp_path / "kernel.db",
        registry=EventKindRegistry()
        .register("effect.intent", required_keys=("intent_id", "idempotency_key"))
        .register("effect.receipt", required_keys=("receipt_id", "intent_id", "status")),
    )
    engine = EffectEngine(
        store_path=tmp_path / "effects.db",
        adapter=adapter,
        ledger=ledger,
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
    )
    intent = EffectIntent(
        intent_id="intent-1",
        idempotency_key="effect:1",
        action_envelope_id="action-1",
        system="fs",
        operation="crash",
        params={},
    )
    engine.submit(intent)
    assert engine.execute("intent-1").status == "FAILED"
    with engine.connect() as connection:
        connection.execute(
            "UPDATE effect_intents SET phase = 'PREPARED' WHERE intent_id = ?", ("intent-1",)
        )
    prepared_before = len(adapter.prepared)
    assert engine.execute("intent-1").status == "FAILED"
    assert len(adapter.prepared) == prepared_before


def test_effect_all_success_reconcile(tmp_path: Path) -> None:
    adapter = RecordingSystemAdapter()
    ledger = Ledger(
        tmp_path / "kernel.db",
        registry=EventKindRegistry()
        .register("effect.intent", required_keys=("intent_id", "idempotency_key"))
        .register("effect.receipt", required_keys=("receipt_id", "intent_id", "status")),
    )
    engine = EffectEngine(
        store_path=tmp_path / "effects.db",
        adapter=adapter,
        ledger=ledger,
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
    )
    for index in (1, 2):
        intent = EffectIntent(
            intent_id=f"intent-{index}",
            idempotency_key=f"effect:{index}",
            action_envelope_id=f"action-{index}",
            system="fs",
            operation="write",
            params={},
        )
        engine.submit(intent)
        engine.execute(intent.intent_id)
    result = engine.reconcile(["intent-1", "intent-2"])
    assert result.reconciliation_status == "SUCCESS"
    assert result.matched is True


def test_event_schema_mismatch_and_create_defaults(tmp_path: Path) -> None:
    registry_used = EventKindRegistry().register("goal.created", required_keys=("goal_id",))
    ledger = Ledger(tmp_path / "kernel.db", registry=registry_used)
    bad = LedgerEvent.create(
        event_type="goal.created",
        payload={"goal_id": "g-1"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="goal:1",
        schema_version=2,
    )
    with pytest.raises(EventIntegrityError):
        ledger.append(bad)
    defaulted = LedgerEvent.create(
        event_type="goal.created",
        payload={"goal_id": "g-2"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="goal:2",
    )
    assert defaulted.event_id == "goal.created:goal:2"
    assert defaulted.occurred_at == "1970-01-01T00:00:00Z"
    explicit = LedgerEvent.create(
        event_type="goal.created",
        payload={"goal_id": "g-3"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="goal:3",
        occurred_at="2026-08-15T00:00:00Z",
        recorded_at="2026-08-15T00:00:01Z",
    )
    assert explicit.recorded_at == "2026-08-15T00:00:01Z"


def test_evidence_artifact_mismatch_and_verdict_integrity() -> None:
    record = bind_artifacts(
        artifacts={"a.txt": b"alpha"},
        producer="builder",
        evidence_id="e-1",
        recorded_at="2026-08-15T00:00:00Z",
    )
    from jarvis_foundry.kernel.evidence import verify_artifacts

    assert verify_artifacts(record, {"a.txt": b"alpha", "b.txt": b"beta"}) is False
    verdict = issue_verdict(
        evidence=record,
        verifier_id="verifier",
        status="PASS",
        reason="ok",
        verdict_id="v-1",
        verified_at="2026-08-15T00:00:00Z",
    )
    self_verdict = VerifierVerdict(
        verdict_id="v-self",
        evidence_id=record.evidence_id,
        verifier_id="builder",
        status="PASS",
        reason="self",
        verified_at="2026-08-15T00:00:00Z",
        artifact_hashes=dict(record.artifact_hashes),
    )
    assert verify_verdict(self_verdict, record) is False
    other_evidence = bind_artifacts(
        artifacts={"a.txt": b"alpha"},
        producer="builder",
        evidence_id="e-2",
        recorded_at="2026-08-15T00:00:00Z",
    )
    assert verify_verdict(verdict, other_evidence) is False


def test_ledger_read_limit_head_and_sequence_gap(tmp_path: Path) -> None:
    registry_used = registry()
    ledger = Ledger(tmp_path / "kernel.db", registry=registry_used)
    assert ledger.head_hash is None
    assert ledger.next_sequence() == 1
    ledger.append(valid_event(1, "goal.created", "a", registry_used))
    ledger.append(valid_event(2, "goal.created", "b", registry_used))
    assert [event.event_id for event in ledger.read(limit=1)] == ["evt-1"]
    events = ledger.replay()
    wrong_previous = "0" * 64
    tampered = events[-1].header_hash_for(4, wrong_previous)
    with ledger.connect() as connection:
        connection.execute(
            "INSERT INTO ledger_events ("
            "sequence, event_id, event_type, occurred_at, recorded_at, source_type, source_id, "
            "correlation_id, causation_id, idempotency_key, schema_version, kind_version, "
            "payload, payload_hash, previous_hash, header_hash"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 1, 1, ?, ?, ?, ?)",
            (
                4,
                "evt-gap",
                "goal.created",
                "2026-08-15T00:00:00Z",
                "2026-08-15T00:00:00Z",
                "r4-test",
                "x",
                "gap:1",
                "{}",
                "0" * 64,
                wrong_previous,
                tampered,
            ),
        )
    report = ledger.verify_chain()
    assert report.valid is False
    assert any("sequence gap" in problem.detail for problem in report.problems)
    assert any("previous_hash mismatch" in problem.detail for problem in report.problems)


def test_ledger_append_with_connection_refuses_financial(tmp_path: Path) -> None:
    registry_used = registry()
    ledger = Ledger(tmp_path / "kernel.db", registry=registry_used)
    payment = LedgerEvent.create(
        event_type="payment.requested",
        payload={"amount": "1", "currency": "USD"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="payment:1",
        event_id="evt-pay",
    )
    with pytest.raises(FinancialActionDeniedError), ledger.transaction() as connection:
        ledger.append_with_connection(connection, payment)
    with pytest.raises(EventIntegrityError), ledger.connect() as connection:
        ledger._row_to_event(connection, 999)


def test_owner_wrong_channel_and_stop_gate_clear_missing(tmp_path: Path) -> None:
    from test_owner_control import SECRET, fresh_service

    service, stop = fresh_service(tmp_path)
    from jarvis_foundry.kernel.owner_control import sign_command

    signature = sign_command(SECRET, "PAUSE", "GLOBAL", "nonce-1", "pause x", "attacker")
    with pytest.raises(AuthorityError):
        service.apply_command(
            command_type="PAUSE",
            target="GLOBAL",
            exact_text="pause x",
            nonce="nonce-1",
            signature=signature,
            channel_id="attacker",
        )
    gate = FileStopGate(stop)
    assert gate.clear() is False


def test_release_invalid_transitions_and_corrupt_stage(tmp_path: Path) -> None:
    ledger = Ledger(
        tmp_path / "kernel.db",
        registry=EventKindRegistry()
        .register("release.created", required_keys=("release_id", "artifact_id"))
        .register("release.transitioned", required_keys=("release_id", "stage")),
    )
    controller = ReleaseController(
        store_path=tmp_path / "releases.db",
        ledger=ledger,
        clock=FixedClock("2026-08-15T00:00:00Z"),
        ids=DeterministicIdGenerator(seed=1),
        adapter=ReleaseFailureAdapter(fail_on=("canary",)),
    )
    release = controller.create(artifact_id="art-1", version="1.0.0")
    with pytest.raises(ReleaseError):
        controller.canary(release.release_id)
    controller.enter_lab(release.release_id)
    controller.shadow(release.release_id)
    with pytest.raises(ReleaseError):
        controller.canary(release.release_id)
    assert controller.get(release.release_id).stage == "QUARANTINED"
    second = controller.create(artifact_id="art-2", version="1.0.0")
    with controller.connect() as connection:
        connection.execute(
            "UPDATE releases SET stage = 'UNKNOWN' WHERE release_id = ?", (second.release_id,)
        )
    with pytest.raises(ReleaseError):
        controller.promote(second.release_id)


def test_secrets_redact_non_containers() -> None:
    from jarvis_foundry.kernel.secrets import Redaction

    redaction = Redaction()
    assert redaction.redact_object(123) == 123
    assert redaction.redact_object(None) is None


def test_supervisor_edge_failures_and_break_path(tmp_path: Path) -> None:
    from test_r4_supervisor import fresh_supervisor

    service = fresh_supervisor(tmp_path, max_running=1)
    with pytest.raises(SupervisorError):
        service.heartbeat("missing")
    with pytest.raises(SupervisorError):
        service.status("missing")
    with pytest.raises(SupervisorError):
        service.lease("missing")
    service.start("instance-1")
    service.acquire_lease("worker-a", "instance-1")
    service.acquire_lease("worker-b", "instance-1", allow_wait=True)
    service.acquire_lease("worker-c", "instance-1", allow_wait=True)
    advanced = service.advance_waiting(at="2026-08-15T00:00:10Z")
    assert list(advanced.promoted) == []


def test_supervisor_expired_lease_reacquired_and_recover_noop(tmp_path: Path) -> None:
    from test_r4_supervisor import fresh_supervisor

    service = fresh_supervisor(tmp_path, max_running=2)
    service.acquire_lease("worker-a", "instance-1")
    with service.connect() as connection:
        connection.execute(
            "UPDATE supervisor_leases SET expires_at = '2026-08-14T00:00:00Z' "
            "WHERE worker_id = 'worker-a'"
        )
    reacquired = service.acquire_lease("worker-a", "instance-2")
    assert reacquired.status == "ACTIVE"
    report = service.recover(at="2026-08-15T00:00:00Z")
    assert list(report.reclaimed_leases) == []


def test_timeutil_naive_and_ordering() -> None:
    assert add_seconds_to_iso("2026-08-15T00:00:00", 60) == "2026-08-15T00:01:00.000Z"
    assert iso_compare("2026-08-15T00:00:00Z", "2026-08-15T00:00:01Z") == -1
    assert iso_compare("2026-08-15T00:00:01Z", "2026-08-15T00:00:00Z") == 1
    assert iso_compare("2026-08-15T00:00:00Z", "2026-08-15T00:00:00Z") == 0


def test_worker_gateway_invalid_decision(tmp_path: Path) -> None:
    from test_worker_gateway import fresh_gateway

    gateway = fresh_gateway(tmp_path)
    proposal = gateway.bounded_context_in(mission_id="mission-1", context={"task": "x"})
    with pytest.raises(AuthorityError):
        gateway.proposal_out(proposal.proposal_id, decision="INVALID")


def test_projection_registry_missing_handler_and_extra_kinds(tmp_path: Path) -> None:
    registry_used = registry()
    ledger = Ledger(tmp_path / "kernel.db", registry=registry_used)
    ledger.append(valid_event(1, "supervisor.started", "x", registry_used))
    dispatcher = ProjectionDispatcher(
        ProjectionRegistry(
            {
                "supervisor": SupervisorProjectionHandler(),
                "effects": EffectProjectionHandler(),
            }
        )
    )
    with pytest.raises(ProjectionError):
        dispatcher.registry.handler("missing")
    snapshot = dispatcher.replay(ledger.replay())
    assert "instance-1" in snapshot.states["supervisor"]
    heartbeat = LedgerEvent.create(
        event_type="supervisor.heartbeat",
        payload={"instance_id": "instance-1", "heartbeat_at": "2026-08-15T00:00:00Z"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="hb:1",
    )
    heartbeat_snapshot = dispatcher.replay([heartbeat])
    assert (
        "supervisor.heartbeat:supervisor.heartbeat:hb:1" in heartbeat_snapshot.states["supervisor"]
    )
    effect_event = LedgerEvent.create(
        event_type="effect.intent",
        payload={"intent_id": "i-1", "idempotency_key": "e:1"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="e:1",
    )
    receipt_event = LedgerEvent.create(
        event_type="effect.receipt",
        payload={"receipt_id": "r-1", "intent_id": "i-1", "status": "SUCCEEDED"},
        source_type="r4-test",
        source_id="x",
        idempotency_key="r:1",
    )
    effect_snapshot = dispatcher.replay([effect_event, receipt_event])
    assert effect_snapshot.states["effects"]["i-1:receipts"][0]["status"] == "SUCCEEDED"
