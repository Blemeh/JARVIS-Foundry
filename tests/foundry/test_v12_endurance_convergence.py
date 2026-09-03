"""Cross-lane V12 campaign/provider convergence proofs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    create_objective,
    rebuild_executive_state,
)
from jarvis_foundry.executive.campaigns import (
    CampaignError,
    campaign_dispatch_context,
    campaign_dispatchable_mission_ids,
    create_campaign,
    declare_child_mission,
    rebuild_campaign_state,
    record_child_state,
)
from jarvis_foundry.executive.continuous import (
    resume_mission_provider_waits,
    schedule_mission_provider_wait,
)
from jarvis_foundry.mission.loop import BLOCKED_OUTCOME, MissionLoop
from jarvis_foundry.roles.adapter import ProviderFailure
from jarvis_foundry.roles.dispatcher import RoleDispatcher
from jarvis_foundry.roles.results import COMPLETED, RoleResult
from jarvis_foundry.roles.router import (
    ProviderRouteCandidate,
    RoleRouter,
    RoutingRequest,
)
from jarvis_foundry.runtime.provider_registry import (
    ProviderReadiness,
    record_provider_readiness,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    create_objective(
        database,
        "V12 endurance convergence",
        objective_id="objective-endurance",
        success_criteria=("all exact campaign children accepted",),
    )
    return database


def _mission(database: FoundryDatabase, mission_id: str, *, state: str = "ACTION_SELECTION") -> None:
    database.append(Event.create(
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": f"execute {mission_id}",
            "resource_id": "resource-endurance",
            "state": "PLANNED",
            "acceptance_criteria": ["accepted final outcome"],
            "next_action": "perform exact child step",
        },
        source_type="test",
        source_id="v12-endurance",
        idempotency_key=f"mission.created:{mission_id}",
    ))
    for index, target in enumerate(("ORIENTING", state)):
        database.append(Event.create(
            "mission.state_changed",
            {
                "mission_id": mission_id,
                "state": target,
                "next_action": "perform exact child step",
            },
            source_type="test",
            source_id="v12-endurance",
            idempotency_key=f"mission.state_changed:{mission_id}:{index}:{target}",
        ))


def _candidate(
    provider: str,
    *,
    readiness: str = "READY",
    capabilities: tuple[str, ...] = ("code", "tests"),
) -> ProviderRouteCandidate:
    available = readiness in {"READY", "DEGRADED"}
    return ProviderRouteCandidate(
        provider=provider,
        model=f"{provider}-model",
        capabilities=capabilities,
        task_classes=("builder",),
        tool_support=("hermes_oneshot",),
        readiness=readiness,
        health="READY" if available else readiness,
        auth_state="READY" if available else "UNKNOWN",
        availability="AVAILABLE" if available else readiness,
        data_use_classification="NO_TRAINING",
        noninteractive_allowed=True,
        owner_allowed=True,
        evidence_refs=(f"probe:{provider}:{readiness}",),
    )


class _Adapter:
    def __init__(self, outcomes: dict[str, list[object]] | None = None) -> None:
        self.outcomes = {
            provider: list(values)
            for provider, values in (outcomes or {}).items()
        }
        self.received = []

    def dispatch(self, manifest, *, heartbeat) -> RoleResult:
        self.received.append(manifest)
        heartbeat()
        queue = self.outcomes.get(manifest.provider, [])
        outcome = queue.pop(0) if queue else None
        if isinstance(outcome, BaseException):
            raise outcome
        return RoleResult(
            run_id=manifest.run_id,
            role=manifest.role,
            status=COMPLETED,
            provider=manifest.provider,
            model=manifest.model,
            verdict="PASS",
            evidence=(f"provider:{manifest.provider}",),
        )


def _router() -> RoleRouter:
    return RoleRouter().clone_with({
        "builder": {
            "preferred_provider": "provider-a",
            "preferred_model": "provider-a-model",
            "fallbacks": ("provider-b",),
        },
    })


def _exact_request() -> RoutingRequest:
    return RoutingRequest(
        task_type="builder",
        required_capabilities=("code", "tests"),
        required_tools=("hermes_oneshot",),
        preferred_provider="provider-a",
        allowed_providers=("provider-a", "provider-b"),
        allowed_data_use=("NO_TRAINING",),
    )


def _bound_child(
    database: FoundryDatabase,
    *,
    campaign_id: str = "campaign-endurance",
    child_id: str = "child-endurance",
    mission_id: str = "mission-endurance",
    request: RoutingRequest | None = None,
    isolation_key: str = "isolation/child-endurance",
    effect_action_key: str = "effect:endurance:child",
) -> dict:
    _mission(database, mission_id)
    create_campaign(
        database,
        "objective-endurance",
        campaign_id=campaign_id,
        max_parallel_children=2,
    )
    declare_child_mission(
        database,
        campaign_id,
        child_id=child_id,
        mission_id=mission_id,
        step_id="step-endurance",
        routing_requirements=request or _exact_request(),
        isolation_key=isolation_key,
        effect_action_key=effect_action_key,
        effect_keys=(effect_action_key,),
    )
    record_child_state(database, child_id, "READY", reason="exact child is ready")
    context = campaign_dispatch_context(
        database,
        mission_id=mission_id,
        step_id="step-endurance",
    )
    assert context is not None
    return context


def test_child_routing_contract_is_hashed_and_objective_bound(tmp_path: Path) -> None:
    database = _database(tmp_path)
    context = _bound_child(database)
    child = rebuild_campaign_state(database).children["child-endurance"]

    assert context["routing_request"].to_dict() == _exact_request().to_dict()
    assert context["routing_identity"] == _exact_request().identity_hash()
    assert child.effect_action_key == "effect:endurance:child"
    assert child.isolation_key == "isolation/child-endurance"
    objective = rebuild_executive_state(database).objectives["objective-endurance"]
    assert objective.mission_ids == ("mission-endurance",)


def test_compatible_fallback_preserves_all_campaign_identities(tmp_path: Path) -> None:
    database = _database(tmp_path)
    context = _bound_child(database)
    adapter = _Adapter()
    dispatcher = RoleDispatcher(
        database,
        _router(),
        adapter,
        provider_candidates=(
            _candidate("provider-a", readiness="OUTAGE"),
            _candidate("provider-b"),
        ),
        max_total_attempts=2,
        max_retries_per_provider=0,
    )
    lineage = {
        key: str(context[key])
        for key in ("objective_id", "campaign_id", "child_id")
    }
    result = dispatcher.dispatch(
        role="builder",
        mission_id="mission-endurance",
        resource_id="resource-endurance",
        capability="write",
        step_id="step-endurance",
        run_id="builder-run-endurance",
        instructions="perform exact child",
        routing_request=context["routing_request"],
        campaign_lineage=lineage,
        campaign_isolation_key=context["isolation_key"],
        effect_action_key=context["effect_action_key"],
    )

    assert result.status == COMPLETED
    assert result.provider == "provider-b"
    assert result.effect_action_key == "effect:endurance:child"
    assert [manifest.provider for manifest in adapter.received] == ["provider-b"]
    manifest = adapter.received[0]
    assert manifest.objective_id == "objective-endurance"
    assert manifest.campaign_id == "campaign-endurance"
    assert manifest.child_id == "child-endurance"
    assert manifest.run_id == "builder-run-endurance"
    assert manifest.routing_identity == _exact_request().identity_hash()
    assert manifest.effect_action_key == "effect:endurance:child"
    for event_type in (
        "provider.route_decided",
        "provider.dispatch_attempted",
        "agent_run.started",
        "provider.dispatch_succeeded",
    ):
        event = next(
            event for event in database.events()
            if event.event_type == event_type
        )
        assert event.payload["objective_id"] == "objective-endurance"
        assert event.payload["campaign_id"] == "campaign-endurance"
        assert event.payload["child_id"] == "child-endurance"
        assert event.payload["builder_run_id"] == "builder-run-endurance"
        assert event.payload["effect_action_key"] == "effect:endurance:child"


def test_incompatible_provider_is_not_a_wait_target(tmp_path: Path) -> None:
    database = _database(tmp_path)
    adapter = _Adapter()
    request = _exact_request()
    result = RoleDispatcher(
        database,
        _router(),
        adapter,
        provider_candidates=(
            _candidate("provider-a", capabilities=("research",)),
            _candidate("provider-b", capabilities=("research",)),
        ),
    ).dispatch(
        role="builder",
        mission_id="mission-no-capability",
        resource_id="resource-endurance",
        capability="write",
        step_id="step-endurance",
        run_id="builder-run-no-capability",
        routing_request=request,
    )
    assert result.error_class == "provider_incompatible"
    assert result.wait_for_providers == ()
    assert adapter.received == []


def test_pre_rejected_outage_remains_in_exact_recovery_set(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    adapter = _Adapter({
        "provider-b": [ProviderFailure(
            "selected alternate also failed cleanly",
            failure_class="OUTAGE",
            possible_effect=False,
        )],
    })
    result = RoleDispatcher(
        database,
        _router(),
        adapter,
        provider_candidates=(
            _candidate("provider-a", readiness="OUTAGE"),
            _candidate("provider-b"),
        ),
    ).dispatch(
        role="builder",
        mission_id="mission-provider-set",
        resource_id="resource-provider-set",
        capability="write",
        step_id="step-provider-set",
        run_id="builder-run-provider-set",
        routing_request=_exact_request(),
    )

    assert result.status == "BLOCKED"
    assert result.wait_for_providers == ("provider-a", "provider-b")
    assert [manifest.provider for manifest in adapter.received] == ["provider-b"]


def test_all_down_wait_stop_and_exact_recovery_resume_once(tmp_path: Path) -> None:
    database = _database(tmp_path)
    context = _bound_child(database)
    adapter = _Adapter()
    lineage = {
        key: str(context[key])
        for key in ("objective_id", "campaign_id", "child_id")
    }
    result = RoleDispatcher(
        database,
        _router(),
        adapter,
        provider_candidates=(
            _candidate("provider-a", readiness="OUTAGE"),
            _candidate("provider-b", readiness="OUTAGE"),
        ),
    ).dispatch(
        role="builder",
        mission_id="mission-endurance",
        resource_id="resource-endurance",
        capability="write",
        step_id="step-endurance",
        run_id="builder-run-wait",
        routing_request=context["routing_request"],
        campaign_lineage=lineage,
        campaign_isolation_key=context["isolation_key"],
        effect_action_key=context["effect_action_key"],
    )
    assert result.wait_for_providers == ("provider-a", "provider-b")
    stop_path = tmp_path / "control" / "V12_STOP"
    wait = schedule_mission_provider_wait(
        database,
        mission_id="mission-endurance",
        step_id="step-endurance",
        run_id="builder-run-wait",
        provider_ids=result.wait_for_providers,
        reason=result.error_detail or "all providers down",
        error_class=str(result.error_class),
        campaign_lineage=lineage,
        routing_identity=context["routing_identity"],
        campaign_isolation_key=context["isolation_key"],
        stop_path=stop_path,
    )
    replayed_wait = schedule_mission_provider_wait(
        database,
        mission_id="mission-endurance",
        step_id="step-endurance",
        run_id="builder-run-wait",
        provider_ids=result.wait_for_providers,
        reason=result.error_detail or "all providers down",
        error_class=str(result.error_class),
        campaign_lineage=lineage,
        routing_identity=context["routing_identity"],
        campaign_isolation_key=context["isolation_key"],
        stop_path=stop_path,
    )
    assert replayed_wait == wait
    assert database.event_count(
        event_type="mission.provider_wait_started"
    ) == 1
    database.append(Event.create(
        "mission.state_changed",
        {
            "mission_id": "mission-endurance",
            "state": "BLOCKED",
            "next_action": "wait for provider",
            "run_id": "builder-run-wait",
        },
        source_type="test",
        source_id="v12-endurance",
        idempotency_key="mission.state_changed:mission-endurance:provider-wait",
    ))
    assert rebuild_campaign_state(database).children["child-endurance"].status == "WAITING"

    stop_path.parent.mkdir()
    stop_path.write_text("disposable stop", encoding="utf-8")
    record_provider_readiness(
        database,
        provider_id="provider-b",
        model="provider-b-model",
        readiness=ProviderReadiness.READY,
        reason="provider recovered while stopped",
    )
    assert resume_mission_provider_waits(database) == []
    assert database.row("missions", "mission_id", "mission-endurance")["state"] == "BLOCKED"

    stop_path.unlink()
    assert resume_mission_provider_waits(database) == [wait["wait_id"]]
    assert resume_mission_provider_waits(database) == []
    assert database.row("missions", "mission_id", "mission-endurance")["state"] == "ACTION_SELECTION"
    child = rebuild_campaign_state(database).children["child-endurance"]
    assert child.status == "READY"
    assert campaign_dispatchable_mission_ids(database) == ("mission-endurance",)
    assert database.event_count(event_type="mission.provider_wait_resumed") == 1


def test_parallel_admission_is_bounded_and_isolation_safe(tmp_path: Path) -> None:
    database = _database(tmp_path)
    try:
        create_campaign(
            database,
            "objective-endurance",
            campaign_id="campaign-invalid-parallel-limit",
            max_parallel_children=0,
        )
    except CampaignError as error:
        assert "parallel" in str(error)
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("zero parallel-child limit was silently clamped")
    create_campaign(
        database,
        "objective-endurance",
        campaign_id="campaign-parallel",
        max_parallel_children=3,
    )
    for child_id, isolation_key in (
        ("child-a", "resource/shared"),
        ("child-b", "resource/shared"),
        ("child-c", "resource/independent"),
    ):
        mission_id = child_id.replace("child", "mission")
        _mission(database, mission_id)
        declare_child_mission(
            database,
            "campaign-parallel",
            child_id=child_id,
            mission_id=mission_id,
            step_id=f"step-{child_id}",
            routing_requirements=_exact_request(),
            isolation_key=isolation_key,
        )
        record_child_state(database, child_id, "READY", reason="parallel child ready")

    assert campaign_dispatchable_mission_ids(database) == (
        "mission-a",
        "mission-c",
    )
    try:
        campaign_dispatch_context(
            database,
            mission_id="mission-b",
            step_id="step-child-b",
        )
    except CampaignError as error:
        assert "bounded admission" in str(error)
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("isolated sibling bypassed bounded admission")
    record_child_state(database, "child-a", "RUNNING", reason="slot acquired")
    assert campaign_dispatchable_mission_ids(database) == (
        "mission-a",
        "mission-c",
    )
    try:
        declare_child_mission(
            database,
            "campaign-parallel",
            child_id="child-duplicate",
            mission_id="mission-a",
            routing_requirements=_exact_request(),
        )
    except CampaignError as error:
        assert "already bound" in str(error)
    else:  # pragma: no cover - fail loudly if duplicate identity is accepted
        raise AssertionError("duplicate mission binding was accepted")


def test_mission_loop_consumes_child_router_contract_and_parks_campaign(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(
        ["git", "init"], cwd=repository, check=True,
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "v12@example.invalid"],
        cwd=repository, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "V12 Endurance Proof"],
        cwd=repository, check=True, capture_output=True, text=True,
    )
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=repository, check=True,
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "baseline"], cwd=repository, check=True,
        capture_output=True, text=True,
    )
    database = _database(tmp_path)
    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-endurance")
    ))
    _mission(database, "mission-endurance")
    set_capability_state(
        database,
        "resource-endurance",
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "v12-endurance-disposable"},
    )
    create_campaign(
        database,
        "objective-endurance",
        campaign_id="campaign-endurance",
    )
    declare_child_mission(
        database,
        "campaign-endurance",
        child_id="child-endurance",
        mission_id="mission-endurance",
        step_id="step-endurance",
        routing_requirements=_exact_request(),
        isolation_key=str(repository),
        effect_action_key="effect:endurance:mission-loop",
        effect_keys=("effect:endurance:mission-loop",),
    )
    record_child_state(
        database,
        "child-endurance",
        "READY",
        reason="mission loop child ready",
    )
    adapter = _Adapter()
    outcome = MissionLoop(
        database,
        _router(),
        adapter,
        provider_candidates=(
            _candidate("provider-a", readiness="OUTAGE"),
            _candidate("provider-b", readiness="OUTAGE"),
        ),
    ).run_step(
        mission_id="mission-endurance",
        step_id="step-endurance",
        capability="write_registered_worktree_files",
        target=repository / "result.txt",
        instructions="create the disposable child result",
        expected_effects=("create result.txt",),
        rollback={"kind": "delete_created_file"},
        run_id="builder-run-mission-loop",
    )

    assert outcome.outcome == BLOCKED_OUTCOME
    assert adapter.received == []
    wait = next(
        event for event in database.events()
        if event.event_type == "mission.provider_wait_started"
    )
    assert wait.payload["objective_id"] == "objective-endurance"
    assert wait.payload["campaign_id"] == "campaign-endurance"
    assert wait.payload["child_id"] == "child-endurance"
    assert wait.payload["builder_run_id"] == "builder-run-mission-loop"
    assert wait.payload["provider_ids"] == ["provider-a", "provider-b"]
    assert wait.payload["effect_action_key"] is None
    # No effect was attempted: the declared effect identity remains on the
    # route/manifest contract but does not create a reconciliation wait.
    child = rebuild_campaign_state(database).children["child-endurance"]
    assert child.status == "WAITING"
    assert child.wait_kind == "PROVIDER"


def test_attempted_clean_outages_wait_for_provider_not_effect(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "attempted-outage"
    repository.mkdir()
    subprocess.run(
        ["git", "init"], cwd=repository, check=True,
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "v12@example.invalid"],
        cwd=repository, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "V12 Endurance Proof"],
        cwd=repository, check=True, capture_output=True, text=True,
    )
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=repository, check=True,
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "baseline"], cwd=repository, check=True,
        capture_output=True, text=True,
    )
    database = _database(tmp_path)
    database.append(registration_event(
        inspect_git_resource(repository, resource_id="resource-endurance")
    ))
    _mission(database, "mission-endurance")
    set_capability_state(
        database,
        "resource-endurance",
        "write_registered_worktree_files",
        "READY",
        evidence={"source": "v12-endurance-disposable"},
    )
    create_campaign(
        database,
        "objective-endurance",
        campaign_id="campaign-endurance",
    )
    declare_child_mission(
        database,
        "campaign-endurance",
        child_id="child-endurance",
        mission_id="mission-endurance",
        step_id="step-endurance",
        routing_requirements=_exact_request(),
        isolation_key=str(repository),
        effect_action_key="effect:endurance:attempted-outage",
        effect_keys=("effect:endurance:attempted-outage",),
    )
    record_child_state(
        database, "child-endurance", "READY", reason="outage child ready"
    )
    adapter = _Adapter({
        "provider-a": [ProviderFailure(
            "provider-a clean outage",
            failure_class="OUTAGE",
            possible_effect=False,
        )],
        "provider-b": [ProviderFailure(
            "provider-b clean outage",
            failure_class="OUTAGE",
            possible_effect=False,
        )],
    })
    outcome = MissionLoop(
        database,
        _router(),
        adapter,
        provider_candidates=(
            _candidate("provider-a"),
            _candidate("provider-b"),
        ),
    ).run_step(
        mission_id="mission-endurance",
        step_id="step-endurance",
        capability="write_registered_worktree_files",
        target=repository / "result.txt",
        instructions="attempt the exact provider child",
        expected_effects=("create result.txt",),
        rollback={"kind": "delete_created_file"},
        run_id="builder-run-attempted-outage",
    )

    assert outcome.outcome == BLOCKED_OUTCOME
    assert [manifest.provider for manifest in adapter.received] == [
        "provider-a", "provider-b",
    ]
    wait = next(
        event for event in database.events()
        if event.event_type == "mission.provider_wait_started"
    )
    assert wait.payload["provider_ids"] == ["provider-a", "provider-b"]
    assert wait.payload["effect_action_key"] is None
    child = rebuild_campaign_state(database).children["child-endurance"]
    assert child.wait_kind == "PROVIDER"


def test_effect_identity_mismatch_never_auto_falls_back(tmp_path: Path) -> None:
    database = _database(tmp_path)
    context = _bound_child(database)
    adapter = _Adapter({
        "provider-a": [ProviderFailure(
            "provider reported a different possible effect",
            failure_class="OUTAGE",
            possible_effect=True,
            effect_action_key="effect:endurance:wrong-identity",
        )],
    })
    lineage = {
        key: str(context[key])
        for key in ("objective_id", "campaign_id", "child_id")
    }
    result = RoleDispatcher(
        database,
        _router(),
        adapter,
        provider_candidates=(
            _candidate("provider-a"),
            _candidate("provider-b"),
        ),
    ).dispatch(
        role="builder",
        mission_id="mission-endurance",
        resource_id="resource-endurance",
        capability="write",
        step_id="step-endurance",
        run_id="builder-run-effect-mismatch",
        routing_request=context["routing_request"],
        campaign_lineage=lineage,
        campaign_isolation_key=context["isolation_key"],
        effect_action_key=context["effect_action_key"],
    )

    assert result.status == "BLOCKED"
    assert result.error_class == "provider_effect_identity_mismatch"
    assert [manifest.provider for manifest in adapter.received] == ["provider-a"]
    assert database.event_count(
        event_type="mission.provider_wait_started"
    ) == 0


def test_routing_mapping_rejects_scalar_provider_collections() -> None:
    payload = _exact_request().to_dict()
    payload["allowed_providers"] = "provider-a"
    try:
        RoutingRequest.from_mapping(payload)
    except ValueError as error:
        assert "allowed_providers must be a sequence" in str(error)
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("scalar provider collection was accepted")


def test_real_disposable_endurance_campaign_script(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "v12_endurance_convergence_proof.py"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = (
        str(script.parents[1] / "src")
        + os.pathsep
        + environment.get("PYTHONPATH", "")
    )
    receipt_path = tmp_path / "v12-proof.json"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--mode", "full",
            "--root", str(tmp_path / "real-campaign"),
            "--output", str(receipt_path),
        ],
        cwd=script.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == proof
    assert proof["verdict"] == "PASS"
    assert proof["source"]["schema_version"] == 40
    assert proof["source"]["activation_authorized"] is False
    assert proof["campaign"]["status"] == "COMPLETED"
    assert proof["campaign"]["accepted_child_count"] == 3
    assert proof["provider_routing"]["fallback_used"] is True
    assert proof["all_down_wait"]["first_outcome"] == "BLOCKED"
    assert proof["recovery"]["resume_receipts"] == 1
    assert proof["deadlines"]["silent_expiry"] is False
    assert proof["parallel_children"]["limit"] == 2
    assert len(proof["parallel_children"]["initial_dispatchable"]) == 2
    assert proof["parallel_children"]["peak_running_observed"] == 2
    assert proof["parallel_children"]["logical_overlap_proven"] is True
    assert proof["joins"]["any"]["status"] == "SATISFIED"
    assert proof["joins"]["all"]["status"] == "SATISFIED"
    assert proof["startup_resume"]["hard_kill_returncode"] == 86
    assert proof["stop"]["dispatches_during_stop"] == 0
    assert proof["ambiguous_effect"]["reconcile_before_fallback"] is True
    assert proof["duplicates"] == {
        "continuations": 0,
        "effects": 0,
        "missions": 0,
    }
