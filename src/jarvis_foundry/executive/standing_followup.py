"""Standing strategic goal continuation: parent/child lineage repair.

A standing owner directive (strategic mandate) authorises a sequence of
bounded executable child objectives.  A child may reach progress 1.0 /
COMPLETED / CLOSE without completing the parent standing goal.  The parent
standing authority remains ACTIVE until the strategic goal is genuinely
satisfied, the owner withdraws/supersedes/vetoes it, a real external
blocker requires owner action, or no valuable executable work exists with
a truthful revisit condition recorded.

Historical truth is preserved: completed children stay COMPLETED.  The same
standing directive produces the next child after the previous one completes.

CLOSE means "close this mission/child objective" (scope CHILD_OBJECTIVE),
never "close the entire owner strategic goal".  One-shot goals (single
bounded report) still terminate normally; only standing directives spawn
follow-ups.  Classification derives from durable intent structure
(deliverable scope, continuation semantics, completion criteria, mandate
type, explicit standing-goal metadata), not crude keywords.

Idempotency: one active child per standing directive at a time.  Replay or
restart returns the existing active child; no duplicate child or mission.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

STANDING_FOLLOWUP_KIND = "STANDING_FOLLOWUP"
STANDING_FOLLOWUP_REASON_CODE = "STANDING_FOLLOWUP_REQUIRED"

_ACTIVE_CHILD_STATUSES = frozenset({"ACTIVE", "PLANNING", "REGRESSED", "BLOCKED", "WAITING"})
_TERMINAL_CHILD_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "SUSPENDED"})


def _normalised(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def standing_followup_id(directive_id: str, generation: int) -> str:
    return f"owner-goal:{directive_id}:followup:{int(generation):02d}"


def list_directive_objectives(state: Any, directive_id: str) -> list[Any]:
    out: list[Any] = []
    for objective in getattr(state, "objectives", {}).values():
        try:
            if str((objective.provenance or {}).get("directive_id") or "") == directive_id:
                out.append(objective)
        except Exception:
            continue
    out.sort(key=lambda row: (str(getattr(row, "created_at", "")), str(getattr(row, "objective_id", ""))))
    return out


def active_directive_children(state: Any, directive_id: str) -> list[Any]:
    return [
        row for row in list_directive_objectives(state, directive_id)
        if str(getattr(row, "status", "")) not in _TERMINAL_CHILD_STATUSES
    ]


def completed_directive_children(state: Any, directive_id: str) -> list[Any]:
    return [
        row for row in list_directive_objectives(state, directive_id)
        if str(getattr(row, "status", "")) == "COMPLETED"
    ]


def directive_record(database: Any, directive_id: str) -> Any | None:
    try:
        from ..owner_directives import project_owner_directives
    except Exception:
        return None
    try:
        for record in project_owner_directives(database):
            if str(getattr(record, "directive_id", "")) == directive_id:
                return record
    except Exception:
        return None
    return None


def directive_is_active(database: Any, directive_id: str) -> bool:
    record = directive_record(database, directive_id)
    if record is None:
        return False
    try:
        if str(getattr(record, "state", "")) != "ACTIVE":
            return False
    except Exception:
        return False
    try:
        with database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT active FROM directives WHERE directive_id = ?",
                (directive_id,),
            ).fetchone()
            if row is None or int(row["active"]) != 1:
                return False
    except Exception:
        return False
    return True


def directive_is_standing(database: Any, directive_id: str) -> bool:
    """True when the directive carries standing strategic authority."""
    record = directive_record(database, directive_id)
    if record is None:
        return False
    text = str(getattr(record, "exact_text", "") or "")
    metadata: Mapping[str, Any] = {}
    try:
        metadata = dict(getattr(record, "metadata", {}) or {})
    except Exception:
        metadata = {}
    try:
        from .standing_intent import standing_directive_kind
    except Exception:
        return False
    try:
        return standing_directive_kind(text, metadata=metadata) == "STANDING"
    except Exception:
        return False


def owner_controls_block(database: Any, directive_id: str, objective_id: str = "") -> tuple[str, str] | None:
    """Return (code, reason) when owner STOP/veto blocks continuation."""
    try:
        from ..control.broker import emergency_stop_reason, matching_veto_reason
    except Exception:
        return None
    try:
        stop = emergency_stop_reason(database)
    except Exception:
        stop = None
    if stop:
        return ("STOP_ACTIVE", f"owner STOP active: {stop}")
    scopes: set[str] = {"GLOBAL", "executive", "objectives", directive_id}
    if objective_id:
        scopes.add(objective_id)
    try:
        veto = matching_veto_reason(database, scopes)
    except Exception:
        veto = None
    if veto:
        return ("VETO_ACTIVE", f"owner veto active: {veto}")
    return None


def standing_satisfied_explicitly(database: Any, directive_id: str) -> bool:
    record = directive_record(database, directive_id)
    if record is None:
        return False
    try:
        metadata = dict(getattr(record, "metadata", {}) or {})
    except Exception:
        return False
    for key in ("standing_satisfied", "standing_complete", "strategic_satisfied"):
        if key in metadata and bool(metadata[key]):
            return True
    return False


def inspect_resource_state(database: Any, resource_id: str) -> dict[str, Any]:
    """Read-only inspection of the registered resource for follow-up grounding.

    Never raises: returns a summary with current evidence (branch, head,
    status count, outcome files) or an UNAVAILABLE reason.  Current evidence
    wins over prior observations.
    """
    summary: dict[str, Any] = {"resource_id": str(resource_id or ""), "status": "UNKNOWN"}
    if not resource_id:
        summary["status"] = "UNAVAILABLE"
        summary["reason"] = "no resource bound"
        return summary
    try:
        row = database.row("resources", "resource_id", resource_id)
    except Exception as error:
        summary["status"] = "UNAVAILABLE"
        summary["reason"] = f"resource lookup failed: {type(error).__name__}"
        return summary
    if row is None:
        summary["status"] = "UNAVAILABLE"
        summary["reason"] = "resource is not registered"
        return summary
    try:
        summary["readiness_state"] = str(row.get("readiness_state") or "")
        summary["resolved_root"] = str(row.get("resolved_root") or row.get("root_path") or "")
    except Exception:
        pass
    # Baseline evidence (durable, not filesystem-dependent).
    try:
        import json as _json

        baseline_raw = row.get("baseline_json") if isinstance(row, dict) else None
        if baseline_raw:
            baseline = _json.loads(str(baseline_raw)) if isinstance(baseline_raw, str) else dict(baseline_raw)
            git = baseline.get("git") if isinstance(baseline, dict) else None
            if isinstance(git, dict):
                summary["baseline_branch"] = str(git.get("branch") or "")
                summary["baseline_head"] = str(git.get("head") or "")[:12]
    except Exception:
        pass
    # Live filesystem inspection (read-only, best-effort).
    try:
        root = str(summary.get("resolved_root") or "")
        if root:
            candidate = Path(root)
            if candidate.exists():
                summary["root_exists"] = True
                # Git head/branch via filesystem (best-effort, no subprocess).
                try:
                    head_file = candidate / ".git" / "HEAD"
                    if head_file.exists():
                        ref = head_file.read_text(encoding="utf-8", errors="replace").strip()
                        if ref.startswith("ref:"):
                            summary["live_branch"] = ref.split("/")[-1][:80]
                        else:
                            summary["live_head"] = ref[:12]
                    # Count uncommitted status via porcelain-like check: list
                    # .jarvis outcome files and recently modified markers.
                    jarvis_dir = candidate / ".jarvis" / "owner-goals"
                    if jarvis_dir.exists():
                        outcome_files = list(jarvis_dir.rglob("outcome.md"))
                        summary["outcome_file_count"] = len(outcome_files)
                    # Frontend/backend presence as current-evidence signals.
                    summary["has_frontend"] = (candidate / "frontend").exists()
                    summary["has_backend"] = (candidate / "backend").exists()
                    summary["has_docs"] = (candidate / "docs").exists() or (candidate / "DOCKER.md").exists()
                    summary["status"] = "AVAILABLE"
                except Exception as error:
                    summary["status"] = "AVAILABLE"
                    summary["inspection_note"] = f"partial inspection: {type(error).__name__}"
            else:
                summary["root_exists"] = False
                summary["status"] = "UNAVAILABLE"
                summary["reason"] = "registered root does not exist"
    except Exception as error:
        summary["status"] = summary.get("status", "UNKNOWN")
        summary["reason"] = f"inspection failed: {type(error).__name__}"
    return summary


def derive_standing_followup_scope(
    database: Any,
    directive_id: str,
    *,
    prior_child: Any | None = None,
    resource_id: str | None = None,
) -> dict[str, Any] | None:
    """Derive the next bounded child scope from current world/project state.

    Returns None when no valuable executable work exists (caller must record
    a justified WAIT with a real revisit condition instead of creating work).
    Never hard-codes a candidate: the scope references current inspection
    evidence and the prior outcome, and the worker inspects live state to
    choose the concrete improvement.
    """
    record = directive_record(database, directive_id)
    if record is None:
        return None
    strategic_text = str(getattr(record, "exact_text", "") or "")
    short_goal = " ".join(strategic_text.split())[:140]
    # Resolve resource: explicit override, else prior child resource, else
    # directive target when it names a registered resource.
    resolved_resource = str(resource_id or "")
    if not resolved_resource and prior_child is not None:
        try:
            resolved_resource = str(getattr(prior_child, "resource_id", "") or "")
        except Exception:
            resolved_resource = ""
    if not resolved_resource:
        try:
            target = str(getattr(record, "target", "") or "").strip()
            if target and target != "GLOBAL":
                with database.connect(readonly=True) as connection:
                    hit = connection.execute(
                        "SELECT resource_id FROM resources WHERE resource_id = ?",
                        (target,),
                    ).fetchone()
                    if hit is not None:
                        resolved_resource = target
        except Exception:
            pass
    # Fall back to the most recently completed same-directive mission's
    # resource (covers standing children that bound their resource at execution).
    if not resolved_resource and prior_child is not None:
        try:
            missions = list(getattr(prior_child, "mission_ids", ()) or ())
            if missions:
                row = database.row("missions", "mission_id", missions[-1])
                if row is not None:
                    resolved_resource = str(row.get("resource_id") or "")
        except Exception:
            pass
    if not resolved_resource:
        return None
    inspection = inspect_resource_state(database, resolved_resource)
    if inspection.get("status") == "UNAVAILABLE":
        return None
    # Readiness gate: only READY resources yield executable follow-ups here.
    # PROBING resources are owned by the readiness-refresh path, not by
    # standing continuation.
    readiness = str(inspection.get("readiness_state") or "")
    if readiness and readiness != "READY":
        return None
    # Prior outcome summary (learning, not hard-coded scope).
    prior_id = ""
    prior_outcome = ""
    try:
        if prior_child is not None:
            prior_id = str(getattr(prior_child, "objective_id", "") or "")
            criteria = list(getattr(prior_child, "success_criteria", ()) or ())
            if criteria:
                prior_outcome = "; ".join(str(value)[:120] for value in criteria[:2])[:240]
    except Exception:
        pass
    # Generation is count-based (stable per directive, not per inspection).
    try:
        from .autonomy import rebuild_executive_state as _rebuild
        state = _rebuild(database)
        generation = len(list_directive_objectives(state, directive_id)) + 1
    except Exception:
        generation = 2
    generation = max(2, int(generation))
    branch = str(inspection.get("live_branch") or inspection.get("baseline_branch") or "")
    head = str(inspection.get("live_head") or inspection.get("baseline_head") or "")
    outcome_count = inspection.get("outcome_file_count", "")
    evidence_bits: list[str] = []
    if branch:
        evidence_bits.append(f"branch {branch}")
    if head:
        evidence_bits.append(f"head {head}")
    if outcome_count != "":
        evidence_bits.append(f"{outcome_count} prior outcome file(s)")
    if inspection.get("has_frontend"):
        evidence_bits.append("frontend present")
    if inspection.get("has_backend"):
        evidence_bits.append("backend present")
    evidence = ", ".join(evidence_bits)[:280] or f"resource {resolved_resource}"
    objective_text = (
        f"Standing follow-up {generation} for strategic goal: inspect current "
        f"resource {resolved_resource} ({evidence}) and implement the next highest-value "
        f"bounded operational improvement toward '{short_goal}'. Prior completed child "
        f"{prior_id or 'none'} outcome: {prior_outcome or 'verified'}. Research when necessary, "
        f"implement and independently verify one bounded slice, record outcome evidence, "
        f"and report the next candidate. Do not expose confidential information or modify "
        f"production systems."
    )
    success_criteria = (
        f"Follow-up generation {generation} bounded improvement is implemented in {resolved_resource} with file changes",
        f"Independent verification PASS with artifact hashes recorded for generation {generation}",
        f"Outcome evidence file exists for generation {generation} and agrees with direct inspection",
    )
    return {
        "objective_text": objective_text,
        "success_criteria": success_criteria,
        "resource_id": resolved_resource,
        "generation": generation,
        "inspection": inspection,
        "prior_child_id": prior_id,
        "rationale": (
            f"standing directive {directive_id} has a completed child and no active child; "
            f"current resource evidence ({evidence}) supports another bounded improvement"
        ),
    }


def standing_followup_needed(
    database: Any,
    directive_id: str,
    *,
    state: Any | None = None,
) -> tuple[bool, str]:
    """Decide whether a standing directive needs a follow-up child now.

    Returns (needed, reason).  False reasons distinguish owner withdrawal,
    genuine satisfaction, active child present, one-shot shape, and missing
    resource so callers record truthful WAITs instead of silent dead ends.
    """
    if not directive_is_active(database, directive_id):
        return False, "owner directive is not ACTIVE"
    if not directive_is_standing(database, directive_id):
        return False, "directive is one-shot (no standing continuation semantics)"
    if standing_satisfied_explicitly(database, directive_id):
        return False, "strategic goal explicitly marked satisfied"
    try:
        from .autonomy import rebuild_executive_state as _rebuild
        current = state if state is not None else _rebuild(database)
    except Exception as error:
        return False, f"executive state unavailable: {type(error).__name__}"
    children = list_directive_objectives(current, directive_id)
    if not children:
        return False, "no prior child objective for standing directive"
    active = [row for row in children if str(getattr(row, "status", "")) not in _TERMINAL_CHILD_STATUSES]
    if active:
        return False, f"active child {active[0].objective_id} already exists"
    completed = [row for row in children if str(getattr(row, "status", "")) == "COMPLETED"]
    if not completed:
        return False, "no completed child to continue from (standing needs prior COMPLETE evidence)"
    # Owner withdrawal/supersession/veto/STOP blocks continuation.
    prior_id = str(getattr(completed[-1], "objective_id", "") or "")
    blocked = owner_controls_block(database, directive_id, prior_id)
    if blocked is not None:
        return False, f"owner control blocks continuation: {blocked[0]}: {blocked[1]}"
    return True, f"standing directive has completed child {completed[-1].objective_id} and no active child"


def ensure_standing_followup(
    database: Any,
    directive_id: str,
    *,
    controller: str = "supervisor",
    capability: str = "write_registered_worktree_files",
    state: Any | None = None,
) -> Any | None:
    """Create the next bounded child for a standing directive when needed.

    Idempotent: returns the existing active child when one exists, creates
    exactly one follow-up per generation, and returns None with a durable
    WAIT wake when no valuable work exists or controls block.  Completed
    children are never reopened or mutated.
    """
    if controller != "supervisor":
        raise PermissionError("only the Supervisor can create standing follow-ups")
    # Idempotency first: an existing active child satisfies replay/restart
    # without creating a duplicate, even though no new child is needed.
    try:
        from .autonomy import rebuild_executive_state as _rebuild
        _current = state if state is not None else _rebuild(database)
        _active = active_directive_children(_current, directive_id)
        if _active:
            return _active[0]
    except Exception:
        pass
    needed, reason = standing_followup_needed(database, directive_id, state=state)
    if not needed:
        return None
    try:
        from .autonomy import rebuild_executive_state as _rebuild
        current = state if state is not None else _rebuild(database)
    except Exception:
        return None
    # Re-check active child under current state (idempotency across replay).
    active = active_directive_children(current, directive_id)
    if active:
        return active[0]
    completed = completed_directive_children(current, directive_id)
    prior = completed[-1] if completed else None
    scope = derive_standing_followup_scope(database, directive_id, prior_child=prior)
    if scope is None:
        # No valuable executable work right now: record a justified WAIT with
        # a real revisit condition instead of a silent dead end.
        try:
            from .continuous import schedule_wait_for_signal as _wait_signal
            signal = f"standing-followup:{directive_id}"
            wake = _wait_signal(
                database,
                f"owner-goal:{directive_id}",
                signal=signal,
                reason=(
                    f"standing directive {directive_id} has no executable follow-up now "
                    f"({reason}); revisit on new project evidence, world signal, or owner direction"
                ),
                controller=controller,
            )
            try:
                from ..events import Event as _Event
                database.append(_Event.create(
                    "executive.standing_followup_waiting",
                    {
                        "directive_id": directive_id,
                        "reason": f"no executable follow-up: {reason}",
                        "wake_id": str(wake.get("wake_id") or ""),
                        "signal": signal,
                    },
                    source_type="supervisor",
                    source_id="v8-executive-autonomy",
                    correlation_id=f"owner-goal:{directive_id}",
                    idempotency_key=f"executive.standing_followup_waiting:{directive_id}:{signal}",
                ))
            except Exception:
                pass
        except Exception:
            pass
        return None
    generation = int(scope.get("generation") or 2)
    followup_id = standing_followup_id(directive_id, generation)
    # Another worker may have created this generation concurrently: return it.
    try:
        existing = current.objectives.get(followup_id)
    except Exception:
        existing = None
    if existing is not None:
        return existing
    # Owner authority for the new child derives from the standing directive.
    try:
        record = directive_record(database, directive_id)
        owner_authority = str(getattr(record, "source_id", "") or getattr(record, "owner_id", "") or "owner") if record is not None else "owner"
        event_id = str(getattr(record, "event_id", "") or "") if record is not None else ""
        correlation_id = str(getattr(record, "correlation_id", "") or "") if record is not None else ""
        nonce = str(getattr(record, "nonce", "") or "") if record is not None else ""
        authenticated = bool(getattr(record, "authenticated", True)) if record is not None else True
    except Exception:
        owner_authority, event_id, correlation_id, nonce, authenticated = "owner", "", "", "", True
    try:
        from .autonomy import create_objective as _create
    except Exception:
        return None
    try:
        created = _create(
            database,
            str(scope["objective_text"]),
            objective_id=followup_id,
            controller=controller,
            priority=1.0,
            urgency=0.0,
            success_criteria=tuple(scope["success_criteria"]),
            resource_id=str(scope["resource_id"]),
            required_capabilities=(capability,),
            owner_authority=owner_authority,
            provenance={
                "directive_id": directive_id,
                "event_id": event_id,
                "correlation_id": correlation_id,
                "nonce": nonce,
                "authenticated": authenticated,
                "standing_followup": True,
                "standing_generation": generation,
                "prior_child_id": str(scope.get("prior_child_id") or ""),
            },
            metadata={
                "standing_goal": True,
                "standing_followup": True,
                "standing_generation": generation,
                "prior_child_id": str(scope.get("prior_child_id") or ""),
                "command_type": "DELEGATE",
                "target": "GLOBAL",
            },
            source_id="v8-standing-continuation",
        )
    except ValueError:
        # Same ID already names this follow-up (replay/restart): return it.
        try:
            from .autonomy import rebuild_executive_state as _rebuild2
            return _rebuild2(database).objectives.get(followup_id)
        except Exception:
            return None
    # Durable creation receipt with current-evidence rationale.
    try:
        from ..events import Event as _Event2
        database.append(_Event2.create(
            "executive.standing_followup_created",
            {
                "directive_id": directive_id,
                "objective_id": followup_id,
                "generation": generation,
                "prior_child_id": str(scope.get("prior_child_id") or ""),
                "resource_id": str(scope.get("resource_id") or ""),
                "rationale": str(scope.get("rationale") or reason),
                "inspection": dict(scope.get("inspection") or {}),
            },
            source_type="supervisor",
            source_id="v8-standing-continuation",
            correlation_id=followup_id,
            idempotency_key=f"executive.standing_followup_created:{followup_id}",
        ))
    except Exception:
        pass
    return created


def ensure_all_standing_followups(
    database: Any,
    *,
    controller: str = "supervisor",
    capability: str = "write_registered_worktree_files",
    state: Any | None = None,
) -> list[Any]:
    """Create follow-ups for every standing directive that needs one now.

    Bounded and failure-isolated per directive: one directive's inspection
    failure never blocks another's continuation.  Returns created/active
    children (newly created follow-ups only; existing active children are
    left to ranking and are not returned here to avoid double-planning).
    """
    try:
        from .autonomy import rebuild_executive_state as _rebuild
        current = state if state is not None else _rebuild(database)
    except Exception:
        return []
    # Candidate directives: every provenance directive with at least one
    # COMPLETED child.  Standing check is per-directive and cheap.
    directive_ids: list[str] = []
    try:
        for objective in current.objectives.values():
            try:
                did = str((objective.provenance or {}).get("directive_id") or "")
            except Exception:
                continue
            if did and did not in directive_ids:
                directive_ids.append(did)
    except Exception:
        return []
    created: list[Any] = []
    for directive_id in sorted(directive_ids):
        try:
            needed, _ = standing_followup_needed(database, directive_id, state=current)
        except Exception:
            continue
        if not needed:
            continue
        try:
            child = ensure_standing_followup(
                database, directive_id, controller=controller,
                capability=capability, state=None,
            )
        except Exception:
            continue
        if child is not None:
            # Refresh state so later directives see earlier creations.
            try:
                from .autonomy import rebuild_executive_state as _rebuild3
                current = _rebuild3(database)
            except Exception:
                pass
            # Only report newly created follow-ups (provenance-marked).
            try:
                if bool((child.provenance or {}).get("standing_followup")):
                    created.append(child)
            except Exception:
                pass
    return created


__all__ = [
    "STANDING_FOLLOWUP_KIND",
    "STANDING_FOLLOWUP_REASON_CODE",
    "standing_followup_id",
    "list_directive_objectives",
    "active_directive_children",
    "completed_directive_children",
    "directive_is_active",
    "directive_is_standing",
    "standing_followup_needed",
    "derive_standing_followup_scope",
    "ensure_standing_followup",
    "ensure_all_standing_followups",
    "inspect_resource_state",
]
