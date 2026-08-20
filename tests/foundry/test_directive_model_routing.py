"""DIR-2026-08-12 mandatory internal model routing tests.

Contract: every REAL JARVIS internal role (executive, builder, verifier,
critic, repair, researcher, premium_reviewer) must resolve to provider
``opencode_go`` + model ``deepseek-v4-flash`` with NO fallbacks. A
non-compliant configuration must be rejected BEFORE any mutation or
dispatch (the dispatch would have to be recorded BLOCKED, never
substituted with another model). The default (builtin) configuration is
the policy; ``enforce_directive_routing`` guards the production wiring
(``v3_loop.run_one_loop``, live loop drivers).

The owner's DeepSeek-only constraint overrides provider-diversity
preferences during this build window; verifier independence is delivered
through separate verifier sessions, read-only verification of frozen
artifacts and deterministic adversarial probes (limitation recorded).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.roles.adapter import FakeHermesAdapter, ProviderUnavailable
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.results import BLOCKED, COMPLETED
from jarvis_foundry.roles.router import (
    BUILTIN_ROLES,
    DIRECTIVE_MODEL,
    DIRECTIVE_PROVIDER,
    RoleConfigurationError,
    RoleRouter,
)

FORBIDDEN_MODELS = (
    "kimi-k2.7-code",
    "kimi-k2.5",
    "glm-5",
    "glm-4.5",
    "gemini-2.5-pro",
    "deepseek-v4-pro",
    "claude-sonnet",
    "qwen3.8-max",
)
FORBIDDEN_PROVIDERS = ("vertex_ai", "anthropic", "vertex")


# ---------------------------------------------------------------------------
# Builtin configuration IS the policy
# ---------------------------------------------------------------------------

def test_every_builtin_role_is_deepseek_only():
    """Every role in the builtin configuration resolves to the directive
    pair with no fallbacks — a new role added to BUILTIN_ROLES must follow."""
    assert set(BUILTIN_ROLES) >= {
        "executive", "builder", "verifier", "critic", "repair",
        "researcher", "premium_reviewer"}
    for role, spec in BUILTIN_ROLES.items():
        assert spec["preferred_provider"] == DIRECTIVE_PROVIDER, role
        assert spec["preferred_model"] == DIRECTIVE_MODEL, role
        assert spec["fallbacks"] == [], role


def test_default_router_passes_enforcement():
    RoleRouter().enforce_directive_routing()  # must not raise


def test_default_router_resolves_every_role():
    router = RoleRouter()
    for role in router.known_roles():
        assert router.provider_chain(role) == (DIRECTIVE_PROVIDER,)
        assert router.config(role).preferred_model == DIRECTIVE_MODEL


# ---------------------------------------------------------------------------
# Enforcement rejects every forbidden deviation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model", FORBIDDEN_MODELS)
def test_enforcement_rejects_forbidden_model(model):
    router = RoleRouter().clone_with({
        "builder": {"preferred_model": model},
    })
    with pytest.raises(RoleConfigurationError, match="DIR-2026-08-12"):
        router.enforce_directive_routing()


@pytest.mark.parametrize("provider", FORBIDDEN_PROVIDERS)
def test_enforcement_rejects_forbidden_provider(provider):
    router = RoleRouter().clone_with({
        "premium_reviewer": {"preferred_provider": provider},
    })
    with pytest.raises(RoleConfigurationError, match="DIR-2026-08-12"):
        router.enforce_directive_routing()


@pytest.mark.parametrize("role", ("builder", "verifier", "repair", "critic",
                                  "researcher", "premium_reviewer",
                                  "executive"))
def test_enforcement_rejects_any_fallback_for_any_role(role):
    router = RoleRouter().clone_with({
        role: {"fallbacks": ("opencode_go",)},
    })
    with pytest.raises(RoleConfigurationError, match="DIR-2026-08-12"):
        router.enforce_directive_routing()


def test_enforcement_rejects_kimi_builder_but_passes_after_repair():
    """A/B: the same role with the old model fails; the directive model passes."""
    router = RoleRouter().clone_with({
        "builder": {"preferred_model": "kimi-k2.7-code"},
    })
    with pytest.raises(RoleConfigurationError):
        router.enforce_directive_routing()
    router = RoleRouter().clone_with({
        "builder": {"preferred_model": DIRECTIVE_MODEL},
    })
    router.enforce_directive_routing()  # must not raise


def test_enforcement_reports_all_offending_roles():
    router = RoleRouter().clone_with({
        "builder": {"preferred_model": "kimi-k2.7-code"},
        "premium_reviewer": {"preferred_provider": "vertex_ai"},
        "repair": {"fallbacks": ("anthropic",)},
    })
    with pytest.raises(RoleConfigurationError) as exc:
        router.enforce_directive_routing()
    message = str(exc.value)
    assert "builder" in message and "premium_reviewer" in message \
        and "repair" in message


# ---------------------------------------------------------------------------
# YAML configuration paths
# ---------------------------------------------------------------------------

def test_yaml_compliant_passes_enforcement(tmp_path):
    config = tmp_path / "roles.yaml"
    config.write_text("""
roles:
  executive:
    preferred_provider: "opencode_go"
    preferred_model: "muse-spark-1.2-contributor"
    fallbacks: []
  builder:
    preferred_provider: "opencode_go"
    preferred_model: "muse-spark-1.2-contributor"
    fallbacks: []
  verifier:
    preferred_provider: "opencode_go"
    preferred_model: "muse-spark-1.2-contributor"
    fallbacks: []
