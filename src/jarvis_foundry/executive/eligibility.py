"""Historical candidate firewall — stale candidates must not auto-activate."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..discovery_candidates import covered_objective
from ..knowledge import canonicalize_statement

# Sources that must never auto-activate without explicit executive epoch marker.
_BLOCKED_SOURCE_PREFIXES = (
    "test", "prompt", "hybrid-probe", "maintenance", "monitor:",
    "research_queue", "proving", "lab", "shadow", "v7-", "wp10",
)
_BLOCKED_SOURCE_EXACT = frozenset({
    "review-of-F8", "system", "vault",
})

ELIGIBLE_STATUSES = frozenset({"NEW", "SCORED"})


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


def _evidence_dict(evidence_json: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(evidence_json or "[]")
        return raw if isinstance(raw, list) else []
    except (TypeError, ValueError):
        return []


def _has_epoch_marker(evidence: list[dict[str, Any]], epoch: str) -> bool:
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if str(item.get("executive_epoch") or "") == epoch:
            return True
        if item.get("executive_eligible") is True:
            return True
    return False


def _source_blocked(source: str) -> bool:
    lower = source.lower()
    if source in _BLOCKED_SOURCE_EXACT:
        return True
    return any(lower.startswith(prefix) for prefix in _BLOCKED_SOURCE_PREFIXES)


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
    objective = str(row.get("objective") or "")
    evidence = _evidence_dict(str(row.get("evidence_json") or "[]"))

    if status not in ELIGIBLE_STATUSES:
        return EligibilityResult(candidate_id, False, f"status_{status.lower()}", {"status": status})

    if _source_blocked(source) and not _has_epoch_marker(evidence, activation_epoch):
        return EligibilityResult(
            candidate_id, False, "blocked_source",
            {"source": source, "epoch": activation_epoch},
        )

    if activation_epoch and not _has_epoch_marker(evidence, activation_epoch):
        return EligibilityResult(
            candidate_id, False, "stale_historical",
            {"epoch": activation_epoch, "requires": "executive_epoch marker"},
        )

    if not activation_epoch and not _has_epoch_marker(evidence, ""):
        # No activation yet and no explicit eligibility — historical firewall.
        if not any(item.get("executive_eligible") for item in evidence if isinstance(item, dict)):
            return EligibilityResult(
                candidate_id, False, "stale_historical",
                {"reason": "missing executive activation epoch"},
            )

    covered = covered_objective(database, objective)
    if covered is not None and covered != candidate_id:
        return EligibilityResult(
            candidate_id, False, "covered_by_mission",
            {"covered_by": covered},
        )

    stop = emergency_stop_reason(database, None)
    if stop:
        return EligibilityResult(candidate_id, False, "stop_active", {"stop": stop})

    veto = matching_veto_reason(database, {"GLOBAL", candidate_id})
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

    # Stale by age when epoch marker present but age exceeds bound.
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
            if age_h > max_age_hours and not any(
                    item.get("fresh_override") for item in evidence if isinstance(item, dict)):
                return EligibilityResult(
                    candidate_id, False, "stale_age",
                    {"age_hours": round(age_h, 1), "max_age_hours": max_age_hours},
                )
        except ValueError:
            pass

    # Dependency block in evidence.
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
            "SELECT * FROM candidates WHERE status IN ('NEW', 'SCORED') "
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
