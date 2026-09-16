"""Lane 4 required demo: load, preflight, run, bind, checkpoint, fail, resume, result.

Run from the lane worktree root:
  $env:PYTHONPATH='src'; python var/lane4-demo/demo.py

Real vs stubbed vs blocked labeling (see DEMO.md):
- REAL: file.*, artifact.docx.compose, file.inspect, workflow.run nesting (local substrate).
- ENGINE PROOF (test-local data, no sibling needed): injected gate failure + resume.
- BLOCKED_ON_SIBLING_LANE: browser.web.read / desktop.* / git.push live legs
  (registered; preflighted; live proof needs Lane 1/2 remotes, desktop session).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.fabric import (  # noqa: E402
    ExecutorContext,
    build_checkpoint,
    dump_recipe,
    list_recipes,
    load_recipe,
    preflight_recipe,
    resume_recipe,
    run_recipe,
    run_workflow,
    summarize_checkpoint,
    validate_recipe,
)
from jarvis_foundry.fabric.loop import seed_builtins  # noqa: E402
from jarvis_foundry.fabric.model import TaskIntent, WorkflowStep  # noqa: E402
from jarvis_foundry.fabric.recipes.model import recipe_from_mapping  # noqa: E402

DEMO_ROOT = Path(__file__).resolve().parent
WORK = DEMO_ROOT / "work"


def _fresh() -> tuple[FoundryDatabase, ExecutorContext]:
    if WORK.exists():
        shutil.rmtree(WORK)
    (WORK / "owned").mkdir(parents=True, exist_ok=True)
    database = FoundryDatabase(WORK / "foundry.db")
    database.initialize()
    seed_builtins(database)
    ctx = ExecutorContext(database=database, owned_root=WORK / "owned")
    return database, ctx


def main() -> int:
    database, ctx = _fresh()
    log: dict = {"phases": []}

    # 1. Load a named recipe.
    recipe = load_recipe("v14.research_to_artifact")
    assert validate_recipe(recipe) == []
    log["phases"].append({"phase": "load_named_recipe", "recipe": recipe.recipe_id, "version": recipe.version})
    print(f"[1] loaded named recipe {recipe.recipe_id} v{recipe.version} ({len(recipe.steps)} steps)")

    # 2. Preflight (structured; never silent-downgrades).
    pre = preflight_recipe(database, recipe, {"source_url": "https://example.invalid/report"}, owned_root=ctx.owned_root)
    log["phases"].append({"phase": "preflight", "ready": pre["ready"], "blocked_step": pre["blocked_step"]})
    print(f"[2] preflight ready={pre['ready']} blocked_step={pre['blocked_step']}")
    for step in pre["steps"]:
        print(f"    - {step['step_id']}: {step['action_kind']} registered={step['registered']}")

    # 2b. Preflight the full close-out (browser/desktop/git legs registered; live proof is sibling-owned).
    close = load_recipe("v14.digital_operator.close")
    pre_close = preflight_recipe(
        database, close,
        {"source_url": "https://example.invalid", "process_id": "demo-notepad", "worktree": str(ctx.owned_root)},
        owned_root=ctx.owned_root,
    )
    log["phases"].append({"phase": "preflight_close", "ready": pre_close["ready"]})
    print(f"[2b] v14.digital_operator.close preflight ready={pre_close['ready']} (live legs BLOCKED_ON_SIBLING_LANE, see DEMO.md)")

    # 3-5. Runnable close-out on the real local substrate (file verbs are REAL).
    demo_recipe = recipe_from_mapping({
        "recipe_id": "lane4.demo_close",
        "version": "1.0.0",
        "description": "Demo close-out: notes, verified readback, gated confirm, publish.",
        "parameters": {},
        "steps": (
            {"step_id": "write", "action_kind": "file.write",
             "payload": {"target": "notes.md", "content": "# Demo notes\nreal local write\n"}},
            {"step_id": "read", "action_kind": "file.read",
             "payload": {"target": "$write.target"}},
            {"step_id": "confirm", "action_kind": "file.read",
             "payload": {"target": "gate.txt"}},
            {"step_id": "publish", "action_kind": "file.write",
             "payload": {"target": "published.txt", "content": "gate said: ${confirm.text}\nsource hash: ${read.content_hash}\n"}},
        ),
        "outputs": {"published": "$publish.target"},
    })
    checkpoint_file = WORK / "checkpoint.json"
    first = run_recipe(
        database, ctx, demo_recipe, {},
        task_id="lane4-demo", workflow_id="workflow:lane4-demo",
        checkpoint_path=checkpoint_file,
    )
    log["phases"].append({
        "phase": "run_with_injected_failure",
        "success": first["success"],
        "stopped_at": first["stopped_at"],
        "completed": first["summary"]["completed"],
    })
    print(f"[3] first run success={first['success']} stopped_at={first['stopped_at']} "
          f"completed={first['summary']['completed']}")
    # 4. Verified-output binding proof: publish payload rendered from $confirm/$read (see resume).
    assert first["stopped_at"] == "confirm"
    assert checkpoint_file.is_file()

    # 6-7. Repair + resume without replaying the completed effect.
    (ctx.owned_root / "gate.txt").write_text("gate-content", encoding="utf-8")
    before_effects = [row["receipt"]["effect_id"] for row in first["results"] if row.get("receipt")]
    second = resume_recipe(
        database, ctx, demo_recipe, {},
        checkpoint=build_checkpoint(first),
        task_id="lane4-demo", workflow_id="workflow:lane4-demo",
        checkpoint_path=checkpoint_file,
    )
    after_effects = [row["receipt"]["effect_id"] for row in second["results"] if row.get("receipt")]
    no_replay = after_effects[0] == before_effects[0] and after_effects[1] == before_effects[1]
    log["phases"].append({
        "phase": "resume",
        "success": second["success"],
        "resumed_steps": second["summary"]["resumed_steps"],
        "no_replay_of_completed_effect": no_replay,
        "outputs": second["summary"]["outputs"],
    })
    print(f"[6] resume success={second['success']} resumed={second['summary']['resumed_steps']} no_replay={no_replay}")
    published = (ctx.owned_root / "published.txt").read_text(encoding="utf-8")
    assert "gate-content" in published
    print(f"[7] published.txt binds verified output: {published.splitlines()[0]!r} ...")

    # 8. General Work composition proof (REAL artifact verbs).
    docx = run_recipe(database, ctx, "v14.research_to_docx", {"doc_title": "Demo", "body": "hello"})
    log["phases"].append({"phase": "general_work_docx", "success": docx["success"], "outputs": docx["summary"]["outputs"]})
    print(f"[8] v14.research_to_docx success={docx['success']} outputs={docx['summary']['outputs']}")

    # 8b. Fabric/MissionLoop entrypoint proof: workflow.run nests the docx recipe.
    intent = TaskIntent(task_id="lane4-demo-nested", required_actions=("workflow.run",))
    nested = run_workflow(database, ctx, intent, (
        WorkflowStep("nested", "workflow.run", {
            "recipe_id": "v14.research_to_docx",
            "params": {"doc_title": "Nested", "body": "via workflow.run",
                       "doc_target": "research/nested.docx",
                       "idempotency_key": "lane4-demo-nested-1"},
        }),
    ), workflow_id="workflow:lane4-demo-nested")
    log["phases"].append({"phase": "workflow_run_entrypoint", "success": nested["success"]})
    print(f"[8b] workflow.run entrypoint success={nested['success']}")

    # Final structured result.
    final = {
        "recipes_available": [item["recipe_id"] for item in list_recipes()],
        "demo_recipe": demo_recipe.recipe_id,
        "first_run": {"success": first["success"], "stopped_at": first["stopped_at"]},
        "resume": {"success": second["success"], "resumed_steps": second["summary"]["resumed_steps"],
                   "no_replay": no_replay, "outputs": second["summary"]["outputs"]},
        "checkpoint": summarize_checkpoint(build_checkpoint(second)),
        "nested_workflow_run": nested["success"],
    }
    (DEMO_ROOT / "demo-result.json").write_text(json.dumps(final, indent=2, sort_keys=True), encoding="utf-8")
    (DEMO_ROOT / "recipe.v14.research_to_artifact.json").write_text(
        json.dumps(dump_recipe("v14.research_to_artifact"), indent=2, sort_keys=True), encoding="utf-8")
    log["final"] = final
    print("[done] demo-result.json + recipe dump written; all REAL steps verified on local substrate")
    ok = first["stopped_at"] == "confirm" and second["success"] and no_replay and nested["success"] and docx["success"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
