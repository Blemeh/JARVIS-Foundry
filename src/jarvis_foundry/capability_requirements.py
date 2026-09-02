"""Read-only V11 candidate-to-Fabric capability requirement bridge.

The Capability Fabric Catalog remains the only readiness authority.  This
module infers a bounded requirement plan from structured JARVIS metadata and
projects that plan over a Catalog snapshot.  It never registers, acquires,
dispatches, or self-expands a capability.

External candidate prose and generic evidence are deliberately excluded from
inference.  A source adapter may attach a reserved, system-created context
record with :func:`trusted_requirement_context`; callers may also pass trusted
project/target/action metadata explicitly.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

from .clock import isoformat
from .database import FoundryDatabase
from .events import canonical_json
from .fabric.catalog import catalog_snapshot


CONTRACT_VERSION = "jarvis.v11.capability-requirements.v1"
INFERENCE_VERSION = "jarvis.v11.capability-inference.v1"
TRUSTED_CONTEXT_VERSION = "jarvis.v11.capability-requirement-context.v1"
TRUSTED_CONTEXT_RECORD = "jarvis.capability_requirement_context"
TRUSTED_CONTEXT_AUTHORITY = "JARVIS_SYSTEM_METADATA"
FABRIC_PATH = "src/jarvis_foundry/fabric/catalog.py"
FABRIC_AUTHORITY = "CAPABILITY_FABRIC_CATALOG"


class CapabilityRequirementError(ValueError):
    """Structured requirement input is invalid or unsafe to interpret."""


class RequirementReadiness(StrEnum):
    READY = "READY"
    PARTIALLY_READY = "PARTIALLY_READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class CapabilityDependency:
    capability_id: str
    depends_on: str
    declared_requirement: str

    def as_dict(self) -> dict[str, str]:
        return {
            "capability_id": self.capability_id,
            "depends_on": self.depends_on,
            "declared_requirement": self.declared_requirement,
        }


@dataclass(frozen=True, slots=True)
class CapabilityReadinessEvidence:
    capability_id: str
    requirement_kind: str
    state: str
    unattended: str
    usable: bool
    blocker: str | None
    version: str | None = None
    kind: str | None = None
    action_kind: str | None = None
    semantic: str | None = None
    effect_class: str | None = None
    declared_requirements: tuple[str, ...] = ()
    provider_dependencies: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    last_verified_at: str | None = None
    last_outcome: str | None = None
    claimed_vs_verified: str = "UNKNOWN"
    consecutive_failures: int = 0
    note: str = ""
    recovery: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "requirement_kind": self.requirement_kind,
            "state": self.state,
            "unattended": self.unattended,
            "usable": self.usable,
            "blocker": self.blocker,
            "version": self.version,
            "kind": self.kind,
            "action_kind": self.action_kind,
            "semantic": self.semantic,
            "effect_class": self.effect_class,
            "declared_requirements": list(self.declared_requirements),
            "provider_dependencies": list(self.provider_dependencies),
            "limitations": list(self.limitations),
            "last_verified_at": self.last_verified_at,
            "last_outcome": self.last_outcome,
            "claimed_vs_verified": self.claimed_vs_verified,
            "consecutive_failures": self.consecutive_failures,
            "note": self.note,
            "recovery": dict(self.recovery),
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class CapabilityRequirementSet:
    """One stable requirement identity with a refreshable Catalog view."""

    requirement_set_id: str
    snapshot_id: str
    candidate_identity: Mapping[str, Any]
    required_capability_ids: tuple[str, ...]
    optional_capability_ids: tuple[str, ...]
    readiness: RequirementReadiness
    executable: bool
    decision: str
    recommended_disposition: str
    missing_capabilities: tuple[str, ...]
    blocked_capabilities: tuple[str, ...]
    optional_unavailable: tuple[str, ...]
    unresolved_actions: tuple[str, ...]
    dependencies: tuple[CapabilityDependency, ...]
    capability_evidence: tuple[CapabilityReadinessEvidence, ...]
    blockers: tuple[Mapping[str, Any], ...]
    action_classes: Mapping[str, str]
    reversibility: Mapping[str, Mapping[str, Any]]
    evidence: tuple[Mapping[str, Any], ...]
    provenance: Mapping[str, Any]
    requirement_plan: Mapping[str, Any]
    catalog_digest: str
    evaluated_at: str
    refreshed_from_snapshot_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the versioned JSON-safe contract consumed by A2/A3."""

        return {
            "contract_version": CONTRACT_VERSION,
            "requirement_set_id": self.requirement_set_id,
            "snapshot_id": self.snapshot_id,
            "candidate_identity": dict(self.candidate_identity),
            "required_capability_ids": list(self.required_capability_ids),
            "optional_capability_ids": list(self.optional_capability_ids),
            "readiness": self.readiness.value,
            "executable": self.executable,
            "decision": self.decision,
            "recommended_disposition": self.recommended_disposition,
            "missing_capabilities": list(self.missing_capabilities),
            "blocked_capabilities": list(self.blocked_capabilities),
            "optional_unavailable": list(self.optional_unavailable),
            "unresolved_actions": list(self.unresolved_actions),
            "dependencies": [item.as_dict() for item in self.dependencies],
            "capability_evidence": [item.as_dict() for item in self.capability_evidence],
            "blockers": [dict(item) for item in self.blockers],
            "action_classes": dict(self.action_classes),
            "reversibility": {
                key: dict(value) for key, value in self.reversibility.items()
            },
            "evidence": [dict(item) for item in self.evidence],
            "provenance": dict(self.provenance),
            "requirement_plan": dict(self.requirement_plan),
            "catalog_digest": self.catalog_digest,
            "evaluated_at": self.evaluated_at,
            "refreshed_from_snapshot_id": self.refreshed_from_snapshot_id,
        }


