"""V5-S8: available-connector activation and provider evidence.

Only connectors with REAL interfaces get activated: the local
filesystem connector operates on a dedicated scratch root with real
probes (create/read/append/delete round-trips) and the full mini-gate
matrix. Online connectors have no credentials on this host — durable
BLOCKED activation records only, never fabricated connector instances.
Provider-side reconciliation is NOT_TESTED (no real provider), recorded
honestly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.connectors.activation import (
    FsConnector,
    activate_local_connectors,
    activation_matrix,
    discover_local_connectors,
    mini_gate,
    record_online_blockers,
)
from jarvis_foundry.connectors.contract import ConnectorSpec
from jarvis_foundry.connectors.registry import (connector_status,
                                                register_connector)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


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


def _register_fs(db: FoundryDatabase, root: Path) -> str:
    register_connector(db, ConnectorSpec(
        connector_id="conn-fs", kind="filesystem", version="1.0.0",
        actions={"file.read": "read", "file.write": "write",
                 "file.append": "write", "file.delete": "execute"},
        capabilities={"read": "READY", "write": "READY",
                      "execute": "READY"},
        retry_policy={"max_attempts": 3, "backoff_s": [1]}))
    db.append(Event.create(
        "system.registered",
        {"system_id": "local-fs", "kind": "filesystem",
         "capabilities": {"read": "READY", "write": "READY",
                          "execute": "READY"},
         "name": "local-fs", "repository": None,
         "folder": str(root)},
        source_type="supervisor", source_id="v5-s8",
        idempotency_key="system.registered:local-fs"))
    return "conn-fs"


# ---------------------------------------------------------------------------
# the real filesystem connector
# ---------------------------------------------------------------------------

def test_fs_connector_real_roundtrip(scratch: Path) -> None:
    """Real bytes through the real interface: write, read, append,
    delete — the after-state hash changes and the file really exists."""
    fs = FsConnector(scratch)
    after = fs.perform("notes.txt", "hello world")
    assert (scratch / "notes.txt").read_text(encoding="utf-8") == \
        "hello world"
    import hashlib
    assert after == "sha256:" + hashlib.sha256(
        b"hello world").hexdigest()  # the real content hash
    read_back = fs.perform("notes.txt", None, action="file.read")
    assert read_back == "hello world"
    fs.perform("notes.txt", "!", action="file.append")
    assert (scratch / "notes.txt").read_text(encoding="utf-8") == \
        "hello world!"
    fs.perform("notes.txt", None, action="file.delete")
    assert not (scratch / "notes.txt").exists()


def test_fs_connector_refuses_traversal(scratch: Path,
                                        tmp_path: Path) -> None:
    """Canonicalization: '../' escapes and absolute paths outside the
    root are refused — the connector can never touch outside its root."""
    fs = FsConnector(scratch)
    outside = tmp_path / "secret.txt"
    outside.write_text("owner data", encoding="utf-8")
    with pytest.raises(ValueError):
        fs.perform("../secret.txt", "pwned")
    with pytest.raises(ValueError):
        fs.perform(str(outside), "pwned")
    assert outside.read_text(encoding="utf-8") == "owner data"
    assert not (scratch / "escaped.txt").exists()


def test_fs_connector_unknown_action_refused(scratch: Path) -> None:
    fs = FsConnector(scratch)
    with pytest.raises(ValueError):
        fs.perform("notes.txt", "x", action="teleport")


# ---------------------------------------------------------------------------
# discovery + activation
# ---------------------------------------------------------------------------

def test_discovery_is_deterministic_and_idempotent(db: FoundryDatabase,
                                                   scratch: Path) -> None:
    first = discover_local_connectors(db, root=scratch)
    second = discover_local_connectors(db, root=scratch)
    assert first == second
    assert "conn-fs" in first
    with db.connect() as connection:
        discovered = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'connector.discovered'").fetchone()[0]
    assert discovered == 1  # re-discovery dedupes


def test_activation_matrix_honest(db: FoundryDatabase, scratch: Path) -> None:
    discover_local_connectors(db, root=scratch)
    matrix = activation_matrix(db)
    row = matrix["conn-fs"]
    assert row["action_classes"]["file.read"] == "read"
    assert row["action_classes"]["file.write"] == "write"
    assert row["action_classes"]["file.delete"] == "execute"
    assert row["provider_reconciliation"] == "NOT_TESTED"
    assert row["health"] == "HEALTHY"
    assert row["disabled"] is False


def test_online_blockers_never_fabricate_connectors(db: FoundryDatabase,
                                                    scratch: Path) -> None:
    """The known online kinds get durable BLOCKED records — the
    registry contains ONLY the real filesystem connector, never an
    invented browser/email/calendar instance."""
    discover_local_connectors(db, root=scratch)
    record_online_blockers(db)
    with db.connect() as connection:
        registered = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'connector.registered'").fetchone()[0]
        blocked = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'connector.activation_blocked'").fetchall()
    assert registered == 1  # only conn-fs
    kinds = {json.loads(row[0])["kind"] for row in blocked}
    assert "browser" in kinds and "email" in kinds and "cloud" in kinds
    for row in blocked:
        payload = json.loads(row[0])
        assert payload["resumable"] is True
        assert "credentials" in payload["reason"] or \
            "interface" in payload["reason"]


def test_mini_gate_full_matrix(db: FoundryDatabase, scratch: Path) -> None:
    """The per-connector mini-gate: identity, declared semantics,
    capabilities, real health probe, quota, provider NOT_TESTED."""
    discover_local_connectors(db, root=scratch)
    gate = mini_gate(db, "conn-fs", root=scratch)
    assert gate["connector_id"] == "conn-fs"
    assert gate["identity"] is True
    assert gate["declared_actions"]["file.write"] == "write"
    assert gate["health"] == "HEALTHY"  # a REAL probe ran
    assert gate["quota"] is not None
    assert gate["provider_reconciliation"] == "NOT_TESTED"


def test_end_to_end_real_fs_effect(db: FoundryDatabase,
                                   scratch: Path) -> None:
    """The honest activation proof: a REAL file effect through the S7
    pipeline — envelope -> authorize -> execute (the real FsConnector
    perform) -> journal COMPLETED -> the file really exists under the
    root."""
    from jarvis_foundry.execution.pipeline import (ActionCandidate,
                                                   run_mission)
    discover_local_connectors(db, root=scratch)
    fs = FsConnector(scratch)
    candidate = ActionCandidate(
        action_id="act-fs-1", action_kind="file.write",
        target_resources=("local-fs",), capability="write",
        expected_effect="create notes.txt",
        expected_effects=("create notes.txt",),
        before_state_capture={"ref": "snap-0", "hash": "none"},
        score=1.0, connector_id="conn-fs",
        goal_alignment="s8-activation")
    # the caller's adapter maps the canonical target to the concrete
    # file under the connector root — the REAL fs operations stay real
    result = run_mission(
        db, "mission-s8", "run-1", [candidate],
        perform_map={"conn-fs": lambda ref, payload:
                     fs.perform("notes.txt", payload)})
    assert result["results"][0]["outcome"] == "COMPLETED"
    assert (scratch / "notes.txt").read_text(encoding="utf-8") == \
        "create notes.txt"
    with db.connect() as connection:
        completed = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'system.action_completed'").fetchone()[0]
    assert completed == 1


def test_fs_effect_never_touches_owner_files(db: FoundryDatabase,
                                             scratch: Path,
                                             tmp_path: Path) -> None:
    """The activation is BOUNDED: an effect targeting an outside path
    is refused — owner files are never in the connector's reach."""
    from jarvis_foundry.execution.pipeline import (ActionCandidate,
                                                   run_mission)
    discover_local_connectors(db, root=scratch)
    fs = FsConnector(scratch)
    owner_file = tmp_path / "owner.txt"
    owner_file.write_text("owner", encoding="utf-8")
    candidate = ActionCandidate(
        action_id="act-fs-2", action_kind="file.write",
        target_resources=("local-fs",), capability="write",
        expected_effect=f"create {owner_file}",
        expected_effects=(f"create {owner_file}",),
        before_state_capture={"ref": "snap-0", "hash": "none"},
        score=1.0, connector_id="conn-fs",
        goal_alignment="s8-activation")
    # the adapter hands the connector an OUTSIDE path — the connector's
    # canonicalization must refuse it (the boundedness is REAL)
    result = run_mission(
        db, "mission-s8", "run-1", [candidate],
        perform_map={"conn-fs": lambda ref, payload:
                     fs.perform(str(owner_file), payload)})
    assert result["results"][0]["outcome"] == "FAILED"
    assert owner_file.read_text(encoding="utf-8") == "owner"


def test_secret_content_refused_in_fs_effect(db: FoundryDatabase,
                                             scratch: Path) -> None:
    """The M2 boundary holds for real fs effects: secret-like effect
    text is refused at the ingestion boundary, zero ledger hits."""
    from jarvis_foundry.execution.pipeline import (ActionCandidate,
                                                   run_mission)
    discover_local_connectors(db, root=scratch)
    fs = FsConnector(scratch)
    candidate = ActionCandidate(
        action_id="act-fs-3", action_kind="file.write",
        target_resources=("local-fs",), capability="write",
        expected_effect="token=abc123",
        expected_effects=("token=abc123",),
        before_state_capture={"ref": "snap-0", "hash": "none"},
        score=1.0, connector_id="conn-fs",
        goal_alignment="s8-activation")
    result = run_mission(db, "mission-s8", "run-1", [candidate],
                         perform_map={"conn-fs": fs.perform})
    assert result["results"][0]["outcome"] == "REFUSED"
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE payload_json LIKE "
            "'%token=abc123%'").fetchall()
    assert rows == []
