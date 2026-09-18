"""Normalized mission intent -> capability/workflow resolution.

This module is a **binder**, not a planner.  The Executive (or an owner goal
captured upstream) decides *what* should happen; this module answers *how* the
existing Fabric can realize that decision:

    normalized mission intent (needs + supplied inputs)
    -> existing recipe registry / Fabric action catalog
    -> bound workflow envelope OR bounded action plan
    -> readiness check
    -> honest GAP when the runtime cannot realize the intent

It never registers, acquires, or dispatches anything.  The Capability Fabric
Catalog remains the only readiness authority and ``effects.py`` remains the
only effect journal.

Need vocabulary (the owner-level semantic requirements; browser/UIA/Git
internals must never appear at owner level):

    web.research_read            read a bounded web source
    web.authenticated_interaction  interact with an authenticated web source
    artifact.create_update       create/update a local artifact
    native.document_verify       open and verify a document natively
    desktop.app_operate          operate a local application
    spreadsheet.document_edit    edit a spreadsheet/document
    document.compose             compose a document deliverable
    spreadsheet.compose          compose a spreadsheet deliverable
    repository.preserve          preserve the finished result in a repository
    repository.publish           publish an already-committed worktree
    repository.inspect           read repository state
    artifact.stage_to_repository stage an artifact into a repository worktree
    note.bounded_write           bounded note write (proving/regression scope)
    compute.file_hash            compute a file hash
    reads.file_read              read a bounded file
    writes.file_write            write a bounded file
    system.diagnose              bounded local diagnosis

Input role vocabulary (supplied facts; recipes declare which role each
parameter binds to via ``bind``, defaulting to the parameter name):

    authenticated_source_url, browser_site_origin, browser_session_state_path,
    profile_id, owner_scope, credential_reference, allowed_hosts,
    allow_insecure_localhost, auth_marker_role, auth_marker_name,
    artifact_target_relative_path, artifact_title, native_process_id,
    native_application, owned_repository_root, repository_remote,
    repository_branch, commit_message, file_to_hash, file_target, file_content
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..clock import isoformat
from ..database import FoundryDatabase
from ..fabric.catalog import record_gap, specs_for_action
from ..fabric.recipes.builtins import builtin_recipes
from ..fabric.recipes.model import Recipe
from ..fabric.model import GapKind, GapRecord, GapStatus

INTENT_VERSION = "jarvis.purpose-recovery-ii-b.mission-intent.v1"
RESOLUTION_VERSION = "jarvis.purpose-recovery-ii-b.mission-resolution.v1"

WORKFLOW = "WORKFLOW"
ACTIONS = "ACTIONS"
GAP = "GAP"
BLOCKED = "BLOCKED"

NEED_WEB_RESEARCH = "web.research_read"
NEED_WEB_AUTHENTICATED = "web.authenticated_interaction"
NEED_ARTIFACT = "artifact.create_update"
NEED_NATIVE_VERIFY = "native.document_verify"
NEED_DESKTOP_APP = "desktop.app_operate"
NEED_SPREADSHEET_EDIT = "spreadsheet.document_edit"
NEED_DOCUMENT_COMPOSE = "document.compose"
NEED_SPREADSHEET_COMPOSE = "spreadsheet.compose"
NEED_REPOSITORY_PRESERVE = "repository.preserve"
NEED_REPOSITORY_PUBLISH = "repository.publish"
NEED_REPOSITORY_INSPECT = "repository.inspect"
NEED_ARTIFACT_STAGE = "artifact.stage_to_repository"
NEED_NOTE_WRITE = "note.bounded_write"
NEED_FILE_HASH = "compute.file_hash"
NEED_FILE_READ = "reads.file_read"
NEED_FILE_WRITE = "writes.file_write"
NEED_SYSTEM_DIAGNOSE = "system.diagnose"

NEED_VOCABULARY = frozenset({
    NEED_WEB_RESEARCH, NEED_WEB_AUTHENTICATED, NEED_ARTIFACT, NEED_NATIVE_VERIFY,
    NEED_DESKTOP_APP, NEED_SPREADSHEET_EDIT, NEED_DOCUMENT_COMPOSE,
    NEED_SPREADSHEET_COMPOSE, NEED_REPOSITORY_PRESERVE, NEED_REPOSITORY_PUBLISH,
    NEED_REPOSITORY_INSPECT, NEED_ARTIFACT_STAGE, NEED_NOTE_WRITE,
    NEED_FILE_HASH, NEED_FILE_READ, NEED_FILE_WRITE, NEED_SYSTEM_DIAGNOSE,
})

# Needs that a single existing Fabric action can realize on its own.  A need
# that is neither covered by a recipe nor declared here is an honest GAP.
_NEED_ACTIONS: dict[str, dict[str, Any]] = {
    NEED_FILE_HASH: {
        "action_kind": "hash.file.sha256",
        "payload": {"path": "$inputs.file_to_hash"},
    },
    NEED_FILE_READ: {
        "action_kind": "file.read",
        "payload": {"target": "$inputs.file_target"},
    },
    NEED_FILE_WRITE: {
        "action_kind": "file.write",
        "payload": {
            "target": "$inputs.file_target",
            "content": "$inputs.file_content",
        },
    },
    NEED_REPOSITORY_INSPECT: {
        "action_kind": "git.inspect",
        "payload": {"worktree": "$inputs.owned_repository_root"},
    },
    NEED_SYSTEM_DIAGNOSE: {
        "action_kind": "system.diagnose",
        "payload": {},
    },
}

_ACTIONS_MISSING = object()

# Declarative semantic needs per **observed candidate class**.  The Executive
# answers "what is worth doing"; this table answers "which normalized needs
# that work implies".  It is keyed by the candidate class the observation
# produced and lives in the vocabulary's own module, so there is no second
# registry, no owner-text keyword compiler, and no registry identifier at the
# Executive boundary.  A class absent from this table has no deterministic
# binding and admission must refuse it honestly.
CANDIDATE_NEEDS: dict[str, tuple[str, ...]] = {
    # Registered-project review / investigation: read the repository state.
    "project_staleness": (NEED_REPOSITORY_INSPECT,),
    "project_drift": (NEED_REPOSITORY_INSPECT,),
    # Bounded local diagnosis needs no supplied role facts.
    "world_signal": (NEED_SYSTEM_DIAGNOSE,),
}

# Classes whose useful work is a bounded repository change.  They are admitted
# (a hand-written allowlist must not auto-refuse discovered owner-repo work)
# but they carry no normalized intent yet: the change author is the model
# worker on the admitted-objective build path, so admitting them as a
# read-only review would silently downgrade what the observation means.  The
# intent binding for authored change is the software-engineering seam and is
# recorded as an open product gap, not faked here.
CANDIDATE_BUILD_CLASSES: frozenset[str] = frozenset({
    "tracked_work",
    "build",
    "local_code_change",
    "repair",
    "repository_improvement",
    "github_issue",
    "github_workflow_failure",
})

# Structured observation keys that name a file the observation is about.
_OBSERVATION_FILE_KEYS = ("file_target", "target_file", "marker_path", "source_file")


def needs_for_candidate_class(candidate_type: str) -> tuple[str, ...]:
    """Declared normalized needs for one observed candidate class."""
    return CANDIDATE_NEEDS.get(str(candidate_type or "").strip().lower(), ())


def observation_file_target(evidence_rows: Iterable[Mapping[str, Any]]) -> str:
    """The file an observation names, when it names one as structured data.

    Only an explicit relative path counts: a sentence that happens to mention a
    filename is not a binding input.
    """
    for row in evidence_rows:
        if not isinstance(row, Mapping):
            continue
        for key in _OBSERVATION_FILE_KEYS:
            raw = str(row.get(key) or "").strip().replace("\\", "/")
            if not raw or raw.startswith("/") or ":" in raw.split("/")[0]:
                continue
            parts = [part for part in raw.split("/") if part not in ("", ".")]
            if parts and ".." not in parts:
                return "/".join(parts)
    return ""


def intent_from_observation(
    *,
    candidate_id: str,
    candidate_type: str,
    objective: str,
    resource_id: str,
    objective_id: str = "",
    evidence_rows: Iterable[Mapping[str, Any]] = (),
    supplied_inputs: Mapping[str, Any] | None = None,
    success_criteria: Sequence[str] = (),
    constraints: Sequence[str] = (),
) -> MissionIntent:
    """Derive the normalized intent for one observed candidate.

    Needs come from the candidate class and from the observation's structured
    evidence; the caller supplies only runtime-owned facts as input roles.
    Neither a recipe id nor a registry-shaped parameter block enters here.

    Raises ``MissionIntentError`` when the observed class has no deterministic
    binding: that is an honest refusal, never a silent downgrade.
    """
    needs = list(needs_for_candidate_class(candidate_type))
    if not needs:
        raise MissionIntentError(
            f"candidate class {candidate_type or 'UNKNOWN'} has no deterministic "
            "need binding"
        )
    inputs = {str(key): value for key, value in dict(supplied_inputs or {}).items()}
    file_target = observation_file_target(evidence_rows)
    if NEED_REPOSITORY_INSPECT in needs and file_target:
        # The observation named the file it is about, so the review reads it.
        for need in (NEED_FILE_READ,):
            if need not in needs:
                needs.append(need)
        inputs.setdefault("file_target", file_target)
    return MissionIntent(
        intent_id=f"intent:{candidate_id}",
        objective=objective,
        resource_id=resource_id,
        needs=tuple(needs),
        inputs=inputs,
        success_criteria=tuple(success_criteria),
        constraints=tuple(constraints),
        evidence_refs=(f"candidate:{candidate_id}",),
        objective_id=objective_id,
        source="executive-observation",
    )


class MissionIntentError(ValueError):
    """Structured intent input is invalid or unsafe to interpret."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MissionIntentError(f"{field_name} must be a non-empty string")
    return value.strip()


