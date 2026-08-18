"""R9 forward-port gate: capability lifecycle, brokerage, and fail-closed
digital-work control via canonical main.

Adapts the certified remaster R9 contract (lifecycle states, canonical
identity/scope, credential brokerage without secret leakage,
expiry/revocation, STOP/fault behavior, idempotency/receipts, and explicit
financial-action denial) onto canonical main's V5 machinery:
``control/capability_broker.py``, ``control/policy.py``,
``control/capabilities.py``, ``capabilities/registry.py``, ``connectors/*``
and ``control/redaction.py``. No remaster package is imported and no
parallel architecture is added.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.capabilities.registry import (
    CapabilityError as CapabilityRegistryError,
)
from jarvis_foundry.capabilities.registry import (
    discover_capabilities,
    register_capability,
)
from jarvis_foundry.connectors.activation import (
    FsConnector,
    activation_matrix,
    discover_local_connectors,
    mini_gate,
    record_online_blockers,
)
from jarvis_foundry.connectors.contract import ConnectorSpec
from jarvis_foundry.connectors.registry import (
    record_health_sample,
    record_quota_use,
    register_connector,
    select_connector,
)
from jarvis_foundry.control.capabilities import (
    InvalidCapabilityState,
    set_capability_state,
)
from jarvis_foundry.control.capability_broker import (
    CapabilityError,
    CapabilityUnavailable,
    capability_state,
    issue_capability,
    resolve_provider_auth,
    revoke_capability,
    use_capability,
)
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.control.redaction import (
    contains_secret_like,
    redact_payload,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.domain import ActionDecision, ActionEnvelope, CapabilityState
from jarvis_foundry.events import Event
from jarvis_foundry.systems import register_system

T0 = "2026-08-13T00:00:00Z"
EXPIRY = "2026-08-13T00:05:00Z"  # T0 + 300s (ttl_seconds=300, boundary inclusive)
CTRL = "supervisor"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


@pytest.fixture()
def scratch(tmp_path: Path) -> Path:
    root = tmp_path / "connector-root"
    root.mkdir()
    return root


def _issue(db: FoundryDatabase, **overrides: object) -> str:
    values: dict[str, object] = {
        "capability_id": "cap-r9-1",
        "mission_id": "mission-1",
        "agent_run_id": "run-1",
        "action_kind": "files.write",
        "target": "system:local-fs",
        "ttl_seconds": 300,
        "now": T0,
    }
    values.update(overrides)
    issue_capability(db, **values)  # type: ignore[arg-type]
    return str(values["capability_id"])


def _fs_spec(connector_id: str) -> ConnectorSpec:
    return ConnectorSpec(
        connector_id=connector_id, kind="filesystem", version="1.0.0",
        actions={"file.read": "read", "file.write": "write"},
        capabilities={"read": "READY", "write": "READY"},
        retry_policy={"max_attempts": 3, "backoff_s": [1]})


def _registered_resource(
        tmp_path: Path) -> tuple[FoundryDatabase, Path]:
    from jarvis_foundry.control.registration import (
        inspect_git_resource,
        registration_event,
    )

    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "jarvis-r9@example.invalid"],
        cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "JARVIS R9 Gate"],
        cwd=repository, check=True)
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"],
                   cwd=repository, check=True)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": T0, "standing_mandate": True},
        source_type="owner", source_id="convergence-r9",
        idempotency_key="system.start-build"))
    resource = inspect_git_resource(repository, resource_id="resource-1")
    database.append(registration_event(resource))
    database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-1", "objective": "r9 capability gate",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["capability lifecycle is durable"]},
        source_type="test", source_id="convergence-r9",
        idempotency_key="mission.created:mission-1"))
    return database, repository


def _envelope(repository: Path, **overrides: object) -> ActionEnvelope:
    values: dict[str, object] = {
        "action_id": "action-r9-1",
        "mission_id": "mission-1",
        "resource_id": "resource-1",
        "action_kind": "file.write",
        "target": repository / "result.txt",
        "capability": "read_registered_files",
        "expected_effects": ("create result.txt",),
        "risk_class": "C1",
        "verification_required": True,
        "rollback": {"kind": "delete_created_file"},
    }
    values.update(overrides)
    return ActionEnvelope(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# lifecycle states
# ---------------------------------------------------------------------------

def test_capability_lifecycle_is_durable_and_policy_gated(
        tmp_path: Path) -> None:
    """DISCOVERED -> PROBING -> READY -> ACTIVE projects durably; policy
    authorizes only READY/ACTIVE and honest NOT_READY otherwise; repeated
    identical transitions dedupe."""
    database, repository = _registered_resource(tmp_path)
    policy = ActionPolicy(database, tmp_path / "STOP")

    for state in (CapabilityState.DISCOVERED, CapabilityState.PROBING,
                  CapabilityState.READY, CapabilityState.ACTIVE):
        set_capability_state(database, "resource-1",
                             "read_registered_files", state.value,
                             evidence={"source": "convergence-r9"})
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.state_changed'").fetchone()[0]
    assert count == 4
    # repeated identical transition is idempotent (fixed event key)
    set_capability_state(database, "resource-1", "read_registered_files",
                         CapabilityState.ACTIVE.value,
                         evidence={"source": "convergence-r9"})
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.state_changed'").fetchone()[0]
    assert count == 4

    database.rebuild_projections()
    row = database.row("resources", "resource_id", "resource-1")
    capabilities = json.loads(row["capabilities_json"])
    assert capabilities["read_registered_files"] == "ACTIVE"
    assert policy.evaluate(_envelope(repository)).decision == \
        ActionDecision.ALLOWED

    set_capability_state(database, "resource-1", "read_registered_files",
                         CapabilityState.DEGRADED.value,
                         evidence={"source": "convergence-r9"})
    set_capability_state(database, "resource-1",
                         "run_registered_local_commands",
                         CapabilityState.PROBING.value,
                         evidence={"source": "convergence-r9"})
    assert policy.evaluate(_envelope(repository)).decision == \
        ActionDecision.NOT_READY
    probing = policy.evaluate(_envelope(
        repository, capability="run_registered_local_commands"))
    assert probing.decision == ActionDecision.NOT_READY
    assert "PROBING" in probing.reason
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.state_changed'").fetchone()[0]
    assert count == 6

    with pytest.raises(InvalidCapabilityState):
        set_capability_state(database, "resource-1",
                             "read_registered_files", "MAYBE")


def test_capability_registry_is_supervisor_owned_and_stop_blocks(
        tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "registry.db")
    database.initialize()
    with pytest.raises(CapabilityRegistryError, match="only the Supervisor"):
        register_capability(
            database,
            capability_id="capability.files",
            name="Files", family="files",
            surfaces=["file-write"], effect_class="file-write",
            risk="C1", verification_hint="artifact hash + read-back",
            controller="candidate-self",
            source_type="convergence-r9", source_id="r9")
    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("", encoding="utf-8")
    with pytest.raises(CapabilityRegistryError, match="STOP sentinel active"):
        register_capability(
            database,
            capability_id="capability.files",
            name="Files", family="files",
            surfaces=["file-write"], effect_class="file-write",
            risk="C1", verification_hint="artifact hash + read-back",
            controller=CTRL,
            source_type="convergence-r9", source_id="r9")
    stop.unlink()
    register_capability(
        database,
        capability_id="capability.files",
        name="Files", family="files",
        surfaces=["file-write"], effect_class="file-write",
        risk="C1", verification_hint="artifact hash + read-back",
        controller=CTRL,
        source_type="convergence-r9", source_id="r9")
    assert any(cap["capability_id"] == "capability.files"
               for cap in discover_capabilities(database))


def test_unavailable_connectors_recorded_honestly(
        tmp_path: Path, scratch: Path) -> None:
    """Online kinds are durable BLOCKED records, never fabricated
    connector instances; provider reconciliation stays NOT_TESTED."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    discover_local_connectors(database, root=scratch)
    record_online_blockers(database)
    with database.connect() as connection:
        registered = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'connector.registered'").fetchone()[0]
    assert registered == 1  # only the real local connector, never fakes
    matrix = activation_matrix(database)
    assert matrix["conn-fs"]["status"] == "READY"
    assert matrix["conn-fs"]["provider_reconciliation"] == "NOT_TESTED"
    assert matrix["conn-fs"]["real_probe"] is True
    for kind in ("browser", "email", "calendar"):
        blocked = matrix[f"{kind} (blocked)"]
        assert blocked["status"] == "BLOCKED"
        assert blocked["resumable"] is True
        assert "credentials" in blocked["reason"]
    unknown = mini_gate(database, "conn-ghost", root=scratch)
    assert unknown["status"] == "NOT_READY"
    assert unknown["identity"] is False


