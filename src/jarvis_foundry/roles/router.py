"""Abstract role routing with provider fallback and diversity preference (WP4).

``RoleRouter`` resolves a role (executive, builder, verifier, ...) to an
ordered provider chain from a role configuration (model_roles YAML template),
enforces the standing provider-diversity preference (builder != verifier when
the configuration makes it possible), and exposes deterministic availability
semantics: preferred -> fallbacks -> BLOCKED.

DIR-2026-08-12 MANDATORY INTERNAL MODEL ROUTING: every REAL JARVIS internal
dispatch (builder, verifier, researcher, repairer, reviewer, planner,
replacement worker, mission specialist, retry/recovery worker, live canary)
must resolve to the machine-local provider configuration (default
``opencode_go`` + ``muse-spark-1.2-contributor``) with NO fallbacks. A
non-compliant role must be recorded BLOCKED, never substituted with another
model. ``builtin_roles()`` implements exactly this policy, and
``RoleRouter.enforce_directive_routing`` rejects any configuration that
deviates (used by the production wiring — ``v3_loop.run_one_loop`` and the
live loop drivers — before any mutation).

The router is pure configuration logic; it never launches anything.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # pragma: no cover - venv provides pyyaml
    yaml = None  # type: ignore[assignment]

from ..runtime.provider_config import ProviderSettings, default_provider_settings

#: Template defaults mirrored in provider.yaml.example.
DIRECTIVE_PROVIDER = "opencode_go"
DIRECTIVE_MODEL = "muse-spark-1.2-contributor"


def builtin_roles(
    settings: ProviderSettings | None = None,
) -> dict[str, dict[str, Any]]:
    """Built-in role routing from trusted provider settings (no disk read)."""
    authority = settings or default_provider_settings()
    provider = authority.provider
    model = authority.model
    return {
        "executive": {
            "preferred_provider": provider,
            "preferred_model": model,
            "fallbacks": [],
            "independence_group": "executive",
            "required_capabilities": ["structured_reasoning"],
        },
        "builder": {
            "preferred_provider": provider,
            "preferred_model": model,
            "fallbacks": [],
            "independence_group": "builder",
            "required_capabilities": ["code", "tools"],
        },
        "verifier": {
            "preferred_provider": provider,
            "preferred_model": model,
            "fallbacks": [],
            "independence_group": "verifier",
            "required_capabilities": ["artifact_inspection", "adversarial_verification"],
        },
        "critic": {
            "preferred_provider": provider,
            "preferred_model": model,
            "fallbacks": [],
            "independence_group": "critic",
            "required_capabilities": ["adversarial_review", "artifact_inspection"],
        },
        "repair": {
            "preferred_provider": provider,
            "preferred_model": model,
            "fallbacks": [],
            "independence_group": "repair",
            "required_capabilities": ["code", "tools", "tests"],
        },
        "researcher": {
            "preferred_provider": provider,
            "preferred_model": model,
            "fallbacks": [],
            "independence_group": "research",
            "required_capabilities": ["research"],
        },
        "premium_reviewer": {
            "preferred_provider": provider,
            "preferred_model": model,
            "fallbacks": [],
            "independence_group": "premium",
            "required_capabilities": ["multimodal"],
        },
    }


# Backward-compatible alias (deterministic defaults, no disk read).
BUILTIN_ROLES: dict[str, dict[str, Any]] = builtin_roles()

DIVERSITY_EXEMPT_ROLES = {"executive", "researcher", "premium_reviewer"}


@dataclass(frozen=True, slots=True)
class RoleConfig:
    role: str
    preferred_provider: str
    preferred_model: str
    fallbacks: tuple[str, ...] = field(default_factory=tuple)
    independence_group: str = ""
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)

    def provider_chain(self) -> tuple[str, ...]:
        """Ordered providers: preferred first, then fallbacks (deduplicated)."""
        seen: list[str] = []
        for provider in (self.preferred_provider, *self.fallbacks):
            if provider and provider not in seen:
                seen.append(provider)
        return tuple(seen)


class RoleConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderRouteCandidate:
    """Observed provider/model capability used by the read-only route planner.

    A candidate is not considered usable when health, authentication, or
    availability is unknown.  This keeps an unprobed provider from becoming a
    silent fallback while allowing the strict production directive to remain
    enforced by :meth:`RoleRouter.enforce_directive_routing`.
    """

    provider: str
    model: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    context_window_tokens: int | None = None
    latency_ms: float | None = None
    reliability: float | None = None
    cost_usd: float | None = None
    health: str = "UNKNOWN"
    auth_state: str = "UNKNOWN"
    availability: str = "UNKNOWN"
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    """Capability and operating constraints for one deterministic route plan."""

    task_type: str
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    reasoning: str = "standard"
    context_tokens: int | None = None
    latency_budget_ms: float | None = None
    reliability_floor: float | None = None
    max_cost_usd: float | None = None
    preferred_provider: str | None = None
    strict_identity: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Auditable route result; a refusal is a first-class result."""

    status: str
    selected: ProviderRouteCandidate | None
    attempted: tuple[ProviderRouteCandidate, ...]
    rejected: tuple[dict[str, Any], ...]
    failure_class: str | None
    reason: str
    fallback_used: bool = False
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        selected = None
        if self.selected is not None:
            selected = _candidate_dict(self.selected)
        return {
            "status": self.status,
            "selected": selected,
            "attempted": [_candidate_dict(item) for item in self.attempted],
            "rejected": [dict(item) for item in self.rejected],
            "failure_class": self.failure_class,
            "reason": self.reason,
            "fallback_used": self.fallback_used,
            "evidence_refs": list(self.evidence_refs),
        }


