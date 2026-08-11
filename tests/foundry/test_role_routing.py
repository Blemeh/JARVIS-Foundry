"""WP4 role routing tests: provider chains, fallback resolution, diversity,
and YAML configuration loading."""
from __future__ import annotations

import pytest

from jarvis_foundry.roles.router import (
    BUILTIN_ROLES,
    RoleConfig,
    RoleConfigurationError,
    RoleRouter,
)

ROOT = pytest.importorskip("jarvis_foundry").__file__  # noqa: F841


def test_builtin_roles_known():
    router = RoleRouter()
    for role in ("executive", "builder", "verifier", "critic", "repair"):
        assert role in router.known_roles()


def test_provider_chain_preferred_first_then_fallbacks():
    router = RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "opencode_go",
            "fallbacks": ("vertex_ai", "anthropic"),
        },
    })
    assert router.provider_chain("builder") == ("opencode_go", "vertex_ai", "anthropic")


def test_provider_chain_deduplicates():
    router = RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "opencode_go",
            "fallbacks": ("opencode_go", "vertex_ai"),
        },
    })
    assert router.provider_chain("builder") == ("opencode_go", "vertex_ai")


def test_unknown_role_rejected():
    router = RoleRouter()
    with pytest.raises(RoleConfigurationError):
        router.provider_chain("no_such_role")


def test_diversity_preference_when_chains_disjoint():
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
        "verifier": {"preferred_provider": "anthropic", "fallbacks": ()},
    })
    assert router.prefer_diversity() is True
    assert router.diversity_resolution("opencode_go") == "anthropic"


def test_diversity_resolution_falls_back_to_same_provider_when_forced():
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
        "verifier": {"preferred_provider": "opencode_go", "fallbacks": ()},
    })
    # No alternative exists -> same provider, limitation is recorded by caller.
    assert router.diversity_resolution("opencode_go") == "opencode_go"


def test_diversity_preferred_when_verifier_chain_contains_alternative():
    router = RoleRouter().clone_with({
        "builder": {"preferred_provider": "opencode_go", "fallbacks": ()},
        "verifier": {"preferred_provider": "opencode_go", "fallbacks": ("vertex_ai",)},
    })
    assert router.diversity_resolution("opencode_go") == "vertex_ai"


def test_from_yaml_loads_roles(tmp_path):
    config = tmp_path / "roles.yaml"
    config.write_text("""
schema_version: 1
status: "TEST"
roles:
  executive:
    preferred_provider: "opencode_go"
    preferred_model: "deepseek-v4-flash"
    fallbacks: []
    required_capabilities: ["structured_reasoning"]
    independence_group: "executive"
  builder:
    preferred_provider: "opencode_go"
    preferred_model: "kimi-k2.7-code"
    fallbacks: ["vertex_ai"]
    required_capabilities: ["code", "tools"]
    independence_group: "builder"
  verifier:
    preferred_provider: "anthropic"
    preferred_model: "claude-sonnet"
    fallbacks: []
    required_capabilities: ["artifact_inspection"]
    independence_group: "verifier"
""", encoding="utf-8")
    router = RoleRouter.from_yaml(config)
    assert router.provider_chain("builder") == ("opencode_go", "vertex_ai")
    assert router.provider_chain("verifier") == ("anthropic",)
    assert router.diversity_resolution("opencode_go") == "anthropic"


def test_from_yaml_missing_file_uses_builtin():
    router = RoleRouter.from_yaml("var/no-such-roles.yaml")
    assert router.known_roles() == tuple(sorted(BUILTIN_ROLES))


def test_from_yaml_rejects_template_placeholders(tmp_path):
    config = tmp_path / "roles.yaml"
    config.write_text("""
roles:
  verifier:
    preferred_provider: "__DIFFERENT_FROM_BUILDER_WHEN_POSSIBLE__"
    preferred_model: "__VERIFY_AT_ACTIVATION__"
    fallbacks: []
""", encoding="utf-8")
    with pytest.raises(RoleConfigurationError):
        RoleRouter.from_yaml(config)


def test_from_yaml_rejects_placeholder_model_with_concrete_provider(tmp_path):
    # Verifier finding (deleg_221bd327): a __-placeholder model must not flow
    # into live manifests even when the provider is concrete.
    config = tmp_path / "roles.yaml"
    config.write_text("""
roles:
  verifier:
    preferred_provider: "anthropic"
    preferred_model: "__VERIFY_AT_ACTIVATION__"
    fallbacks: []
""", encoding="utf-8")
    with pytest.raises(RoleConfigurationError):
        RoleRouter.from_yaml(config)


def test_from_yaml_rejects_malformed(tmp_path):
    config = tmp_path / "roles.yaml"
    config.write_text("not: [a valid structure", encoding="utf-8")
    with pytest.raises((RoleConfigurationError, Exception)):
        RoleRouter.from_yaml(config)


def test_clone_with_unknown_role_rejected():
    router = RoleRouter()
    with pytest.raises(RoleConfigurationError):
        router.clone_with({"ghost": {"preferred_provider": "x"}})


def test_role_config_provider_chain_empty_when_no_providers():
    config = RoleConfig(role="x", preferred_provider="", preferred_model="")
    assert config.provider_chain() == ()