""", encoding="utf-8")
    router = RoleRouter.from_yaml(config)
    router.enforce_directive_routing()  # must not raise


def test_yaml_kimi_or_fallback_rejected_under_enforcement(tmp_path):
    config = tmp_path / "roles.yaml"
    config.write_text("""
roles:
  builder:
    preferred_provider: "opencode_go"
    preferred_model: "kimi-k2.7-code"
    fallbacks: ["vertex_ai"]
  verifier:
    preferred_provider: "opencode_go"
    preferred_model: "muse-spark-1.2-contributor"
    fallbacks: []
""", encoding="utf-8")
    router = RoleRouter.from_yaml(config)
    with pytest.raises(RoleConfigurationError, match="DIR-2026-08-12"):
        router.enforce_directive_routing()


# ---------------------------------------------------------------------------
# Production wiring refuses a non-compliant router BEFORE any mutation
# ---------------------------------------------------------------------------

def test_run_one_loop_refuses_noncompliant_router_before_mutation(tmp_path):
    from jarvis_foundry.v3_loop import run_one_loop

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    with database.connect() as connection:
        before = connection.execute("SELECT count(*) FROM events").fetchone()[0]
    router = RoleRouter().clone_with({
        "builder": {"preferred_model": "kimi-k2.7-code"},
    })
    with pytest.raises(RoleConfigurationError, match="DIR-2026-08-12"):
        run_one_loop(
            database, loop_id="refuse-1",
            adapter=FakeHermesAdapter(), router=router,
            mission_id="mission-test", resource_id="resource-test",
            target_root=tmp_path / "target")
    # zero events appended, zero mutations: the loop refused to even observe
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == before
        assert connection.execute("SELECT count(*) FROM missions").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0


def test_run_one_loop_accepts_directive_router(tmp_path):
    """The compliant router passes the gate (A/B against the refusal above)."""
    from jarvis_foundry.v3_loop import run_one_loop

    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "project.registered",
        {"resource_id": "resource-test", "kind": "git_worktree",
         "root_path": str(tmp_path), "resolved_root": str(tmp_path),
         "baseline": {"branch": "main", "head": "x", "remotes": [],
                      "status_porcelain": []},
         "capabilities": {
             "write_registered_worktree_files": "READY",
             "read_registered_files": "READY"},
         "readiness_state": "READY"},
        source_type="test", source_id="test-suite",
        idempotency_key="project.registered:resource-test"))
    report = run_one_loop(
        database, loop_id="accept-1",
        adapter=FakeHermesAdapter(), router=RoleRouter(),
        mission_id="mission-test", resource_id="resource-test",
        target_root=tmp_path / "target", vault_root=tmp_path / "vault",
        handoff_dir=tmp_path / "handoffs")
    # enforcement passed; the loop ran — with an empty candidate table the
    # loop must report honest WAITING, never crash (regression: the WAITING
    # reason read top[0] on an empty list -> IndexError)
    assert report["loop_id"] == "accept-1"
    assert report["outcome"] == "WAITING"
    assert "no candidate" in report["reason"]


# ---------------------------------------------------------------------------
# Dispatcher: no substitution — single-provider exhaustion is BLOCKED
# ---------------------------------------------------------------------------

def test_dispatch_blocked_on_provider_unavailable_no_substitution(tmp_path):
    """DIR-2026-08-12 step 5: with the directive router, a provider outage
    must land in BLOCKED — the dispatcher must NOT substitute another
    provider/model (there are no fallbacks configured)."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()

    class AllDown(FakeHermesAdapter):
        def dispatch(self, manifest, *, heartbeat):
            raise ProviderUnavailable(f"down: {manifest.provider}")

    dispatcher = RoleDispatcher(database, RoleRouter(), AllDown())
    result = dispatcher.dispatch(
        role="builder", mission_id="mission-1",
        resource_id="resource-1", capability="write", run_id="run-ds1")
    assert result.status == BLOCKED
    assert result.error_class == "provider_unavailable"
    assert result.provider == DIRECTIVE_PROVIDER
    assert result.model == DIRECTIVE_MODEL
    # ledger truth: started once with the directive pair, failed as
    # provider_unavailable, never completed, never substituted
    row = database.row("agent_runs", "run_id", "run-ds1")
    assert row["provider"] == DIRECTIVE_PROVIDER
    assert row["model"] == DIRECTIVE_MODEL
    assert row["status"] == "FAILED"
    assert row["failure_class"] == "provider_unavailable"
    with database.connect() as connection:
        leases = connection.execute("SELECT * FROM leases").fetchall()
    assert leases == []  # lease released even on BLOCKED


def test_dispatch_records_deepseek_model_in_ledger(tmp_path):
    """A real dispatch through the default router must record the directive
    pair in agent_run.started — the ledger row is the audit truth."""
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    dispatcher = RoleDispatcher(database, RoleRouter(), FakeHermesAdapter())
    result = dispatcher.dispatch(
        role="builder", mission_id="mission-1",
        resource_id="resource-1", capability="write", run_id="run-ds2")
    assert result.status == COMPLETED
    row = database.row("agent_runs", "run_id", "run-ds2")
    assert row["provider"] == DIRECTIVE_PROVIDER
    assert row["model"] == DIRECTIVE_MODEL


def test_diversity_limitation_recorded_on_default_config():
    """The DeepSeek-only constraint overrides the diversity preference:
    the default configuration cannot offer a distinct verifier provider —
    independence is delivered by separate sessions + frozen artifacts."""
    router = RoleRouter()
    assert router.prefer_diversity() is False
    assert router.diversity_resolution(DIRECTIVE_PROVIDER) == DIRECTIVE_PROVIDER