# ---------------------------------------------------------------------------
# canonical identity and scope
# ---------------------------------------------------------------------------

def test_canonical_identity_normalization_and_bounded_targets(
        tmp_path: Path, scratch: Path) -> None:
    from jarvis_foundry.control.broker import (
        normalize_action_kind,
        resolve_canonical_target,
    )

    assert normalize_action_kind("BROWSER//Navigate") == "browser.navigate"
    with pytest.raises(ValueError, match="fuzzy"):
        resolve_canonical_target("${OWNER_DIR}/x")
    fs = FsConnector(scratch)
    outside = tmp_path / "outside.txt"
    outside.write_text("owner", encoding="utf-8")
    with pytest.raises(ValueError):
        fs.perform("../outside.txt", "pwned")
    with pytest.raises(ValueError):
        fs.perform(str(outside), "pwned")
    assert outside.read_text(encoding="utf-8") == "owner"
    assert not (scratch / "escaped.txt").exists()


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
    context = {"mission_id": "mission-1", "agent_run_id": "run-1",
               "action_kind": "files.write", "target": "system:local-fs"}
    context.update(override)
    with pytest.raises(CapabilityError) as exc:
        use_capability(db, capability_id, now=T0, **context)
    assert reason in str(exc.value)


# ---------------------------------------------------------------------------
# credential brokerage without secret leakage
# ---------------------------------------------------------------------------

