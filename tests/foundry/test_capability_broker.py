"""V5-S3: scoped capability and credential broker (architecture §8).

Workers receive short-lived capabilities, never reusable secrets:
bound to mission/run/action/target/expiry; one-shot (replay refused);
immediately dead on STOP/veto; dead outside scope (escalation,
forwarding); provider auth resolved at execution time — identifier
only, never a value. No registered credential = honest NOT_READY.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.control.capability_broker import (
    CapabilityError,
    CapabilityUnavailable,
    capability_state,
    issue_capability,
    issue_capability_for_envelope,
    resolve_provider_auth,
    revoke_capability,
    use_capability,
)
from jarvis_foundry.control.redaction import (
    contains_secret_like,
    redact_payload,
    redact_text,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionEnvelope
from jarvis_foundry.events import Event
from jarvis_foundry.systems import register_system

T0 = "2026-08-13T00:00:00Z"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _issue(db: FoundryDatabase, **overrides: object) -> str:
    values: dict[str, object] = {
        "capability_id": "cap-1",
        "mission_id": "mission-1",
        "agent_run_id": "run-1",
        "action_kind": "browser.navigate",
        "target": "system:sys-browser",
        "ttl_seconds": 300,
        "now": T0,
    }
    values.update(overrides)
    issue_capability(db, **values)  # type: ignore[arg-type]
    return str(values["capability_id"])


# ---------------------------------------------------------------------------
# issuance + state derivation
# ---------------------------------------------------------------------------

def test_issue_binds_and_derives_state(db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    assert capability_state(db, capability_id, now=T0) == "ISSUED"
    assert capability_state(db, capability_id, now="2026-08-13T00:05:00Z") \
        == "EXPIRED"  # ttl 300s, boundary inclusive


def test_issue_identical_redelivery_dedupes(db: FoundryDatabase) -> None:
    _issue(db)
    _issue(db)
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.issued'").fetchone()[0]
    assert count == 1


def test_issue_unknown_state(db: FoundryDatabase) -> None:
    assert capability_state(db, "cap-ghost", now=T0) == "UNKNOWN"


def test_issue_requires_allowance_ticket(db: FoundryDatabase,
                                         tmp_path: Path) -> None:
    envelope = ActionEnvelope(
        action_id="act-1", mission_id="mission-1", resource_id="res-1",
        action_kind="browser.navigate", target_resources=("sys-browser",),
        capabilities_required=("navigate",), goal_alignment="mission-1",
        before_state_capture={"hash": "x"}, verification_plan={"kind": "d"},
        idempotency_key="act-1-key")
    with pytest.raises(PermissionError):
        issue_capability_for_envelope(db, envelope, now=T0)


def test_issue_for_envelope_after_allowance(db: FoundryDatabase,
                                            tmp_path: Path) -> None:
    from jarvis_foundry.control.broker import (
        PolicyBroker,
        record_decision,
    )
    register_system(db, "sys-browser", "browser", "local chromium",
                    capabilities={"navigate": "READY"})
    envelope = ActionEnvelope(
        action_id="act-1", mission_id="mission-1", resource_id="res-1",
        action_kind="browser.navigate", target_resources=("sys-browser",),
        capabilities_required=("navigate",), goal_alignment="mission-1",
        before_state_capture={"hash": "x"}, verification_plan={"kind": "d"},
        idempotency_key="act-1-key", requested_by_agent_run_id="run-1")
    broker = PolicyBroker(db, tmp_path / "EMERGENCY_STOP")
    record_decision(db, envelope, broker.decide(envelope))
    capability = issue_capability_for_envelope(db, envelope, now=T0)
    assert capability.mission_id == "mission-1"
    assert capability.agent_run_id == "run-1"
    assert capability.action_kind == "browser.navigate"
    assert capability.target == "system:sys-browser"
    # identical re-delivery re-issues the SAME capability (deduped)
    again = issue_capability_for_envelope(db, envelope, now=T0)
    assert again.capability_id == capability.capability_id
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.issued'").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# use: one-shot, expiry, scope, STOP/veto, forwarding
# ---------------------------------------------------------------------------

def test_use_once_then_replay_refused(db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    assert use_capability(db, capability_id, now=T0,
                          mission_id="mission-1",
                          agent_run_id="run-1",
                          action_kind="browser.navigate",
                          target="system:sys-browser") == "USED"
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0)
    assert "USED" in str(exc.value)


def test_use_expired_refused(db: FoundryDatabase) -> None:
    capability_id = _issue(db)  # expires 00:05:00Z
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now="2026-08-13T00:05:00Z")
    assert "EXPIRED" in str(exc.value)


def test_use_unknown_refused(db: FoundryDatabase) -> None:
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, "cap-ghost", now=T0)
    assert "UNKNOWN" in str(exc.value)


@pytest.mark.parametrize("override,reason", [
    ({"mission_id": "mission-2"}, "mission"),
    ({"agent_run_id": "run-2"}, "forwarding"),
    ({"action_kind": "repo.read"}, "action"),
    ({"target": "system:sys-other"}, "target"),
])
def test_use_outside_scope_refused(db: FoundryDatabase,
                                   override: dict[str, str],
                                   reason: str) -> None:
    capability_id = _issue(db)
    context = {"agent_run_id": "run-1", "mission_id": "mission-1",
               "action_kind": "browser.navigate",
               "target": "system:sys-browser"}
    context.update(override)
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0, **context)
    assert reason in str(exc.value)


def test_use_stop_marker_revokes_immediately(db: FoundryDatabase,
                                             tmp_path: Path) -> None:
    capability_id = _issue(db)
    marker = tmp_path / "EMERGENCY_STOP"
    marker.write_text("", encoding="utf-8")
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0, stop_path=marker)
    assert "STOP" in str(exc.value)


def test_use_stop_directive_revokes(db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-stop", "command_type": "STOP_EVERYTHING",
         "target": "GLOBAL", "exact_text": "stop everything", "reason": "t"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-stop"))
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0)
    assert "STOP" in str(exc.value)


def test_use_frozen_supervisor_revokes(db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    db.append(Event.create(
        "emergency_stop.frozen", {"instance_id": "i-1"},
        source_type="test", source_id="test",
        idempotency_key="emergency_stop.frozen:i-1"))
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0)
    assert "STOP" in str(exc.value)


def test_use_veto_revokes(db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-veto", "command_type": "VETO_ACTION",
         "target": "browser.navigate", "exact_text": "veto navigate",
         "reason": "t"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-veto"))
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0)
    assert "veto" in str(exc.value)


def test_use_veto_on_bare_system_id_revokes(db: FoundryDatabase) -> None:
    """MEDIUM (S3 verifier deleg_deff8b38): a VETO_ACTION on the BARE
    system id must deny capability use exactly as it denies the
    envelope — the F1 fix applies to use_capability too."""
    capability_id = _issue(db)  # bound to target system:sys-browser
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-bare", "command_type": "VETO_ACTION",
         "target": "sys-browser", "exact_text": "veto the browser system",
         "reason": "t"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-bare"))
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0, agent_run_id="run-1",
                       mission_id="mission-1",
                       action_kind="browser.navigate",
                       target="system:sys-browser")
    assert "veto" in str(exc.value)
    # and the prefixed spelling still revokes too
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-prefixed", "command_type": "VETO_ACTION",
         "target": "system:sys-browser", "exact_text": "veto prefixed",
         "reason": "t"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-prefixed"))
    with pytest.raises(CapabilityError):
        use_capability(db, capability_id, now=T0, agent_run_id="run-1",
                       mission_id="mission-1",
                       action_kind="browser.navigate",
                       target="system:sys-browser")


def test_use_requires_agent_run_id(db: FoundryDatabase) -> None:
    """LOW (S3 verifier deleg_deff8b38): a use that presents NO run
    context cannot be checked against the binding — refused."""
    capability_id = _issue(db)
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0, mission_id="mission-1")
    assert "agent run id" in str(exc.value)


def test_explicit_revoke(db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    revoke_capability(db, capability_id, reason="owner revoked", now=T0)
    assert capability_state(db, capability_id, now=T0) == "REVOKED"
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0)
    assert "REVOKED" in str(exc.value)


# ---------------------------------------------------------------------------
# provider auth — identifier only, honest NOT_READY
# ---------------------------------------------------------------------------

def test_provider_auth_unavailable_not_ready(db: FoundryDatabase) -> None:
    with pytest.raises(CapabilityUnavailable):
        resolve_provider_auth(db, "gmail", mission_id="mission-1",
                              agent_run_id="run-1", now=T0)


def test_provider_auth_ready_returns_identifier_only(
        db: FoundryDatabase) -> None:
    register_system(db, "sys-creds", "credentials", "credential broker",
                    capabilities={"gmail": "READY"})
    capability = resolve_provider_auth(
        db, "gmail", mission_id="mission-1", agent_run_id="run-1", now=T0)
    assert capability.action_kind == "provider.auth.gmail"
    assert capability.target == "provider:gmail"
    # the auth capability is itself bound and one-shot
    assert use_capability(
        db, capability.capability_id, now=T0, mission_id="mission-1",
        agent_run_id="run-1", action_kind="provider.auth.gmail",
        target="provider:gmail") == "USED"


# ---------------------------------------------------------------------------
# redaction + ledger scans
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("api_key=sk-live-1234", "api_key=[REDACTED]"),
    ("token=tok-abc", "token=[REDACTED]"),
    ("password=hunter2", "password=[REDACTED]"),
    ("private_key=-----BEGIN RSA PRIVATE KEY-----",
     "private_key=[REDACTED]"),
    ("Authorization: Bearer xyz", "Authorization=[REDACTED]"),
    ("secret: s3cr3t", "secret=[REDACTED]"),
])
def test_redact_text_matrix(raw: str, expected: str) -> None:
    assert redact_text(raw) == expected


@pytest.mark.parametrize("raw", [
    '{"token": "abc123"}',
    '{"api_key": "sk-live-9"}',
    '{"Authorization": "Bearer xyz"}',
    'payload={"password": "hunter2"}',
])
def test_redact_text_quoted_json(raw: str) -> None:
    """MEDIUM (S3 verifier deleg_deff8b38): quoted-key JSON — the most
    common serialization on stdout/logs/errors/evidence — must redact
    exactly like the bare form, and the scan must detect it."""
    redacted = redact_text(raw)
    assert "[REDACTED]" in redacted
    for secret in ("abc123", "sk-live-9", "xyz", "hunter2"):
        assert secret not in redacted
    assert contains_secret_like(raw)
    # the redacted form still carries the key label (debuggability) — the
    # scan is a conservative tripwire and flags any assignment form


@pytest.mark.parametrize("benign", [
    "api is the interface", "keyboard input", "tokenize this",
    "secretary called", "navigate to url", "api_key is documented",
])
def test_redact_text_no_false_positives(benign: str) -> None:
    assert redact_text(benign) == benign


def test_redact_payload_recursive() -> None:
    payload = {"capabilities": {"navigate": "READY"},
               "config": [{"api_key": "sk-live-1"},
                          {"token": "tok-2"}],
               "note": "password=guessme"}
    redacted = redact_payload(payload)
    assert redacted["config"][0]["api_key"] == "[REDACTED]"
    assert redacted["config"][1]["token"] == "[REDACTED]"
    assert redacted["note"] == "password=[REDACTED]"
    assert redacted["capabilities"] == {"navigate": "READY"}


def test_contains_secret_like_scan() -> None:
    assert contains_secret_like("api_key=sk-live")
    assert not contains_secret_like("nothing here")


def test_ledger_scan_no_secret_payloads(db: FoundryDatabase) -> None:
    """Capability events carry identifiers and outcomes only — never
    secret values (V5 exit evidence 3)."""
    _issue(db, capability_id="cap-2", now=T0)
    use_capability(db, "cap-2", now=T0, mission_id="mission-1",
                   agent_run_id="run-1", action_kind="browser.navigate",
                   target="system:sys-browser")
    revoke_capability(db, "cap-3", reason="test", now=T0)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM events WHERE "
            "event_type LIKE 'capability.%'").fetchall()
    for event_type, payload in rows:
        assert not contains_secret_like(payload), \
            f"secret-like payload in {event_type}: {payload[:120]}"


# ---------------------------------------------------------------------------
# durability
# ---------------------------------------------------------------------------

def test_capability_events_survive_rebuild(db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    use_capability(db, capability_id, now=T0, mission_id="mission-1",
                   agent_run_id="run-1", action_kind="browser.navigate",
                   target="system:sys-browser")
    db.rebuild_projections()
    assert capability_state(db, capability_id, now=T0) == "USED"
