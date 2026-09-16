# LANE 4 — Orientation (base 4b2ae28, schema 40)

## Workflow model (`fabric/compose.py:50-234`)
- `run_workflow(database, ctx, intent, steps, workflow_id, write_world_model)` executes ordered `WorkflowStep`s.
- Duplicate `step_id` rejected; empty workflow rejected.
- Each step: `bind_payload(step.payload, binding_context)` then `execute_action(..., workflow_id, step_id, step_version, attempt)`.
- `attempt` taken from payload `attempt` int>0 else 1.
- `missing` capability → one `try_acquire` retry, else BLOCKED.
- Result row: `result.as_dict()` + `step_id`. `context[step]=observed` (diagnostic, includes failures). `binding_context[step]=observed` ONLY on `result.success` (COMPLETED+MATCH).
- Stop: first non-success with `on_failure != "continue"` sets `stopped_at`, breaks. `on_failure=="continue"` records and proceeds.
- Outcome: PASS only if all COMPLETED+MATCH and full length. BLOCKED if any BLOCKED. UNKNOWN if any receipt FROZEN/UNKNOWN or verified UNKNOWN. Else FAILED.
- World report: only when every result verified MATCH/MISMATCH, not BLOCKED, receipt not FROZEN/UNKNOWN → `fabric:verified_workflow` observation (dedupe by workflow_id+outcome+evidence_refs). Else NOT_EMITTED.

## Binding semantics (`compose.py:27-47`, `model.py:251-286`)
- `lookup(path, context)`: dotted `a.b.c` walk over Mappings; raises `FabricError("workflow binding $path is missing")`.
- `bind_value`: full-string `$path` → lookup; `$$` escaped (returned verbatim); Mapping/list recurse; else verbatim.
- No params/defaults layer, no embedded interpolation, no `${}` form. Missing binding raises FabricError (good — must preserve).
- Verified-output-only rule is enforced: failed/blocked/ambiguous observations never enter `binding_context`.

## Failure behavior
- `WorkflowStep.on_failure` in {"stop","continue"} only (`model.py:269`). Default "stop" — must stay fail-closed.
- FAILED-but-honest claims verify MATCH; MISMATCH only on lying COMPLETED. STOP returns BLOCKED/UNKNOWN.
- No soft/optional/retry policy; no per-step retry count; repair delegated to MissionLoop (not in workflow engine).

## Attempt / effect identity (`loop.py:1157-1203`, `effects.py:91-138`, `contracts.py:394-480`)
- `build_adapter_request(mission_id, workflow_id, step_id, step_version, attempt, ...)` derives stable `effect_id` (system+target+capability/version+input hash) and `idempotency_key` (effect+mission/workflow/step).
- `effect_id` stable across `attempt` and control fields (test `test_typed_metadata...` asserts attempt 1 vs 2 same effect_id; stop_path/timeout excluded).
- `execute_action` first calls `_existing_canonical_effect_binding`: rebuilds request from current payload for every spec of that action_kind; if `effect_state in {STARTED,COMPLETED,FAILED,FROZEN}` → reconcile path (no new selection), ambiguous multi-match refuses.
- Mutating adapters go through `execute_fabric_effect` (STARTED→COMPLETED/FAILED/FROZEN, monotonic, crash→FROZEN, never re-execute terminal).
- `execute_for_spine` proves idempotent replay (same effect_id/idempotency_key, one `fabric.spine.result` event).

## CLI entrypoint (`cli/main.py:121-143,379-440`)
- `jarvis fabric run --workflow <json inside disposable home> [--mcp-config]` → `_fabric_run`: reads `{task_id, owned_root, steps[]}`, builds TaskIntent(required_actions=step kinds), `run_workflow`, prints JSON, exit 0 iff success.
- No list/inspect/preflight/resume verbs. No recipe name resolution. `jarvis fabric catalog` only other verb.

## General Work verbs (`fabric/general_work.py:31-59`, `general_work/model.py`)
- FILE: file.inspect/search/copy/move/rename (+ safe_text_edit path, not a separate action_kind).
- ARTIFACT: artifact.table.merge/transform, artifact.xlsx.create/update, artifact.docx.compose/edit, artifact.pdf.create/combine/split, artifact.pptx.create/update.
- VERIFY: general_work.verify (recorded immutable plan only).
- Legacy local_artifacts verbs still in loop verifier: artifact.parse/table.summary/docx.create/docx.verify/table.export/pdf.copy/pptx.copy/image.transform.
- File observed: file.write {target,path,content_hash,exists}; file.read {target,exists,content_hash,text}; transfer {target_identity...}. Artifact observed: {target_ref,output_sha256,source_ref/source_sha256/source_refs...} plus readback.
- Personal-operator route owns file.inspect/search/copy/move/rename + some artifact verbs when route_owner personal_operator.

## Canonical action names relevant to V14 recipes (80 total, `seed_builtins`)
- read/research: browser.web.read, browser.web.wait, http.doc.read, github.*.read, file.read/inspect/search, system.diagnose
- artifact: file.write, artifact.docx.compose/edit, artifact.xlsx.create/update, artifact.pdf.create, artifact.pptx.create, artifact.table.merge/transform, general_work.verify, file.read (readback)
- desktop: desktop.open/focus/close, desktop.window.observe, desktop.control.inspect/click/replace_text/type/select/wait/assert, desktop.action.reconcile, local.process.launch/inspect/reconcile/close
- git/github effect: git.inspect/diff/branch/commit/log/push/read_ref, github.issue.create, github.pull_request.create/publish, github.repo.read (readback)
- No `workflow.run` / `cap.workflow.run` yet — Slice 8 must add smallest canonical capability.

## What checkpoint/resume can reuse
- Effect journal already gives no-replay for mutating steps with identical (mission/workflow/step/payload→effect_id). READ_ONLY steps have no journal and safely re-execute.
- Missing: durable workflow-level checkpoint (steps completed, verified outputs, current step, attempts, effect ids, terminal state) + resume that skips verified-completed steps and rebinds their outputs without redispatch.
- Plan: checkpoint as JSON sidecar (owned_root or explicit path) + `run_recipe/resume_recipe` that reuses binding_context from checkpoint; never replay a COMPLETED irreversible effect (skip + reconcile via effect journal on ambiguity).

## Verified-output binding rule (preserved)
- Only successful/verified observations may feed downstream trusted bindings. Checkpoint resume must only restore verified outputs.
