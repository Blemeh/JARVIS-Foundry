# LANE 4 MASTER PROMPT — V14 Digital Operator composition (this is the actual V14 product)

**Paste this entire file as the first message in a new GPT chat.**  
**Preferred model: GPT. Fallback: Claude.**

---

You are a principal JARVIS engineer. Tonight you build the **missing V14 product**: one owner objective that crosses browser + files + desktop + git **without a second brain**. V13 built the pieces. V14 is composition. You will implement a lot of workflow machinery and a named Digital Operator recipe. You will **not** create Executive v2, MissionLoop v2, Fabric catalog v2, or an “Orchestrator Agent.”

JARVIS is internal, single-owner. Do not security-theater this. Do not write an encyclopedia of tests. Make `run_workflow` drive a real multi-step operator journey.

Do not touch live foundry home. Isolated worktree. Do not merge main.

## Mission

Ship a named workflow the rest of the program can run:

```text
browser.web.read (public page)
  -> artifact.docx.compose or artifact.xlsx.create  (General Work, already exists)
  -> desktop.open + desktop.control.assert          (Lane 2 verbs)
  -> github.pull_request.publish or git.push        (Lane 3 verbs)
```

Bindings via existing `$step.field` in `compose.py`. If a step fails, stop (unless `on_failure=continue`). Only **verified** observations enter `binding_context` — that rule is already in `run_workflow`. Keep it. Build **around** it: named recipes, resume, defaults, a demo.

Live close (Playwright → doc → Notepad → one PR → STOP → resume) is later. You make it **invocable**.

## Current truth

- Base + live: `4b2ae28da582b369982661c2a1d2b1792e2f48cb`
- Worktree:

```powershell
New-Item -ItemType Directory -Force -Path C:\temp\JARVIS | Out-Null
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
git fetch origin
git worktree add -b candidate/lane-4-v14-compose-20260915 "C:\temp\JARVIS\lane-4-v14-compose-20260915" 4b2ae28da582b369982661c2a1d2b1792e2f48cb
cd "C:\temp\JARVIS\lane-4-v14-compose-20260915"
```

## What already exists

- `src/jarvis_foundry/fabric/compose.py`
  - `lookup`, `bind_value`, `bind_payload`, `run_workflow`
  - `WorkflowStep`, `TaskIntent` in `fabric/model.py`
  - Binding context only gets successful verified observations
- CLI already calls `run_workflow` from `src/jarvis_foundry/cli/main.py` (~line 387)
- General Work verbs in `fabric/general_work.py`: `artifact.xlsx.create`, `artifact.docx.compose`, `artifact.pptx.create`, `file.copy`, …
- Browser / desktop / github verbs are in `fabric/loop.py` `seed_builtins` — **call them by those names**
- Dead: `connectors/composed.py` V5 harness. Do not revive.

Stay out of implementing Playwright internals, UIA internals, GitHub HTTP, voice, CC chrome. You **compose** those verbs. If a verb is missing a payload field, add a thin adapter function in compose/recipes, or document the gap — don’t steal Lane 1–3 files unless a one-line payload alias is required.

You **may** add:

- `src/jarvis_foundry/fabric/recipes/` (new) for named workflows
- extensions to `compose.py` (recipe loader, resume from last good step, pretty result)
- a way for MissionLoop / general work to **select** a named recipe (inspect `mission/loop.py` first; a small hook is allowed; a new loop is not)

## Backlog — keep going

### Slice 1 — Recipe registry

- `WorkflowStep` sequences as data (Python or YAML matching current dataclasses).
- `load_recipe("v14.digital_operator.close")` returns steps.
- Include `$step` bindings for URL → extracted title → docx path → desktop payload → git payload.

### Slice 2 — Digital Operator close recipe (the product)

Concrete default demo (harmless):

1. `browser.web.read` of a public docs/GitHub URL (or Lane 1 toy page if file:// is easier in this worktree)
2. `artifact.docx.compose` into `var/lane4-demo/out.docx` using `$read.title` / `$read.text`
3. `desktop.open` that path with notepad (payload shape from `computer_operator.py`)
4. `desktop.control.assert` text present
5. `git.push` to a **local** bare remote created in the demo folder (not origin), **or** a no-op stub step with `on_failure=continue` if git isn’t wired yet — prefer real `git.push`

### Slice 3 — Resume

- If step 4 fails, a second `run_workflow` with `attempt` / last-good-step must not redo step 5’s effect.
- Use existing execute_action attempt + effect keys. Don’t start EffectJournal2.

### Slice 4 — MissionLoop hook

- Find where a mission step could call `run_workflow`. Add the smallest path: a fabric action_kind like `workflow.run` **only if** catalog already has a workflow kind, or a helper used by an existing executor.
- Inspect `Kind.WORKFLOW` in `fabric/model.py`. Prefer registering one capability `cap.workflow.run` in `seed_builtins` if that’s the native pattern. That’s allowed. A second MissionLoop is not.

### Slice 5 — Operator journey library

Add 2–3 more recipes once the engine is happy:

- `research_to_docx` (browser + artifact only)
- `open_and_assert` (desktop only wrapper)
- `publish_branch` (git only wrapper)

### Slice 6 — Demo CLI

`python -m jarvis_foundry ...` if a subcommand already runs workflows; otherwise `scripts/lane4_digital_operator_demo.py`. `DEMO.md` with the recipe JSON/YAML dump.

## How to work

- The comment in `compose.py` says combinations stay data. Obey that: recipes are data, engine stays generic.
- Do not implement “AI planner that writes new Python.” Planning already lives in Executive/MissionLoop.
- Don’t Gmail. Don’t voice. Don’t 72h.
- Tests: bind_value missing `$step` still errors; happy-path recipe with stub executors if live Playwright is heavy. One integration demo is worth more than 30 tests.

## Final report

```text
VERDICT:
BASE: 4b2ae28da582b369982661c2a1d2b1792e2f48cb
FINAL:
BRANCH: candidate/lane-4-v14-compose-20260915
WORKTREE: C:\temp\JARVIS\lane-4-v14-compose-20260915
RECIPES SHIPPED:
ENGINE CHANGES:
MISSIONLOOP HOOK:
DEMO:
LIVE JARVIS: not touched
NEXT:
```
