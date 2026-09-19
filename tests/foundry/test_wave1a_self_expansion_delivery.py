"""Wave 1A — acquired capability must be used by the resumed mission itself.

Goal section 3.  Every pin here is DISCRIMINATING: each one fails on the
pre-change tree (product source ``28e1608``) and passes on the repaired head.

The pins are grouped by the two seams the goal names:

* the contract that EXECUTES a plan step records the Fabric capabilities that
  step DECLARED as required, resolved from a source that exists BEFORE
  materialization (the work item metadata and the plan step it declares) — and
  the requirement is carried OUTSIDE the hash-anchored canonical payload, so
  every stored ``contract_hash`` stays valid;
* the acquired capability's observation names the invocation subject, so the
  canonical verifier re-derives the verdict over the subject the contract
  declared instead of declining and handing a runtime-owned predicate to a
  model.  The verifier's subject rule itself is NOT weakened, and a pin proves
  it still refuses a receipt that does not name the declared subject.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.control.capabilities import capability_state_recorded
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import (
    PlanStep,
    active_plan,
    build_plan,
    create_objective,
)
from jarvis_foundry.fabric.adapter import _subject_diverges
from jarvis_foundry.fabric.expansion_resolver import JARVIS_SOURCE_ADAPTERS
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    latest_contract,
    record_step_contract,
)

from worker_test_helpers import mission_database

PROVEN = "cap.file.write"
UNPROVEN = "cap.wave1a.not.registered"
LEGACY = "write_registered_worktree_files"
RESOURCE_ID = "resource-wave1a"
OBJECTIVE_ID = "objective-wave1a"


def _database(tmp_path: Path) -> tuple[FoundryDatabase, Path]:
    database, repository = mission_database(tmp_path)
    seed_builtins(database)
    database.append(registration_event(
        inspect_git_resource(repository, resource_id=RESOURCE_ID)))
    return database, repository


def _owner_goal_work_item(
    database: FoundryDatabase,
    *,
    required_capabilities: tuple[str, ...],
    item_id: str = "executive-step:objective-wave1a:execute",
) -> dict:
    """Register the work item the runtime itself produces for a plan step.

    The metadata mirrors ``generate_missions_for_objective``: it DECLARES the
    objective and the plan step it belongs to, plus the step's declared
    requirement set.
    """
    from jarvis_foundry.evolution.executive_portfolio import register_work_item

    register_work_item(
        database,
        item_id=item_id,
        kind="OWNER_GOAL",
        objective="Perform the bounded owner goal in the registered resource.",
        resource_id=RESOURCE_ID,
        metadata={
            "executive_objective_id": OBJECTIVE_ID,
            "executive_plan_id": f"plan:{OBJECTIVE_ID}:wave1a",
            "executive_step_id": "execute",
            "required_capabilities": list(required_capabilities),
            "success_criteria": ["the outcome is verified"],
        },
        source_type="supervisor",
        source_id="wave1a-pin",
    )
    return {
        "decision_id": "portfolio-executive:wave1a-pin",
        "selected_class": "OWNER_GOAL",
        "selected_item": {
            "item_id": item_id,
            "objective": "Perform the bounded owner goal in the registered resource.",
            "resource_id": RESOURCE_ID,
            "metadata": {
                "executive_objective_id": OBJECTIVE_ID,
                "executive_step_id": "execute",
                "required_capabilities": list(required_capabilities),
                "success_criteria": ["the outcome is verified"],
            },
        },
    }


def _contract(**changes: object) -> MissionStepContract:
    values: dict[str, object] = {
        "mission_id": "mission-1",
        "step_id": "01-action",
        "version": 1,
        "resource_id": "resource-1",
        "capability": "git_local_commit",
        "target_relative_path": "artifacts/wave1a.json",
        "instructions": json.dumps({"target": "artifacts/wave1a.json", "content": "x"}),
        "expected_effects": ("the artifact is written",),
        "rollback": {"method": "delete_file", "reversible": True},
        "verification_criteria": ("the artifact exists",),
        "recorded_by": "supervisor",
        "source": "wave1a-pin",
    }
    values.update(changes)
    return MissionStepContract(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Seam A - the declared requirement reaches the executing contract
# --------------------------------------------------------------------------


def test_declared_requirement_is_outside_the_hash_anchor(tmp_path: Path) -> None:
    """The field is carried, persisted and reloaded, and never re-hashes a row."""
    plain = _contract()
    carrying = _contract(required_capabilities=(PROVEN,))
    assert carrying.required_capabilities == (PROVEN,)
    assert "required_capabilities" not in carrying.canonical_payload()
    assert "required_capabilities" in carrying.to_payload()
    # The whole point: the same logical contract hashes identically with and
    # without the field, so every stored contract_hash stays valid.
    assert carrying.content_hash() == plain.content_hash()

    # A pre-change payload that has no such key still parses.
    pre_change = dict(plain.canonical_payload())
    pre_change.update({
        "contract_id": plain.contract_id, "contract_hash": plain.content_hash(),
    })
    assert MissionStepContract.from_payload(pre_change).required_capabilities == ()

    database, _ = mission_database(tmp_path)
    record_step_contract(database, carrying)
    assert database.rebuild_projections() > 0
    assert latest_contract(database, "mission-1") == carrying
    assert latest_contract(database, "mission-1").required_capabilities == (PROVEN,)


def test_declared_requirement_must_use_the_canonical_namespace() -> None:
    with pytest.raises(Exception, match="cap\\.\\* namespace"):
        _contract(required_capabilities=(LEGACY,))


def test_a_pre_change_row_still_loads_and_reconciles_without_a_rebuild(
    tmp_path: Path,
) -> None:
    """The migrated-but-not-rebuilt state must already reconcile.

    The projection rebuilds this column deterministically as ``canonical_json(())``
    == ``'[]'`` for a contract that declares nothing, so a migrated row left at
    NULL would make ``reconcile()`` report an inconsistency on every existing
    database until something rebuilt its projections - and nothing in the
    product rebuilds the live database.  The constant default is what closes
    that gap, and this pin is what keeps it closed.
    """
    from jarvis_foundry.reconcile import reconcile

    database, _ = mission_database(tmp_path)
    # A contract that declares nothing is the real pre-change shape: the
    # projection rebuilds it as canonical_json(()) == '[]', which is exactly the
    # value the migration's constant default backfills.
    contract = _contract()
    record_step_contract(database, contract)
    # Simulate the as-migrated value a pre-change row carries.  SQLite backfills
    # the default EXPRESSION's value (the string `[]`), while pragma_table_info
    # reports the expression TEXT ('[]' with its SQL quotes), so the quotes are
    # stripped here rather than written into the row.
    with database.transaction() as connection:
        default_expression = connection.execute(
            "SELECT dflt_value FROM pragma_table_info('mission_step_contracts') "
            "WHERE name = 'required_capabilities_json'").fetchone()[0]
        connection.execute(
            "UPDATE mission_step_contracts SET required_capabilities_json = ? "
            "WHERE contract_id = ?",
            (str(default_expression).strip().strip("'"), contract.contract_id))

    # NO rebuild_projections() here - that is the whole point.
    report = reconcile(database)
    assert report.consistent is True, (
        "a migrated-but-not-rebuilt database must already reconcile; "
        f"mismatches={getattr(report, 'mismatches', None)}")

    loaded = latest_contract(database, "mission-1")
    assert loaded is not None
    assert loaded.required_capabilities == ()
    assert loaded.content_hash() == contract.content_hash()


def test_migration_41_is_versioned_and_backfills_a_constant_default(
    tmp_path: Path,
) -> None:
    database, _ = _database(tmp_path)
    assert SCHEMA_VERSION == 41
    with database.connect(readonly=True) as connection:
        columns = {str(row[1]): row for row in connection.execute(
            "PRAGMA table_info(mission_step_contracts)")}
        versions = [int(row[0]) for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version")]
    assert "required_capabilities_json" in columns
    # NOT NULL with a CONSTANT default: existing rows are backfilled to the same
    # value the projection rebuilds for them, so a migrated database reconciles
    # without anyone rebuilding its projections.
    assert int(columns["required_capabilities_json"][3]) == 1
    assert str(columns["required_capabilities_json"][4]) == "'[]'"
    assert max(versions) == 41


def test_materialization_records_the_requirement_from_the_work_item_metadata(
    tmp_path: Path,
) -> None:
    """Drive the REAL materialization path, not the helper."""
    from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection

    database, _ = _database(tmp_path)
    decision = _owner_goal_work_item(database, required_capabilities=(PROVEN,))
    result = materialize_portfolio_selection(
        database, decision=decision, resource_id=RESOURCE_ID,
        source_id="wave1a-pin")
    assert result["materialized"] is True
    mission_id = str(result["mission_id"])

    rows = database.all("mission_step_contracts", mission_id=mission_id)
    assert rows, "the mission must have recorded step contracts"
    for row in rows:
        recorded = json.loads(str(row["required_capabilities_json"] or "[]"))
        assert recorded == [PROVEN], (
            "the contract that executes the step must carry the declared "
            "Fabric requirement")
    # The requirement is bound onto the mission's resource, so the mission can
    # actually select it rather than merely recording it beside itself.
    assert capability_state_recorded(database, RESOURCE_ID, PROVEN) is True


def test_declared_requirement_is_read_from_the_declared_plan_step(tmp_path: Path) -> None:
    """A requirement attached to the step AFTER the work item was written.

    The runtime only discovers some requirements while materializing
    (``_record_unrealizable_intent`` attaches the missing ``cap.*`` to the plan
    step), so the stale work-item metadata alone is not enough: the plan step
    the item DECLARES must be read too.
    """
    from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection

    database, _ = _database(tmp_path)
    create_objective(
        database,
        "Perform the bounded owner goal in the registered resource.",
        objective_id=OBJECTIVE_ID, resource_id=RESOURCE_ID,
        required_capabilities=(LEGACY,),
        success_criteria=("the outcome is verified",),
        budget={"max_missions": 2, "max_retries": 1}, controller="supervisor",
    )
    build_plan(
        database, OBJECTIVE_ID,
        steps=[PlanStep(
            step_id="execute", objective_id=OBJECTIVE_ID,
            description="Execute the bounded work.",
            required_capabilities=(LEGACY,), resource_id=RESOURCE_ID)],
        controller="supervisor",
    )
    decision = _owner_goal_work_item(database, required_capabilities=(LEGACY,))
    # The plan step gains the requirement only now, after the item existed.
    build_plan(
        database, OBJECTIVE_ID,
        steps=[PlanStep(
            step_id="execute", objective_id=OBJECTIVE_ID,
            description="Execute the bounded work.",
            required_capabilities=(LEGACY, PROVEN), resource_id=RESOURCE_ID)],
        controller="supervisor",
    )
    plan = active_plan(database, OBJECTIVE_ID)
    assert PROVEN in next(
        step.required_capabilities for step in plan.steps if step.step_id == "execute")

    result = materialize_portfolio_selection(
        database, decision=decision, resource_id=RESOURCE_ID, source_id="wave1a-pin")
    assert result["materialized"] is True
    rows = database.all("mission_step_contracts", mission_id=str(result["mission_id"]))
    assert rows
    for row in rows:
        assert json.loads(str(row["required_capabilities_json"] or "[]")) == [PROVEN]


def test_legacy_and_unproven_capabilities_are_not_recorded(tmp_path: Path) -> None:
    """Non-vacuity: only Catalog-proven cap.* ids qualify."""
    from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection

    database, _ = _database(tmp_path)
    decision = _owner_goal_work_item(
        database, required_capabilities=(LEGACY, UNPROVEN))
    result = materialize_portfolio_selection(
        database, decision=decision, resource_id=RESOURCE_ID, source_id="wave1a-pin")
    assert result["materialized"] is True
    rows = database.all("mission_step_contracts", mission_id=str(result["mission_id"]))
    assert rows
    for row in rows:
        assert json.loads(str(row["required_capabilities_json"] or "[]")) == []
    assert capability_state_recorded(database, RESOURCE_ID, UNPROVEN) is False
    assert capability_state_recorded(database, RESOURCE_ID, LEGACY) is False


# --------------------------------------------------------------------------
# Seam B - the acquired capability's observation names its subject
# --------------------------------------------------------------------------


def test_acquired_capability_declares_the_invocation_subject_in_its_observation() -> None:
    spec = JARVIS_SOURCE_ADAPTERS["hash.file.sha256"]
    subject_fields = ("path", "target", "source_ref", "worktree")
    declared = spec["output"]
    assert any(field in declared for field in subject_fields), (
        "the observation must name the subject the action was invoked on")
    # The declared subject is bound to the SAME invocation input the arguments
    # use, so the observation cannot name a different subject than the one acted
    # on.  The adapter runtime resolves `$input.<field>` (adapter_factory
    # runtime._resolve), which is the language exercised here.
    from jarvis_foundry.adapter_factory.runtime import _resolve

    resolved = _resolve(dict(declared), {
        "input": {"path": "C:/owned/evidence.bin"},
        "config": {}, "request": {}, "raw": {"value": "a" * 64},
        "artifact": {"root": "C:/owned"}, "artifact_root": "C:/owned",
    })
    assert resolved["path"] == "C:/owned/evidence.bin"
    assert resolved["sha256"] == "a" * 64


def test_canonical_verifier_still_refuses_a_receipt_that_omits_the_subject() -> None:
    """Guard: the repair must NOT have been made by weakening this rule."""
    declared = {"path": "C:/owned/evidence.bin"}
    assert _subject_diverges(declared, {"sha256": "b" * 64}) is True
    assert _subject_diverges(declared, {"path": ""}) is True
    assert _subject_diverges(declared, {"path": "C:/other/file.bin"}) is True
    # And it agrees when the observation does name the declared subject.
    assert _subject_diverges(
        declared, {"path": "C:/owned/evidence.bin", "sha256": "c" * 64}) is False