@dataclass(frozen=True, slots=True)
class _CandidateRule:
    required_actions: tuple[str, ...]
    optional_actions: tuple[str, ...] = ()


_ACTION_ALIASES: dict[str, str] = {
    "browser.research": "browser.web.read",
    "browser/web research": "browser.web.read",
    "filesystem.read": "file.read",
    "filesystem.write": "file.write",
    "git.read": "git.inspect",
    "provider.github.issue.create": "github.issue.create",
    "provider.github.pull_request.create": "github.pull_request.create",
    "provider.gmail.send": "gmail.send",
    "test.run": "tests.run",
    "worker.code": "script.run",
    "worker.test": "tests.run",
}

_LOCAL_CODE_ACTIONS = (
    "file.read",
    "file.write",
    "git.inspect",
    "git.diff",
    "git.commit",
    "script.run",
    "tests.run",
)

_CANDIDATE_RULES: dict[str, _CandidateRule] = {
    "build": _CandidateRule(_LOCAL_CODE_ACTIONS),
    "local_code_change": _CandidateRule(_LOCAL_CODE_ACTIONS),
    "repair": _CandidateRule(_LOCAL_CODE_ACTIONS),
    "repository_improvement": _CandidateRule(_LOCAL_CODE_ACTIONS),
    "tracked_work": _CandidateRule(_LOCAL_CODE_ACTIONS),
    "github_issue": _CandidateRule(
        ("github.issue.read", *_LOCAL_CODE_ACTIONS),
    ),
    "github_workflow_failure": _CandidateRule(
        ("github.check.read", *_LOCAL_CODE_ACTIONS),
    ),
    "project_drift": _CandidateRule(("git.inspect", "git.diff")),
    "research": _CandidateRule(
        ("browser.web.read",), optional_actions=("http.doc.read",),
    ),
    "web_research": _CandidateRule(
        ("browser.web.read",), optional_actions=("http.doc.read",),
    ),
    "web_watch": _CandidateRule(
        ("browser.web.read",), optional_actions=("http.doc.read",),
    ),
    "world_signal": _CandidateRule(("system.diagnose",)),
    "github_issue_create": _CandidateRule(("github.issue.create",)),
    "github_pull_request_create": _CandidateRule(
        ("github.pull_request.create",),
    ),
    "gmail_send": _CandidateRule(("gmail.send",)),
    "calendar_create": _CandidateRule(("calendar.create",)),
}

_LOCAL_TARGET_KINDS = frozenset({
    "git_worktree", "local_repository", "project", "repository",
})
_WEB_TARGET_KINDS = frozenset({"browser", "browser_endpoint", "web"})
_MUTATING_OPERATIONS = frozenset({
    "build", "change", "code_change", "commit", "edit", "modify", "repair", "write",
})
_READ_OPERATIONS = frozenset({"inspect", "read", "research", "review"})


