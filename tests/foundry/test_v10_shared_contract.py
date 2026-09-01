"""V10 shared-contract and composition proof cases."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    EffectClass,
    ExecutorContext,
    TaskIntent,
    WorkflowStep,
    build_adapter_request,
    capability_metadata,
    execute_action,
    execute_for_spine,
    get_spec,
    run_workflow,
    seed_builtins,
)
from jarvis_foundry.fabric.model import VerifyVerdict


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _ctx(tmp_path: Path, database: FoundryDatabase) -> ExecutorContext:
    return ExecutorContext(database=database, owned_root=tmp_path / "owned")


def test_typed_metadata_and_effect_identity_are_restart_stable(tmp_path: Path) -> None:
    database = _db(tmp_path)
    seed_builtins(database)
    spec = get_spec(database, "cap.file.write")
    assert spec is not None
    metadata = capability_metadata(database, spec.capability_id)
    assert metadata is not None
    assert metadata.effect_class is EffectClass.REVERSIBLE_LOCAL
    assert metadata.world_model["verified_terminal_only"] is True

    # Resource references may be Windows paths or labels with spaces; only
    # the derived IDs are token-like identities.
    payload = {"target": "stable folder/stable.txt", "content": "stable"}
    first = build_adapter_request(
        mission_id="mission-contract",
        workflow_id="workflow-contract",
        step_id="write",
        step_version=1,
        attempt=1,
        action_kind="file.write",
        payload=payload,
        spec=spec,
    )
    replay = build_adapter_request(
        mission_id="mission-contract",
        workflow_id="workflow-contract",
        step_id="write",
        step_version=1,
        attempt=1,
        action_kind="file.write",
        payload=payload,
        spec=spec,
    )
    retry = build_adapter_request(
        mission_id="mission-contract",
        workflow_id="workflow-contract",
        step_id="write",
        step_version=1,
        attempt=2,
        action_kind="file.write",
        payload=payload,
        spec=spec,
    )
    assert first.effect_id == replay.effect_id == retry.effect_id
    assert first.idempotency_key == replay.idempotency_key == retry.idempotency_key
    assert first.request_id != retry.request_id
    assert "content" not in first.to_evidence()


def test_workflow_composes_only_verified_outputs_and_writes_one_world_report(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    intent = TaskIntent(task_id="mission-composition", required_actions=("file.write", "file.read"))
    result = run_workflow(
        database,
        ctx,
        intent,
        (
            WorkflowStep(
                "write",
                "file.write",
                {"target": "source.txt", "content": "verified"},
                postconditions=("exists",),
            ),
            WorkflowStep(
                "read",
                "file.read",
                {"target": "$write.target"},
                postconditions=("exists",),
            ),
            WorkflowStep(
                "copy-hash",
                "file.write",
                {"target": "hash.txt", "content": "$read.content_hash"},
            ),
        ),
        workflow_id="workflow-composition",
    )
    assert result["success"] is True
    assert result["context"]["read"]["exists"] is True
    assert (ctx.owned_root / "hash.txt").read_text(encoding="utf-8")
    assert result["world_report"]["outcome"] == "PASS"
    assert result["world_report"]["observation"]["deduplicated"] is False
    assert all(item["receipt"]["state"] == "VERIFIED" for item in result["results"])

    replay = run_workflow(
        database,
        ctx,
        intent,
        (
            WorkflowStep("write", "file.write", {"target": "source.txt", "content": "verified"}, postconditions=("exists",)),
            WorkflowStep("read", "file.read", {"target": "$write.target"}, postconditions=("exists",)),
            WorkflowStep("copy-hash", "file.write", {"target": "hash.txt", "content": "$read.content_hash"}),
        ),
        workflow_id="workflow-composition",
    )
    assert replay["success"] is True
    assert replay["world_report"]["deduplicated"] is True
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'world.observation_recorded'"
        ).fetchone()[0]
    assert count == 1


def test_postcondition_failure_and_stop_never_claim_pass_or_write_follow_on(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    intent = TaskIntent(task_id="mission-guards", required_actions=("file.write",))
    seed_builtins(database)
    failed = execute_action(
        database,
        ctx,
        intent,
        "file.write",
        {
            "target": "postcondition.txt",
            "content": "written but not accepted",
            "postconditions": [{"field": "exists", "equals": False}],
        },
    )
    assert failed.claimed.value == "COMPLETED"
    assert failed.verified is VerifyVerdict.MISMATCH
    assert failed.success is False
    assert failed.receipt["state"] == "FAILED"

    stop_path = ctx.owned_root / "STOP"
    stop_path.write_text("owner stop\n", encoding="utf-8")
    blocked = run_workflow(
        database,
        ctx,
        intent,
        (WorkflowStep("blocked-write", "file.write", {
            "target": "must-not-exist.txt",
            "content": "blocked",
            "stop_path": str(stop_path),
        }),),
        workflow_id="workflow-stop",
    )
    assert blocked["success"] is False
    assert blocked["results"][0]["claimed"] == "BLOCKED"
    assert not (ctx.owned_root / "must-not-exist.txt").exists()
    assert blocked["world_report"]["status"] == "NOT_EMITTED"


def test_spine_receipt_and_journal_are_idempotent_across_replay(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    first = execute_for_spine(
        database,
        ctx,
        mission_id="mission-spine",
        step_id="write",
        action_kind="file.write",
        payload={"target": "spine.txt", "content": "once"},
        workflow_id="workflow-spine",
    )
    second = execute_for_spine(
        database,
        ctx,
        mission_id="mission-spine",
        step_id="write",
        action_kind="file.write",
        payload={"target": "spine.txt", "content": "once"},
        workflow_id="workflow-spine",
    )
    assert first["mission_id"] == "mission-spine"
    assert first["effect_id"] == second["effect_id"]
    assert first["idempotency_key"] == second["idempotency_key"]
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'fabric.spine.result'"
        ).fetchone()[0]
    assert count == 1
