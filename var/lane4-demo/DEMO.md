# Lane 4 demo — V14 maximum cross-app composition

Run from the lane worktree root:

```powershell
$env:PYTHONPATH='src'; python var/lane4-demo/demo.py
```

Expected: exit 0 and `demo-result.json` with `resume.success=true`,
`no_replay=true`, `nested_workflow_run=true`.

## What the demo proves (numbered to `demo.py`)

1. **Load** named recipe `v14.research_to_artifact` from the canonical registry.
2. **Preflight** it: all three action kinds registered, params resolved, no silent downgrade.
3. **Run** `lane4.demo_close` (notes → verified readback → gated confirm → publish).
   Steps `write`/`read` are REAL `file.*` capabilities; `publish` binds the
   verified `${confirm.text}` and `${read.content_hash}` outputs.
4. **Checkpoint** written to `work/checkpoint.json` after every step (atomic tmp+rename).
5. **Injected failure**: `confirm` reads not-yet-existing `gate.txt` → honest
   FAILED/MATCH, workflow stops at `confirm` (ENGINE PROOF of failure path).
6. **Repair + resume**: `gate.txt` created, second invocation resumes from the
   checkpoint — `write`/`read` are NOT redispatched (`resumed_steps`).
7. **No replay**: resumed effect ids equal first-run effect ids; the
   `system_actions` journal holds exactly one row for the completed write.
8. **General Work composition**: `v14.research_to_docx` runs REAL
   `artifact.docx.compose` + `file.inspect` and returns `output_hash`.
9. **Entrypoint**: a `workflow.run` step nests `v14.research_to_docx`
   (bounded depth 3) through the canonical effect journal and verifies MATCH.

## Step provenance labels

- **REAL (this sandbox)**: `file.write/read/inspect/copy`, `artifact.docx.compose`,
  `artifact.table.transform`, `artifact.xlsx.create`, `workflow.run` nesting,
  checkpoint files, `demo-result.json`.
- **ENGINE PROOF (deterministic, no sibling needed)**: injected `gate.txt`
  failure, resume-skip, retry/soft policies (covered in
  `tests/foundry/test_v14_workflow_composition.py`).
- **BLOCKED_ON_SIBLING_LANE (registered + preflighted, live proof later)**:
  - `browser.web.read {url}` → observed `{final_url, source_sha256, document_id,
    page_revision, authority: DATA_ONLY}` (Lane 1 owns Playwright internals).
  - `desktop.open {process_id, application, target, idempotency_key}` →
    observed `{process_id, window_observation, readback_verified}` (Lane 2 owns
    UIA/session proof; needs a real desktop session).
  - `desktop.control.assert {window: {process_id}, value}` → verified by
    re-observation (Lane 2).
  - `git.push {worktree, remote, branch}` → observed `{remote_head,
    observed_remote_head}` (Lane 3 owns remote/effects proof; disposable
    remote only).
  - `github.pull_request.publish` / provider readback legs (Lane 3).

## Semantic correctness

Default recipes bind TXT/Markdown artifacts to Notepad-class viewers, never
`DOCX → Notepad`. The DOCX/XLSX recipes verify via their own artifact
readback (`output_sha256`, `target_ref`).

## Artifacts in this directory

- `demo.py` — executable demo (above).
- `demo-result.json` — final structured workflow result (regenerated per run).
- `recipe.v14.research_to_artifact.json` — example recipe dump.
- `ORIENTATION.md` — base engine map.
- `HANDOFF.json` — machine-readable lane handoff.
- `work/` — disposable demo runtime (db, owned files, checkpoint); safe to delete.