def seed_builtins_catalog(database: FoundryDatabase) -> None:
    """Idempotently ensure the canonical builtin catalog exists for readiness."""
    from ..fabric.loop import seed_builtins

    seed_builtins(database)


def _text_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise MissionIntentError(f"{field_name} must be a list of strings")
    return tuple(_text(item, f"{field_name} item") for item in value)


@dataclass(frozen=True, slots=True)
class MissionIntent:
    """One normalized mission requirement.

    ``inputs`` carries supplied facts keyed by the role vocabulary; the
    resolution maps roles into the selected recipe's declared parameters.  A
    caller never supplies a recipe id or a recipe-shaped parameter block.
    """

    intent_id: str
    objective: str
    resource_id: str
    needs: tuple[str, ...]
    inputs: Mapping[str, Any] = field(default_factory=dict)
    success_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    unattended: bool = True
    evidence_refs: tuple[str, ...] = ()
    objective_id: str = ""
    source: str = "mission-intent"

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _text(self.intent_id, "intent_id"))
        object.__setattr__(self, "objective", _text(self.objective, "objective"))
        object.__setattr__(self, "resource_id", _text(self.resource_id, "resource_id"))
        needs = _text_tuple(self.needs, "needs")
        if not needs:
            raise MissionIntentError("intent requires at least one normalized need")
        object.__setattr__(self, "needs", needs)
        if not isinstance(self.inputs, Mapping):
            raise MissionIntentError("inputs must be an object")
        object.__setattr__(self, "inputs", {str(k): v for k, v in self.inputs.items()})
        object.__setattr__(self, "success_criteria", _text_tuple(self.success_criteria, "success_criteria"))
        object.__setattr__(self, "constraints", _text_tuple(self.constraints, "constraints"))
        if not isinstance(self.unattended, bool):
            raise MissionIntentError("unattended must be a boolean")
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "objective_id", str(self.objective_id or ""))
        object.__setattr__(self, "source", str(self.source or "mission-intent"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": INTENT_VERSION,
            "intent_id": self.intent_id,
            "objective": self.objective,
            "resource_id": self.resource_id,
            "needs": list(self.needs),
            "inputs": dict(self.inputs),
            "success_criteria": list(self.success_criteria),
            "constraints": list(self.constraints),
            "unattended": self.unattended,
            "evidence_refs": list(self.evidence_refs),
            "objective_id": self.objective_id,
            "source": self.source,
            "recorded_at": isoformat(),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, intent_id: str, resource_id: str,
                     objective: str, source: str = "mission-intent") -> "MissionIntent":
        """Build an intent from a normalized ``intent`` metadata block."""
        if not isinstance(payload, Mapping):
            raise MissionIntentError("intent metadata must be an object")
        return cls(
            intent_id=str(payload.get("intent_id") or intent_id),
            objective=str(payload.get("objective") or objective),
            resource_id=str(payload.get("resource_id") or resource_id),
            needs=tuple(payload.get("needs") or ()),
            inputs=payload.get("inputs") or {},
            success_criteria=tuple(payload.get("success_criteria") or ()),
            constraints=tuple(payload.get("constraints") or ()),
            unattended=bool(payload.get("unattended", True)),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
            objective_id=str(payload.get("objective_id") or ""),
            source=str(payload.get("source") or source),
        )


@dataclass(frozen=True, slots=True)
class MissionResolution:
    """Deterministic outcome of binding one intent to the existing Fabric."""

    intent_id: str
    kind: str
    reason: str
    recipe_id: str | None = None
    recipe_version: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    actions: tuple[Mapping[str, Any], ...] = ()
    gap: Mapping[str, Any] | None = None
    missing: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return self.kind in {WORKFLOW, ACTIONS}

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": RESOLUTION_VERSION,
            "intent_id": self.intent_id,
            "kind": self.kind,
            "reason": self.reason,
            "recipe_id": self.recipe_id,
            "recipe_version": self.recipe_version,
            "params": dict(self.params),
            "actions": [dict(item) for item in self.actions],
            "gap": dict(self.gap) if self.gap else None,
            "missing": list(self.missing),
            "provenance": dict(self.provenance),
        }