def _bounded_identity(value: Any, field_name: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 256 or any(char.isspace() for char in result):
        raise CapabilityRequirementError(
            f"{field_name} must be a non-empty bounded identity"
        )
    return result


def _identity_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise CapabilityRequirementError(f"{field_name} must be a list of identities")
    return tuple(sorted({
        _bounded_identity(item, f"{field_name} item") for item in values
    }))


def _json_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CapabilityRequirementError(f"{field_name} must be an object")
    try:
        normalized = json.loads(canonical_json(dict(value)))
    except (TypeError, ValueError) as error:
        raise CapabilityRequirementError(
            f"{field_name} must contain JSON-safe values"
        ) from error
    if not isinstance(normalized, dict):
        raise CapabilityRequirementError(f"{field_name} must be an object")
    return normalized


def _action_metadata(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, (str, Mapping)):
        values: Sequence[Any] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise CapabilityRequirementError("action_metadata must be a list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if isinstance(item, str):
            row: dict[str, Any] = {"action_kind": item}
        elif isinstance(item, Mapping):
            row = _json_mapping(item, f"action_metadata[{index}]")
        else:
            raise CapabilityRequirementError(
                f"action_metadata[{index}] must be a string or object"
            )
        action = str(row.get("action_kind") or "").strip()
        capability = str(row.get("capability_id") or "").strip()
        if not action and not capability:
            raise CapabilityRequirementError(
                f"action_metadata[{index}] requires action_kind or capability_id"
            )
        clean: dict[str, Any] = {"optional": bool(row.get("optional", False))}
        if action:
            clean["action_kind"] = _canonical_action(action)
        if capability:
            clean["capability_id"] = _bounded_identity(
                capability, f"action_metadata[{index}].capability_id"
            )
        normalized.append(clean)
    normalized.sort(key=lambda row: canonical_json(row))
    return tuple(normalized)


def trusted_requirement_context(
    *,
    required_capability_ids: Sequence[str] = (),
    optional_capability_ids: Sequence[str] = (),
    required_actions: Sequence[str] = (),
    optional_actions: Sequence[str] = (),
    proposed_target: Mapping[str, Any] | None = None,
    project_profile: Mapping[str, Any] | None = None,
    action_metadata: Sequence[Mapping[str, Any] | str] = (),
    source: str = "system",
) -> dict[str, Any]:
    """Build the reserved evidence record only system adapters may attach."""

    return {
        "record_type": TRUSTED_CONTEXT_RECORD,
        "contract_version": TRUSTED_CONTEXT_VERSION,
        "authority": TRUSTED_CONTEXT_AUTHORITY,
        "source": str(source or "system")[:120],
        "required_capability_ids": list(_identity_list(
            required_capability_ids, "required_capability_ids"
        )),
        "optional_capability_ids": list(_identity_list(
            optional_capability_ids, "optional_capability_ids"
        )),
        "required_actions": sorted({
            _canonical_action(item) for item in required_actions if str(item).strip()
        }),
        "optional_actions": sorted({
            _canonical_action(item) for item in optional_actions if str(item).strip()
        }),
        "proposed_target": _json_mapping(proposed_target, "proposed_target"),
        "project_profile": _json_mapping(project_profile, "project_profile"),
        "action_metadata": list(_action_metadata(action_metadata)),
    }


def _canonical_action(value: Any) -> str:
    action = _bounded_identity(value, "action_kind").lower()
    return _ACTION_ALIASES.get(action, action)


def _valid_context_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if (
        value.get("record_type") != TRUSTED_CONTEXT_RECORD
        or value.get("contract_version") != TRUSTED_CONTEXT_VERSION
        or value.get("authority") != TRUSTED_CONTEXT_AUTHORITY
    ):
        return None
    return trusted_requirement_context(
        required_capability_ids=value.get("required_capability_ids") or (),
        optional_capability_ids=value.get("optional_capability_ids") or (),
        required_actions=value.get("required_actions") or (),
        optional_actions=value.get("optional_actions") or (),
        proposed_target=value.get("proposed_target") or {},
        project_profile=value.get("project_profile") or {},
        action_metadata=value.get("action_metadata") or (),
        source=str(value.get("source") or "candidate-source-provenance"),
    )


def _stored_context(
    database: FoundryDatabase,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Load only system-projected source provenance, never evidence prose."""

    candidate_id = str(candidate.get("candidate_id") or "").strip()
    if not candidate_id:
        return trusted_requirement_context(source="none")
    try:
        with database.connect(readonly=True) as connection:
            rows = connection.execute(
                "SELECT provenance_json FROM candidate_sources "
                "WHERE candidate_id = ? ORDER BY rowid",
                (candidate_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        rows = ()
    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row[0] or "{}"))
        except (TypeError, ValueError):
            continue
        record = _valid_context_record(
            payload.get("capability_requirement_context")
            if isinstance(payload, Mapping) else None
        )
        if record is not None:
            records.append(record)
    unique = {
        canonical_json(record): record for record in records
    }
    if not unique:
        return trusted_requirement_context(source="none")

    required_ids: set[str] = set()
    optional_ids: set[str] = set()
    required_actions: set[str] = set()
    optional_actions: set[str] = set()
    action_metadata: dict[str, dict[str, Any]] = {}
    targets: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    sources: list[str] = []
    for key in sorted(unique):
        record = unique[key]
        required_ids.update(record.get("required_capability_ids") or ())
        optional_ids.update(record.get("optional_capability_ids") or ())
        required_actions.update(record.get("required_actions") or ())
        optional_actions.update(record.get("optional_actions") or ())
        for item in record.get("action_metadata") or ():
            action_metadata[canonical_json(item)] = dict(item)
        if record.get("proposed_target"):
            targets.append(dict(record["proposed_target"]))
        if record.get("project_profile"):
            profiles.append(dict(record["project_profile"]))
        sources.append(str(record.get("source") or "unknown"))

    def merge_records(items: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        merged: dict[str, Any] = {}
        conflicts: list[str] = []
        for item in items:
            for key, value in item.items():
                if value in (None, "", [], {}):
                    continue
                if key in merged and merged[key] != value:
                    conflicts.append(key)
                    merged.pop(key, None)
                    continue
                if key not in conflicts:
                    merged[key] = value
        return merged, conflicts

    target, target_conflicts = merge_records(targets)
    profile, profile_conflicts = merge_records(profiles)
    combined = trusted_requirement_context(
        required_capability_ids=sorted(required_ids),
        optional_capability_ids=sorted(optional_ids - required_ids),
        required_actions=sorted(required_actions),
        optional_actions=sorted(optional_actions - required_actions),
        proposed_target=target,
        project_profile=profile,
        action_metadata=tuple(action_metadata[key] for key in sorted(action_metadata)),
        source="candidate_sources:" + _digest(sorted(set(sources)))[7:23],
    )
    combined["context_conflicts"] = sorted({
        *(f"proposed_target.{key}" for key in target_conflicts),
        *(f"project_profile.{key}" for key in profile_conflicts),
    })
    return combined


def _catalog_index(
    database: FoundryDatabase,
    supplied: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    raw: Any = supplied if supplied is not None else catalog_snapshot(database)
    if isinstance(raw, Mapping) and "capabilities" in raw:
        rows = raw.get("capabilities") or ()
        return {
            str(row.get("spec", {}).get("capability_id")): {
                "spec": dict(row.get("spec") or {}),
                "metadata": dict(row.get("metadata") or {}),
                "readiness": dict(row.get("readiness") or {}),
            }
            for row in rows
            if isinstance(row, Mapping)
            and isinstance(row.get("spec"), Mapping)
            and row.get("spec", {}).get("capability_id")
        }
    if isinstance(raw, Mapping):
        result: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                continue
            spec = dict(value.get("spec") or {})
            capability_id = str(spec.get("capability_id") or key)
            if not capability_id:
                continue
            spec.setdefault("capability_id", capability_id)
            result[capability_id] = {
                "spec": spec,
                "metadata": dict(value.get("metadata") or {}),
                "readiness": dict(value.get("readiness") or {}),
            }
        return result
    raise CapabilityRequirementError("catalog_snapshot must be a mapping")


def _merge_context(
    embedded: Mapping[str, Any],
    *,
    proposed_target: Mapping[str, Any] | None,
    project_profile: Mapping[str, Any] | None,
    action_metadata: Sequence[Mapping[str, Any] | str],
) -> dict[str, Any]:
    target = dict(embedded.get("proposed_target") or {})
    target.update(_json_mapping(proposed_target, "proposed_target"))
    profile = dict(embedded.get("project_profile") or {})
    profile.update(_json_mapping(project_profile, "project_profile"))
    actions = list(_action_metadata(embedded.get("action_metadata") or ()))
    actions.extend(_action_metadata(action_metadata))
    unique_actions = {
        canonical_json(row): row for row in actions
    }
    result = trusted_requirement_context(
        required_capability_ids=embedded.get("required_capability_ids") or (),
        optional_capability_ids=embedded.get("optional_capability_ids") or (),
        required_actions=embedded.get("required_actions") or (),
        optional_actions=embedded.get("optional_actions") or (),
        proposed_target=target,
        project_profile=profile,
        action_metadata=tuple(unique_actions[key] for key in sorted(unique_actions)),
        source=str(embedded.get("source") or "system"),
    )
    result["context_conflicts"] = list(embedded.get("context_conflicts") or ())
    return result


def _candidate_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    fingerprint = str(candidate.get("fingerprint") or "").strip()
    if not candidate_id and not fingerprint:
        raise CapabilityRequirementError(
            "candidate_id or fingerprint is required for stable requirement identity"
        )
    return {
        "candidate_id": candidate_id or None,
        "fingerprint": fingerprint or None,
        "candidate_type": str(candidate.get("candidate_type") or "").strip().lower() or None,
        "resource_id": str(candidate.get("resource_id") or "").strip() or None,
    }


def _resource_profile(
    database: FoundryDatabase,
    candidate_identity: Mapping[str, Any],
) -> dict[str, Any]:
    resource_id = candidate_identity.get("resource_id")
    if not resource_id:
        return {}
    row = database.row("resources", "resource_id", str(resource_id))
    if row is None:
        return {"resource_id": str(resource_id), "registered": False}
    return {
        "resource_id": str(resource_id),
        "registered": True,
        "kind": row.get("kind"),
        "resolved_root": row.get("resolved_root"),
    }


def _target_actions(target: Mapping[str, Any]) -> tuple[set[str], set[str], list[str]]:
    required: set[str] = set()
    optional: set[str] = set()
    blockers: list[str] = []
    kind = str(target.get("kind") or target.get("target_kind") or "").strip().lower()
    operation = str(target.get("operation") or "").strip().lower()
    provider = str(target.get("provider") or "").strip().lower()
    for key, destination in (
        ("action_kind", required),
        ("required_actions", required),
        ("optional_actions", optional),
    ):
        value = target.get(key)
        if value is None:
            continue
        values = (value,) if isinstance(value, str) else value
        if isinstance(values, Sequence):
            destination.update(
                _canonical_action(item) for item in values if str(item).strip()
            )
    if kind in _LOCAL_TARGET_KINDS:
        if operation in _MUTATING_OPERATIONS:
            required.update(_LOCAL_CODE_ACTIONS)
        elif operation in _READ_OPERATIONS:
            required.update(("file.read", "git.inspect"))
    elif kind in _WEB_TARGET_KINDS and operation in _READ_OPERATIONS:
        required.add("browser.web.read")
    if provider == "github":
        if operation in {"issue.create", "issue_create", "create_issue"}:
            required.add("github.issue.create")
        elif operation in {
            "pull_request.create", "pull_request_create", "create_pull_request",
        }:
            required.add("github.pull_request.create")
    elif provider in {"gmail", "google_mail"} and operation in {
        "message.send", "send", "send_message",
    }:
        required.add("gmail.send")
    elif provider in {"calendar", "google_calendar"} and operation in {
        "create", "event.create", "event_create",
    }:
        required.add("calendar.create")
    if kind in {"provider", "provider_account"} and not provider:
        blockers.append("SPECIFIC_PROVIDER_REQUIRED")
    if kind in {"provider", "provider_account"} and provider and not (
        operation or target.get("action_kind") or target.get("required_actions")
    ):
        blockers.append("SPECIFIC_PROVIDER_ACTION_REQUIRED")
    return required, optional, blockers


def _profile_actions(profile: Mapping[str, Any]) -> tuple[set[str], set[str], list[str]]:
    target = {
        "kind": profile.get("kind") or profile.get("project_kind"),
        "operation": profile.get("operation") or profile.get("intended_operation"),
        "action_kind": profile.get("action_kind"),
        "required_actions": profile.get("required_actions"),
        "optional_actions": profile.get("optional_actions"),
    }
    required, optional, blockers = _target_actions(target)
    if profile.get("test_command") or profile.get("tests_available") is True:
        required.add("tests.run")
    if profile.get("requires_code_worker") is True:
        required.add("script.run")
    if profile.get("writable") is False and any(
        action in required for action in _LOCAL_CODE_ACTIONS
        if action in {"file.write", "git.commit"}
    ):
        blockers.append("PROJECT_PROFILE_NOT_WRITABLE")
    if profile.get("registered") is False:
        blockers.append("PROJECT_PROFILE_NOT_REGISTERED")
    return required, optional, blockers


def _inference_plan(
    candidate: Mapping[str, Any],
    candidate_identity: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    required_actions = set(
        _canonical_action(item) for item in (context.get("required_actions") or ())
    )
    optional_actions = set(
        _canonical_action(item) for item in (context.get("optional_actions") or ())
    )
    required_ids = set(_identity_list(
        context.get("required_capability_ids"), "required_capability_ids"
    ))
    optional_ids = set(_identity_list(
        context.get("optional_capability_ids"), "optional_capability_ids"
    ))
    blockers: list[str] = []
    evidence: list[dict[str, Any]] = []
    if context.get("context_conflicts"):
        blockers.append("AMBIGUOUS_TRUSTED_CONTEXT")
        evidence.append({
            "source": "candidate_source_context",
            "conflicts": list(context.get("context_conflicts") or ()),
        })

    candidate_type = str(candidate_identity.get("candidate_type") or "")
    rule = _CANDIDATE_RULES.get(candidate_type)
    if rule is not None:
        required_actions.update(rule.required_actions)
        optional_actions.update(rule.optional_actions)
        evidence.append({
            "source": "candidate.candidate_type",
            "value": candidate_type,
            "rule": INFERENCE_VERSION,
        })
    elif candidate_type == "provider_write":
        evidence.append({
            "source": "candidate.candidate_type",
            "value": candidate_type,
            "rule": "specific provider action required",
        })
        target_preview, _, _ = _target_actions(
            context.get("proposed_target") or {}
        )
        if not context.get("action_metadata") and not context.get("required_actions") \
                and not context.get("required_capability_ids") and not target_preview:
            blockers.append("SPECIFIC_PROVIDER_ACTION_REQUIRED")
    elif candidate_type:
        evidence.append({
            "source": "candidate.candidate_type",
            "value": candidate_type,
            "rule": "NO_REGISTERED_RULE",
        })

    target_required, target_optional, target_blockers = _target_actions(
        context.get("proposed_target") or {}
    )
    required_actions.update(target_required)
    optional_actions.update(target_optional)
    blockers.extend(target_blockers)
    if context.get("proposed_target"):
        evidence.append({
            "source": "proposed_target",
            "hash": _digest(context.get("proposed_target")),
        })

    profile_required, profile_optional, profile_blockers = _profile_actions(
        context.get("project_profile") or {}
    )
    required_actions.update(profile_required)
    optional_actions.update(profile_optional)
    blockers.extend(profile_blockers)
    if (
        (context.get("project_profile") or {}).get("writable") is False
        and {"file.write", "git.commit"} & required_actions
    ):
        blockers.append("PROJECT_PROFILE_NOT_WRITABLE")
    if context.get("project_profile"):
        evidence.append({
            "source": "project_profile",
            "hash": _digest(context.get("project_profile")),
        })

    for item in _action_metadata(context.get("action_metadata") or ()):
        destination_actions = optional_actions if item.get("optional") else required_actions
        destination_ids = optional_ids if item.get("optional") else required_ids
        if item.get("action_kind"):
            destination_actions.add(_canonical_action(item["action_kind"]))
        if item.get("capability_id"):
            destination_ids.add(str(item["capability_id"]))
    if context.get("action_metadata"):
        evidence.append({
            "source": "trusted_action_metadata",
            "hash": _digest(context.get("action_metadata")),
        })
    if context.get("required_capability_ids") or context.get("optional_capability_ids"):
        evidence.append({
            "source": "trusted_capability_metadata",
            "hash": _digest({
                "required": context.get("required_capability_ids") or (),
                "optional": context.get("optional_capability_ids") or (),
            }),
        })

    optional_actions.difference_update(required_actions)
    optional_ids.difference_update(required_ids)
    if not required_actions and not required_ids:
        blockers.append("NO_BOUNDED_CAPABILITY_REQUIREMENT")
    return {
        "inference_version": INFERENCE_VERSION,
        "candidate_identity": dict(candidate_identity),
        "required_actions": sorted(required_actions),
        "optional_actions": sorted(optional_actions),
        "required_capability_ids": sorted(required_ids),
        "optional_capability_ids": sorted(optional_ids),
        "context_blockers": sorted(set(blockers)),
        "evidence": evidence,
        "external_candidate_text_used": False,
        "generic_candidate_capability_claims_used": False,
    }


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def _action_candidates(
    index: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for capability_id, row in index.items():
        action = str((row.get("spec") or {}).get("action_kind") or "")
        if action:
            result.setdefault(action, []).append(capability_id)
    for values in result.values():
        values.sort()
    return result


def _usable(row: Mapping[str, Any], *, unattended: bool) -> bool:
    readiness = row.get("readiness") or {}
    spec = row.get("spec") or {}
    if str(readiness.get("state") or "UNKNOWN") != "READY":
        return False
    if unattended and str(spec.get("unattended") or "UNKNOWN") != "ALLOWED":
        return False
    return True


def _select_for_action(
    action: str,
    index: Mapping[str, Mapping[str, Any]],
    by_action: Mapping[str, Sequence[str]],
    *,
    unattended: bool,
) -> tuple[str, bool]:
    candidates = list(by_action.get(action) or ())
    if candidates:
        candidates.sort(key=lambda capability_id: (
            not _usable(index[capability_id], unattended=unattended),
            capability_id,
        ))
        return candidates[0], True
    # Capability IDs conventionally namespace the structured Fabric action.
    # This mechanical missing-ID projection is evidence of an absent Catalog
    # entry; it is not a registration or an alternative readiness source.
    return (action if action.startswith("cap.") else f"cap.{action}"), False


def _resolve_dependencies(
    direct_required: set[str],
    direct_optional: set[str],
    index: Mapping[str, Mapping[str, Any]],
    by_action: Mapping[str, Sequence[str]],
    *,
    unattended: bool,
) -> tuple[set[str], set[str], list[CapabilityDependency], set[str]]:
    required = set(direct_required)
    optional = set(direct_optional) - required
    unresolved: set[str] = set()
    edges: list[CapabilityDependency] = []
    queue: list[tuple[str, bool]] = [
        *((item, False) for item in sorted(required)),
        *((item, True) for item in sorted(optional)),
    ]
    seen: set[tuple[str, bool]] = set()
    while queue:
        parent, parent_optional = queue.pop(0)
        marker = (parent, parent_optional)
        if marker in seen:
            continue
        seen.add(marker)
        row = index.get(parent) or {}
        spec = row.get("spec") or {}
        metadata = row.get("metadata") or {}
        declared = list(spec.get("requirements") or ())
        for item in metadata.get("requirements") or ():
            if item not in declared:
                declared.append(item)
        for raw in declared:
            token = str(raw or "").strip()
            if not token:
                continue
            if token in index:
                dependency_id = token
            else:
                dependency_id, _ = _select_for_action(
                    _canonical_action(token), index, by_action,
                    unattended=unattended,
                )
            if not dependency_id:
                unresolved.add(token)
                continue
            edges.append(CapabilityDependency(parent, dependency_id, token))
            if parent_optional:
                if dependency_id not in required:
                    optional.add(dependency_id)
                    queue.append((dependency_id, True))
            else:
                required.add(dependency_id)
                optional.discard(dependency_id)
                queue.append((dependency_id, False))
    unique_edges = {
        (item.capability_id, item.depends_on, item.declared_requirement): item
        for item in edges
    }
    return (
        required,
        optional,
        [unique_edges[key] for key in sorted(unique_edges)],
        unresolved,
    )


def _capability_evidence(
    capability_id: str,
    requirement_kind: str,
    row: Mapping[str, Any] | None,
    *,
    unattended: bool,
) -> CapabilityReadinessEvidence:
    if row is None:
        return CapabilityReadinessEvidence(
            capability_id=capability_id,
            requirement_kind=requirement_kind,
            state="MISSING",
            unattended="UNKNOWN",
            usable=False,
            blocker="CAPABILITY_MISSING",
            note="capability is not registered in the Fabric Catalog",
            provenance={
                "authority": FABRIC_AUTHORITY,
                "source": "fabric.catalog.catalog_snapshot",
                "fabric_path": FABRIC_PATH,
            },
        )
    spec = row.get("spec") or {}
    metadata = row.get("metadata") or {}
    readiness = row.get("readiness") or {}
    state = str(readiness.get("state") or "UNKNOWN")
    unattended_state = str(spec.get("unattended") or "UNKNOWN")
    usable = state == "READY" and (
        not unattended or unattended_state == "ALLOWED"
    )
    blocker: str | None = None
    if state != "READY":
        blocker = f"CAPABILITY_{state}"
    elif unattended and unattended_state != "ALLOWED":
        blocker = (
            "INTERACTIVE_REQUIRED"
            if unattended_state == "INTERACTIVE_REQUIRED"
            else "UNATTENDED_READINESS_UNKNOWN"
        )
    provenance = dict(metadata.get("provenance") or {})
    provenance.update({
        "authority": FABRIC_AUTHORITY,
        "source": "fabric.catalog.catalog_snapshot",
        "fabric_path": FABRIC_PATH,
    })
    return CapabilityReadinessEvidence(
        capability_id=capability_id,
        requirement_kind=requirement_kind,
        state=state,
        unattended=unattended_state,
        usable=usable,
        blocker=blocker,
        version=str(spec.get("version")) if spec.get("version") is not None else None,
        kind=str(spec.get("kind")) if spec.get("kind") is not None else None,
        action_kind=str(spec.get("action_kind")) if spec.get("action_kind") is not None else None,
        semantic=str(spec.get("semantic")) if spec.get("semantic") is not None else None,
        effect_class=(
            str(metadata.get("effect_class"))
            if metadata.get("effect_class") is not None else None
        ),
        declared_requirements=tuple(str(item) for item in spec.get("requirements") or ()),
        provider_dependencies=tuple(str(item) for item in spec.get("provider_deps") or ()),
        limitations=tuple(str(item) for item in spec.get("limitations") or ()),
        last_verified_at=(
            str(readiness.get("last_verified_at"))
            if readiness.get("last_verified_at") is not None else None
        ),
        last_outcome=(
            str(readiness.get("last_outcome"))
            if readiness.get("last_outcome") is not None else None
        ),
        claimed_vs_verified=str(readiness.get("claimed_vs_verified") or "UNKNOWN"),
        consecutive_failures=int(readiness.get("consecutive_failures") or 0),
        note=str(readiness.get("note") or ""),
        recovery=dict(metadata.get("recovery") or {}),
        provenance=provenance,
    )


def _evaluate_plan(
    database: FoundryDatabase,
    plan: Mapping[str, Any],
    *,
    requirement_set_id: str,
    unattended: bool,
    supplied_catalog: Mapping[str, Any] | None = None,
    refreshed_from_snapshot_id: str | None = None,
) -> CapabilityRequirementSet:
    index = _catalog_index(database, supplied_catalog)
    by_action = _action_candidates(index)
    direct_required = set(str(item) for item in plan.get("required_capability_ids") or ())
    direct_optional = set(str(item) for item in plan.get("optional_capability_ids") or ())
    unresolved = set()
    for action in plan.get("required_actions") or ():
        selected, _registered = _select_for_action(
            str(action), index, by_action, unattended=unattended,
        )
        if selected:
            direct_required.add(selected)
        else:
            unresolved.add(str(action))
    for action in plan.get("optional_actions") or ():
        selected, _registered = _select_for_action(
            str(action), index, by_action, unattended=unattended,
        )
        if selected and selected not in direct_required:
            direct_optional.add(selected)

    required, optional, dependencies, dependency_unresolved = _resolve_dependencies(
        direct_required,
        direct_optional,
        index,
        by_action,
        unattended=unattended,
    )
    unresolved.update(dependency_unresolved)
    optional.difference_update(required)

    evidence_rows = tuple(
        _capability_evidence(
            capability_id,
            "REQUIRED" if capability_id in required else "OPTIONAL",
            index.get(capability_id),
            unattended=unattended,
        )
        for capability_id in sorted(required | optional)
    )
    required_evidence = [
        item for item in evidence_rows if item.requirement_kind == "REQUIRED"
    ]
    missing = tuple(sorted(
        item.capability_id for item in required_evidence if item.state == "MISSING"
    ))
    blocked = tuple(sorted(
        item.capability_id for item in required_evidence if not item.usable
    ))
    optional_unavailable = tuple(sorted(
        item.capability_id for item in evidence_rows
        if item.requirement_kind == "OPTIONAL" and not item.usable
    ))
    blockers: list[dict[str, Any]] = [
        {"code": code, "source": "structured_context"}
        for code in plan.get("context_blockers") or ()
    ]
    blockers.extend(
        {
            "code": item.blocker,
            "capability_id": item.capability_id,
            "state": item.state,
            "unattended": item.unattended,
            "evidence": {
                "last_verified_at": item.last_verified_at,
                "last_outcome": item.last_outcome,
                "claimed_vs_verified": item.claimed_vs_verified,
                "note": item.note,
            },
        }
        for item in required_evidence if not item.usable
    )
    blockers.extend(
        {"code": "UNRESOLVED_ACTION", "action_kind": action}
        for action in sorted(unresolved)
    )

    executable = bool(required_evidence) and not blockers
    unavailable_count = sum(not item.usable for item in required_evidence)
    ready_count = sum(item.usable for item in required_evidence)
    if executable:
        readiness = RequirementReadiness.READY
    elif plan.get("context_blockers") and not unavailable_count and not unresolved:
        readiness = RequirementReadiness.BLOCKED
    elif ready_count:
        readiness = RequirementReadiness.PARTIALLY_READY
    else:
        readiness = RequirementReadiness.BLOCKED
    decision = "EXECUTABLE" if executable else "BLOCKED"
    disposition = "READY_FOR_ADMISSION" if executable else "DEFER"

    catalog_material = [
        {
            "capability_id": item.capability_id,
            "state": item.state,
            "unattended": item.unattended,
            "usable": item.usable,
            "version": item.version,
            "effect_class": item.effect_class,
            "last_verified_at": item.last_verified_at,
            "last_outcome": item.last_outcome,
            "claimed_vs_verified": item.claimed_vs_verified,
            "consecutive_failures": item.consecutive_failures,
            "declared_requirements": list(item.declared_requirements),
        }
        for item in evidence_rows
    ]
    catalog_digest = _digest(catalog_material)
    snapshot_id = "capreq-snapshot-" + hashlib.sha256(canonical_json({
        "requirement_set_id": requirement_set_id,
        "catalog_digest": catalog_digest,
        "blockers": blockers,
        "required_capability_ids": sorted(required),
        "optional_capability_ids": sorted(optional),
    }).encode("utf-8")).hexdigest()[:24]
    action_classes = {
        item.capability_id: item.effect_class
        for item in evidence_rows if item.effect_class is not None
    }
    reversibility = {
        item.capability_id: dict(item.recovery)
        for item in evidence_rows if item.recovery
    }
    inference_evidence = tuple(
        dict(item) for item in plan.get("evidence") or ()
    )
    return CapabilityRequirementSet(
        requirement_set_id=requirement_set_id,
        snapshot_id=snapshot_id,
        candidate_identity=dict(plan.get("candidate_identity") or {}),
        required_capability_ids=tuple(sorted(required)),
        optional_capability_ids=tuple(sorted(optional)),
        readiness=readiness,
        executable=executable,
        decision=decision,
        recommended_disposition=disposition,
        missing_capabilities=missing,
        blocked_capabilities=blocked,
        optional_unavailable=optional_unavailable,
        unresolved_actions=tuple(sorted(unresolved)),
        dependencies=tuple(dependencies),
        capability_evidence=evidence_rows,
        blockers=tuple(blockers),
        action_classes=action_classes,
        reversibility=reversibility,
        evidence=inference_evidence,
        provenance={
            "authority": FABRIC_AUTHORITY,
            "fabric_path": FABRIC_PATH,
            "catalog_reader": "jarvis_foundry.fabric.catalog.catalog_snapshot",
            "requirement_inference": INFERENCE_VERSION,
            "external_source_text": "DATA_ONLY_NOT_USED",
            "legacy_registry": "NON_AUTHORITATIVE_NOT_READ",
            "self_expansion": "DISABLED_V11",
            "read_only": True,
        },
        requirement_plan=dict(plan),
        catalog_digest=catalog_digest,
        evaluated_at=isoformat(),
        refreshed_from_snapshot_id=refreshed_from_snapshot_id,
    )


def resolve_capability_requirements(
    database: FoundryDatabase,
    candidate: Mapping[str, Any],
    *,
    proposed_target: Mapping[str, Any] | None = None,
    project_profile: Mapping[str, Any] | None = None,
    action_metadata: Sequence[Mapping[str, Any] | str] = (),
    unattended: bool = True,
    catalog: Mapping[str, Any] | None = None,
) -> CapabilityRequirementSet:
    """Infer and evaluate one candidate without mutating any authority.

    ``candidate.objective`` and ordinary evidence fields such as
    ``required_capabilities`` are never inspected.  Only the candidate's
    controlled type/identity, a reserved system context record, registered
    project identity, and explicit trusted arguments participate.
    """

    if not isinstance(candidate, Mapping):
        raise CapabilityRequirementError("candidate must be an object")
    identity = _candidate_identity(candidate)
    embedded = _stored_context(database, candidate)
    registered_profile = _resource_profile(database, identity)
    merged_profile = dict(registered_profile)
    merged_profile.update(dict(embedded.get("project_profile") or {}))
    merged_profile.update(_json_mapping(project_profile, "project_profile"))
    context = _merge_context(
        embedded,
        proposed_target=proposed_target,
        project_profile=merged_profile,
        action_metadata=action_metadata,
    )
    plan = _inference_plan(candidate, identity, context)
    plan["unattended"] = bool(unattended)
    requirement_set_id = "capreq-" + hashlib.sha256(canonical_json({
        "contract_version": CONTRACT_VERSION,
        "plan": plan,
        "unattended": bool(unattended),
    }).encode("utf-8")).hexdigest()[:24]
    return _evaluate_plan(
        database,
        plan,
        requirement_set_id=requirement_set_id,
        unattended=bool(unattended),
        supplied_catalog=catalog,
    )


def refresh_capability_requirements(
    database: FoundryDatabase,
    requirement_set: CapabilityRequirementSet,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> CapabilityRequirementSet:
    """Re-evaluate the same stable plan after Fabric readiness changes."""

    if not isinstance(requirement_set, CapabilityRequirementSet):
        raise CapabilityRequirementError(
            "requirement_set must be a CapabilityRequirementSet"
        )
    unattended = bool(
        requirement_set.requirement_plan.get("unattended", True)
    )
    # Older in-memory instances created before the explicit plan flag default
    # to the V11 autonomous/unattended policy.
    return _evaluate_plan(
        database,
        requirement_set.requirement_plan,
        requirement_set_id=requirement_set.requirement_set_id,
        unattended=unattended,
        supplied_catalog=catalog,
        refreshed_from_snapshot_id=requirement_set.snapshot_id,
    )


__all__ = [
    "CapabilityDependency",
    "CapabilityReadinessEvidence",
    "CapabilityRequirementError",
    "CapabilityRequirementSet",
    "CONTRACT_VERSION",
    "FABRIC_AUTHORITY",
    "FABRIC_PATH",
    "INFERENCE_VERSION",
    "RequirementReadiness",
    "TRUSTED_CONTEXT_AUTHORITY",
    "TRUSTED_CONTEXT_RECORD",
    "TRUSTED_CONTEXT_VERSION",
    "refresh_capability_requirements",
    "resolve_capability_requirements",
    "trusted_requirement_context",
]
