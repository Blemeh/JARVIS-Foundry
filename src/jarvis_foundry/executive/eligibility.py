"""Historical candidate firewall — stale candidates must not auto-activate.

Executive eligibility is governed by durable admission events, never by
untrusted candidate/evidence JSON claims (executive_eligible, eligibility_provenance,
fresh_override in evidence are data, not authority).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..discovery_candidates import covered_objective
from ..events import Event

# Lab/test proving sources — never receive organic executive admission.
_LAB_SOURCE_PREFIXES = (
    "test", "prompt", "hybrid-probe", "proving", "lab", "shadow", "v7-", "wp10",
)
_LAB_SOURCE_EXACT = frozenset({
    "review-of-F8",
})

ELIGIBLE_STATUSES = frozenset({"NEW", "SCORED", "ELIGIBLE"})

# SQL fragment for selection queries: the single source of truth for
# which candidate statuses are selectable. V3 WP2 added ELIGIBLE (the
# portfolio state after policy scoring); the executive must see it.
_SELECTABLE_STATUS_SQL = "(" + ", ".join(
    f"'{status}'" for status in sorted(ELIGIBLE_STATUSES)) + ")"

WAIT_NO_ELIGIBLE = "NO_ELIGIBLE_CANDIDATES"
WAIT_COMPARATIVE = "COMPARATIVE_WAIT"

ADMISSION_EVENT = "executive.candidate_admitted"
AUTHORITY_ENABLED = "executive.admission_authority_enabled"
AUTHORITY_DISABLED = "executive.admission_authority_disabled"
FRESH_OVERRIDE_EVENT = "executive.fresh_override_granted"

TRUSTED_ADMISSION_ISSUERS = frozenset({
    "discovery.candidate_generation",
    "executive.worker_loop",
})

ACTIVE_MISSION_STATES = frozenset({
    "PLANNED", "ORIENTING", "ACTION_SELECTION", "READY",
    "EXECUTING", "VERIFYING", "REPAIRING", "ACCEPTING",
})


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    candidate_id: str
    eligible: bool
    reason: str
    details: dict[str, Any]


def current_activation_epoch(database: FoundryDatabase) -> str:
    """Return the latest executive activation epoch, or empty if none."""
    epoch = ""
    for event in database.events():
        if event.event_type == "executive.activation_recorded":
            epoch = str(event.payload.get("epoch") or epoch)
    return epoch


def admission_authority_active(database: FoundryDatabase) -> tuple[bool, str]:
    """Return whether executive admission authority is active for the current epoch."""
    epoch = current_activation_epoch(database)
    if not epoch:
        return False, ""
    enabled = False
    for event in database.events():
        if event.event_type == AUTHORITY_ENABLED:
            if str(event.payload.get("epoch") or "") == epoch:
                enabled = True
        elif event.event_type == AUTHORITY_DISABLED:
            if str(event.payload.get("epoch") or "") == epoch:
                enabled = False
    return enabled, epoch


def enable_admission_authority(
    database: FoundryDatabase,
    epoch: str,
    *,
    source_id: str = "executive",
) -> None:
    """Record that executive admission authority is active for one epoch."""
    database.append(Event.create(
        AUTHORITY_ENABLED,
        {"epoch": epoch},
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"{AUTHORITY_ENABLED}:{epoch}",
    ))


def disable_admission_authority(
    database: FoundryDatabase,
    epoch: str,
    *,
    source_id: str = "executive",
) -> None:
    """Record that executive admission authority is inactive for one epoch."""
    database.append(Event.create(
        AUTHORITY_DISABLED,
        {"epoch": epoch},
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"{AUTHORITY_DISABLED}:{epoch}",
    ))


def is_candidate_admitted(
    database: FoundryDatabase,
    candidate_id: str,
    activation_epoch: str,
) -> bool:
    """True only when a trusted admission event exists for this candidate+epoch."""
    for event in database.events():
        if event.event_type != ADMISSION_EVENT:
            continue
        payload = event.payload
        if (
            str(payload.get("candidate_id") or "") == candidate_id
            and str(payload.get("epoch") or "") == activation_epoch
        ):
            issuer = str(payload.get("issuer") or "")
            if issuer in TRUSTED_ADMISSION_ISSUERS:
                return True
    return False


def admit_candidate(
    database: FoundryDatabase,
    candidate_id: str,
    *,
    epoch: str,
    source: str,
    issuer: str = "discovery.candidate_generation",
    source_id: str = "executive",
) -> bool:
    """Emit a governed admission record when authority is active."""
    if not epoch:
        return False
    active, active_epoch = admission_authority_active(database)
    if not active or active_epoch != epoch:
        return False
    if _source_is_lab_provenance(source):
        return False
    if issuer not in TRUSTED_ADMISSION_ISSUERS:
        return False
    database.append(Event.create(
        ADMISSION_EVENT,
        {
            "candidate_id": candidate_id,
            "epoch": epoch,
            "issuer": issuer,
            "source": source,
        },
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"{ADMISSION_EVENT}:{candidate_id}:{epoch}",
    ))
    return True


def grant_fresh_override(
    database: FoundryDatabase,
    candidate_id: str,
    *,
    epoch: str,
    reason: str,
    source_id: str = "executive",
) -> None:
    """Trusted internal override for stale-age bound — not from evidence JSON."""
    database.append(Event.create(
        FRESH_OVERRIDE_EVENT,
        {"candidate_id": candidate_id, "epoch": epoch, "reason": reason},
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"{FRESH_OVERRIDE_EVENT}:{candidate_id}:{epoch}",
    ))


def has_trusted_fresh_override(
    database: FoundryDatabase,
    candidate_id: str,
    activation_epoch: str,
) -> bool:
    """True only when a trusted fresh_override event exists — not evidence claims."""
    for event in database.events():
        if event.event_type != FRESH_OVERRIDE_EVENT:
            continue
        payload = event.payload
        if (
            str(payload.get("candidate_id") or "") == candidate_id
            and str(payload.get("epoch") or "") == activation_epoch
        ):
            return True
    return False


def compute_resource_contention(
    database: FoundryDatabase,
    resource_id: str,
) -> float:
    """Return [0,1] contention from active missions holding the same resource."""
    if not resource_id:
        return 0.0
    placeholders = ",".join("?" for _ in ACTIVE_MISSION_STATES)
    with database.connect() as connection:
        row = connection.execute(
            f"SELECT COUNT(*) FROM missions WHERE resource_id = ? "
            f"AND state IN ({placeholders})",
            (resource_id, *ACTIVE_MISSION_STATES),
        ).fetchone()
    active_count = int(row[0]) if row else 0
    if active_count <= 0:
        return 0.0
    return min(1.0, 0.5 + 0.25 * max(0, active_count - 1))


def _evidence_dict(evidence_json: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(evidence_json or "[]")
        return raw if isinstance(raw, list) else []
    except (TypeError, ValueError):
        return []


def _source_is_lab_provenance(source: str) -> bool:
    """True when source provenance marks lab/test/historical proving work."""
    lower = source.lower()
    if source in _LAB_SOURCE_EXACT:
        return True
    return any(lower.startswith(prefix) for prefix in _LAB_SOURCE_PREFIXES)


def stamp_executive_eligibility(
    database: FoundryDatabase,
    evidence: list[dict[str, Any]] | None,
    *,
    source: str,
    candidate_id: str | None = None,
    stamped_by: str = "discovery.candidate_generation",
) -> list[dict[str, Any]]:
    """Record governed admission when authority is active; evidence is informational only."""
    epoch = current_activation_epoch(database)
    items = list(evidence or [])
    if candidate_id and epoch:
        admit_candidate(
            database, candidate_id, epoch=epoch, source=source,
            issuer=stamped_by if stamped_by in TRUSTED_ADMISSION_ISSUERS else "discovery.candidate_generation",
        )
    return items


def assess_eligibility(
    database: FoundryDatabase,
    row: dict[str, Any],
    *,
    activation_epoch: str,
    resource_id: str | None = None,
    capability: str = "write_registered_worktree_files",
    max_age_hours: float = 168.0,
) -> EligibilityResult:
    """Determine whether one candidate may enter executive comparison."""
    candidate_id = str(row["candidate_id"])
    status = str(row.get("status") or "")
    source = str(row.get("source") or "")

    if status not in ELIGIBLE_STATUSES:
        return EligibilityResult(candidate_id, False, f"status_{status.lower()}", {"status": status})

    if _source_is_lab_provenance(source) and not is_candidate_admitted(
            database, candidate_id, activation_epoch):
        return EligibilityResult(
            candidate_id, False, "blocked_source",
            {"source": source, "epoch": activation_epoch},
        )

    if not activation_epoch:
        return EligibilityResult(
            candidate_id, False, "stale_historical",
            {"reason": "missing executive activation epoch"},
        )

    if not is_candidate_admitted(database, candidate_id, activation_epoch):
        return EligibilityResult(
            candidate_id, False, "stale_historical",
            {"epoch": activation_epoch, "requires": "trusted executive.candidate_admitted event"},
        )

    objective = str(row.get("objective") or "")
    covered = covered_objective(database, objective)
    if covered is not None and covered != candidate_id:
        return EligibilityResult(
            candidate_id, False, "covered_by_mission",
            {"covered_by": covered},
        )

    stop = emergency_stop_reason(database, None)
    if stop:
        return EligibilityResult(candidate_id, False, "stop_active", {"stop": stop})

    veto_identities = {"GLOBAL", candidate_id}
    if resource_id:
        veto_identities.add(str(resource_id))
    veto = matching_veto_reason(database, veto_identities)
    if veto:
        return EligibilityResult(candidate_id, False, "veto_active", {"veto": veto})

    if resource_id:
        resource = database.row("resources", "resource_id", resource_id)
        if resource is None:
            return EligibilityResult(candidate_id, False, "resource_missing", {"resource_id": resource_id})
        caps = json.loads(str(resource["capabilities_json"]))
        if caps.get(capability) not in ("READY", "ACTIVE"):
            return EligibilityResult(
                candidate_id, False, "capability_not_ready",
                {"capability": capability, "state": caps.get(capability)},
            )

    created_at = None
    with database.connect() as connection:
        event_row = connection.execute(
            "SELECT recorded_at FROM events WHERE event_type = "
            "'discovery.candidate_generated' AND payload_json LIKE ? "
            "ORDER BY sequence LIMIT 1",
            (f'%"candidate_id":"{candidate_id}"%',),
        ).fetchone()
        if event_row:
            created_at = event_row[0]
    if created_at and max_age_hours > 0:
        try:
            from datetime import datetime, timezone
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_h = max(0.0, (now - created).total_seconds() / 3600.0)
            if age_h > max_age_hours and not has_trusted_fresh_override(
                    database, candidate_id, activation_epoch):
                return EligibilityResult(
                    candidate_id, False, "stale_age",
                    {"age_hours": round(age_h, 1), "max_age_hours": max_age_hours},
                )
        except ValueError:
            pass

    evidence = _evidence_dict(str(row.get("evidence_json") or "[]"))
    for item in evidence:
        if isinstance(item, dict) and item.get("blocked_dependency"):
            return EligibilityResult(
                candidate_id, False, "blocked_dependency",
                {"dependency": item.get("blocked_dependency")},
            )

    return EligibilityResult(candidate_id, True, "eligible", {"source": source})


def eligible_candidates(
    database: FoundryDatabase,
    *,
    activation_epoch: str,
    resource_id: str | None = None,
    capability: str = "write_registered_worktree_files",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return eligible candidate rows for executive comparison."""
    with database.connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM candidates WHERE status IN "
            f"{_SELECTABLE_STATUS_SQL} "
            "ORDER BY rowid DESC LIMIT ?",
            (max(1, int(limit) * 4),),
        ).fetchall()
    eligible: list[dict[str, Any]] = []
    for row in rows:
        result = assess_eligibility(
            database, dict(row),
            activation_epoch=activation_epoch,
            resource_id=resource_id,
            capability=capability,
        )
        if result.eligible:
            eligible.append(dict(row))
        if len(eligible) >= limit:
            break
    return eligible