def _bind_params(
    recipe: Recipe, inputs: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Bind supplied role inputs into the recipe's declared parameters."""
    params: dict[str, Any] = {}
    missing: list[str] = []
    for param in recipe.parameters:
        role = (param.bind or param.name).strip() or param.name
        if role in inputs:
            params[param.name] = inputs[role]
            continue
        if param.has_default:
            continue
        if param.required:
            missing.append(role)
    return params, tuple(missing)


def _bind_actions(
    needs: tuple[str, ...], inputs: Mapping[str, Any],
) -> tuple[list[dict[str, Any]] | None, tuple[str, ...]]:
    """Expand simple needs into one bounded action plan; None when not simple."""
    actions: list[dict[str, Any]] = []
    missing: list[str] = []
    for need in needs:
        plan = _NEED_ACTIONS.get(need)
        if plan is None:
            return None, ()
        payload: dict[str, Any] = {}
        for key, value in dict(plan["payload"]).items():
            if isinstance(value, str) and value.startswith("$inputs."):
                role = value[len("$inputs."):]
                if role not in inputs:
                    missing.append(role)
                    continue
                payload[key] = inputs[role]
            else:
                payload[key] = value
        actions.append({"need": need, "action_kind": str(plan["action_kind"]), "payload": payload})
    return actions, tuple(missing)


def resolve_mission_intent(
    database: FoundryDatabase,
    intent: MissionIntent,
) -> MissionResolution:
    """Resolve one normalized intent against the existing Fabric registry.

    Deterministic: candidate workflows are matched by declared need coverage,
    ranked by the smallest extra-need footprint, then by recipe id.  A direct
    action plan is used only when every need maps to one declared action.
    Anything else is an honest GAP (capability absent) or BLOCKED (supplied
    inputs insufficient), never a silent rewrite of the requirement.
    """
    unknown = sorted(set(intent.needs) - NEED_VOCABULARY)
    if unknown:
        return MissionResolution(
            intent_id=intent.intent_id, kind=GAP,
            reason=f"unsupported intent needs: {', '.join(unknown)}",
            gap=record_gap(database, GapRecord(
                gap_id=f"gap.intent.{intent.intent_id}.unsupported",
                needed_action="intent.unsupported",
                missing=GapKind.TOOL,
                status=GapStatus.DETECTED,
                detail=f"intent declares needs outside the runtime vocabulary: {', '.join(unknown)}",
            )).as_dict(),
            missing=tuple(unknown),
            provenance={"stage": "vocabulary", "unknown_needs": unknown},
        )

    seed_builtins_catalog(database)
    wanted = set(intent.needs)
    candidates: list[tuple[int, str, Recipe, tuple[str, ...]]] = []
    missing_actions: dict[str, list[str]] = {}
    for recipe in builtin_recipes():
        declared = set(recipe.needs)
        if not wanted.issubset(declared):
            continue
        absent = tuple(
            action for action in recipe.required_capabilities
            if not specs_for_action(database, action)
        )
        if absent:
            missing_actions[recipe.recipe_id] = list(absent)
            continue
        candidates.append((len(declared - wanted), recipe.recipe_id, recipe, absent))
    candidates.sort(key=lambda item: (item[0], item[1]))

    if candidates:
        extra, recipe_id, recipe, _absent = candidates[0]
        params, missing_roles = _bind_params(recipe, intent.inputs)
        provenance = {
            "stage": "workflow",
            "considered": [item[1] for item in candidates],
            "selected_reason": f"declares every required need with {extra} extra need(s)",
            "missing_capability_candidates": missing_actions,
        }
        if missing_roles:
            return MissionResolution(
                intent_id=intent.intent_id, kind=BLOCKED,
                reason=(
                    f"workflow {recipe_id} requires supplied inputs that the intent "
                    f"does not carry: {', '.join(missing_roles)}"
                ),
                recipe_id=recipe_id, recipe_version=recipe.version,
                params=params, missing=missing_roles, provenance=provenance,
            )
        return MissionResolution(
            intent_id=intent.intent_id, kind=WORKFLOW,
            reason=f"workflow {recipe_id} selected for declared needs",
            recipe_id=recipe_id, recipe_version=recipe.version,
            params=params, provenance=provenance,
        )

    actions, missing_roles = _bind_actions(intent.needs, intent.inputs)
    if actions is not None:
        absent: list[str] = []
        for item in actions:
            specs = specs_for_action(database, str(item["action_kind"]))
            if not specs:
                absent.append(str(item["action_kind"]))
            else:
                item["capability_id"] = specs[0].capability_id
        provenance = {
            "stage": "actions",
            "missing_capability_candidates": missing_actions,
            "action_kinds": [str(item["action_kind"]) for item in actions],
        }
        if absent:
            action = absent[0]
            gap = record_gap(database, GapRecord(
                gap_id=f"gap.intent.{intent.intent_id}.{action.replace('.', '-')}",
                needed_action=action,
                missing=GapKind.TOOL,
                status=GapStatus.DETECTED,
                detail=(
                    f"intent {intent.intent_id} requires {action}; no registered "
                    "capability realizes it in this runtime"
                ),
            ))
            return MissionResolution(
                intent_id=intent.intent_id, kind=GAP,
                reason=f"no registered capability for required action {action}",
                actions=tuple(actions), gap=gap.as_dict(),
                missing=tuple(absent), provenance=provenance,
            )
        if missing_roles:
            return MissionResolution(
                intent_id=intent.intent_id, kind=BLOCKED,
                reason=(
                    "action plan requires supplied inputs that the intent does not "
                    f"carry: {', '.join(missing_roles)}"
                ),
                actions=tuple(actions), missing=missing_roles, provenance=provenance,
            )
        return MissionResolution(
            intent_id=intent.intent_id, kind=ACTIONS,
            reason="action plan bound from declared needs",
            actions=tuple(actions), provenance=provenance,
        )

    if missing_actions:
        recipe_id, absent = sorted(missing_actions.items())[0]
        action = absent[0]
        gap = record_gap(database, GapRecord(
            gap_id=f"gap.intent.{intent.intent_id}.{action.replace('.', '-')}",
            needed_action=action,
            missing=GapKind.TOOL,
            status=GapStatus.DETECTED,
            detail=(
                f"intent {intent.intent_id} selects workflow {recipe_id}, which "
                f"requires {action}; that capability is not registered"
            ),
        ))
        return MissionResolution(
            intent_id=intent.intent_id, kind=GAP,
            reason=f"selected workflow {recipe_id} requires missing capability {action}",
            recipe_id=recipe_id, gap=gap.as_dict(), missing=tuple(absent),
            provenance={"stage": "workflow", "missing_capability_candidates": missing_actions},
        )

    need_list = ", ".join(intent.needs)
    gap = record_gap(database, GapRecord(
        gap_id=f"gap.intent.{intent.intent_id}.unrealizable",
        needed_action="intent.unrealizable",
        missing=GapKind.TOOL,
        status=GapStatus.DETECTED,
        detail=f"no registered workflow or action realizes needs: {need_list}",
    ))
    return MissionResolution(
        intent_id=intent.intent_id, kind=GAP,
        reason=f"no registered workflow or action realizes needs: {need_list}",
        gap=gap.as_dict(),
        provenance={"stage": "unsupported"},
    )


__all__ = [
    "ACTIONS", "BLOCKED", "GAP", "WORKFLOW", "INTENT_VERSION",
    "RESOLUTION_VERSION", "MissionIntent", "MissionIntentError",
    "MissionResolution", "NEED_VOCABULARY", "resolve_mission_intent",
]
