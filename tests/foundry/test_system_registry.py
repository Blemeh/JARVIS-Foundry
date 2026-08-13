"""V5-S1: System Registry — catalog + policy gate + effect journal.

Roadmap V5 exit evidence (as tests):
1. Revocation and STOP immediately block new actions.
2. Retries do not duplicate external effects (idempotency keys bound
   to system+target+payload-hash).
3. Secrets never appear in prompts/logs/notes/reports (API-boundary
   refusal + ledger scan).

No live external connections in this slice — readiness states are
honest READY / NOT_READY / NOT_TESTED, never fabricated.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.systems import (
    external_action_key,
    probe_system,
    record_external_action,
    register_system,
    systems_allowed_by_policy,
)

_SECRET_PATTERN = re.compile(
    r"(api[_-]?key|token|password|passwd|secret|private[_-]?key|"
    r"authorization|bearer)\s*[:=]", re.IGNORECASE)


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


# ---------------------------------------------------------------------------
# catalog + readiness
# ---------------------------------------------------------------------------

def test_register_system_projects_row(db):
    register_system(db, "sys-browser", "browser", "local chromium",
                    capabilities={"navigate": "NOT_TESTED",
                                  "extract": "NOT_TESTED"})
    with db.connect() as connection:
        row = connection.execute(
            "SELECT system_id, kind, name, capabilities_json, readiness "
            "FROM systems WHERE system_id = ?", ("sys-browser",)).fetchone()
    assert row[0] == "sys-browser" and row[1] == "browser"
    assert row[2] == "local chromium"
    assert row[3] and "navigate" in row[3]
    assert row[4] == "NOT_TESTED"  # honest: never probed


def test_register_system_idempotent(db):
    register_system(db, "sys-a", "repo", "the repo")
    register_system(db, "sys-a", "repo", "the repo")
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM systems WHERE system_id = 'sys-a'"
        ).fetchone()[0]
    assert count == 1


def test_probe_updates_readiness(db):
    register_system(db, "sys-a", "repo", "the repo",
                    capabilities={"read": "NOT_TESTED"})
    probe_system(db, "sys-a", {"read": "READY"})
    with db.connect() as connection:
        row = connection.execute(
            "SELECT readiness, last_probe_at, capabilities_json FROM "
            "systems WHERE system_id = 'sys-a'").fetchone()
    assert row[0] == "READY" and row[1] is not None
    assert '"READY"' in row[2]
    # a failed probe moves readiness to UNREADY (honest, no fabrication)
    probe_system(db, "sys-a", {"read": "FAILED"})
    with db.connect() as connection:
        readiness = connection.execute(
            "SELECT readiness FROM systems WHERE system_id = 'sys-a'"
        ).fetchone()[0]
    assert readiness == "UNREADY"


def test_probe_unknown_system_raises(db):
    with pytest.raises(ValueError):
        probe_system(db, "sys-ghost", {"read": "READY"})


# ---------------------------------------------------------------------------
# exit evidence 1: STOP / veto immediately block new actions
# ---------------------------------------------------------------------------

def test_policy_allows_when_running(db, tmp_path):
    assert systems_allowed_by_policy(
        db, "browser.navigate", stop_path=tmp_path / "EMERGENCY_STOP") is True


def test_stop_marker_blocks(db, tmp_path):
    marker = tmp_path / "EMERGENCY_STOP"
    marker.write_text("", encoding="utf-8")
    assert systems_allowed_by_policy(
        db, "browser.navigate", stop_path=marker) is False


def test_stop_everything_directive_blocks(db, tmp_path):
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-stop-1", "command_type": "STOP_EVERYTHING",
         "target": "GLOBAL", "exact_text": "stop everything", "reason": "test"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-stop-1"))
    assert systems_allowed_by_policy(
        db, "browser.navigate", stop_path=tmp_path / "EMERGENCY_STOP") is False


def test_veto_matching_action_kind_blocks(db, tmp_path):
    db.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-veto-1", "command_type": "VETO_ACTION",
         "target": "browser.navigate", "exact_text": "veto navigate", "reason": "test"},
        source_type="owner", source_id="test",
        idempotency_key="owner_directive.recorded:dir-veto-1"))
    assert systems_allowed_by_policy(
        db, "browser.navigate", stop_path=tmp_path / "EMERGENCY_STOP") is False
    # an unrelated action kind stays allowed (the veto is scoped)
    assert systems_allowed_by_policy(
        db, "repo.read", stop_path=tmp_path / "EMERGENCY_STOP") is True


def test_frozen_supervisor_blocks(db, tmp_path):
    db.append(Event.create(
        "emergency_stop.frozen", {"instance_id": "i-1"},
        source_type="test", source_id="test",
        idempotency_key="emergency_stop.frozen:i-1"))
    assert systems_allowed_by_policy(
        db, "browser.navigate", stop_path=tmp_path / "EMERGENCY_STOP") is False


def test_reconciled_after_frozen_blocks_until_started(db, tmp_path):
    """A reconciled STOP is STILL a freeze: the projection moves the
    supervisor to SUSPENDED, which blocks until supervisor.started
    reopens the gate (fail-closed — verifier deleg_d7639766 P2(d))."""
    db.append(Event.create(
        "emergency_stop.frozen", {"instance_id": "i-1"},
        source_type="test", source_id="test",
        idempotency_key="emergency_stop.frozen:i-1"))
    db.append(Event.create(
        "emergency_stop.reconciled", {"instance_id": "i-1"},
        source_type="test", source_id="test",
        idempotency_key="emergency_stop.reconciled:i-1"))
    assert systems_allowed_by_policy(
        db, "browser.navigate", stop_path=tmp_path / "EMERGENCY_STOP") is False
    # the gate reopens only through an explicit supervisor.started
    db.append(Event.create(
        "supervisor.started", {"instance_id": "i-1", "trust_state": "LAB"},
        source_type="test", source_id="test",
        idempotency_key="supervisor.started:i-1"))
    assert systems_allowed_by_policy(
        db, "browser.navigate", stop_path=tmp_path / "EMERGENCY_STOP") is True


def test_plain_stopped_supervisor_state_not_blocking(db, tmp_path):
    """A plain STOPPED supervisor row (precursor state, no freeze event)
    is not an emergency STOP: the gate blocks only FROZEN/SUSPENDED
    states and active stop/veto directives (verifier deleg_d7639766
    P2(e))."""
    with db.connect() as connection:
        connection.execute(
            "UPDATE supervisor_state SET status = 'STOPPED' "
            "WHERE singleton = 1")
    assert systems_allowed_by_policy(
        db, "browser.navigate", stop_path=tmp_path / "EMERGENCY_STOP") is True


# ---------------------------------------------------------------------------
# exit evidence 2: retries never duplicate external effects
# ---------------------------------------------------------------------------

def test_action_key_binds_system_target_payload(db):
    first = external_action_key("sys-a", "target-1", "payload-hash-1")
    second = external_action_key("sys-a", "target-1", "payload-hash-1")
    assert first == second  # identical retry -> identical key
    assert external_action_key("sys-b", "target-1", "payload-hash-1") != first
    assert external_action_key("sys-a", "target-2", "payload-hash-1") != first
    assert external_action_key("sys-a", "target-1", "payload-hash-2") != first


def test_duplicate_delivery_dedupes(db):
    register_system(db, "sys-a", "repo", "the repo",
                    capabilities={"write": "READY"})
    record_external_action(db, "sys-a", "target-1", "payload-hash-1",
                           kind="write")
    record_external_action(db, "sys-a", "target-1", "payload-hash-1",
                           kind="write")
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM system_actions WHERE system_id = 'sys-a'"
        ).fetchone()[0]
        events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'system.action_started'").fetchone()[0]
    assert rows == 1  # one journal row — the effect happened once
    assert events == 1  # one started event — identical re-delivery deduped


def test_changed_payload_is_a_new_effect(db):
    register_system(db, "sys-a", "repo", "the repo",
                    capabilities={"write": "READY"})
    record_external_action(db, "sys-a", "target-1", "payload-hash-1",
                           kind="write")
    record_external_action(db, "sys-a", "target-1", "payload-hash-2",
                           kind="write")
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM system_actions WHERE system_id = 'sys-a'"
        ).fetchone()[0]
    assert rows == 2  # a genuinely different payload IS a different effect


# ---------------------------------------------------------------------------
# exit evidence 3: secrets never appear
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("secret", [
    {"api_key": "sk-live-1234"},
    {"navigate": "READY", "auth_token": "tok-abc"},
    {"password": "hunter2"},
    {"private_key": "-----BEGIN RSA PRIVATE KEY-----"},
    {"authorization": "Bearer xyz"},
])
def test_secret_like_capabilities_refused(db, secret):
    with pytest.raises(ValueError):
        register_system(db, "sys-x", "browser", "x", capabilities=secret)


@pytest.mark.parametrize("secret", [
    {"api_key": "sk-live-1234"},
    {"token": "tok-abc"},
])
def test_secret_like_probes_refused(db, secret):
    register_system(db, "sys-a", "browser", "x",
                    capabilities={"navigate": "NOT_TESTED"})
    with pytest.raises(ValueError):
        probe_system(db, "sys-a", secret)


def test_ledger_scan_no_secret_payloads(db):
    """After the slice's own operations, no ledger payload may contain
    secret-like assignments (exit evidence 3 — deterministic scan)."""
    register_system(db, "sys-a", "repo", "the repo",
                    capabilities={"read": "READY"})
    probe_system(db, "sys-a", {"read": "READY"})
    record_external_action(db, "sys-a", "target-1", "payload-hash-1",
                           kind="read")
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM events").fetchall()
    for event_type, payload in rows:
        assert _SECRET_PATTERN.search(payload) is None, \
            f"secret-like payload in {event_type}: {payload[:120]}"
