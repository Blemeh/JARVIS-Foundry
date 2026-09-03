"""V12 product proof through the canonical mission/provider runtime path."""
from __future__ import annotations

from dataclasses import replace
import subprocess
from pathlib import Path

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import execute_fabric_effect, reconcile_effect
from jarvis_foundry.events import Event
from jarvis_foundry.executive.continuous import resume_mission_provider_waits
from jarvis_foundry.fabric.adapter import FabricRoleAdapter
from jarvis_foundry.mission.loop import BLOCKED_OUTCOME, MissionLoop, PASSED
from jarvis_foundry.roles.adapter import ProviderFailure
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.results import COMPLETED, RoleResult
from jarvis_foundry.roles.router import ProviderRouteCandidate, RoleRouter
from jarvis_foundry.runtime.provider_registry import (
    ProviderReadiness,
    record_provider_readiness,
)
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.provider_config import (
    ProviderSettings,
    load_provider_settings_pool,
)
from jarvis_foundry.workers.adapters.fakes import ScriptedWorkerAdapter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES
from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter
from jarvis_foundry.workers.router import READY, WorkerRouter


ALL_CAPABILITIES = (
    "structured_reasoning", "code", "tools", "tests", "research",
    "artifact_inspection", "adversarial_verification", "adversarial_review",
)


def candidate(
    provider: str,
    *,
    readiness: str = "READY",
    capabilities: tuple[str, ...] = ALL_CAPABILITIES,
    policy_allowed: bool = True,
    owner_allowed: bool = True,
    cost_usd: float = 0.01,
) -> ProviderRouteCandidate:
    available = readiness in {"READY", "DEGRADED"}
    return ProviderRouteCandidate(
        provider=provider,
        model=f"{provider}-model",
        capabilities=capabilities,
        task_classes=("builder", "verifier", "repair"),
        tool_support=("hermes_oneshot",),
        readiness=readiness,
        health="READY" if available else readiness,
        auth_state="READY" if available else "UNKNOWN",
        availability="AVAILABLE" if available else readiness,
        data_use_classification="NO_TRAINING",
        noninteractive_allowed=policy_allowed,
        owner_allowed=owner_allowed,
        cost_usd=cost_usd,
        evidence_refs=(f"probe:{provider}:{readiness}",),
    )


class ProviderScript:
    """One adapter boundary with deterministic per-provider behavior."""

    def __init__(self, outcomes: dict[str, list[object]] | None = None,
                 *, worktree: Path | None = None) -> None:
        self.outcomes = {key: list(value) for key, value in (outcomes or {}).items()}
        self.received = []
        self.worktree = worktree

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        self.received.append(manifest)
        heartbeat()
        queue = self.outcomes.get(manifest.provider, [])
        outcome = queue.pop(0) if queue else "PASS"
        if isinstance(outcome, BaseException):
            raise outcome
        if self.worktree is not None and manifest.role in {"builder", "repair"}:
            (self.worktree / "result.txt").write_text(
                f"built by {manifest.provider}\n", encoding="utf-8")
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider=manifest.provider,
            model=manifest.model,
            verdict=str(outcome),
            evidence=(f"{manifest.provider}:{manifest.role}",),
        )


def database(tmp_path: Path) -> FoundryDatabase:
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    return db


def router(*, verifier_provider: str = "provider-a") -> RoleRouter:
    return RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "provider-a",
            "preferred_model": "provider-a-model",
            "fallbacks": ("provider-b",),
        },
        "verifier": {
            "preferred_provider": verifier_provider,
            "preferred_model": f"{verifier_provider}-model",
            "fallbacks": (),
        },
        "repair": {
            "preferred_provider": "provider-a",
            "preferred_model": "provider-a-model",
            "fallbacks": ("provider-b",),
        },
    })


def dispatch(db: FoundryDatabase, adapter: ProviderScript,
             candidates: tuple[ProviderRouteCandidate, ...],
             *, run_id: str, budget_usd: float = 0.0) -> RoleResult:
    return RoleDispatcher(
        db, router(), adapter,
        provider_candidates=candidates,
        max_total_attempts=3,
        max_retries_per_provider=0,
    ).dispatch(
        role="builder", mission_id="mission-route", resource_id="resource-route",
        capability="write", step_id="step-route", run_id=run_id,
        instructions="perform the bounded disposable build",
        budget_usd=budget_usd,
    )