def _candidate_dict(candidate: ProviderRouteCandidate) -> dict[str, Any]:
    return {
        "provider": candidate.provider,
        "model": candidate.model,
        "capabilities": list(candidate.capabilities),
        "context_window_tokens": candidate.context_window_tokens,
        "latency_ms": candidate.latency_ms,
        "reliability": candidate.reliability,
        "cost_usd": candidate.cost_usd,
        "health": candidate.health,
        "auth_state": candidate.auth_state,
        "availability": candidate.availability,
        "evidence_refs": list(candidate.evidence_refs),
    }


def route_provider_candidates(
    request: RoutingRequest,
    candidates: Iterable[ProviderRouteCandidate],
) -> RoutingDecision:
    """Choose a capable, observed provider/model without inventing readiness.

    This function is pure and does not dispatch or mutate the ledger.  The
    caller can use ``strict_identity`` for the mandatory internal route, while
    staging/research callers may provide explicit, observed fallbacks.
    """
    if not request.task_type.strip():
        return RoutingDecision(
            "BLOCKED", None, (), (), "INVALID_REQUEST", "task_type is required")
    if request.context_tokens is not None and request.context_tokens < 0:
        return RoutingDecision(
            "BLOCKED", None, (), (), "INVALID_REQUEST", "context_tokens must be non-negative")
    candidates_list = [item for item in candidates if item.provider and item.model]
    # Stable preference order first, then a deterministic quality key.  The
    # quality key only uses measured values; unknown measurements are rejected
    # below instead of being treated as zero-cost/zero-latency.
    preferred = request.preferred_provider
    if preferred is None and request.strict_identity is not None:
        preferred = request.strict_identity[0]
    candidates_list.sort(key=lambda item: (
        0 if preferred and item.provider == preferred else 1,
        -float(item.reliability) if item.reliability is not None else 0.0,
        float(item.latency_ms) if item.latency_ms is not None else math.inf,
        float(item.cost_usd) if item.cost_usd is not None else math.inf,
        item.provider,
        item.model,
    ))
    rejected: list[dict[str, Any]] = []
    eligible: list[ProviderRouteCandidate] = []
    for candidate in candidates_list:
        reason: str | None = None
        failure_class: str | None = None
        if request.strict_identity is not None and (
            candidate.provider, candidate.model
        ) != request.strict_identity:
            reason, failure_class = "strict directive identity mismatch", "DIRECTIVE_MISMATCH"
        elif candidate.health not in {"HEALTHY", "READY"}:
            health_class = "HEALTH_UNKNOWN" if candidate.health == "UNKNOWN" else "HEALTH_UNAVAILABLE"
            reason, failure_class = f"health is {candidate.health}", health_class
        elif candidate.auth_state not in {"READY", "CONFIGURED"}:
            auth_class = "AUTH_UNKNOWN" if candidate.auth_state == "UNKNOWN" else "AUTH_UNAVAILABLE"
            reason, failure_class = f"auth is {candidate.auth_state}", auth_class
        elif candidate.availability not in {"AVAILABLE", "READY"}:
            availability_class = "PROVIDER_UNKNOWN" if candidate.availability == "UNKNOWN" else "PROVIDER_UNAVAILABLE"
            reason, failure_class = f"availability is {candidate.availability}", availability_class
        elif request.required_capabilities:
            declared = set(candidate.capabilities)
            missing = sorted(set(request.required_capabilities) - declared)
            if missing:
                reason, failure_class = (
                    f"missing capabilities: {', '.join(missing)}",
                    "CAPABILITY_MISMATCH",
                )
        if reason is None and request.context_tokens is not None:
            if candidate.context_window_tokens is None:
                reason, failure_class = "context window is UNKNOWN", "CONTEXT_UNKNOWN"
            elif candidate.context_window_tokens < request.context_tokens:
                reason, failure_class = "context window is too small", "CONTEXT_LIMIT"
        if reason is None and request.latency_budget_ms is not None:
            if candidate.latency_ms is None:
                reason, failure_class = "latency is UNKNOWN", "LATENCY_UNKNOWN"
            elif candidate.latency_ms > request.latency_budget_ms:
                reason, failure_class = "latency budget exceeded", "LATENCY_LIMIT"
        if reason is None and request.reliability_floor is not None:
            if candidate.reliability is None:
                reason, failure_class = "reliability is UNKNOWN", "RELIABILITY_UNKNOWN"
            elif candidate.reliability < request.reliability_floor:
                reason, failure_class = "reliability floor not met", "RELIABILITY_LIMIT"
        if reason is None and request.max_cost_usd is not None:
            if candidate.cost_usd is None:
                reason, failure_class = "cost is UNKNOWN", "COST_UNKNOWN"
            elif candidate.cost_usd > request.max_cost_usd:
                reason, failure_class = "cost limit exceeded", "COST_LIMIT"
        if reason is None:
            eligible.append(candidate)
        else:
            rejected.append({
                "provider": candidate.provider,
                "model": candidate.model,
                "reason": reason,
                "failure_class": failure_class,
                "evidence_refs": list(candidate.evidence_refs),
            })
    if eligible:
        selected = eligible[0]
        fallback_used = bool(preferred and selected.provider != preferred)
        return RoutingDecision(
            "FALLBACK" if fallback_used else "SELECTED",
            selected,
            tuple(eligible),
            tuple(rejected),
            None,
            "selected observed capable provider/model" if not fallback_used
            else f"rerouted from {preferred}: preferred candidate was not eligible",
            fallback_used=fallback_used,
            evidence_refs=tuple(dict.fromkeys(
                ref for item in (selected, *eligible[1:]) for ref in item.evidence_refs)),
        )
    if not candidates_list:
        return RoutingDecision(
            "UNKNOWN", None, (), (), "NO_CANDIDATES",
            "no provider/model candidates were registered")
    unknown_classes = {
        "HEALTH_UNAVAILABLE", "HEALTH_UNKNOWN", "AUTH_UNAVAILABLE", "AUTH_UNKNOWN",
        "PROVIDER_UNAVAILABLE", "PROVIDER_UNKNOWN",
        "CONTEXT_UNKNOWN", "LATENCY_UNKNOWN", "RELIABILITY_UNKNOWN", "COST_UNKNOWN",
    }
    failures = [item.get("failure_class") for item in rejected]
    if any(item in unknown_classes for item in failures):
        status = "UNKNOWN" if any(item and item.endswith("UNKNOWN") for item in failures) else "UNAVAILABLE"
    else:
        status = "BLOCKED"
    first = rejected[0] if rejected else {}
    return RoutingDecision(
        status, None, (), tuple(rejected),
        str(first.get("failure_class") or "NO_ELIGIBLE_PROVIDER"),
        "no candidate satisfied the requested capability and operating constraints",
        evidence_refs=tuple(dict.fromkeys(
            ref for item in candidates_list for ref in item.evidence_refs)),
    )


