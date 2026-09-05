"""Authenticated, non-executing standing-order representation and evaluation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..owner_directives import active_directives_for
from .models import ContextScope, OwnerContextQuery, OwnerContextRecord
from .projection import project_owner_context


STANDING_ORDER_CONTRACT = "jarvis.standing-order.v1"
STANDING_ORDER_EVALUATION_CONTRACT = "jarvis.standing-order-evaluation.v1"
STANDING_ORDER_PARSER_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class StandingOrderDraft:
    """A conservative structured interpretation of exact owner text."""

    owner_text: str
    scope: ContextScope
    trigger: dict[str, Any]
    intended_behavior: dict[str, Any]
    priority: int
    status: str
    confidence: float
    unknown_fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": STANDING_ORDER_CONTRACT,
            "owner_text": self.owner_text,
            "scope": self.scope.as_dict(),
            "trigger": dict(self.trigger),
            "intended_behavior": dict(self.intended_behavior),
            "priority": self.priority,
            "state": "ACTIVE" if self.status == "STRUCTURED" else "UNKNOWN",
            "evaluation": "ADVISORY_ONLY",
            "parser": {
                "version": STANDING_ORDER_PARSER_VERSION,
                "status": self.status,
                "confidence": self.confidence,
                "unknown_fields": list(self.unknown_fields),
            },
        }

    def as_metadata(self) -> dict[str, Any]:
        if self.status != "STRUCTURED":
            raise ValueError(
                "unknown standing-order text cannot be admitted as active",
            )
        return {"standing_order": self.as_dict()}


_RULES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (
        re.compile(r"\bnever\b.*\bbroken build\b.*\bovernight\b", re.I),
        {
            "trigger": {
                "kind": "BUILD_STATE",
                "keywords_all": ["broken", "build"],
                "states": ["BROKEN"],
                "temporal_window": "OVERNIGHT",
                "condition_text": "a build remains broken overnight",
            },
            "intended_behavior": {
                "kind": "PREVENT_PERSISTENT_BUILD_FAILURE",
                "description": "investigate or recover the broken build",
                "owner_interruption": "ONLY_IF_NOT_RECOVERABLE",
            },
            "priority": 90,
        },
    ),
    (
        re.compile(r"\bif\b.*\bproject\b.*\bblocked\b.*\binvestigate\b", re.I),
        {
            "trigger": {
                "kind": "PROJECT_STATE",
                "keywords_all": ["project", "blocked"],
                "states": ["BLOCKED"],
                "condition_text": "a project becomes blocked",
            },
            "intended_behavior": {
                "kind": "INVESTIGATE",
                "description": "investigate the project blocker",
                "owner_interruption": "NOT_REQUIRED",
            },
            "priority": 80,
        },
    ),
    (
        re.compile(r"\bkeep\b.*\bprojects?\b.*\bclean\b", re.I),
        {
            "trigger": {
                "kind": "PROJECT_HYGIENE",
                "keywords_all": ["project"],
                "keywords_any": ["dirty", "drift", "unclean", "cleanup", "clean"],
                "states": ["DIRTY", "DRIFTED"],
                "condition_text": "project hygiene drifts from its clean state",
            },
            "intended_behavior": {
                "kind": "MAINTAIN_PROJECT_HYGIENE",
                "description": "restore bounded project hygiene",
                "owner_interruption": "NOT_REQUIRED",
            },
            "priority": 60,
        },
    ),
    (
        re.compile(
            r"\b(?:do not|don't|dont)\b.*\bbother me\b.*"
            r"\bsmall\b.*\brecoverable errors?\b",
            re.I,
        ),
        {
            "trigger": {
                "kind": "RECOVERABLE_ERROR",
                "keywords_all": ["recoverable", "error"],
                "severities": ["SMALL"],
                "condition_text": "a small recoverable error occurs",
            },
            "intended_behavior": {
                "kind": "RECOVER_WITHOUT_OWNER_INTERRUPTION",
                "description": "recover locally without interrupting the owner",
                "owner_interruption": "PROHIBITED_FOR_MATCH",
            },
            "priority": 50,
        },
    ),
    (
        re.compile(
            r"\bbefore\b.*\b(?:purchas(?:e|ing)|buy(?:ing)?)\b.*"
            r"\b(?:show|present)\b.*\bfinal choice\b",
            re.I,
        ),
        {
            "trigger": {
                "kind": "BEFORE_EFFECT",
                "keywords_any": ["purchase", "purchasing", "buy", "buying"],
                "before_effect": "PURCHASE",
                "condition_text": "before a purchase is committed",
            },
            "intended_behavior": {
                "kind": "PRESENT_FINAL_CHOICE",
                "description": "show the owner the final purchase choice",
                "owner_interruption": "REQUIRED_BEFORE_EFFECT",
            },
            "priority": 100,
        },
    ),
)


def compile_standing_order(
    owner_text: str,
    *,
    scope: ContextScope | Mapping[str, Any] | None = None,
    trigger: Mapping[str, Any] | None = None,
    intended_behavior: Mapping[str, Any] | None = None,
    priority: int | None = None,
) -> StandingOrderDraft:
    """Convert exact owner text into a bounded structured draft.

    Unsupported language stays ``UNKNOWN``. Callers may supply explicit
    structured fields when a trusted owner-facing interpreter has already
    resolved the text.
    """

    text = str(owner_text or "").strip()
    if not text:
        raise ValueError("standing-order owner text is required")
    scope_value = ContextScope.from_mapping(scope)
    if trigger is not None or intended_behavior is not None:
        trigger_value = dict(trigger or {})
        behavior_value = dict(intended_behavior or {})
        unknown = tuple(
            name
            for name, value in (
                ("trigger", trigger_value),
                ("intended_behavior", behavior_value),
            )
            if not value
        )
        return StandingOrderDraft(
            owner_text=text,
            scope=scope_value,
            trigger=trigger_value,
            intended_behavior=behavior_value,
            priority=max(0, min(100, int(priority if priority is not None else 50))),
            status="UNKNOWN" if unknown else "STRUCTURED",
            confidence=0.0 if unknown else 1.0,
            unknown_fields=unknown,
        )

    for pattern, values in _RULES:
        if pattern.search(text):
            return StandingOrderDraft(
                owner_text=text,
                scope=scope_value,
                trigger=dict(values["trigger"]),
                intended_behavior=dict(values["intended_behavior"]),
                priority=max(
                    0,
                    min(
                        100,
                        int(priority if priority is not None else values["priority"]),
                    ),
                ),
                status="STRUCTURED",
                confidence=1.0,
            )
    return StandingOrderDraft(
        owner_text=text,
        scope=scope_value,
        trigger={},
        intended_behavior={},
        priority=max(0, min(100, int(priority if priority is not None else 50))),
        status="UNKNOWN",
        confidence=0.0,
        unknown_fields=("trigger", "intended_behavior"),
    )


def validate_standing_order_metadata(
    exact_text: str,
    metadata: Mapping[str, Any] | None,
    *,
    target: str | None,
) -> dict[str, Any]:
    """Validate the signed metadata before owner-control admission."""

    raw = dict(metadata or {}).get("standing_order")
    if not isinstance(raw, dict):
        raise ValueError("STANDING_ORDER requires metadata.standing_order")
    if raw.get("contract") != STANDING_ORDER_CONTRACT:
        raise ValueError("unsupported standing-order contract")
    if str(raw.get("owner_text") or "").strip() != str(exact_text or "").strip():
        raise ValueError("standing-order owner_text must equal exact_text")
    if str(raw.get("evaluation") or "") != "ADVISORY_ONLY":
        raise ValueError("standing orders must remain ADVISORY_ONLY")
    parser = raw.get("parser")
    if not isinstance(parser, dict) or parser.get("status") != "STRUCTURED":
        raise ValueError("standing-order interpretation must be STRUCTURED")
    trigger = raw.get("trigger")
    behavior = raw.get("intended_behavior")
    if not isinstance(trigger, dict) or not trigger:
        raise ValueError("standing-order trigger is required")
    if not isinstance(behavior, dict) or not behavior:
        raise ValueError("standing-order intended_behavior is required")
    scope = ContextScope.from_mapping(raw.get("scope"))
    if target and target != "GLOBAL":
        scoped = set((
            *scope.program_ids,
            *scope.project_ids,
            *scope.objective_ids,
            *scope.entity_refs,
        ))
        if target not in scoped:
            raise ValueError(
                "standing-order target must be present in its structured scope",
            )
    try:
        priority = int(raw.get("priority"))
    except (TypeError, ValueError) as error:
        raise ValueError("standing-order priority must be an integer") from error
    if not 0 <= priority <= 100:
        raise ValueError("standing-order priority must be within [0, 100]")
    return dict(raw)


def project_standing_orders(
    database: FoundryDatabase,
    *,
    now: str | None = None,
    include_history: bool = True,
) -> list[dict[str, Any]]:
    projection = project_owner_context(database, now=now)
    source = (
        projection.by_kind("STANDING_ORDER")
        if include_history
        else projection.by_kind("STANDING_ORDER", current_only=True)
    )
    result: list[dict[str, Any]] = []
    for record in source:
        result.append({
            "contract": STANDING_ORDER_CONTRACT,
            "standing_order_id": record.context_id,
            "directive_id": record.provenance.owner_directive_id,
            "owner_text": record.text,
            "scope": record.scope.as_dict(),
            "trigger": dict(record.attributes.get("trigger") or {}),
            "intended_behavior": dict(
                record.attributes.get("intended_behavior") or {},
            ),
            "priority": record.priority,
            "state": record.state,
            "active": record.current(projection.generated_at),
            "freshness": record.freshness(projection.generated_at),
            "provenance": record.provenance.as_dict(),
            "recorded_at": record.recorded_at,
            "updated_at": record.updated_at,
            "unknown_fields": list(record.unknown_fields),
        })
    return result


def _scope_matches(
    scope: ContextScope,
    query: OwnerContextQuery,
) -> tuple[bool, list[str]]:
    if scope.global_scope:
        return True, ["global_scope"]
    reasons: list[str] = []
    checks = (
        (query.program_id, scope.program_ids, "program"),
        (query.project_id, scope.project_ids, "project"),
        (query.objective_id, scope.objective_ids, "objective"),
    )
    for value, candidates, label in checks:
        if candidates:
            if value not in candidates:
                return False, []
            reasons.append(f"{label}:{value}")
    query_entities = {
        value
        for value in (
            query.program_id,
            query.project_id,
            query.objective_id,
            query.referenced_entity,
            *query.entity_refs,
        )
        if value
    }
    if scope.entity_refs:
        overlap = query_entities & set(scope.entity_refs)
        if not overlap:
            return False, []
        reasons.extend(f"entity:{value}" for value in sorted(overlap))
    return bool(reasons), reasons


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _trigger_matches(
    record: OwnerContextRecord,
    query: OwnerContextQuery,
) -> tuple[str, list[str]]:
    trigger = record.attributes.get("trigger")
    if not isinstance(trigger, dict) or not trigger:
        return "UNKNOWN", ["missing_trigger"]
    reasons: list[str] = []
    event_types = {
        str(value).upper()
        for value in trigger.get("event_types") or ()
    }
    if event_types:
        if not query.event_type or query.event_type.upper() not in event_types:
            return "NO_MATCH", []
        reasons.append(f"event_type:{query.event_type}")
    states = {
        str(value).upper()
        for value in trigger.get("states") or ()
    }
    if states:
        if not query.situation_state or query.situation_state.upper() not in states:
            return "NO_MATCH", []
        reasons.append(f"state:{query.situation_state}")
    severities = {
        str(value).upper()
        for value in trigger.get("severities") or ()
    }
    if severities:
        if not query.severity or query.severity.upper() not in severities:
            return "NO_MATCH", []
        reasons.append(f"severity:{query.severity}")

    corpus = _tokens(query.query_text())
    required = {
        str(value).lower()
        for value in trigger.get("keywords_all") or ()
    }
    if required and not required.issubset(corpus):
        return "NO_MATCH", []
    if required:
        reasons.append(f"keywords_all:{','.join(sorted(required))}")
    optional = {
        str(value).lower()
        for value in trigger.get("keywords_any") or ()
    }
    if optional and not (optional & corpus):
        return "NO_MATCH", []
    if optional:
        reasons.append(
            f"keywords_any:{','.join(sorted(optional & corpus))}",
        )
    before_effect = str(trigger.get("before_effect") or "").upper()
    if before_effect:
        effect_tokens = {
            "PURCHASE": {"purchase", "purchasing", "buy", "buying"},
        }.get(before_effect, {before_effect.lower()})
        if not (effect_tokens & corpus):
            return "NO_MATCH", []
        reasons.append(f"before_effect:{before_effect}")
    temporal_window = str(trigger.get("temporal_window") or "").upper()
    if temporal_window:
        return "UNKNOWN", [
            *reasons,
            f"temporal_window_not_observed:{temporal_window}",
        ]
    if not any((event_types, states, severities, required, optional, before_effect)):
        return "UNKNOWN", ["unstructured_trigger"]
    return "MATCH", reasons


class StandingOrderEvaluator:
    """Read-only seam for the future Executive; never performs behavior."""

    def __init__(
        self,
        database: FoundryDatabase,
        *,
        stop_path: str | Path | None = None,
    ) -> None:
        self.database = database
        self.stop_path = Path(stop_path) if stop_path is not None else None

    def evaluate(
        self,
        query: OwnerContextQuery | Mapping[str, Any],
    ) -> dict[str, Any]:
        request = OwnerContextQuery.from_mapping(query)
        identities = request.identities()
        stopped = emergency_stop_reason(
            self.database, self.stop_path, identities=identities,
        )
        vetoed = matching_veto_reason(self.database, identities)
        precedence_reason = stopped or vetoed
        controls = active_directives_for(
            self.database,
            command_types=frozenset({
                "GOAL",
                "CONSTRAINT",
                "ADD_CONSTRAINT",
                "REPRIORITIZE",
                "REDIRECT",
                "VETO_ACTION",
                "STOP_MISSION",
                "STOP_EVERYTHING",
                "PAUSE",
            }),
            targets=identities,
            now=request.now,
        )
        if precedence_reason:
            return {
                "contract": STANDING_ORDER_EVALUATION_CONTRACT,
                "status": "BLOCKED_BY_OWNER_CONTROL",
                "query": request.as_dict(),
                "owner_control_reason": precedence_reason,
                "owner_control_ids": [
                    record.directive_id for record in controls
                ],
                "matched": [],
                "unknown": [],
                "authority_precedence": [
                    "STOP",
                    "VETO",
                    "AUTHENTICATED_OWNER_CONTROL",
                    "STANDING_ORDER_ADVISORY",
                ],
                "execution": {"allowed": False, "performed": False},
            }

        stronger_controls = [
            record
            for record in controls
            if record.command_type in {
                "CONSTRAINT",
                "ADD_CONSTRAINT",
                "REPRIORITIZE",
                "REDIRECT",
            }
        ]
        if stronger_controls:
            strongest = stronger_controls[0]
            return {
                "contract": STANDING_ORDER_EVALUATION_CONTRACT,
                "status": "DEFERRED_TO_OWNER_CONTROL",
                "query": request.as_dict(),
                "owner_control_reason": (
                    f"active {strongest.command_type} directive "
                    f"{strongest.directive_id} applies"
                ),
                "owner_control_ids": [
                    record.directive_id for record in controls
                ],
                "matched": [],
                "unknown": [],
                "authority_precedence": [
                    "STOP",
                    "VETO",
                    "AUTHENTICATED_OWNER_CONTROL",
                    "STANDING_ORDER_ADVISORY",
                ],
                "execution": {"allowed": False, "performed": False},
            }

        projection = project_owner_context(
            self.database, now=request.now,
        )
        matched: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        for record in projection.by_kind("STANDING_ORDER", current_only=True):
            if record.provenance.authority != "AUTHENTICATED_OWNER":
                unknown.append({
                    "standing_order_id": record.context_id,
                    "reason": "standing_order_is_not_authenticated_owner_truth",
                })
                continue
            in_scope, scope_evidence = _scope_matches(record.scope, request)
            if not in_scope:
                continue
            outcome, trigger_evidence = _trigger_matches(record, request)
            if outcome == "UNKNOWN":
                unknown.append({
                    "standing_order_id": record.context_id,
                    "reason": ",".join(trigger_evidence),
                })
                continue
            if outcome != "MATCH":
                continue
            matched.append({
                "standing_order_id": record.context_id,
                "directive_id": record.provenance.owner_directive_id,
                "owner_text": record.text,
                "priority": record.priority,
                "scope_evidence": scope_evidence,
                "trigger_evidence": trigger_evidence,
                "intended_behavior": dict(
                    record.attributes.get("intended_behavior") or {},
                ),
                "provenance": record.provenance.as_dict(),
            })
        matched.sort(
            key=lambda value: (
                -float(value["priority"]),
                str(value["standing_order_id"]),
            ),
        )
        return {
            "contract": STANDING_ORDER_EVALUATION_CONTRACT,
            "status": "MATCHED" if matched else "UNKNOWN" if unknown else "NO_MATCH",
            "query": request.as_dict(),
            "owner_control_reason": None,
            "owner_control_ids": [record.directive_id for record in controls],
            "matched": matched,
            "unknown": unknown,
            "authority_precedence": [
                "STOP",
                "VETO",
                "AUTHENTICATED_OWNER_CONTROL",
                "STANDING_ORDER_ADVISORY",
            ],
            "execution": {"allowed": False, "performed": False},
        }


__all__ = [
    "STANDING_ORDER_CONTRACT",
    "STANDING_ORDER_EVALUATION_CONTRACT",
    "StandingOrderDraft",
    "StandingOrderEvaluator",
    "compile_standing_order",
    "project_standing_orders",
    "validate_standing_order_metadata",
]
