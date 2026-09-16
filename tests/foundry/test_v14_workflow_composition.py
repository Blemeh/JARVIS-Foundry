"""Lane 4: recipe composition, checkpoint/resume, failure policies, workflow.run."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    WorkflowStep,
    bind_payload,
    build_checkpoint,
    dump_recipe,
    list_recipes,
    load_recipe,
    preflight_recipe,
    resume_recipe,
    run_recipe,
    run_workflow,
    validate_recipe,
    workflow_result_summary,
)
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import FabricError
from jarvis_foundry.fabric.recipes.model import recipe_from_mapping


def _db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _ctx(tmp_path: Path, database: FoundryDatabase) -> ExecutorContext:
    return ExecutorContext(database=database, owned_root=tmp_path / "owned")


def _intent(*actions: str) -> TaskIntent:
    return TaskIntent(task_id="lane4-test", required_actions=tuple(actions))


def test_recipe_registry_lists_registered_valid_recipes() -> None:
    listed = list_recipes()
    ids = sorted(item["recipe_id"] for item in listed)
    assert ids == [
        "v14.artifact_to_local_repo",
        "v14.digital_operator.close",
        "v14.open_and_verify",
        "v14.publish_verified_work",
        "v14.research_to_artifact",
        "v14.research_to_docx",
        "v14.research_to_xlsx",
        "v16.bounded_note",
    ]
    for item in listed:
        assert validate_recipe(load_recipe(item["recipe_id"])) == []


def test_unknown_recipe_raises() -> None:
    with pytest.raises(FabricError):
        load_recipe("v14.nope")


def test_preflight_ready_and_missing_params(tmp_path: Path) -> None:
    database = _db(tmp_path)
    seed_builtins(database)
    recipe = load_recipe("v14.research_to_artifact")
    ready = preflight_recipe(database, recipe, {"source_url": "https://example.invalid"})
    assert ready["ready"] is True
    assert ready["blocked_step"] is None
    not_ready = preflight_recipe(database, recipe, {})
    assert not_ready["ready"] is False
    assert not_ready["params_ok"] is False
    with pytest.raises(FabricError):
        run_recipe(database, _ctx(tmp_path, database), recipe, {})


def test_preflight_reports_blocked_step_for_unknown_action(tmp_path: Path) -> None:
    database = _db(tmp_path)
    seed_builtins(database)
    recipe = recipe_from_mapping({
        "recipe_id": "lane4.probe",
        "version": "1.0.0",
        "description": "probe",
        "required_capabilities": ("nope.action",),
        "parameters": {},
        "steps": ({"step_id": "s1", "action_kind": "nope.action", "payload": {}},),
        "outputs": {},
    })
    report = preflight_recipe(database, recipe, {})
    assert report["ready"] is False
    assert report["blocked_step"] == "s1"


def test_params_defaults_render_deterministically(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    recipe = recipe_from_mapping({
        "recipe_id": "lane4.defaults",
        "version": "1.0.0",
        "description": "defaults",
        "parameters": {"target": {"default": "out.md"}, "title": {"default": "T"}},
        "steps": (
            {"step_id": "w", "action_kind": "file.write",
             "payload": {"target": "$params.target", "content": "# ${params.title}\n"}},
            {"step_id": "r", "action_kind": "file.read",
             "payload": {"target": "$w.target"}},
        ),
        "outputs": {"target": "$w.target"},
    })
    result = run_recipe(database, ctx, recipe, {})
    assert result["success"] is True
    assert result["summary"]["outputs"] == {"target": "out.md"}
    assert (ctx.owned_root / "out.md").read_text(encoding="utf-8") == "# T\n"


def test_missing_binding_raises_clear_error(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    with pytest.raises(FabricError, match=r"workflow binding \$missing\.field is missing"):
        run_workflow(database, ctx, _intent("file.read"), (
            WorkflowStep("r", "file.read", {"target": "$missing.field"}),
        ))


def test_embedded_interpolation_and_escape(tmp_path: Path) -> None:
    bound = bind_payload(
        {"content": "a-${x.y}-b", "literal": "$${x.y}", "whole": "$x.y"},
        {"x": {"y": "V"}},
    )
    assert bound["content"] == "a-V-b"
    assert bound["literal"] == "${x.y}"
    assert bound["whole"] == "V"


def test_verified_output_only_failed_step_cannot_feed_downstream(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    with pytest.raises(FabricError, match="workflow binding"):
        run_workflow(database, ctx, _intent("file.read", "file.write"), (
            WorkflowStep("missing", "file.read", {"target": "absent.txt"}, on_failure="continue"),
            WorkflowStep("use", "file.write", {"target": "out.txt", "content": "$missing.text"}),
        ))


def test_checkpoint_resume_skips_completed_effect_without_replay(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    steps = (
        WorkflowStep("write", "file.write", {"target": "artifact.md", "content": "v1"}),
        WorkflowStep("read", "file.read", {"target": "$write.target"}),
        WorkflowStep("confirm", "file.read", {"target": "gate.txt"}),
        WorkflowStep("publish", "file.write", {"target": "published.txt", "content": "$confirm.text"}),
    )
    intent = TaskIntent(task_id="lane4-resume", required_actions=("file.write", "file.read"))
    checkpoint_path = tmp_path / "checkpoint.json"
    first = run_workflow(
        database, ctx, intent, steps,
        workflow_id="workflow:lane4-resume", checkpoint_path=checkpoint_path,
    )
    assert first["success"] is False
    assert first["stopped_at"] == "confirm"
    assert checkpoint_path.is_file()
    first_effects = [row["receipt"]["effect_id"] for row in first["results"] if row.get("receipt")]
    # Repair the gate, then resume from the checkpoint.
    (ctx.owned_root / "gate.txt").write_text("gate-content", encoding="utf-8")
    resumed = run_workflow(
        database, ctx, intent, steps,
        workflow_id="workflow:lane4-resume",
        resume_checkpoint=first["checkpoint"],
        checkpoint_path=checkpoint_path,
    )
    assert resumed["success"] is True
    assert resumed["resumed_steps"] == ["write", "read"]
    assert resumed["stopped_at"] is None
    second_effects = [row["receipt"]["effect_id"] for row in resumed["results"] if row.get("receipt")]
    assert second_effects[0] == first_effects[0]
    assert second_effects[1] == first_effects[1]
    assert (ctx.owned_root / "published.txt").read_text(encoding="utf-8") == "gate-content"
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM system_actions WHERE action_key = ?", (first_effects[0],)
        ).fetchone()[0]
    assert count == 1


def test_recipe_runner_resume_path(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    recipe = recipe_from_mapping({
        "recipe_id": "lane4.runner",
        "version": "1.0.0",
        "description": "runner resume",
        "parameters": {},
        "steps": (
            {"step_id": "w", "action_kind": "file.write",
             "payload": {"target": "a.txt", "content": "A"}},
            {"step_id": "gate", "action_kind": "file.read",
             "payload": {"target": "gate.txt"}},
            {"step_id": "p", "action_kind": "file.write",
             "payload": {"target": "b.txt", "content": "$gate.text"}},
        ),
        "outputs": {"done": "$p.target"},
    })
    first = run_recipe(database, ctx, recipe, {}, task_id="lane4-runner", workflow_id="workflow:lane4-runner")
    assert first["success"] is False
    (ctx.owned_root / "gate.txt").write_text("G", encoding="utf-8")
    second = resume_recipe(
        database, ctx, recipe, {}, checkpoint=build_checkpoint(first),
        task_id="lane4-runner", workflow_id="workflow:lane4-runner",
    )
    assert second["success"] is True
    assert second["summary"]["resumed_steps"] == ["w"]
    assert second["summary"]["outputs"] == {"done": "b.txt"}


def test_soft_failure_continues_but_marks_summary(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    result = run_workflow(database, ctx, _intent("file.read", "file.write"), (
        WorkflowStep("soft", "file.read", {"target": "absent.txt"}, on_failure="soft"),
        WorkflowStep("after", "file.write", {"target": "after.txt", "content": "ok"}),
    ))
    assert result["success"] is False
    assert result["stopped_at"] is None
    assert result["soft_failed_steps"] == ["soft"]
    assert (ctx.owned_root / "after.txt").is_file()


def test_retry_policy_reexecutes_once(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    result = run_workflow(database, ctx, _intent("file.read"), (
        WorkflowStep("r", "file.read", {"target": "absent.txt"}, on_failure="retry"),
    ))
    assert result["success"] is False
    assert result["results"][0]["attempts"] == 2


def test_workflow_run_entrypoint_nests_file_recipe(tmp_path: Path) -> None:
    database = _db(tmp_path)
    seed_builtins(database)
    from jarvis_foundry.fabric.catalog import get_spec

    assert get_spec(database, "cap.workflow.run") is not None
    ctx = _ctx(tmp_path, database)
    result = run_workflow(database, ctx, _intent("workflow.run"), (
        WorkflowStep("nested", "workflow.run", {
            "recipe_id": "v14.research_to_docx",
            "params": {"doc_title": "Nested", "body": "hello"},
        }),
    ), workflow_id="workflow:lane4-nested")
    assert result["success"] is True
    assert (ctx.owned_root / "research" / "report.docx").is_file()


def test_workflow_result_summary_aggregates_receipts(tmp_path: Path) -> None:
    database = _db(tmp_path)
    ctx = _ctx(tmp_path, database)
    result = run_workflow(database, ctx, _intent("file.write", "file.read"), (
        WorkflowStep("w", "file.write", {"target": "s.txt", "content": "x"}),
        WorkflowStep("r", "file.read", {"target": "$w.target"}),
    ))
    summary = workflow_result_summary(result)
    assert summary["success"] is True
    assert summary["completed"] == ["w", "r"]
    assert len(summary["effect_ids"]) == 2
    assert len(summary["receipt_ids"]) == 2
