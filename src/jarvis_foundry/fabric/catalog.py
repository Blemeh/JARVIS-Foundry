"""Event-sourced capability catalog.

The catalog describes what JARVIS can actually do. Registration is not
readiness. A restart rebuilds state from append-only fabric events.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event, payload_hash
from .contracts import CapabilityMetadata, metadata_for_spec
from .model import (
    CapabilityTrust,
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
DISABLED = "fabric.capability.disabled"
PROBATION_BLOCKED = "fabric.capability.probation_blocked"


def _append(
    database: FoundryDatabase,
    event_type: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> None:
    database.append(Event.create(
        event_type,
        payload,
        source_type="supervisor",
        source_id=SOURCE,
        idempotency_key=idempotency_key or f"{event_type}:{payload_hash(payload)}",
    ))


def _registration_id(spec: CapabilitySpec, metadata: CapabilityMetadata) -> str:
    """Derive a stable registration identity from semantic content only.

    The executor path is intentionally excluded.  A disposable trial can be
    recreated under a different temporary root without becoming a second
    logical capability registration.
    """

    metadata_payload = metadata.as_dict()
    # The installed executor is a deployment detail.  Keeping it out of the
    # derived identity lets the same logical capability be replayed from a
    # different disposable root; the explicit V12 trial id still binds the
    # actual adapter manifest when it is adopted.
    execute = metadata_payload.get("execute")
    if isinstance(execute, Mapping):
        execute = dict(execute)
        execute.pop("executor", None)
        metadata_payload["execute"] = execute
    identity = {
        "capability_id": spec.capability_id,
        "version": spec.version,
        "action_kind": spec.action_kind,
        "metadata": metadata_payload,
    }
    return f"fabric-registration:{payload_hash(identity)[:24]}"


def _active_payloads(database: FoundryDatabase) -> dict[str, dict[str, Any]]:
    """Rebuild active catalog entries from the append-only Fabric events."""

    active: dict[str, dict[str, Any]] = {}
    for event in database.events(
        readonly=True,
        event_types=(REGISTERED, SUPERSEDED, DISABLED),
    ):
        payload = dict(event.payload)
        if event.event_type == REGISTERED:
            capability_id = str(payload["capability_id"])
            # Legacy V9 registrations predate explicit trust/identity fields.
            # They remain established and receive a deterministic compatibility
            # identity derived from their immutable descriptor.
            payload.setdefault("trust", CapabilityTrust.ESTABLISHED.value)
            payload.setdefault("enabled", True)
            if not payload.get("registration_id"):
                metadata = payload.get("adapter_metadata")
                try:
                    typed = CapabilityMetadata.from_mapping(metadata) if isinstance(metadata, Mapping) else metadata_for_spec(CapabilitySpec.from_mapping(payload))
                    payload["registration_id"] = _registration_id(CapabilitySpec.from_mapping(payload), typed)
                except (KeyError, TypeError, ValueError):
                    payload["registration_id"] = f"fabric-registration:legacy:{payload_hash(payload)[:24]}"
            active[capability_id] = payload
        elif event.event_type == SUPERSEDED:
            active.pop(str(payload.get("capability_id")), None)
        else:
            capability_id = str(payload.get("capability_id") or "")
            current = active.get(capability_id)
            registration_id = payload.get("registration_id")
            if current is not None and (
                not registration_id or registration_id == current.get("registration_id")
            ):
                active.pop(capability_id, None)
    return active


def capability_registration(
    database: FoundryDatabase,
    capability_id: str,
) -> dict[str, Any] | None:
    """Return the active registration sidecar, or ``None`` when disabled."""

    return _active_payloads(database).get(capability_id)


def _historical_registration(
    database: FoundryDatabase,
    registration_id: str,
) -> dict[str, Any] | None:
    """Return the first registration carrying an explicit stable identity."""

    for event in database.events(readonly=True, event_types=(REGISTERED,)):
        payload = dict(event.payload)
        if payload.get("registration_id") == registration_id:
            return payload
    return None


def capability_trust(database: FoundryDatabase, capability_id: str) -> CapabilityTrust | None:
    registration = capability_registration(database, capability_id)
    if registration is None:
        return None
    try:
        return CapabilityTrust(str(registration.get("trust") or CapabilityTrust.ESTABLISHED.value))
    except ValueError:
        # Unknown trust is fail-closed for callers that enforce probation.
        return CapabilityTrust.PROBATION


def catalog_truth_snapshot(database: FoundryDatabase) -> dict[str, Any]:
    """Return a hash-bound, active-only catalog truth snapshot.

    Historical trial/registration/disable events are deliberately excluded;
    this is the state an owner would observe before or after a disposable
    adoption.  It is useful for proving that disabling a new capability
    restored every pre-existing catalog entry exactly.
    """

    rows: list[dict[str, Any]] = []
    for capability_id, payload in sorted(_active_payloads(database).items()):
        spec = CapabilitySpec.from_mapping(payload)
        metadata = capability_metadata(database, capability_id)
        rows.append({
            "capability_id": capability_id,
            "spec": spec.as_dict(),
            "metadata": metadata.as_dict() if metadata is not None else None,
            "registration_id": payload.get("registration_id"),
            "trust": payload.get("trust", CapabilityTrust.ESTABLISHED.value),
            "readiness": observed_readiness(database, capability_id).as_dict(),
        })
    return {"capabilities": rows, "count": len(rows), "sha256": payload_hash(rows)}


def register_capability(
    database: FoundryDatabase,
    spec: CapabilitySpec,
    *,
    note: str = "registered",
    metadata: CapabilityMetadata | Mapping[str, Any] | None = None,
    trust: CapabilityTrust | str = CapabilityTrust.ESTABLISHED,
    registration_id: str | None = None,
    adoption: Mapping[str, Any] | None = None,
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
    if registration_id is not None:
        if not isinstance(registration_id, str) or not registration_id.strip() or any(
            char.isspace() or ord(char) < 32 for char in registration_id
        ) or len(registration_id) > 256:
            raise FabricError("registration_id must be a bounded identity")
        registration_id = registration_id.strip()
    if not isinstance(trust, CapabilityTrust):
        try:
            trust = CapabilityTrust(trust)
        except ValueError as error:
            raise FabricError(f"unsupported capability trust: {trust!r}") from error
    if trust is CapabilityTrust.DISABLED:
        raise FabricError("a disabled capability cannot be registered as active")
    if trust is CapabilityTrust.PROBATION and spec.supersedes:
        raise FabricError("PROBATION capability cannot supersede an existing catalog registration")
    # Trust is owned by this one Catalog registration event.  Metadata mirrors
    # it for older descriptor readers, but callers cannot establish a second
    # independent trust value.
    descriptor = replace(descriptor, trust_state=trust)
    stable_registration_id = registration_id or _registration_id(spec, descriptor)
    payload["adapter_metadata"] = descriptor.as_dict()
    existing = capability_registration(database, spec.capability_id)
    historical = _historical_registration(database, stable_registration_id) if registration_id is not None else None
    if historical is not None and str(historical.get("capability_id")) != spec.capability_id:
        raise FabricError(
            f"registration identity {stable_registration_id} is already bound to {historical.get('capability_id')}"
        )
    if existing is not None and existing.get("registration_id") == stable_registration_id:
        comparable = dict(existing)
        comparable.setdefault("adapter_metadata", descriptor.as_dict())
        comparable.setdefault("adoption", {})
        for key in ("note", "enabled"):
            comparable.pop(key, None)
        proposed = dict(payload)
        proposed.update({
            "trust": trust.value,
            "enabled": True,
            "registration_id": stable_registration_id,
            "adoption": dict(adoption or {}),
        })
        for key in ("note", "enabled"):
            proposed.pop(key, None)
        if comparable == proposed:
            return spec
        raise FabricError(
            f"registration identity {stable_registration_id} already exists with different content"
        )
    if registration_id is not None and existing is None and historical is not None:
        raise FabricError(
            f"registration identity {stable_registration_id} has already been used"
        )
    if registration_id is not None and existing is not None and existing.get("capability_id") == spec.capability_id:
        raise FabricError(
            f"capability {spec.capability_id} is already registered; use a new version or disable first"
        )
    payload.update({
        "trust": trust.value,
        "enabled": True,
        "registration_id": stable_registration_id,
        "adoption": dict(adoption or {}),
    })
    _append(
        database,
        REGISTERED,
        payload,
        idempotency_key=f"{REGISTERED}:{stable_registration_id}",
    )
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

    payload = capability_registration(database, capability_id)
    if payload is None:
        return None
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


def disable_capability(
    database: FoundryDatabase,
    capability_id: str,
    *,
    reason: str,
    registration_id: str | None = None,
) -> dict[str, Any]:
    """Disable exactly one active Fabric registration, idempotently.

    This is the catalog-owned rollback boundary.  It never deletes events or
    edits a second registry; replay simply omits the disabled registration.
    """

    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise FabricError("disable reason is required")
    current = capability_registration(database, capability_id)
    if current is None:
        return {
            "capability_id": capability_id,
            "registration_id": registration_id,
            "disabled": False,
            "already_disabled": True,
        }
    active_id = str(current.get("registration_id") or "")
    if registration_id is not None and registration_id != active_id:
        raise FabricError(
            f"registration identity mismatch for {capability_id}: expected {active_id}, got {registration_id}"
        )
    payload = {
        "capability_id": capability_id,
        "registration_id": active_id,
        "reason": clean_reason,
        "previous_trust": current.get("trust", CapabilityTrust.ESTABLISHED.value),
        "previous_version": current.get("version"),
    }
    _append(
        database,
        DISABLED,
        payload,
        idempotency_key=f"{DISABLED}:{active_id}",
    )
    return {**payload, "disabled": True, "already_disabled": False}


def record_probation_block(
    database: FoundryDatabase,
    capability_id: str,
    *,
    action_kind: str,
    effect_class: str,
) -> None:
    """Record a truthful refusal without creating an effect or outcome."""

    payload = {
        "capability_id": capability_id,
        "action_kind": action_kind,
        "effect_class": effect_class,
        "reason": "PROBATION capability cannot perform irreversible effects",
    }
    _append(
        database,
        PROBATION_BLOCKED,
        payload,
        idempotency_key=f"{PROBATION_BLOCKED}:{capability_id}:{action_kind}:{payload_hash(payload)}",
    )


def _specs(database: FoundryDatabase) -> dict[str, CapabilitySpec]:
    return {
        capability_id: CapabilitySpec.from_mapping(payload)
        for capability_id, payload in _active_payloads(database).items()
    }


def listed_capabilities(database: FoundryDatabase) -> tuple[CapabilitySpec, ...]:
    specs = _specs(database)
    return tuple(specs[key] for key in sorted(specs))


def get_spec(database: FoundryDatabase, capability_id: str) -> CapabilitySpec | None:
    return _specs(database).get(capability_id)


def _latest_outcome(database: FoundryDatabase, capability_id: str) -> dict[str, Any] | None:
    event = next((
        event
        for event in database.events(
            event_types=(OUTCOME,),
            newest_first=True,
            readonly=True,
        )
        if str(event.payload.get("capability_id")) == capability_id
    ), None)
    return event.payload if event is not None else None


def observed_readiness(database: FoundryDatabase, capability_id: str) -> ObservedReadiness:
    spec = get_spec(database, capability_id)
    if spec is None:
        return ObservedReadiness(
            capability_id=capability_id,
            state=Readiness.MISSING,
            note="capability is not registered",
        )
    trust_state = capability_trust(database, capability_id) or CapabilityTrust.DISABLED
    payload = _latest_outcome(database, capability_id)
    if payload is None:
        return ObservedReadiness(
            capability_id=capability_id,
            state=Readiness.UNKNOWN,
            note="registered but never verified",
            trust_state=trust_state,
        )
    return ObservedReadiness(
        capability_id=capability_id,
        state=Readiness(payload["state"]),
        last_verified_at=payload.get("at"),
        last_outcome=payload.get("last_outcome"),
        consecutive_failures=int(payload.get("consecutive_failures") or 0),
        note=str(payload.get("note") or ""),
        claimed_vs_verified=VerifyVerdict(payload.get("verified") or "UNKNOWN"),
        trust_state=trust_state,
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
            "registration": capability_registration(database, spec.capability_id),
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
