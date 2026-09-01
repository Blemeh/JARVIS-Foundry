"""Event-sourced capability catalog.

The catalog describes what JARVIS can actually do. Registration is not
readiness. A restart rebuilds state from append-only fabric events.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event, payload_hash
from .contracts import CapabilityMetadata, metadata_for_spec
from .model import (
    FAILURE_THRESHOLD,
    CapabilitySpec,
    FabricError,
    GapRecord,
    GapStatus,
    ObservedReadiness,
    Readiness,
    VerifyVerdict,
)

SOURCE = "v9-capability-fabric"
REGISTERED = "fabric.capability.registered"
SUPERSEDED = "fabric.capability.superseded"
OUTCOME = "fabric.capability.outcome"
DEGRADED = "fabric.capability.degraded"
GAP = "fabric.gap.recorded"


def _append(database: FoundryDatabase, event_type: str, payload: dict[str, Any]) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="supervisor",
        source_id=SOURCE,
        idempotency_key=f"{event_type}:{payload_hash(payload)}",
    ))


def register_capability(
    database: FoundryDatabase,
    spec: CapabilitySpec,
    *,
    note: str = "registered",
    metadata: CapabilityMetadata | Mapping[str, Any] | None = None,
) -> CapabilitySpec:
    payload = spec.as_dict()
    payload["note"] = note
    descriptor = (
        metadata
        if isinstance(metadata, CapabilityMetadata)
        else CapabilityMetadata.from_mapping(metadata)
        if metadata is not None
        else metadata_for_spec(spec)
    )
    payload["adapter_metadata"] = descriptor.as_dict()
    _append(database, REGISTERED, payload)
    if spec.supersedes:
        _append(database, SUPERSEDED, {
            "capability_id": spec.supersedes,
            "replaced_by": spec.capability_id,
            "version": spec.version,
        })
    return spec


def capability_metadata(
    database: FoundryDatabase,
    capability_id: str,
) -> CapabilityMetadata | None:
    """Return the latest typed descriptor sidecar for a Fabric capability."""

    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = ? AND "
            "json_extract(payload_json, '$.capability_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (REGISTERED, capability_id),
        ).fetchone()
    if row is None:
        return None
    import json
    payload = json.loads(row[0])
    raw = payload.get("adapter_metadata")
    if isinstance(raw, Mapping):
        return CapabilityMetadata.from_mapping(raw)
    spec = CapabilitySpec.from_mapping(payload)
    return metadata_for_spec(spec)


def record_outcome(
    database: FoundryDatabase,
    capability_id: str,
    *,
    success: bool,
    verified: VerifyVerdict,
    note: str = "",
    at: str | None = None,
) -> ObservedReadiness:
    stamp = at or isoformat()
    current = observed_readiness(database, capability_id)
    failures = 0 if success else current.consecutive_failures + 1
    if success and verified is VerifyVerdict.MATCH:
        state = Readiness.READY
        outcome = "SUCCESS"
    elif verified is VerifyVerdict.MISMATCH:
        state = Readiness.DEGRADED
        outcome = "FAIL"
    elif failures >= FAILURE_THRESHOLD:
        state = Readiness.DEGRADED
        outcome = "FAIL"
    else:
        state = Readiness.NOT_READY
        outcome = "FAIL"
    _append(database, OUTCOME, {
        "capability_id": capability_id,
        "success": success,
        "verified": verified.value,
        "note": note,
        "at": stamp,
        "consecutive_failures": failures,
        "state": state.value,
        "last_outcome": outcome,
    })
    if state is Readiness.DEGRADED:
        _append(database, DEGRADED, {
            "capability_id": capability_id,
            "reason": note or "verified failure",
            "consecutive_failures": failures,
        })
    return observed_readiness(database, capability_id)


def record_gap(database: FoundryDatabase, gap: GapRecord) -> GapRecord:
    _append(database, GAP, gap.as_dict())
    return gap


def _specs(database: FoundryDatabase) -> dict[str, CapabilitySpec]:
    specs: dict[str, CapabilitySpec] = {}
    superseded: set[str] = set()
    for event in database.events(event_types=(REGISTERED, SUPERSEDED), readonly=True):
        payload = event.payload
        if event.event_type == REGISTERED:
            spec = CapabilitySpec.from_mapping(payload)
            specs[spec.capability_id] = spec
        else:
            superseded.add(str(payload["capability_id"]))
    for cap_id in superseded:
        specs.pop(cap_id, None)
    return specs


def listed_capabilities(database: FoundryDatabase) -> tuple[CapabilitySpec, ...]:
    specs = _specs(database)
    return tuple(specs[key] for key in sorted(specs))


def get_spec(database: FoundryDatabase, capability_id: str) -> CapabilitySpec | None:
    return _specs(database).get(capability_id)


def _latest_outcome(database: FoundryDatabase, capability_id: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = ? AND "
            "json_extract(payload_json, '$.capability_id') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (OUTCOME, capability_id),
        ).fetchone()
    if row is None:
        return None
    import json
    return json.loads(row[0])


def observed_readiness(database: FoundryDatabase, capability_id: str) -> ObservedReadiness:
    spec = get_spec(database, capability_id)
    if spec is None:
        return ObservedReadiness(
            capability_id=capability_id,
            state=Readiness.MISSING,
            note="capability is not registered",
        )
    payload = _latest_outcome(database, capability_id)
    if payload is None:
        return ObservedReadiness(
            capability_id=capability_id,
            state=Readiness.UNKNOWN,
            note="registered but never verified",
        )
    return ObservedReadiness(
        capability_id=capability_id,
        state=Readiness(payload["state"]),
        last_verified_at=payload.get("at"),
        last_outcome=payload.get("last_outcome"),
        consecutive_failures=int(payload.get("consecutive_failures") or 0),
        note=str(payload.get("note") or ""),
        claimed_vs_verified=VerifyVerdict(payload.get("verified") or "UNKNOWN"),
    )


def catalog_snapshot(database: FoundryDatabase) -> dict[str, Any]:
    rows = []
    for spec in listed_capabilities(database):
        ready = observed_readiness(database, spec.capability_id)
        descriptor = capability_metadata(database, spec.capability_id)
        rows.append({
            "spec": spec.as_dict(),
            "metadata": descriptor.as_dict() if descriptor is not None else None,
            "readiness": ready.as_dict(),
        })
    return {"capabilities": rows, "count": len(rows)}


def latest_gap(database: FoundryDatabase, needed_action: str) -> GapRecord | None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = ? AND "
            "json_extract(payload_json, '$.needed_action') = ? "
            "ORDER BY sequence DESC LIMIT 1",
            (GAP, needed_action),
        ).fetchone()
    if row is None:
        return None
    import json
    payload = json.loads(row[0])
    return GapRecord(
        gap_id=payload["gap_id"],
        needed_action=payload["needed_action"],
        missing=payload["missing"],
        status=GapStatus(payload["status"]),
        detail=payload["detail"],
        candidate_path=payload.get("candidate_path"),
    )


def specs_for_action(database: FoundryDatabase, action_kind: str) -> tuple[CapabilitySpec, ...]:
    return tuple(
        spec for spec in listed_capabilities(database)
        if spec.action_kind == action_kind
    )


def reliability(database: FoundryDatabase, capability_id: str) -> dict[str, int]:
    """Count verified successes and failures from durable outcome events."""
    successes = 0
    failures = 0
    for event in database.events(event_types=(OUTCOME,), readonly=True):
        payload = event.payload
        if str(payload.get("capability_id")) != capability_id:
            continue
        if payload.get("success") and payload.get("verified") == "MATCH":
            successes += 1
        else:
            failures += 1
    return {"successes": successes, "failures": failures}


def require_registered(database: FoundryDatabase, capability_id: str) -> CapabilitySpec:
    spec = get_spec(database, capability_id)
    if spec is None:
        raise FabricError(f"capability {capability_id} is not registered")
    return spec