def mission_fixture(tmp_path: Path) -> tuple[FoundryDatabase, Path, ActionPolicy]:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "v12@example.invalid"],
                   cwd=repository, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "V12 Proof"],
                   cwd=repository, check=True, capture_output=True, text=True)
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repository,
                   check=True, capture_output=True, text=True)
    db = database(tmp_path)
    db.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-09-03T00:00:00Z", "standing_mandate": True},
        source_type="owner", source_id="v12-proof",
        idempotency_key="system.start-build:v12-proof",
    ))
    db.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-v12")))
    db.append(Event.create(
        "mission.created",
        {
            "mission_id": "mission-v12", "objective": "provider resilience proof",
            "resource_id": "resource-v12", "state": "PLANNED",
            "acceptance_criteria": ["result exists"],
            "next_action": "build result through a compatible provider",
        },
        source_type="test", source_id="v12-proof",
        idempotency_key="mission.created:mission-v12",
    ))
    for state in ("ORIENTING", "ACTION_SELECTION"):
        db.append(Event.create(
            "mission.state_changed",
            {
                "mission_id": "mission-v12", "state": state,
                "next_action": "build result through a compatible provider",
            },
            source_type="test", source_id="v12-proof",
            idempotency_key=f"mission.state_changed:mission-v12:{state}",
        ))
    set_capability_state(
        db, "resource-v12", "git_local_commit", "READY",
        evidence={"source": "v12-disposable-proof"},
    )
    return db, repository, ActionPolicy(db, tmp_path / "STOP")


def step(repository: Path, *, run_id: str | None = None) -> dict[str, object]:
    values: dict[str, object] = {
        "mission_id": "mission-v12",
        "step_id": "step-v12",
        "capability": "write_registered_worktree_files",
        "target": repository / "result.txt",
        "instructions": "create result.txt",
        "expected_effects": ("create result.txt",),
        "rollback": {"kind": "delete_created_file"},
        "expected_artifacts": ("result.txt",),
    }
    if run_id is not None:
        values["run_id"] = run_id
    return values


def test_primary_healthy_routes_normally(tmp_path):
    db = database(tmp_path)
    adapter = ProviderScript()
    result = dispatch(
        db, adapter, (candidate("provider-a"), candidate("provider-b")),
        run_id="run-primary",
    )
    assert result.status == COMPLETED, result.to_payload()
    assert [item.provider for item in adapter.received] == ["provider-a"]


def test_machine_provider_pool_extends_existing_primary_boundary(tmp_path):
    path = tmp_path / "provider.yaml"
    path.write_text(
        "provider: opencode_go\n"
        "model: muse-spark-1.2-contributor\n"
        "base_url: https://opencode.ai/zen/go/v1\n"
        "api_key: primary-test-key\n"
        "providers:\n"
        "  - provider: opencode_zen\n"
        "    model: x-preview-f-free\n"
        "    base_url: https://opencode.ai/zen/v1\n"
        "    api_key: fallback-test-key\n",
        encoding="utf-8",
    )
    pool = load_provider_settings_pool(config_path=path)
    assert pool.primary.provider == "opencode_go"
    assert pool.identities() == (
        ("opencode_go", "muse-spark-1.2-contributor"),
        ("opencode_zen", "x-preview-f-free"),
    )


def test_profile_cannot_enable_noninteractive_training_policy(tmp_path):
    from jarvis_foundry.runtime._stack import _provider_candidates

    db = database(tmp_path)
    settings = ProviderSettings(
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        base_url="https://opencode.ai/zen/go/v1",
        api_key="disposable-test-key",
        source="test",
    )
    config = WorkerRuntimeConfig(
        provider="opencode_go",
        model="muse-spark-1.2-contributor",
        provider_settings=settings,
        provider_settings_pool=(settings,),
        provider_resilience_enabled=True,
        hermes={},
        provider_resilience={
            "enabled": True,
            "profiles": {
                "opencode_go": {
                    "readiness": "READY",
                    "evidence_refs": ["disposable-policy-probe"],
                    # Even a profile assertion cannot bypass the existing
                    # Hermes owner-consent authority.
                    "data_use_classification": "NO_TRAINING",
                },
            },
        },
    )
    candidates = _provider_candidates(db, config)
    assert candidates[0].noninteractive_allowed is False
    adapter = ProviderScript()
    result = RoleDispatcher(
        db, RoleRouter(provider_settings=settings), adapter,
        provider_candidates=candidates,
    ).dispatch(
        role="builder", mission_id="policy-mission",
        resource_id="policy-resource", capability="write",
        step_id="policy-step", run_id="policy-run",
        instructions="must remain blocked without owner consent",
    )
    assert result.error_class == "provider_policy_blocked"
    assert adapter.received == []
    assert config.hermes.get(
        "allow_data_training_tiers_noninteractive") is not True


