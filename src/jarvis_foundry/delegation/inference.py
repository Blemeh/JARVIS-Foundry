"""Deterministic semantic inference for bounded owner delegation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..clock import isoformat
from .context import DelegationContext
from .contracts import (
    ConsequenceClass,
    DelegationDecision,
    DelegationIntent,
    DelegationIntentStatus,
    DelegationMode,
    MaterialAmbiguity,
    MethodAssessment,
    Reversibility,
    stable_intent_id,
)


DELEGATION_INFERENCE_CONTRACT = "jarvis.delegation-inference.v1"

_TOKEN = re.compile(r"[a-z0-9]+")
_MODE_FEATURES: dict[DelegationMode, tuple[tuple[str, float], ...]] = {
    DelegationMode.TELL_ME: (
        ("tell me", 1.0),
        ("explain", 0.9),
        ("report", 0.75),
        ("show me", 0.65),
        ("what happened", 0.9),
        ("why", 0.55),
        ("status", 0.45),
        ("find out", 0.45),
    ),
    DelegationMode.PREPARE: (
        ("prepare", 1.0),
        ("draft", 0.95),
        ("plan", 0.55),
        ("proposal", 0.7),
        ("options", 0.45),
        ("ready for review", 0.8),
        ("do not apply", 0.85),
        ("without applying", 0.85),
        ("without sending", 0.85),
    ),
    DelegationMode.HANDLE: (
        ("handle", 1.0),
        ("fix", 0.95),
        ("resolve", 0.9),
        ("repair", 0.9),
        ("take care", 0.9),
        ("finish", 0.65),
        ("complete", 0.55),
        ("continue", 0.75),
        ("do something useful", 0.6),
    ),
    DelegationMode.WATCH: (
        ("watch", 1.0),
        ("monitor", 1.0),
        ("keep an eye", 0.95),
        ("alert", 0.7),
        ("notify", 0.65),
        ("track", 0.55),
        ("state change", 0.45),
    ),
    DelegationMode.TAKE_OVER: (
        ("take over", 1.2),
        ("own this", 1.0),
        ("run with it", 0.95),
        ("make the calls", 0.85),
        ("end to end", 0.7),
        ("through completion", 0.6),
        ("you decide", 0.85),
    ),
    DelegationMode.STANDING: (
        ("standing order", 1.2),
        ("whenever", 1.0),
        ("every time", 0.95),
        ("from now on", 0.9),
        ("always", 0.65),
        ("never", 0.65),
        ("if this happens", 0.65),
    ),
}
_GENERIC_ACTION = frozenset({
    "continue",
    "do",
    "fix",
    "handle",
    "here",
    "it",
    "me",
    "over",
    "please",
    "prepare",
    "something",
    "take",
    "tell",
    "that",
    "the",
    "this",
    "useful",
    "watch",
})
_IRREVERSIBLE = frozenset({
    "buy",
    "delete",
    "destroy",
    "erase",
    "pay",
    "purchase",
    "terminate",
    "transfer",
})
_HIGH_CONSEQUENCE = frozenset({
    "deploy",
    "email",
    "merge",
    "payment",
    "production",
    "promote",
    "publish",
    "send",
})
_BAD_METHOD = frozenset({
    "bypass",
    "delete",
    "disable",
    "erase",
    "fabricate",
    "fake",
    "hide",
    "ignore",
    "skip",
    "suppress",
})
_TRUTH_GOAL = frozenset({
    "audit",
    "check",
    "evidence",
    "history",
    "log",
    "proof",
    "report",
    "test",
    "verification",
    "verifier",
})


@dataclass(frozen=True, slots=True)
class ModeInference:
    mode: DelegationMode
    confidence: float
    scores: dict[str, float]
    ambiguous: bool


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


#: Stopwords excluded from constraint-conflict overlap. A shared generic word
#: ("a", "the", "for", …) is not evidence that a request violates a constraint;
#: treating it as a material conflict parks every owner directive in ASKING.
_CONFLICT_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "in", "into", "is", "make", "me", "my", "of", "on", "or", "the", "this",
    "to", "with", "you",
})


def _conflict_tokens(text: str) -> set[str]:
    return {
        token
        for token in _tokens(text)
        if token not in _GENERIC_ACTION
        and token not in _CONFLICT_STOPWORDS
        and len(token) > 1
    }


_NEGATION_CUES = ("do not ", "don't ", "never ", "no ")


def _regulated_terms(constraint: str) -> set[str]:
    """Conflict tokens of the constraint's prohibition sentences.

    A shared generic word ("only", "owner", "when") is not evidence that
    a request violates a constraint; only overlap with what the constraint
    actually regulates (the prohibition scope after a negation cue) counts
    as a method conflict.  Without this, any goal sharing two function
    words with a constraint parks in ASKING with a vacuous question.
    """
    terms: set[str] = set()
    for sentence in re.split(r"[.!?;\n]+", constraint.lower()):
        if any(cue in sentence for cue in _NEGATION_CUES):
            terms |= _conflict_tokens(sentence)
    return terms


def _shares_regulated_conflict(goal_tokens: set[str], constraint: str) -> bool:
    shared = goal_tokens & _conflict_tokens(constraint)
    if len(shared) < 2:
        return False
    return bool(shared & _regulated_terms(constraint))


def _mode_inference(
    text: str,
    metadata: Mapping[str, Any],
    calibration: Mapping[str, Any] | None = None,
) -> ModeInference:
    lowered = " ".join(text.lower().split())
    tokens = _tokens(text)
    scores = {mode: 0.0 for mode in DelegationMode}
    scores[DelegationMode.HANDLE] = 0.2
    for mode, features in _MODE_FEATURES.items():
        for feature, weight in features:
            if feature in lowered:
                scores[mode] += weight
    if text.strip().endswith("?"):
        scores[DelegationMode.TELL_ME] += 0.35
    if "if " in lowered and any(
        value in lowered for value in (" then ", "whenever", "every time")
    ):
        scores[DelegationMode.STANDING] += 0.45
    if "keep " in lowered and any(
        value in lowered for value in ("watch", "monitor", "track", "eye on")
    ):
        scores[DelegationMode.WATCH] += 0.3
    if tokens & (_BAD_METHOD | _IRREVERSIBLE | _HIGH_CONSEQUENCE):
        scores[DelegationMode.TELL_ME] = max(
            0.0,
            scores[DelegationMode.TELL_ME] - 0.65,
        )
        scores[DelegationMode.HANDLE] += 0.35
    hint = str(metadata.get("mode") or metadata.get("mode_hint") or "").upper()
    try:
        scores[DelegationMode(hint)] += 1.5
    except ValueError:
        pass
    mode_adjustments = (
        calibration.get("mode_adjustments")
        if isinstance(calibration, Mapping)
        else {}
    )
    if isinstance(mode_adjustments, Mapping):
        for mode in DelegationMode:
            try:
                adjustment = float(mode_adjustments.get(mode.value, 0.0))
            except (TypeError, ValueError):
                adjustment = 0.0
            scores[mode] += max(-0.20, min(0.08, adjustment))
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0].value))
    mode, top = ordered[0]
    second = ordered[1][1]
    ambiguous = top > 0.25 and top - second < 0.16
    confidence = round(min(0.99, 0.48 + top * 0.32), 4)
    if top <= 0.25:
        confidence = 0.52
    return ModeInference(
        mode=mode,
        confidence=confidence,
        scores={key.value: round(value, 4) for key, value in scores.items()},
        ambiguous=ambiguous,
    )


def _substantive_tokens(text: str) -> set[str]:
    return _tokens(text) - _GENERIC_ACTION


def _desired_outcome(
    text: str,
    *,
    mode: DelegationMode,
    context: DelegationContext,
    metadata: Mapping[str, Any],
    method_assessment: MethodAssessment | None = None,
) -> str:
    if (
        method_assessment is not None
        and method_assessment.disposition == "RECOMMEND_ALTERNATIVE"
    ):
        return (
            f"{method_assessment.owner_goal.rstrip('.')}. "
            f"{method_assessment.recommended_method}"
        )[:2048]
    explicit = str(metadata.get("desired_outcome") or "").strip()
    if explicit:
        return explicit[:2048]
    referent = context.selected.text if context.selected is not None else ""
    if referent:
        if mode == DelegationMode.TELL_ME:
            return f"Produce an evidence-backed answer about {referent}."
        if mode == DelegationMode.PREPARE:
            return (
                f"Prepare a review-ready next step for {referent} without "
                "committing consequential effects."
            )
        if mode == DelegationMode.WATCH:
            return f"Monitor {referent} and surface a material state change."
        if mode == DelegationMode.TAKE_OVER:
            return (
                f"Own {referent} through a verified terminal outcome within "
                "active owner controls."
            )
        if mode == DelegationMode.STANDING:
            return f"Apply the authenticated standing behavior when {referent} recurs."
        if "continue" in text.lower():
            return f"Continue {referent} to its next verified terminal outcome."
        return f"Resolve {referent} and independently verify the result."
    normalized = " ".join(text.strip().split())
    if mode == DelegationMode.TELL_ME:
        return f"Produce an evidence-backed response to: {normalized}"
    if mode == DelegationMode.PREPARE:
        return f"Prepare, without applying consequential effects: {normalized}"
    if mode == DelegationMode.WATCH:
        return f"Monitor for material changes related to: {normalized}"
    if mode == DelegationMode.TAKE_OVER:
        return f"Own through a verified terminal outcome: {normalized}"
    if mode == DelegationMode.STANDING:
        return f"Structure and apply as an authenticated standing behavior: {normalized}"
    return normalized


def _success_criteria(
    mode: DelegationMode,
    metadata: Mapping[str, Any],
    *,
    text: str,
    context: DelegationContext,
) -> tuple[str, ...]:
    explicit = metadata.get("success_criteria")
    if isinstance(explicit, str) and explicit.strip():
        return (explicit.strip(),)
    if isinstance(explicit, (list, tuple)):
        values = tuple(
            str(value).strip()
            for value in explicit
            if str(value).strip()
        )
        if values:
            return _mode_guarded_success(mode, values)
    inferred = _success_from_owner_text(text)
    if inferred:
        return _mode_guarded_success(mode, inferred)
    if (
        context.selected is not None
        and context.selected.kind in {
            "BLOCKER",
            "FAILURE",
            "UNRESOLVED_ITEM",
        }
    ):
        condition = context.selected.text.rstrip(".")[:800]
        return _mode_guarded_success(mode, (
            (
                "Independent evidence confirms the referenced condition is "
                f"resolved: {condition}."
            ),
            "Direct inspection agrees with the terminal verification.",
        ))
    if mode == DelegationMode.TELL_ME:
        return (
            "The answer cites direct evidence and labels material uncertainty.",
        )
    if mode == DelegationMode.PREPARE:
        return (
            "A review-ready deliverable exists.",
            "No consequential final effect was committed.",
        )
    if mode == DelegationMode.WATCH:
        return (
            "The existing wake or scheduler authority holds a durable condition.",
            "Only a material change, failure, or required owner action is surfaced.",
        )
    if mode == DelegationMode.TAKE_OVER:
        return (
            "The objective reaches a verified terminal outcome.",
            "STOP, veto, budget, and effect boundaries remain enforced.",
        )
    if mode == DelegationMode.STANDING:
        return (
            "Only an authenticated, active, in-scope standing order can match.",
            "Each match is evidence-backed and still passes current owner controls.",
        )
    return (
        "The requested outcome is independently verified.",
        "Durable evidence records the result and any remaining uncertainty.",
    )


def _success_from_owner_text(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    markers = (
        "success criteria:",
        "acceptance criteria:",
        "success:",
        "done when:",
        "complete when:",
        "verified when:",
    )
    positions = [
        (lowered.find(marker), marker)
        for marker in markers
        if lowered.find(marker) >= 0
    ]
    if not positions:
        return ()
    index, marker = min(positions, key=lambda value: value[0])
    tail = text[index + len(marker):].strip()
    if not tail:
        return ()
    values = tuple(
        value.strip(" .")
        for value in re.split(r"[;\n]+", tail)
        if value.strip(" .")
    )
    return values[:16]


def _mode_guarded_success(
    mode: DelegationMode,
    values: tuple[str, ...],
) -> tuple[str, ...]:
    result = list(values)
    if mode == DelegationMode.PREPARE:
        result.append("No consequential final effect was committed.")
    elif mode == DelegationMode.TELL_ME:
        result.append("No action occurred beyond bounded evidence gathering.")
    elif mode == DelegationMode.WATCH:
        result.append(
            "Only a material change, failure, or required owner action is "
            "surfaced."
        )
    return tuple(dict.fromkeys(result))[:16]


#: Ambiguity option display limit (contracts.MaterialAmbiguity options).
_OPTION_LIMIT = 512


def _clip_option(value: Any) -> str:
    """Clip an ambiguity option to the contract limit.

    Options are short owner-facing choices; the full source text already
    lives in evidence. An overlong option poisoned the intent ledger row and
    made the whole delegation projection unparseable.
    """
    text = str(value or "").strip()
    if len(text) > _OPTION_LIMIT:
        return (text[:_OPTION_LIMIT - 1].rstrip() + "…")[:_OPTION_LIMIT]
    return text


def _goal_clause(text: str) -> str:
    normalized = " ".join(text.strip().split())
    lowered = normalized.lower()
    for separator in (" so that ", " in order to ", " to "):
        index = lowered.find(separator)
        if index >= 0:
            clause = normalized[index + len(separator):].strip(" .")
            if clause:
                return clause[:1024]
    return "Achieve the requested result with trustworthy evidence"


def _method_assessment(
    text: str,
    *,
    context: DelegationContext,
    metadata: Mapping[str, Any],
    consequence: ConsequenceClass,
) -> MethodAssessment | None:
    explicit = metadata.get("method_assessment")
    if isinstance(explicit, Mapping):
        return MethodAssessment.from_mapping(explicit)
    tokens = _tokens(text)
    bad = sorted(tokens & _BAD_METHOD)
    truth = sorted(tokens & _TRUTH_GOAL)
    conflict_content = _conflict_tokens(text)
    conflicting_constraints = [
        constraint
        for constraint in context.active_constraints
        if _shares_regulated_conflict(conflict_content, constraint)
    ]
    if not (bad and truth) and not conflicting_constraints:
        return None
    normalized = " ".join(text.strip().split())
    goal = str(metadata.get("actual_goal") or "").strip() or _goal_clause(text)
    evidence = [
        *(f"unsafe_method_signal:{value}" for value in bad),
        *(f"truth_goal_signal:{value}" for value in truth),
        *(
            f"active_constraint:{constraint[:240]}"
            for constraint in conflicting_constraints[:4]
        ),
    ]
    if conflicting_constraints:
        recommended = (
            "Choose an approach that preserves the active owner constraint: "
            f"{conflicting_constraints[0]}"
        )
        reason = (
            "The requested method conflicts with an active authenticated "
            "owner constraint."
        )
        disposition = "ASK_OWNER"
    else:
        recommended = (
            "Repair the underlying condition and preserve the evidence trail."
        )
        reason = (
            "The requested method would weaken evidence rather than achieve "
            "a trustworthy outcome."
        )
        disposition = (
            "ASK_OWNER"
            if consequence in {
                ConsequenceClass.HIGH,
                ConsequenceClass.IRREVERSIBLE,
            }
            else "RECOMMEND_ALTERNATIVE"
        )
    return MethodAssessment(
        requested_method=normalized[:1024],
        owner_goal=goal,
        recommended_method=recommended[:1024],
        reason=reason,
        disposition=disposition,
        evidence=tuple(evidence)[:16],
        preserves_explicit_constraints=True,
    )


_EFFECT_NEGATION_CUES = (
    "do not",
    "don't",
    "does not",
    "never",
    "without",
    "forbid",
    "prohibit",
)


def _unnegated_tokens(text: str) -> set[str]:
    """Tokens occurring outside prohibition sentences.

    A high-consequence word inside an explicit prohibition ("do not modify
    production systems") describes withheld authority, not a requested
    effect.  Scoring it as requested consequence interrogates the owner
    about a boundary they already set.  Only unnegated occurrences count
    toward the requested effect classification.
    """
    kept: set[str] = set()
    for sentence in re.split(r"[.!?;\n]+", str(text or "").lower()):
        if any(cue in sentence for cue in _EFFECT_NEGATION_CUES):
            continue
        kept |= _tokens(sentence)
    return kept


def _risk(
    text: str,
    *,
    mode: DelegationMode,
    metadata: Mapping[str, Any],
) -> tuple[ConsequenceClass, Reversibility, tuple[str, ...]]:
    explicit_consequence = str(metadata.get("consequence_class") or "").upper()
    explicit_reversibility = str(metadata.get("reversibility") or "").upper()
    tokens = _tokens(text)
    labels: list[str] = []
    try:
        consequence = ConsequenceClass(explicit_consequence)
        labels.append("EXPLICIT_CONSEQUENCE")
    except ValueError:
        requested_effects = tokens & (_BAD_METHOD | _IRREVERSIBLE | _HIGH_CONSEQUENCE)
        requested_effects &= _unnegated_tokens(text)
        if requested_effects & _IRREVERSIBLE:
            consequence = ConsequenceClass.IRREVERSIBLE
            labels.append("IRREVERSIBLE_EFFECT_LANGUAGE")
        elif requested_effects & _HIGH_CONSEQUENCE:
            consequence = ConsequenceClass.HIGH
            labels.append("HIGH_CONSEQUENCE_LANGUAGE")
        elif mode == DelegationMode.TAKE_OVER:
            consequence = ConsequenceClass.MEDIUM
            labels.append("BROAD_DELEGATION_SCOPE")
        else:
            consequence = ConsequenceClass.LOW
            labels.append("LOW_CONSEQUENCE_DEFAULT")
    try:
        reversibility = Reversibility(explicit_reversibility)
        labels.append("EXPLICIT_REVERSIBILITY")
    except ValueError:
        if consequence == ConsequenceClass.IRREVERSIBLE:
            reversibility = Reversibility.IRREVERSIBLE
        elif consequence == ConsequenceClass.HIGH:
            reversibility = Reversibility.UNKNOWN
        else:
            reversibility = Reversibility.REVERSIBLE
    if mode in {
        DelegationMode.TELL_ME,
        DelegationMode.PREPARE,
        DelegationMode.WATCH,
    }:
        consequence = ConsequenceClass.LOW
        reversibility = Reversibility.REVERSIBLE
        labels.append("NON_EFFECTING_MODE")
    return consequence, reversibility, tuple(dict.fromkeys(labels))


def infer_delegation_intent(
    directive: Mapping[str, Any],
    context: DelegationContext,
    *,
    now: str | None = None,
) -> DelegationIntent:
    """Infer a bounded intent from one authenticated owner directive."""

    values = dict(directive)
    if values.get("authenticated") is not True:
        raise PermissionError("delegation inference requires authenticated owner text")
    directive_id = str(values.get("directive_id") or "").strip()
    exact_text = str(values.get("exact_text") or "").strip()
    source_identity = str(
        values.get("source_identity")
        or values.get("owner_source_identity")
        or f"owner:{values.get('owner_id') or 'local-owner'}"
    ).strip()
    if not directive_id or not exact_text:
        raise ValueError("directive_id and exact_text are required")
    metadata = dict(values.get("metadata") or {})
    mode_result = _mode_inference(
        exact_text,
        metadata,
        calibration=context.calibration,
    )
    consequence, reversibility, risk_labels = _risk(
        exact_text,
        mode=mode_result.mode,
        metadata=metadata,
    )
    method_assessment = _method_assessment(
        exact_text,
        context=context,
        metadata=metadata,
        consequence=consequence,
    )
    ambiguities: list[MaterialAmbiguity] = []
    generic_reference = not _substantive_tokens(exact_text)
    if context.resolution_status == "AMBIGUOUS" and generic_reference:
        ambiguities.append(MaterialAmbiguity(
            ambiguity_id=f"ambiguity:{directive_id}:referent",
            code="REFERENT_AMBIGUOUS",
            question="Which current item should I apply this instruction to?",
            options=tuple(_clip_option(candidate.text) for candidate in context.candidates[:4]),
            evidence=tuple(
                f"{candidate.context_id}:{candidate.confidence}"
                for candidate in context.candidates[:4]
            ),
        ))
    elif context.resolution_status == "UNKNOWN" and generic_reference:
        ambiguities.append(MaterialAmbiguity(
            ambiguity_id=f"ambiguity:{directive_id}:referent",
            code="REFERENT_UNKNOWN",
            question="What should I apply this instruction to?",
            options=tuple(_clip_option(candidate.text) for candidate in context.candidates[:4]),
            evidence=("NO_CONFIDENT_CONTEXTUAL_REFERENT",),
        ))
    if mode_result.ambiguous:
        ambiguities.append(MaterialAmbiguity(
            ambiguity_id=f"ambiguity:{directive_id}:mode",
            code="MODE_AMBIGUOUS",
            question=(
                "Should I only prepare the work, or carry it through to a "
                "verified result?"
            ),
            options=("Prepare only", "Handle through verification"),
            evidence=tuple(
                f"{name}:{score}"
                for name, score in sorted(
                    mode_result.scores.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:2]
            ),
        ))
    if (
        method_assessment is not None
        and method_assessment.disposition == "ASK_OWNER"
    ):
        ambiguities.append(MaterialAmbiguity(
            ambiguity_id=f"ambiguity:{directive_id}:method",
            code="METHOD_GOAL_MISMATCH",
            question=(
                f"{method_assessment.reason} Should I use the recommended "
                "approach instead?"
            ),
            options=(
                _clip_option(method_assessment.recommended_method),
                "Keep the requested method within current owner controls",
            ),
            evidence=method_assessment.evidence,
        ))
    approval = bool(
        metadata.get("consequential_effect_approved")
        or metadata.get("owner_approval")
    )
    if consequence in {
        ConsequenceClass.HIGH,
        ConsequenceClass.IRREVERSIBLE,
    } and mode_result.mode not in {
        DelegationMode.TELL_ME,
        DelegationMode.PREPARE,
        DelegationMode.WATCH,
    } and not approval and not any(
        ambiguity.code == "METHOD_GOAL_MISMATCH"
        for ambiguity in ambiguities
    ):
        ambiguities.append(MaterialAmbiguity(
            ambiguity_id=f"ambiguity:{directive_id}:consequence",
            code="CONSEQUENTIAL_EFFECT_BOUNDARY",
            question=(
                "Should I prepare this for review without committing the "
                "consequential effect?"
            ),
            options=("Prepare for review", "Authorize the final effect"),
            evidence=(consequence.value, reversibility.value),
        ))
    if (
        mode_result.mode == DelegationMode.STANDING
        and not context.standing_order_ids
    ):
        ambiguities.append(MaterialAmbiguity(
            ambiguity_id=f"ambiguity:{directive_id}:standing-order",
            code="STANDING_ORDER_UNMATCHED",
            question=(
                "Should I create a structured standing order, or apply a "
                "specific active standing order?"
            ),
            options=(
                "Create a structured standing order",
                "Apply an active standing order",
            ),
            evidence=("NO_ACTIVE_IN_SCOPE_STANDING_ORDER_MATCH",),
        ))
    if ambiguities:
        decision = DelegationDecision.ASK
        status = DelegationIntentStatus.AWAITING_CLARIFICATION
    elif mode_result.mode == DelegationMode.PREPARE:
        decision = DelegationDecision.PREPARE
        status = DelegationIntentStatus.READY
    elif mode_result.mode == DelegationMode.WATCH:
        decision = DelegationDecision.WATCH
        status = DelegationIntentStatus.READY
    else:
        decision = DelegationDecision.ACT
        status = DelegationIntentStatus.READY
    timestamp = now or isoformat()
    referent_confidence = (
        context.selected.confidence
        if context.selected is not None
        else 0.72 if not generic_reference else 0.0
    )
    confidence = round(
        min(
            0.99,
            max(
                0.0,
                mode_result.confidence * 0.55
                + referent_confidence * 0.45
                + float(
                    (
                        context.calibration.get("resolution_adjustments")
                        or {}
                    ).get(context.resolution_status, 0.0)
                    if isinstance(context.calibration, Mapping)
                    else 0.0
                ),
            ),
        ),
        4,
    )
    constraints = list(context.active_constraints)
    explicit_constraints = metadata.get("constraints")
    if isinstance(explicit_constraints, str):
        constraints.append(explicit_constraints)
    elif isinstance(explicit_constraints, (list, tuple)):
        constraints.extend(str(value) for value in explicit_constraints)
    if mode_result.mode == DelegationMode.PREPARE:
        constraints.append("Do not commit the final consequential effect.")
    elif mode_result.mode == DelegationMode.TELL_ME:
        constraints.append("Do not act beyond evidence gathering for the answer.")
    elif mode_result.mode == DelegationMode.WATCH:
        constraints.append("Use the existing scheduler and wake authority.")
    rationale = [
        f"MODE_{mode_result.mode.value}",
        f"CONSEQUENCE_{consequence.value}",
        f"REVERSIBILITY_{reversibility.value}",
        f"REFERENT_{context.resolution_status}",
        *risk_labels,
    ]
    if context.standing_order_ids:
        rationale.append("STANDING_ORDER_MATCHED")
    if method_assessment is not None:
        rationale.extend((
            "METHOD_GOAL_MISMATCH",
            f"METHOD_{method_assessment.disposition}",
        ))
    mode_calibration = (
        (context.calibration.get("mode_adjustments") or {}).get(
            mode_result.mode.value,
            0.0,
        )
        if isinstance(context.calibration, Mapping)
        else 0.0
    )
    resolution_calibration = (
        (context.calibration.get("resolution_adjustments") or {}).get(
            context.resolution_status,
            0.0,
        )
        if isinstance(context.calibration, Mapping)
        else 0.0
    )
    if mode_calibration or resolution_calibration:
        rationale.append("CALIBRATION_APPLIED")
    if ambiguities:
        rationale.append("MATERIAL_AMBIGUITY")
    else:
        rationale.append("NO_MATERIAL_AMBIGUITY")
    return DelegationIntent(
        intent_id=stable_intent_id(
            directive_id,
            exact_text,
            request_identity=str(metadata.get("request_id") or "") or None,
        ),
        owner_directive_id=directive_id,
        owner_source_identity=source_identity,
        exact_owner_text=exact_text,
        desired_outcome=_desired_outcome(
            exact_text,
            mode=mode_result.mode,
            context=context,
            metadata=metadata,
            method_assessment=method_assessment,
        ),
        mode=mode_result.mode,
        decision=decision,
        success_criteria=_success_criteria(
            mode_result.mode,
            metadata,
            text=exact_text,
            context=context,
        ),
        consequence_class=consequence,
        reversibility=reversibility,
        confidence=confidence,
        created_at=timestamp,
        updated_at=timestamp,
        status=status,
        resolved_referents=(
            (context.selected,) if context.selected is not None else ()
        ),
        explicit_constraints=tuple(dict.fromkeys(
            str(value).strip()
            for value in constraints
            if str(value).strip()
        ))[:24],
        standing_order_ids=context.standing_order_ids,
        context_refs=tuple(
            candidate.context_id for candidate in context.candidates[:8]
        ),
        evidence_refs=context.evidence_refs,
        ambiguities=tuple(ambiguities[:8]),
        method_assessment=method_assessment,
        rationale_labels=tuple(dict.fromkeys(rationale)),
        program_id=context.program_id,
        project_id=context.project_id,
        context_objective_id=context.objective_id,
        context_contract=context.context_contract,
        context_package_hash=context.context_package_hash,
        reference_resolution_contract=(
            context.reference_resolution_contract
        ),
        clarification_question=(
            ambiguities[0].question if ambiguities else None
        ),
        clarification_revision=0,
    )


__all__ = [
    "DELEGATION_INFERENCE_CONTRACT",
    "ModeInference",
    "infer_delegation_intent",
]
