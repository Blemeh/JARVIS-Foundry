"""Reproduction for the two contract defects found live 2026-10-02.

DEFECT D1 (executive/portfolio_bridge.py, execute-contract synthesis):
the CI_FAILURE execute contract lists the investigation artifact as a MANDATORY
deliverable while `.jarvis/` is inside its forbidden_paths.  The artifact
existence check and the forbidden-path containment check therefore contradict
each other: a worker that writes the required file is condemned for touching a
forbidden path, and a worker that obeys the forbidden list fails with
"expected artifacts missing".  The contract is unsatisfiable, so the mission
can never legitimately PASS.

DEFECT D2 (mission/contracts.py, next_executable_contract):
dispatch order is lexicographic on step_id.  "discovery-execute" sorts before
"discovery-plan", so the execute step is dispatched FIRST and the investigate
step the execute instructions explicitly depend on never runs.  The execute
step's own instructions say "Read <investigation artifact> first" against a file
that does not exist yet.

Live evidence (2026-10-02, live foundry.db, mission-ecdb7918c2d4):
  4 agent_runs, ALL on step 'discovery-execute'; zero on 'discovery-plan'.
  3 verifier verdicts, ALL FAIL with
  "expected artifacts missing: ['.jarvis/discovery/investigation-579a0fa1ce.md']".

These tests FAIL against 506045c and PASS after the repair.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    contract_plan_order,
    latest_contracts_by_step,
    next_executable_contract,
    record_step_contract,
)

from worker_test_helpers import mission_database


def _within(candidate: str, base: str) -> bool:
    """True when `candidate` is `base` or lives under it."""
    candidate_parts = PurePosixPath(candidate.replace("\\", "/")).parts
    base_parts = PurePosixPath(base.replace("\\", "/").rstrip("/")).parts
    return candidate_parts[: len(base_parts)] == base_parts


def _contract(**overrides) -> MissionStepContract:
    payload = {
        "mission_id": "mission-1",
        "step_id": "step-a",
        "version": 1,
        "resource_id": "resource-1",
        "capability": "write_registered_worktree_files",
        "target_relative_path": "probe.py",
        "instructions": "do the work",
        "expected_effects": ("probe.py changes",),
        "rollback": {"method": "git_checkout", "reversible": True},
        "risk_class": "C1",
        "verification_criteria": ("probe.py is correct",),
        "expected_artifacts": (),
        "forbidden_paths": (),
        "recorded_by": "supervisor",
        "source": "test",
    }
    payload.update(overrides)
    return MissionStepContract(**payload)


# ---------------------------------------------------------------- D1


def test_no_discovery_template_forbids_its_own_required_artifacts() -> None:
    """D1: a template's mandatory artifacts may never lie inside its own
    forbidden set — that is an unsatisfiable contract by construction."""
    from jarvis_foundry.executive.portfolio_bridge import (
        _DISCOVERY_CONTRACT_TEMPLATES,
    )

    checked = 0
    for kind, template in _DISCOVERY_CONTRACT_TEMPLATES.items():
        forbidden = tuple(template["forbidden"])
        artifacts = tuple(template["artifacts"])
        offenders = [
            artifact for artifact in artifacts
            if any(_within(artifact, entry) for entry in forbidden)
        ]
        assert offenders == [], (
            f"{kind}: mandatory artifacts {offenders} lie inside forbidden "
            f"paths {list(forbidden)}"
        )
        checked += 1
    assert checked >= 3


def test_synthesized_discovery_contract_permits_its_own_deliverables(tmp_path) -> None:
    """D1 at the synthesis site: for every synthesized discovery mission, no
    expected artifact may sit inside that same step's forbidden paths.

    Exercises the real production entry point (materialize_portfolio_selection
    through the discovery branch) for each opportunity kind, then inspects the
    contracts the mission actually recorded.
    """
    from jarvis_foundry.executive.portfolio_bridge import (
        _DISCOVERY_CONTRACT_TEMPLATES,
        materialize_portfolio_selection,
    )

    database, repository = mission_database(tmp_path)
    from jarvis_foundry.evolution.executive_portfolio import rebuild_portfolio_state

    checked = 0
    for kind in _DISCOVERY_CONTRACT_TEMPLATES:
        item_id = f"pw-{kind}"
        # Register the discovery opportunity exactly as the discovery feed
        # does: a portfolio work item carrying typed provenance.
        from jarvis_foundry.evolution.executive_portfolio import register_work_item

        register_work_item(
            database,
            item_id=item_id,
            kind="REPAIR",
            objective=f"opportunity {kind}",
            resource_id="resource-1",
            provenance={
                "source_lane": "live-github-research-activation",
                "opportunity_kind": kind,
                "problem_statement": f"probe {kind} problem",
                "repository": "blemeh/repo",
                "duplicate_key": f"dup-{kind}",
                "why_now": "live regression",
            },
            source_id="test",
        )
        # A real portfolio SELECT decision carries the FULL work item, not a
        # bare reference: the materializer reads the item's provenance to
        # classify the opportunity.  Pass the projection, exactly as the
        # executive selector does.
        item = rebuild_portfolio_state(database).work_items[item_id]
        selected_item = (item.to_payload() if hasattr(item, "to_payload")
                         else dict(item.__dict__))
        result = materialize_portfolio_selection(
            database,
            decision={
                "decision": "SELECT",
                "decision_id": f"portfolio-executive:test-{kind}",
                "entity_id": item_id,
                "selected_item": selected_item,
                "selected_class": "REPAIR",
            },
            resource_id="resource-1",
            capability="write_registered_worktree_files",
            source_id="test",
        )
        if not result.get("materialized"):
            # Refused for an unrelated scope reason; not what this test measures.
            continue
        mission_id = str(result["mission_id"])
        contracts = latest_contracts_by_step(database, mission_id)
        assert contracts, f"{kind}: mission materialized without a contract"
        for step_id, contract in contracts.items():
            for artifact in contract.expected_artifacts:
                for entry in contract.forbidden_paths:
                    assert not _within(artifact, entry), (
                        f"{kind}/{step_id}: mandatory deliverable {artifact} "
                        f"lies inside forbidden {entry}"
                    )
        checked += 1
    assert checked >= 1, "no discovery mission was synthesized to check"
    assert repository.is_dir()


# ---------------------------------------------------------------- D2


def test_next_executable_contract_preserves_recorded_step_order(tmp_path) -> None:
    """D2: investigate-before-execute must hold regardless of step_id text.

    The recording order is the plan, and production records the investigate
    step first.  Live ledger, mission-ecdb7918c2d4:
      rowid 113 -> discovery-plan    (ledger seq 333681)
      rowid 114 -> discovery-execute (ledger seq 333682)
    The lexicographic selector ran 114 first anyway.
    """
    database, _repo = mission_database(tmp_path)
    record_step_contract(database, _contract(
        step_id="discovery-plan",
        target_relative_path=".jarvis/discovery/investigation-abc.md",
        expected_artifacts=(".jarvis/discovery/investigation-abc.md",),
        forbidden_paths=("src/", "docs/", "var/"),
    ))
    record_step_contract(database, _contract(
        step_id="discovery-execute", target_relative_path=".github/workflows",
        expected_artifacts=(".github/workflows/ci.yml",),
        forbidden_paths=("src/", "docs/", ".jarvis/", "var/"),
    ))

    nxt = next_executable_contract(database, "mission-1")
    assert nxt is not None
    assert nxt.step_id == "discovery-plan", (
        "the investigate step must dispatch first; ordering ran "
        f"{nxt.step_id} first"
    )


def test_execute_cannot_bypass_an_unaccepted_prerequisite(tmp_path) -> None:
    """D2: execute stays unreachable while its prerequisite is unaccepted,
    and becomes next only once the prerequisite is accepted — including after
    a replay of the same selection."""
    from recovery_test_helpers import accept_step

    database, _repo = mission_database(tmp_path)
    record_step_contract(database, _contract(
        step_id="discovery-plan",
        target_relative_path=".jarvis/discovery/investigation-abc.md",
    ))
    record_step_contract(database, _contract(
        step_id="discovery-execute", target_relative_path=".github/workflows",
    ))

    # Replay: asking repeatedly must never skip ahead to execute.
    for _ in range(3):
        assert next_executable_contract(
            database, "mission-1").step_id == "discovery-plan"

    accept_step(database, mission_id="mission-1", step_id="discovery-plan")
    assert next_executable_contract(
        database, "mission-1").step_id == "discovery-execute"


def test_plan_order_survives_contract_version_bump(tmp_path) -> None:
    """D2: a v2 bump of an earlier step keeps its ORIGINAL plan position.

    A version bump appends a new projection row.  Position must come from the
    step's FIRST recorded row, or the bumped step would drift to the end and
    be dispatched after its own successor.
    """
    database, _repo = mission_database(tmp_path)
    plan_v1 = _contract(
        step_id="discovery-plan",
        target_relative_path=".jarvis/discovery/investigation-abc.md",
    )
    execute_v1 = _contract(
        step_id="discovery-execute", target_relative_path=".github/workflows",
    )
    record_step_contract(database, plan_v1)
    record_step_contract(database, execute_v1)
    assert contract_plan_order(
        database, "mission-1") == ("discovery-plan", "discovery-execute")

    # Now bump ONLY the plan step to v2.
    plan_v2 = _contract(
        step_id="discovery-plan",
        version=2,
        target_relative_path=".jarvis/discovery/investigation-def.md",
        supersedes_hash=plan_v1.content_hash(),
    )
    record_step_contract(database, plan_v2)

    assert contract_plan_order(
        database, "mission-1") == ("discovery-plan", "discovery-execute"), (
        "a version bump must not move a step's plan position"
    )
    nxt = next_executable_contract(database, "mission-1")
    assert nxt.step_id == "discovery-plan"
    assert nxt.version == 2, "the bumped contract is the one dispatched"
    assert nxt.target_relative_path == (
        ".jarvis/discovery/investigation-def.md")


def test_legacy_prefixed_steps_keep_their_intended_order(tmp_path) -> None:
    """The 01-/02- prefixed production contracts replay in plan order."""
    database, _repo = mission_database(tmp_path)
    record_step_contract(database, _contract(step_id="01-workflow"))
    record_step_contract(database, _contract(step_id="02-execute"))
    assert contract_plan_order(
        database, "mission-1") == ("01-workflow", "02-execute")


def test_sequential_dispatch_advances_after_pass(tmp_path) -> None:
    """D2 (disjoint ordering): execute was recorded FIRST here, so execute is
    legitimately next even while plan is outstanding.  Plan order is
    authoritative — this is not the discovery shape, and it must NOT be
    rewritten into plan-first by the selector."""
    from recovery_test_helpers import accept_step

    database, _repo = mission_database(tmp_path)
    record_step_contract(database, _contract(
        step_id="discovery-execute", target_relative_path=".github/workflows",
    ))
    record_step_contract(database, _contract(
        step_id="discovery-plan",
        target_relative_path=".jarvis/discovery/investigation-abc.md",
    ))
    assert next_executable_contract(
        database, "mission-1").step_id == "discovery-execute"

    accept_step(database, mission_id="mission-1", step_id="discovery-execute")
    nxt = next_executable_contract(database, "mission-1")
    assert nxt is not None and nxt.step_id == "discovery-plan"


def test_ordering_repair_keeps_every_step_in_the_plan(tmp_path) -> None:
    """The ordering repair must not drop any step from the plan."""
    database, _repo = mission_database(tmp_path)
    for step in ("discovery-plan", "discovery-execute", "discovery-verify"):
        record_step_contract(database, _contract(step_id=step))
    assert set(latest_contracts_by_step(database, "mission-1")) == {
        "discovery-plan", "discovery-execute", "discovery-verify",
    }


def test_legacy_step_ids_keep_lexicographic_dispatch(tmp_path) -> None:
    """The repair orders by recorded plan position, so existing step names
    that were already dispatched in lexicographic order are unaffected."""
    database, _repo = mission_database(tmp_path)
    record_step_contract(database, _contract(step_id="step-a"))
    record_step_contract(database, _contract(step_id="step-b"))
    assert next_executable_contract(database, "mission-1").step_id == "step-a"