def test_credential_brokerage_never_leaks_and_not_ready_is_honest(
        db: FoundryDatabase) -> None:
    with pytest.raises(CapabilityUnavailable):
        resolve_provider_auth(db, "gmail", mission_id="mission-1",
                              agent_run_id="run-1", now=T0)
    register_system(db, "sys-creds", "credentials", "credential broker",
                    capabilities={"gmail": "READY"})
    capability = resolve_provider_auth(
        db, "gmail", mission_id="mission-1", agent_run_id="run-1", now=T0)
    assert capability.action_kind == "provider.auth.gmail"
    assert capability.target == "provider:gmail"
    assert use_capability(
        db, capability.capability_id, now=T0, mission_id="mission-1",
        agent_run_id="run-1", action_kind="provider.auth.gmail",
        target="provider:gmail") == "USED"

    with db.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM events WHERE "
            "event_type LIKE 'capability.%'").fetchall()
    for event_type, payload in rows:
        assert not contains_secret_like(payload), \
            f"secret-like payload in {event_type}"
    redacted = redact_payload({"config": [{"api_key": "sk-live-1"},
                                          {"token": "tok-2"}]})
    assert redacted["config"][0]["api_key"] == "[REDACTED]"
    assert redacted["config"][1]["token"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# expiry and revocation
# ---------------------------------------------------------------------------

def test_expiry_and_revocation_refuse_use(db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    assert capability_state(db, capability_id, now=T0) == "ISSUED"
    with pytest.raises(CapabilityError, match="EXPIRED"):
        use_capability(db, capability_id, now=EXPIRY, mission_id="mission-1",
                       agent_run_id="run-1", action_kind="files.write",
                       target="system:local-fs")
    live = _issue(db, capability_id="cap-r9-live")
    revoke_capability(db, live, reason="owner revocation", now=T0)
    assert capability_state(db, live, now=T0) == "REVOKED"
    with pytest.raises(CapabilityError, match="REVOKED"):
        use_capability(db, live, now=T0, mission_id="mission-1",
                       agent_run_id="run-1", action_kind="files.write",
                       target="system:local-fs")
    with db.connect() as connection:
        revoked = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.revoked'").fetchone()[0]
    assert revoked == 1


# ---------------------------------------------------------------------------
# STOP / fault behavior
# ---------------------------------------------------------------------------

def test_stop_and_fault_behavior(tmp_path: Path,
                                 db: FoundryDatabase) -> None:
    capability_id = _issue(db)
    marker = tmp_path / "EMERGENCY_STOP"
    marker.write_text("", encoding="utf-8")
    with pytest.raises(CapabilityError, match="STOP"):
        use_capability(db, capability_id, now=T0, stop_path=marker)

    register_connector(db, _fs_spec("conn-a"))
    register_connector(db, _fs_spec("conn-b"))
    record_health_sample(db, "conn-a", "DEGRADED")
    record_health_sample(db, "conn-b", "HEALTHY")
    selected = select_connector(db, "file.read",
                                capabilities_required=("read",))
    assert selected.connector is not None
    assert selected.connector.connector_id == "conn-b"  # outage isolated
    stop_outcome = select_connector(db, "file.read", stop_path=marker)
    assert stop_outcome.connector is None and "STOP" in stop_outcome.reason
    record_quota_use(db, "conn-b", used=10, limit=10)
    quota_outcome = select_connector(db, "file.read",
                                     capabilities_required=("read",))
    assert quota_outcome.connector is None and "quota" in quota_outcome.reason
    mismatch = select_connector(db, "file.write",
                                capabilities_required=("unlisted",))
    assert mismatch.connector is None and "capability" in mismatch.reason


# ---------------------------------------------------------------------------
# idempotency and receipts
# ---------------------------------------------------------------------------

def test_idempotent_execution_and_real_receipts(db: FoundryDatabase,
                                                scratch: Path) -> None:
    _issue(db)
    _issue(db)
    with db.connect() as connection:
        issued = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.issued'").fetchone()[0]
    assert issued == 1  # identical re-delivery dedupes

    register_connector(db, _fs_spec("conn-fs"))
    register_connector(db, _fs_spec("conn-fs"))
    with db.connect() as connection:
        registered = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'connector.registered'").fetchone()[0]
    assert registered == 1

    discover_local_connectors(db, root=scratch)
    discover_local_connectors(db, root=scratch)
    with db.connect() as connection:
        discovered = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'connector.discovered'").fetchone()[0]
    assert discovered == 1

    payload = "canary content"
    after = FsConnector(scratch).perform("canary.txt", payload)
    assert after == "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    assert (scratch / "canary.txt").read_text(encoding="utf-8") == payload


# ---------------------------------------------------------------------------
# financial-action denial
# ---------------------------------------------------------------------------

def test_financial_actions_are_denied(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "registry.db")
    database.initialize()
    assert discover_capabilities(database) == []
    with pytest.raises(CapabilityRegistryError, match="unknown effect class"):
        register_capability(
            database,
            capability_id="capability.finance.transfer",
            name="Transfers", family="finance",
            surfaces=["transfer"], effect_class="payment",
            risk="C3", verification_hint="receipt",
            controller=CTRL,
            source_type="convergence-r9", source_id="r9")
    no_connector = select_connector(database, "payment.execute")
    assert no_connector.connector is None
    assert "no registered connector" in no_connector.reason

    policy_db, repository = _registered_resource(tmp_path)
    policy = ActionPolicy(policy_db, tmp_path / "STOP")
    verdict = policy.evaluate(_envelope(
        repository, action_kind="finance.transfer",
        capability="finance.transfer",
        expected_effects=("transfer funds",)))
    assert verdict.decision == ActionDecision.NOT_READY
    assert "UNREGISTERED" in verdict.reason


def test_financial_authorization_denied_even_with_ready_finance_system(
        tmp_path: Path) -> None:
    """Regression (independent investigator): a READY finance system must
    never let PolicyBroker.decide / execution.pipeline.authorize ALLOW a
    financial action class. The denial precedes capability lookup and is
    therefore immune to ledger capability state."""
    from jarvis_foundry.control.broker import BrokerDecision, PolicyBroker
    from jarvis_foundry.execution.pipeline import authorize

    database = FoundryDatabase(tmp_path / "finance.db")
    database.initialize()
    database.append(Event.create(
        "owner_directive.recorded",
        {"directive_id": "dir-start", "command_type": "STANDING_MANDATE",
         "target": "GLOBAL", "exact_text": "begin", "reason": "r9"},
        source_type="owner", source_id="convergence-r9",
        idempotency_key="owner_directive.recorded:dir-start"))
    # The adversary's premise: a finance system registered READY for the
    # exact financial capability the envelope requests.
    register_system(database, "sys-bank", "finance", "bank",
                    capabilities={"finance": "READY"})
    database.append(Event.create(
        "mission.created",
        {"mission_id": "mission-1", "objective": "finance regression",
         "resource_id": "resource-1", "state": "PLANNED",
         "acceptance_criteria": ["denied"]},
        source_type="test", source_id="convergence-r9",
        idempotency_key="mission.created:mission-1"))

    envelope = ActionEnvelope(
        action_id="act-finance-1", mission_id="mission-1",
        resource_id="resource-1", action_kind="finance.transfer.extra",
        target_resources=("sys-bank",),
        capabilities_required=("finance",), goal_alignment="mission-1",
        before_state_capture={"hash": "x"}, verification_plan={"kind": "d"},
        idempotency_key="act-finance-1-key",
        requested_by_agent_run_id="run-1",
        risk_class="C1", reversibility="reversible")
    outcome = PolicyBroker(database, tmp_path / "EMERGENCY_STOP").decide(
        envelope)
    assert outcome.decision == BrokerDecision.NOT_READY
    assert "financial" in outcome.reason
    assert "denied" in outcome.reason
    with pytest.raises(PermissionError, match="not allowed"):
        authorize(database, envelope, stop_path=tmp_path / "EMERGENCY_STOP")
    with database.connect() as connection:
        decided = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'action.decided'").fetchone()[0]
        issued = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'capability.issued'").fetchone()[0]
    assert decided == 0  # no ALLOW ticket, no terminal decision
    assert issued == 0  # no capability was ever issued


def test_financial_namespace_boundary_preserves_unrelated_transfer() -> None:
    from jarvis_foundry.control.broker import _financial_class, normalize_action_kind

    assert _financial_class(normalize_action_kind("finance.transfer.extra"))
    assert _financial_class(normalize_action_kind("Finance / Transfer"))
    assert _financial_class(normalize_action_kind("payment.execute"))
    assert _financial_class(normalize_action_kind("pay_invoice"))
    assert _financial_class(normalize_action_kind("file.transfer")) is None


def test_emergency_stop_precedes_financial_denial(tmp_path: Path) -> None:
    """Global STOP remains the first broker decision for every action kind."""
    from jarvis_foundry.control.broker import BrokerDecision, PolicyBroker

    database = FoundryDatabase(tmp_path / "stop-finance.db")
    database.initialize()
    stop = tmp_path / "EMERGENCY_STOP"
    stop.write_text("owner-stop", encoding="utf-8")
    envelope = ActionEnvelope(
        action_id="act-finance-stop", mission_id="mission-stop",
        resource_id="resource-stop", action_kind="finance.transfer",
        target_resources=("unregistered-system",), risk_class="C1",
        reversibility="reversible")
    outcome = PolicyBroker(database, stop).decide(envelope)
    assert outcome.decision == BrokerDecision.EMERGENCY_STOPPED
    assert "emergency stop" in outcome.reason