class RoleRouter:
    def __init__(
        self,
        roles: dict[str, RoleConfig] | None = None,
        *,
        provider_settings: ProviderSettings | None = None,
    ) -> None:
        self._provider_settings = provider_settings or default_provider_settings()
        self._roles = dict(roles or {})
        for role, defaults in builtin_roles(self._provider_settings).items():
            self._roles.setdefault(role, RoleConfig(role=role, **defaults))

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        provider_settings: ProviderSettings | None = None,
    ) -> "RoleRouter":
        """Load a role configuration from a model_roles YAML file.

        Unknown/missing files fall back to the built-in configuration so the
        router is always usable in staging; a malformed file raises.
        """
        path = Path(path)
        if not path.exists():
            return cls()
        if yaml is None:  # pragma: no cover
            raise RoleConfigurationError("pyyaml is required to load role configuration")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "roles" not in raw:
            raise RoleConfigurationError(f"role configuration {path} must contain a 'roles' mapping")
        roles: dict[str, RoleConfig] = {}
        for role, spec in raw["roles"].items():
            if not isinstance(spec, dict):
                raise RoleConfigurationError(f"role {role!r} must be a mapping")
            preferred = str(spec.get("preferred_provider") or "")
            model = str(spec.get("preferred_model") or "")
            if not preferred or preferred.startswith("__"):
                raise RoleConfigurationError(
                    f"role {role!r} has no concrete preferred_provider (template placeholder)")
            if not model or model.startswith("__"):
                raise RoleConfigurationError(
                    f"role {role!r} has no concrete preferred_model (template placeholder)")
            fallbacks = tuple(
                str(item) for item in spec.get("fallbacks", [])
                if str(item) and not str(item).startswith("__")
            )
            roles[role] = RoleConfig(
                role=role,
                preferred_provider=preferred,
                preferred_model=model,
                fallbacks=fallbacks,
                independence_group=str(spec.get("independence_group", role)),
                required_capabilities=tuple(
                    str(item) for item in spec.get("required_capabilities", [])),
            )
        return cls(roles, provider_settings=provider_settings)

    def known_roles(self) -> tuple[str, ...]:
        return tuple(sorted(self._roles))

    @property
    def provider_settings(self) -> ProviderSettings:
        """Trusted provider policy used to construct this router."""
        return self._provider_settings

    def config(self, role: str) -> RoleConfig:
        if role not in self._roles:
            raise RoleConfigurationError(f"unknown role: {role!r}")
        return self._roles[role]

    def provider_chain(self, role: str) -> tuple[str, ...]:
        return self.config(role).provider_chain()

    def route(
        self,
        request: RoutingRequest,
        candidates: Iterable[ProviderRouteCandidate],
        *,
        role: str | None = None,
    ) -> RoutingDecision:
        """Plan a capability-aware route using this router's role policy.

        ``role`` only supplies the preferred provider and required capability
        defaults.  The result is still a pure, read-only decision; production
        dispatch must continue to call ``enforce_directive_routing`` before
        executing any real work.
        """
        if role is None:
            return route_provider_candidates(request, candidates)
        config = self.config(role)
        required = request.required_capabilities or config.required_capabilities
        preferred = request.preferred_provider or config.preferred_provider
        return route_provider_candidates(
            RoutingRequest(
                task_type=request.task_type,
                required_capabilities=tuple(required),
                reasoning=request.reasoning,
                context_tokens=request.context_tokens,
                latency_budget_ms=request.latency_budget_ms,
                reliability_floor=request.reliability_floor,
                max_cost_usd=request.max_cost_usd,
                preferred_provider=preferred,
                strict_identity=request.strict_identity,
            ),
            candidates,
        )

    def enforce_directive_routing(self) -> None:
        """Reject any configuration that deviates from DIR-2026-08-12.

        Every role must resolve to exactly ``DIRECTIVE_PROVIDER`` +
        ``DIRECTIVE_MODEL`` with NO fallbacks; a non-compliant role may never
        run — it must be recorded BLOCKED, never substituted with another
        model. Raises RoleConfigurationError naming the offending roles.
        Called by the production wiring (``v3_loop.run_one_loop`` and the
        live loop drivers) BEFORE any mutation or dispatch.
        """
        authority = self._provider_settings
        offenders = sorted(
            role for role, cfg in self._roles.items()
            if cfg.preferred_provider != authority.provider
            or cfg.preferred_model != authority.model
            or bool(cfg.fallbacks)
        )
        if offenders:
            raise RoleConfigurationError(
                "DIR-2026-08-12 mandatory model routing violated for roles "
                f"{offenders}: every real JARVIS role must resolve to "
                f"provider={authority.provider!r} model={authority.model!r} "
                "with no fallbacks; a non-compliant role must be recorded "
                "BLOCKED, never substituted with another model")

    def prefer_diversity(self) -> bool:
        """Provider diversity (builder != verifier) is the standing preference.

        True when the configuration makes a distinct verifier provider
        possible — some verifier-chain provider differs from the builder's
        preferred provider. This is consistent with ``diversity_resolution``:
        when this returns True, ``diversity_resolution(builder_provider)``
        can actually return a different provider.

        NOTE (DIR-2026-08-12): during this build window the owner's
        DeepSeek-only routing constraint overrides the diversity preference,
        so the DEFAULT configuration returns False — verifier independence
        is delivered through separate verifier sessions, read-only
        verification of frozen artifacts and deterministic adversarial
        probes, with the provider-diversity limitation recorded visibly.
        """
        builder = self._roles["builder"].provider_chain()
        verifier = self._roles["verifier"].provider_chain()
        if not builder or not verifier:
            return False
        return any(provider != builder[0] for provider in verifier)

    def diversity_resolution(self, builder_provider: str) -> str:
        """Best-effort verifier provider distinct from the builder's.

        Returns the first verifier-chain provider that differs from
        ``builder_provider``; if none exists, returns the verifier's
        preferred provider (limitation recorded by the caller).
        """
        for provider in self._roles["verifier"].provider_chain():
            if provider != builder_provider:
                return provider
        return self._roles["verifier"].preferred_provider

    def clone_with(self, overrides: dict[str, Any] | None = None, **kwargs: dict[str, Any]) -> "RoleRouter":
        """Return a router with per-role overrides (used for tests and staging).

        Accepts either a single mapping of role -> patch, or keyword overrides.
        """
        if overrides is None:
            overrides = kwargs
        roles = {role: copy.copy(cfg) for role, cfg in self._roles.items()}
        for role, patch in overrides.items():
            if role not in roles:
                raise RoleConfigurationError(f"unknown role in override: {role!r}")
            current = roles[role]
            roles[role] = RoleConfig(
                role=current.role,
                preferred_provider=str(patch.get("preferred_provider", current.preferred_provider)),
                preferred_model=str(patch.get("preferred_model", current.preferred_model)),
                fallbacks=tuple(patch.get("fallbacks", current.fallbacks)),
                independence_group=str(patch.get("independence_group", current.independence_group)),
                required_capabilities=tuple(patch.get("required_capabilities", current.required_capabilities)),
            )
        return RoleRouter(roles, provider_settings=self._provider_settings)
