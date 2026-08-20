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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    def config(self, role: str) -> RoleConfig:
        if role not in self._roles:
            raise RoleConfigurationError(f"unknown role: {role!r}")
        return self._roles[role]

    def provider_chain(self, role: str) -> tuple[str, ...]:
        return self.config(role).provider_chain()

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