def test_unknown_data_use_policy_fails_closed_even_with_noninteractive_flag(tmp_path):
    db = database(tmp_path)
    adapter = ProviderScript()
    unknown_policy = replace(
        candidate("provider-b", policy_allowed=True),
        data_use_classification="UNKNOWN_POLICY",
    )
    result = dispatch(
        db,
        adapter,
        (candidate("provider-a", readiness="OUTAGE"), unknown_policy),
        run_id="run-unknown-policy",
    )
    assert result.error_class == "provider_policy_blocked"
    assert adapter.received == []
    rejected = [
        item
        for event in db.events()
        if event.event_type == "provider.route_decided"
        for item in event.payload["rejected"]
    ]
    assert any(
        item["provider"] == "provider-b"
        and item["failure_class"] == "BLOCKED_POLICY"
        for item in rejected
    )


def test_outage_and_rate_limit_take_one_compatible_alternate(tmp_path):
    for name, failure in (
        ("outage", ProviderFailure("network outage", failure_class="OUTAGE")),
        ("rate", ProviderFailure(
            "HTTP 429", failure_class="RATE_LIMITED", retryable=True,
            retry_after_seconds=30,
        )),
    ):
        db = database(tmp_path / name)
        adapter = ProviderScript({"provider-a": [failure]})
        result = dispatch(
            db, adapter, (candidate("provider-a"), candidate("provider-b")),
            run_id=f"run-{name}",
        )
        assert result.status == COMPLETED
        assert [item.provider for item in adapter.received] == [
            "provider-a", "provider-b",
        ]
        attempts = [event for event in db.events()
                    if event.event_type == "provider.dispatch_attempted"]
        assert len(attempts) == 2


def test_classified_failure_crosses_real_hybrid_worker_bridge(tmp_path):
    db, repository, _policy = mission_fixture(tmp_path)

    class HybridProviderAdapter(ScriptedWorkerAdapter):
        def __init__(self) -> None:
            super().__init__(WORKER_HERMES)
            self.providers: list[str] = []

        def dispatch(self, contract, *, heartbeat):
            self.providers.append(contract.provider)
            if contract.provider == "provider-a":
                raise ProviderFailure(
                    "provider-a transport unavailable", failure_class="OUTAGE")
            return super().dispatch(contract, heartbeat=heartbeat)

    worker = HybridProviderAdapter()
    worker_router = WorkerRouter()
    worker_router.mark_ready(
        WORKER_HERMES, state=READY, note="disposable V12 bridge",
        evidence=("v12-bridge-proof",),
    )
    bridge = HybridWorkerRoleAdapter(
        database=db,
        router=worker_router,
        registry=WorkerRegistry({WORKER_HERMES: worker}),
        role_signals={"builder": {
            "ambiguous": True, "root_cause_required": True,
        }},
    )
    result = RoleDispatcher(
        db,
        router(),
        bridge,
        provider_candidates=(candidate("provider-a"), candidate("provider-b")),
        max_total_attempts=2,
    ).dispatch(
        role="builder",
        mission_id="mission-v12",
        resource_id="resource-v12",
        capability="write_registered_worktree_files",
        step_id="step-hybrid",
        run_id="run-hybrid-provider-fallback",
        instructions="exercise the canonical hybrid provider boundary",
    )
    assert result.status == COMPLETED, result.to_payload()
    assert worker.providers == ["provider-a", "provider-b"]
    assert result.provider == "provider-b"


