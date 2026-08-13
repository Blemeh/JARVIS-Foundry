"""V5-S8: available-connector activation and provider evidence.

Only connectors with REAL interfaces are activated. On this host the
one genuinely available local connector is the filesystem — a real
connector bound to a DEDICATED scratch root (created by discovery,
never the owner's files). Every other known connector kind (browser,
email, calendar, messaging, cloud, git remote) gets a durable
BLOCKED activation record: no credentials or interface exist on this
host, and fabricating them is forbidden (master prompt §4). Provider-
side reconciliation stays NOT_TESTED (R-EXT-01) — recorded honestly,
never simulated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..connectors.contract import (ConnectorSpec, check_action_semantics,
                                   validate_connector_spec)
from ..connectors.registry import (connector_status, record_health_sample,
                                   register_connector)
from ..database import FoundryDatabase
from ..events import Event

#: The connector id of the real local filesystem connector.
FS_CONNECTOR_ID = "conn-fs"
#: The system entity for the local filesystem root (S1 registry).
FS_SYSTEM_ID = "local-fs"
#: Known connector kinds that require credentials or an online
#: interface absent on this host — durable BLOCKED records only.
ONLINE_KINDS = ("browser", "email", "calendar", "messaging", "cloud",
                "git_remote")

_READ = "file.read"
_WRITE = "file.write"
_APPEND = "file.append"
_DELETE = "file.delete"

_ACTIONS = {_READ: "read", _WRITE: "write", _APPEND: "write",
            _DELETE: "execute"}
_CAPABILITIES = {"read": "READY", "write": "READY", "execute": "READY"}


class FsConnector:
    """The REAL local filesystem connector — bounded to a dedicated
    root. Canonicalization refuses every escape: '../' segments,
    absolute paths outside the root, symlinks out of the root, and
    unknown action kinds. The after-state is a content hash (the S6
    after-state capture contract)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"connector root {self.root} is not a "
                             "directory")

    def _resolve(self, ref: str) -> Path:
        if not ref or ref != ref.strip():
            raise ValueError("empty or whitespace file ref refused")
        candidate = (self.root / ref).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(
                f"ref {ref!r} escapes the connector root — refused")
        return candidate

    def perform(self, ref: str, payload: str | None, *,
                action: str = _WRITE) -> str:
        """The pipeline perform signature: (ref, payload) -> after-state
        hash. Reads return the content; writes/append/delete mutate and
        return the content hash of the resulting file."""
        if action not in _ACTIONS:
            raise ValueError(f"connector {FS_CONNECTOR_ID} does not "
                             f"declare action {action}")
        target = self._resolve(ref)
        if action == _READ:
            return target.read_text(encoding="utf-8")
        if action == _APPEND:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(payload or "")
            return self._after_state(target)
        if action == _DELETE:
            target.unlink()
            return "sha256:deleted"
        with target.open("w", encoding="utf-8") as handle:
            handle.write(payload or "")
        return self._after_state(target)

    def _after_state(self, path: Path) -> str:
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        return f"sha256:{digest}"


def _probe_root(database: FoundryDatabase, root: Path) -> str:
    """A REAL health probe: create + read back a probe file under the
    root. Returns the probe hash (the durable health evidence)."""
    probe = FsConnector(root)
    ref = ".health-probe"
    probe.perform(ref, "v5-s8 probe", action=_WRITE)
    content = probe.perform(ref, None, action=_READ)
    if content != "v5-s8 probe":
        raise RuntimeError("filesystem probe failed: read-back mismatch")
    return hashlib.sha256(b"v5-s8 probe").hexdigest()


def discover_local_connectors(database: FoundryDatabase, *,
                              root: str | Path | None = None) -> list[str]:
    """Deterministic discovery: register the REAL filesystem connector
    (dedicated scratch root — created if missing), bind its system
    entity, run a real health probe, and record the discovery. Re-
    discovery is idempotent (fixed event keys, registry upsert)."""
    root_path = Path(root) if root is not None else \
        Path(database.path).parent / "connector-root"
    root_path.mkdir(parents=True, exist_ok=True)
    validate_connector_spec(ConnectorSpec(
        connector_id=FS_CONNECTOR_ID, kind="filesystem", version="1.0.0",
        actions=dict(_ACTIONS), capabilities=dict(_CAPABILITIES),
        retry_policy={"max_attempts": 3, "backoff_s": [1]}))
    register_connector(database, ConnectorSpec(
        connector_id=FS_CONNECTOR_ID, kind="filesystem", version="1.0.0",
        actions=dict(_ACTIONS), capabilities=dict(_CAPABILITIES),
        retry_policy={"max_attempts": 3, "backoff_s": [1]}))
    probe_hash = _probe_root(database, root_path)
    record_health_sample(database, FS_CONNECTOR_ID, "HEALTHY")
    database.append(Event.create(
        "connector.discovered",
        {"connector_id": FS_CONNECTOR_ID, "kind": "filesystem",
         "root": str(root_path), "actions": list(_ACTIONS),
         "capabilities": dict(_CAPABILITIES),
         "health_probe": {"result": "read-back ok",
                          "probe_hash": probe_hash}},
        source_type="supervisor", source_id="v5-s8",
        idempotency_key=f"connector.discovered:{FS_CONNECTOR_ID}"))
    database.append(Event.create(
        "system.registered",
        {"system_id": FS_SYSTEM_ID, "kind": "filesystem",
         "capabilities": dict(_CAPABILITIES),
         "name": "local-fs", "repository": None, "folder": str(root_path)},
        source_type="supervisor", source_id="v5-s8",
        idempotency_key=f"system.registered:{FS_SYSTEM_ID}"))
    return [FS_CONNECTOR_ID]