def test_canonical_local_fabric_transition_is_explicit_and_compatible(tmp_path):
    db, repository, policy = mission_fixture(tmp_path)
    inner = ProviderScript(worktree=repository)
    adapter = FabricRoleAdapter(db, owned_root=repository, inner=inner)
    loop = MissionLoop(
        db,
        router(verifier_provider="provider-a"),
        adapter,
        policy=policy,
        provider_candidates=(candidate("provider-a"), candidate("provider-b")),
    )
    outcome = loop.run_step(**step(repository, run_id="run-fabric-transition"))
    assert outcome.outcome == PASSED
    assert [item for item in inner.received if item.role == "builder"] == []
    transitions = [event.payload for event in db.events()
                   if event.event_type == "provider.identity_transition_authorized"]
    assert len(transitions) == 1
    assert transitions[0]["observed_provider"] == "fabric"
    assert transitions[0]["authority"] == "canonical_capability_fabric"


def test_incompatible_policy_blocked_and_over_budget_fallbacks_are_rejected(tmp_path):
    cases = {
        "incompatible": candidate(
            "provider-b", capabilities=("structured_reasoning",)),
        "policy": candidate("provider-b", policy_allowed=False),
        "owner": candidate("provider-b", owner_allowed=False),
        "cost": candidate("provider-b", cost_usd=5.0),
    }
    for name, fallback in cases.items():
        db = database(tmp_path / name)
        adapter = ProviderScript()
        result = dispatch(
            db,
            adapter,
            (candidate("provider-a", readiness="OUTAGE"), fallback),
            run_id=f"run-reject-{name}",
            budget_usd=0.50 if name == "cost" else 0.0,
        )
        assert result.status != COMPLETED
        assert adapter.received == []
        decisions = [event.payload for event in db.events()
                     if event.event_type == "provider.route_decided"]
        assert decisions and decisions[-1]["rejected"]


def test_fallback_chain_enforces_cumulative_cost_budget(tmp_path):
    db = database(tmp_path)
    adapter = ProviderScript({
        "provider-a": [ProviderFailure("outage", failure_class="OUTAGE")],
    })
    result = dispatch(
        db,
        adapter,
        (candidate("provider-a", cost_usd=0.40),
         candidate("provider-b", cost_usd=0.40)),
        run_id="run-cumulative-cost",
        budget_usd=0.50,
    )
    assert result.error_class == "provider_cost_blocked"
    assert [item.provider for item in adapter.received] == ["provider-a"]
    skipped = [event.payload for event in db.events()
               if event.event_type == "provider.dispatch_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["provider"] == "provider-b"
    assert skipped[0]["failure_class"] == "COST_LIMIT"


def test_provider_identity_switch_is_blocked_without_alternate_replay(tmp_path):
    class IdentitySwitchAdapter(ProviderScript):
        def dispatch(self, manifest, *, heartbeat) -> RoleResult:
            self.received.append(manifest)
            heartbeat()
            return RoleResult(
                run_id=manifest.run_id,
                role=manifest.role,
                status=COMPLETED,
                provider="provider-z",
                model="provider-z-model",
                verdict="PASS",
            )

    db = database(tmp_path)
    adapter = IdentitySwitchAdapter()
    result = dispatch(
        db,
        adapter,
        (candidate("provider-a"), candidate("provider-b")),
        run_id="run-identity-switch",
    )
    assert result.error_class == "provider_incompatible"
    assert [item.provider for item in adapter.received] == ["provider-a"]
    mismatches = [event.payload for event in db.events()
                  if event.event_type == "provider.identity_mismatch"]
    assert len(mismatches) == 1
    assert mismatches[0]["requested_provider"] == "provider-a"
    assert mismatches[0]["observed_provider"] == "provider-z"


def test_all_down_wait_then_positive_recovery_resumes_same_mission_and_run(tmp_path):
    db, repository, policy = mission_fixture(tmp_path)
    adapter = ProviderScript(worktree=repository)
    candidates = (
        candidate("provider-a", readiness="OUTAGE"),
        candidate("provider-b", readiness="RATE_LIMITED"),
    )
    loop = MissionLoop(
        db, router(verifier_provider="provider-a"), adapter, policy=policy,
        provider_candidates=candidates,
    )
    first = loop.run_step(**step(repository, run_id="run-continuity"))
    assert first.outcome == BLOCKED_OUTCOME
    assert db.row("missions", "mission_id", "mission-v12")["state"] == "BLOCKED"
    assert len([event for event in db.events()
                if event.event_type == "mission.provider_wait_started"]) == 1

    record_provider_readiness(
        db,
        provider_id="provider-a",
        model="provider-a-model",
        readiness=ProviderReadiness.READY,
        reason="disposable recovery probe passed",
        evidence_refs=("probe:provider-a:recovered",),
    )
    assert db.row("missions", "mission_id", "mission-v12")["state"] == "ACTION_SELECTION"
    resumed = [event for event in db.events()
               if event.event_type == "mission.provider_wait_resumed"]
    assert len(resumed) == 1
    second = loop.run_step(**step(repository))
    assert second.outcome == PASSED
    assert second.run_id == "run-continuity"
    assert len([event for event in db.events()
                if event.event_type == "mission.created"]) == 1
    builder_runs = {item.run_id for item in adapter.received if item.role == "builder"}
    assert builder_runs == {"run-continuity"}
    assert len([event for event in db.events()
                if event.event_type == "mission.provider_wait_resumed"]) == 1


def test_identical_outage_can_recur_after_a_recovery_transition(tmp_path):
    db = database(tmp_path)
    for readiness in (
        ProviderReadiness.OUTAGE,
        ProviderReadiness.READY,
        ProviderReadiness.OUTAGE,
    ):
        record_provider_readiness(
            db,
            provider_id="provider-a",
            model="provider-a-model",
            readiness=readiness,
            reason="same probe payload" if readiness is ProviderReadiness.OUTAGE
            else "recovered",
            notify_wakes=False,
        )
    states = [event.payload["readiness"] for event in db.events()
              if event.event_type == "provider.readiness_observed"]
    assert states == ["OUTAGE", "READY", "OUTAGE"]


class AmbiguousEffectAdapter(ProviderScript):
    def __init__(self, db: FoundryDatabase, worktree: Path) -> None:
        super().__init__(worktree=worktree)
        self.db = db
        self.primary_attempted = False
        self.effect_action_key = "effect-v12-provider-ambiguity"

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        if manifest.role == "builder" and manifest.provider == "provider-a" \
                and not self.primary_attempted:
            self.primary_attempted = True
            self.received.append(manifest)
            heartbeat()
            result = execute_fabric_effect(
                self.db,
                action_key=self.effect_action_key,
                system_id="provider-a",
                target_ref="disposable-target",
                payload_hash="v12-payload",
                kind="provider_mutation",
                connector_id="provider-a-adapter",
                perform=lambda: (_ for _ in ()).throw(
                    TimeoutError("response lost after possible mutation")),
            )
            assert result["journal_state"] == "FROZEN"
            raise ProviderFailure(
                "provider timed out after possible mutation",
                failure_class="OUTAGE",
                possible_effect=True,
                effect_action_key=self.effect_action_key,
            )
        return super().dispatch(manifest, heartbeat=heartbeat)


def test_ambiguous_effect_reconciles_before_alternate_dispatch(tmp_path):
    db, repository, policy = mission_fixture(tmp_path)
    adapter = AmbiguousEffectAdapter(db, repository)
    loop = MissionLoop(
        db, router(verifier_provider="provider-b"), adapter, policy=policy,
        provider_candidates=(candidate("provider-a"), candidate("provider-b")),
    )
    first = loop.run_step(**step(repository, run_id="run-ambiguous"))
    assert first.outcome == BLOCKED_OUTCOME
    assert [item.provider for item in adapter.received] == ["provider-a"]

    reconcile_effect(
        db,
        adapter.effect_action_key,
        provider_state={"exists": True, "provider_ref": "confirmed-v12"},
    )
    assert resume_mission_provider_waits(db) == [
        next(event.payload["wait_id"] for event in db.events()
             if event.event_type == "mission.provider_wait_started")
    ]
    second = loop.run_step(**step(repository))
    assert second.outcome == PASSED
    assert second.run_id == "run-ambiguous"
    builder_providers = [item.provider for item in adapter.received
                         if item.role == "builder"]
    assert builder_providers == ["provider-a", "provider-b"]
    ordered = [event.event_type for event in db.events()]
    reconciled_at = ordered.index("provider.effect_reconciled")
    fallback_at = next(
        index for index, event in enumerate(db.events())
        if event.event_type == "provider.dispatch_attempted"
        and event.payload.get("provider") == "provider-b"
    )
    assert reconciled_at < fallback_at
    assert len([event for event in db.events()
                if event.event_type == "mission.created"]) == 1
    assert len([event for event in db.events()
                if event.event_type == "system.action_started"]) == 1