def record_online_blockers(database: FoundryDatabase) -> list[str]:
    """Durable BLOCKED activation records for the online kinds — no
    connector instances are ever registered for them (nothing is
    fabricated); the records are resumable (credentials would unblock).
    Idempotent (fixed keys per kind)."""
    recorded: list[str] = []
    for kind in ONLINE_KINDS:
        database.append(Event.create(
            "connector.activation_blocked",
            {"kind": kind,
             "reason": "no credentials or interface for this kind exist "
                       "on this host — activation BLOCKED (resumable: "
                       "would unblock if a verified credential and "
                       "interface arrive)",
             "resumable": True},
            source_type="supervisor", source_id="v5-s8",
            idempotency_key=f"connector.activation_blocked:{kind}"))
        recorded.append(kind)
    return recorded


def activate_local_connectors(database: FoundryDatabase, *,
                              root: str | Path | None = None) -> dict[str, Any]:
    """The honest activation: discover the real connector, run its
    mini-gate, record the online blockers, and return the activation
    record — statuses are READY/NOT_READY/BLOCKED/NOT_TESTED, never
    invented."""
    discovered = discover_local_connectors(database, root=root)
    gate = mini_gate(database, FS_CONNECTOR_ID,
                     root=Path(root) if root is not None else None)
    record_online_blockers(database)
    database.append(Event.create(
        "connector.activated",
        {"connector_id": FS_CONNECTOR_ID, "kind": "filesystem",
         "status": gate["status"], "matrix": gate,
         "provider_reconciliation": "NOT_TESTED"},
        source_type="supervisor", source_id="v5-s8",
        idempotency_key=f"connector.activated:{FS_CONNECTOR_ID}"))
    return {"activated": discovered,
            "gate": gate,
            "online_blocked": list(ONLINE_KINDS),
            "provider_reconciliation": "NOT_TESTED"}


def mini_gate(database: FoundryDatabase, connector_id: str, *,
              root: str | Path | None = None) -> dict[str, Any]:
    """The per-connector mini-gate matrix: identity, declared action
    semantics, capability states, a REAL health probe, quota, and the
    honest provider-reconciliation status."""
    spec = None
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'connector.registered' AND json_extract(payload_json, "
            "'$.connector_id') = ? ORDER BY sequence",
            (connector_id,)).fetchall()
    for row in rows:
        payload = json.loads(row[0])
        try:
            candidate = ConnectorSpec(**{k: payload[k] for k in
                                         ("connector_id", "kind",
                                          "version", "actions",
                                          "capabilities",
                                          "retry_policy")})
            validate_connector_spec(candidate)
            spec = candidate
        except Exception:  # noqa: BLE001 — latest valid spec wins
            continue
    declared: dict[str, str] = dict(_ACTIONS)
    semantics_gaps: dict[str, str] = {}
    for action in _ACTIONS:
        gap = check_action_semantics(database, connector_id, action) \
            if spec else "no registered spec"
        if gap is not None:
            semantics_gaps[action] = gap
    probe_ok = False
    if root is not None:
        try:
            probe_ok = _probe_root(database, Path(root)) is not None
        except Exception:  # noqa: BLE001 — a failed probe is honest data
            probe_ok = False
    status_row = connector_status(database, connector_id)
    ready = (status_row.get("health") == "HEALTHY"
             and not status_row.get("disabled") and probe_ok)
    return {
        "connector_id": connector_id,
        "identity": spec is not None,
        "declared_actions": declared,
        "semantics_gaps": semantics_gaps,
        "capabilities": dict(_CAPABILITIES),
        "health": status_row.get("health"),
        "quota": status_row.get("quota"),
        "disabled": status_row.get("disabled"),
        "real_probe": bool(probe_ok),
        "provider_reconciliation": "NOT_TESTED",
        "status": "READY" if ready else "NOT_READY",
    }


def activation_matrix(database: FoundryDatabase) -> dict[str, Any]:
    """The capability matrix: per DISCOVERED connector, per action
    class (status from the registry + a real probe on the recorded
    root); online kinds appear as BLOCKED (from their durable records);
    provider reconciliation is NOT_TESTED everywhere (R-EXT-01)."""
    matrix: dict[str, Any] = {}
    with database.connect() as connection:
        discovered = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'connector.discovered'").fetchall()
        blocked = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'connector.activation_blocked'").fetchall()
    for row in discovered:
        payload = json.loads(row[0])
        connector_id = payload["connector_id"]
        status = connector_status(database, connector_id)
        probe_ok = False
        try:
            probe_ok = _probe_root(database, Path(payload["root"])) \
                is not None
        except Exception:  # noqa: BLE001 — a failed probe is honest data
            probe_ok = False
        ready = (status.get("health") == "HEALTHY"
                 and not status.get("disabled") and probe_ok)
        matrix[connector_id] = {
            "kind": payload["kind"], "status": "READY" if ready
            else "NOT_READY",
            "action_classes": {a: k for a, k in _ACTIONS.items()},
            "provider_reconciliation": "NOT_TESTED",
            "health": status.get("health"),
            "disabled": status.get("disabled"),
            "real_probe": bool(probe_ok),
        }
    for row in blocked:
        payload = json.loads(row[0])
        matrix[f"{payload['kind']} (blocked)"] = {
            "kind": payload["kind"], "status": "BLOCKED",
            "action_classes": {}, "provider_reconciliation": "NOT_TESTED",
            "resumable": payload["resumable"], "reason": payload["reason"],
        }
    return matrix
