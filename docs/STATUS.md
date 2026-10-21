# JARVIS Foundry — Status

**Reconciled 2026-10-21.** This page is the short, honest "where are we"; it adds
no authority. The authority order is still `AGENTS.md`; the long-form current-state
record is `docs/CURRENT_STATE.md`. Everything below was checked against the
repository or a command run on the date above, and is marked when it was *not*
verified.

## What it is

A durable, local-first autonomy kernel for a personal AI organization
(`IDEA.md`): an append-only SQLite ledger, an independent Supervisor with STOP and
recovery, brokered workers, independent verification, long-term memory, and a
Command Center for the owner. About 190k lines of Python under `src/jarvis_foundry`
(45+ subsystems), a React command center under `ui/command-center`, schema v41.

## Where the code is

| Thing | State |
| --- | --- |
| `origin/main` | Contains the owner-experience redesign merged with all of main's backend work. |
| Installed live release | `CURRENT` = `4cac1948…` (built *before* the redesign), `PREVIOUS` = `0bc96b70…`. **Alive on 2026-10-21 (read-only observation):** the `JARVIS-Foundry-Supervisor` task is Running (launcher process up since 10-12 21:11), a `worker_loop --once` cycle had just started (02:34), and the 633 MB ledger had been written within the minute. **Not touched** by this work; health/trust values were not read. |
| Gap between them | Everything on main after `4cac1948` (the redesigned Command Center, the fixes below) is **not live** until a release is promoted via `docs/agent/workflows/promotion.md`, which needs an explicit owner goal. |

## The live system today (read-only ledger observation, 2026-10-21)

Observed by reading the live ledger through a read-only connection (counts and
reasons only; nothing was written and no live process was touched):

* **Awake but idle.** About 70 autonomy cycles and 1,200 Supervisor heartbeats per
  day, yet **0 missions created or completed in the last 7 days**. The last owner
  directive was recorded on **2026-10-06**.
* **Why nothing starts.** The portfolio decides `WAIT` ("no positive-value work is
  currently executable") every cycle. An owner delegation has been blocked 140
  times in 24 h with `EXECUTIVE_OBJECTIVE_REQUIRED` ("delegation cannot choose
  among multiple Executive objectives"), and active owner-goal objectives conflict
  over one shared resource. The ledger holds 43 objectives: 12 completed, 14
  suspended, 9 blocked, 6 failed, 2 active; the two "active" ones date from
  2026-09-24 and are trivial lint fixes that never ran.
* **What unblocks it** (owner decisions, none taken here): give JARVIS a fresh,
  single goal, and/or close or revoke the stale suspended/blocked objectives so a
  delegation can resolve to one objective.
* **Config coupling hazard.** The running worker loop is started with
  `--config <this checkout>\config\worker_routing.yaml`, i.e. it reads the
  *working tree* of this repository. Switching branches, a conflicted merge or an
  edit of that file changes the live worker immediately. One cycle was blocked
  (`worker_loop.activation_blocked`, 2026-10-20 14:43 UTC, YAML parse error from
  merge-conflict markers) during a merge in this very work, and recovered on the
  next cycle. Keep this checkout on a good branch with a valid file; moving the
  config out of the working tree is an owner/promotion decision. `python
  scripts/doctor.py` now checks the file and states this coupling.
## Observed product gaps (deferred, not built)

Per the frozen roadmap, out-of-phase discoveries are recorded, not implemented.

1. **Silent stall on a blocked delegation.** A delegation blocked on
   `EXECUTIVE_OBJECTIVE_REQUIRED` re-emitted `delegation.intent_blocked` every
   cycle (3,301 events in the live ledger; the idempotency key embeds a fresh
   timestamp each evaluation) while the owner was asked for clarification
   **once** (`delegation.clarification_requested`, 2026-10-06) and never again.
   **Fixed in source:** `delegation/service.py::_block` now skips the write when
   the intent's latest delegation event is the identical block (a changed reason
   or any intervening transition still records), with tests. Not live until a
   release is promoted. **Still deferred (owner-gated, changes Executive
   behavior):** re-escalating a stalled delegation to the owner on a schedule.
2. **Stale "ACTIVE" objectives** that never execute (two from 2026-09-24) keep
   competing for the shared resource; stale suspended/blocked objectives are
   never closed without an explicit owner action.
3. **Ledger growth from idle cycles.** The live ledger holds 475k events
   (342 MB of payload, a 633 MB database). The largest contributors are snapshots
   written on every cycle even when nothing changed: `executive.context_assembled`
   (5.0k events, 93 MB), `executive.autonomy_cycle_recorded` (4.8k, 89 MB) and
   `executive.project_context_resolved` (91k, 54 MB); `worker_loop.cycle_started`
   and heartbeats add 172k and 75k small events. It is append-only by design, so
   any dedupe/compaction (for example, skip a snapshot identical to the previous
   one) is an Executive-behavior change for the owner to schedule; nothing here
   is urgent at the current size.
4. **Worker config read from a working tree** (see above).
## Incident from this cleanup (found and repaired)

The live ledger registers **38 resources** (project checkouts the Executive may work
on). Early in this cleanup I deleted a large set of old worktrees and `C:\temp`
folders. Afterwards the live Executive's project-context resolver stopped seeing
the resources whose folders were gone (it had resolved all of them as AVAILABLE
before). Two kinds were affected:

* **`resource-jarvis-canonical`** (the Executive's own-repository target) was
  registered at `C:\temp\JARVIS\wave2-main-promotion-20260924`, one of the
  worktrees removed. **Restored:** the worktree was re-created detached at the
  commit `main` had when it was removed (`4cac1948`), with its four untracked
  paths put back from the salvage copy (they match the registered baseline).
* **25 old proof fixtures** (`resource-hybrid-probe-*`, `resource-prompt*-*`,
  `resource-owner-e2e-*`, `resource-scenario-b*`, `resource-v6-*`) are registered
  as READY/PROBING but their folders no longer exist. They are rehearsal
  leftovers from August-September proofs; leaving them unresolved is harmless and
  arguably cleaner, but they remain registered. `python scripts/doctor.py` now
  lists registered resources with missing roots so this cannot happen silently.

Lesson recorded in the doctor and here: a folder can be a *registered live
resource*; check the ledger before deleting checkouts the system points at. The
live install itself (`%LOCALAPPDATA%\JARVIS`, `...\hermes\foundry`) was never touched.
## Which document to believe

The repo has several overlapping "state" documents written at different times.
They disagree; this is how to read them.

| Document | Dated | Verdict |
| --- | --- | --- |
| `docs/CURRENT_STATE.md` | 2026-09-28 (V7 readiness/live proof) | Newest long-form record. Still the reference for live/runtime claims, but re-verify drift-prone facts. Its documentation pin is enforced by `scripts/doc_staleness_check.py`. |
| `docs/roadmap/JARVIS_1_0_EXECUTION_STATE.yaml` | 2026-09-03 | **Stale.** Names `V11 / A3_A4 / BLOCKED_BY_PROVIDER` as the only authorized gate and a v38/v40 schema; the repo has moved far past that (schema 41, V6/V7/V12/V13 work). The frozen roadmap says only the session that closes a gate updates it, so it was deliberately left untouched. |
| `docs/CAPABILITY_MATRIX.md` | 2026-09-27 | Partly stale. It lists the launcher fix (`647b6e7`, `2ffaa4f`, `6a15cf5`), self-expansion (`a750c7d`), V6 intake (`6da8d8f`, `482896e`) and trust reports (`ece09ec`) as "candidate, not canonical". All of them are ancestors of `main`. A trust-report integration test also exists now (`test_trust_lifecycle.py`). |
| `PROJECT_STATE.md` | 2026-08-13 | Historical snapshot; says so itself. |
| `README.md` | — | Accurate at the summary level. |

## Verified on 2026-10-21

* **Command Center UI** (`ui/command-center`): typecheck clean, lint clean
  (`--max-warnings 0`), production build succeeds, **244 / 244** unit tests pass.
  Rendered in simulated mode: Home, Work and World load with no console errors.
* **Python suite** (371 files in `tests/foundry`, each run in its own process,
  6 in parallel, 900 s per-file timeout; result in
  `docs/evidence/test-baseline-2026-10-21-final.json`, the first pass of the day
  in `docs/evidence/test-baseline-2026-10-21.json`): **343 pass, 27 fail, 1 times
  out**. `test_doc_staleness_check` is among the failures only until the
  docs-only reconciliation commit that closes this work (it pins the documentation
  to its parent). `test_v13_workforce_steering` is load-sensitive: it fails in a
  6-way parallel run and passes alone.
* **No regressions from this work.** The 37 files that did not pass in the first
  run were re-run on plain `main` in the same environment; 35 fail or time out
  identically there, and none of the 28 non-passing files in the final run is
  outside that first set. Nine of the 37 now pass. The rest need real `git
  clone`/`push` transport, process spawning, Windows task/tray APIs, `pip`
  downloads, or a writable system temp directory, none of which the environment
  used for this run provides. They have **not** been root-caused one by one, so
  treat them as *unproven here*, not *proven broken*. Run `python scripts/verify.py`
  on a normal machine to see the real numbers.* **Two known-stale proofs** (deliberately not rewritten): the
  `test_lane3_product_truth_autonomy` fixture still scripts "false claim -> repair
  -> pass" for an owner-goal mission, but those missions now fail closed to the
  Executive by design (`mission/loop.py::_repair_or_block`); and the
  supervisor-process variant in `test_owner_goal_execution_spine` needs real
  process spawning.
## Fixed in this stretch

Real defects found while building a trustworthy baseline:

1. **Campaign children could starve each other** (`executive/autonomy.py`): a
   campaign child's dependency wake parked the *whole objective*, so the ready
   first child never materialized while the second waited on it (regression from
   `9e140545`). Campaign-child wakes now release only their own child.
2. **Observation rotation broke** (`initiative_a2.py`): a total source-count
   ceiling added to the continuous cycle in `01a215e4` made the cycle fail
   instead of rotating when more sources than the per-cycle limit were configured.
3. **Tests could change the owner's machine.** `jarvis setup` writes launchers
   and prepends the install bin directory to the *user PATH in the registry*; the
   setup tests reached it. The user PATH had accumulated **83 leaked pytest
   directories (about 8 KB)**; they were removed (original saved to
   `Documents\JARVIS-salvage-20261020\user-PATH-backup-before-cleanup.txt`). The
   provider-setup tests also wrote to the real Hermes profile (`HERMES_HOME/.env`).
   `tests/foundry/conftest.py` now makes registry writes fail, makes the PATH
   writer a no-op, and redirects `LOCALAPPDATA` / `HERMES_HOME` for the whole suite.
4. **Time bomb**: the v13 world-intelligence proof recorded evidence at a fixed
   date but judged candidate expiry against the wall clock, so it began failing
   after 2026-10-04. The proof now pins the scoring clock.
5. **Stale test fixtures** aligned with decisions already in the code: Muse Spark
   1.3 as the authority (not 1.2), the training-consent flag and single-provider
   pool in worker config, and the Go-only policy that now refuses a Zen dispatch.
6. **Paths with spaces**: the fake worker adapter extracted the target directory
   with `split()[-1]`, which breaks for this very checkout path.
5. **The owner's "Needs you" queue was unusable on the live ledger.** Measured with
   the project's own read-only queue builder: **100 open items, all `UNKNOWN`, all
   `executive.project_context_resolved` telemetry** (5,691 of the newest 10,000
   events), while the 14 genuine escalations and the stalled delegation were absent.
   Causes: unmapped per-cycle events fell through to `EVENT/UNKNOWN`
   (`briefing_items.py::_rule`), `executive.escalation_opened` and
   `delegation.*` were unmapped, and only the newest 10,000 events were scanned, so
   any item older than a day or two of ledger traffic silently dropped out.
   **Fixed in source** (`briefing_items.py`, `decision_queue.py`; projection only, no
   authority): telemetry is skipped; escalations, owner-actionable delegation
   blocks/clarifications and worker-activation failures are classified
   `DECISION_REQUIRED`; escalations are read regardless of age and closed by
   `executive.escalation_resolved`; a stalled intent is one item while it is still
   BLOCKED/AWAITING_CLARIFICATION; activation failures close when a later worker
   cycle starts. On the live ledger the queue goes from 100 junk items to 14
   escalations plus the one stalled delegation. Not live until a release is
   promoted. This also cleans the daily briefing, which counted `UNKNOWN` as an
   exception. The same flood drove notifications: the toast router fires once per *new*
   queue item, and its seen-store records about 4,100 toasts emitted between
   2026-10-05 23:38 and 2026-10-06 11:30, 3,597 of them `project_context_resolved`
   telemetry (owner routing is `in-app-toast`) - roughly twelve hours of "UNKNOWN:
   AVAILABLE" popups, a likely trigger for the owner-experience rescue.
New tooling: **`scripts/verify.py`** — one command for the health check
(`--lane python|ui|docs|all`). It isolates each test file with a timeout, redirects
the live-machine environment, and prints attributable failure lines.

## How to work without drifting

1. Read `AGENTS.md`, then this page, then `docs/CURRENT_STATE.md`.
2. Work on a branch; run `python scripts/verify.py --lane ui` and
   `--lane python --match <area>` while building, the full run at boundaries.
3. Do not touch the live install, databases, Scheduler, trust or release
   pointers without an explicit owner promotion goal.
4. Out-of-phase ideas are recorded, not built (frozen-roadmap rule).

## Documentation pin

`scripts/doc_staleness_check.py` requires `CURRENT_STATE.md` to name the immediate
parent of the docs-only commit that updates it, so the final commit of this work
changes only `docs/CURRENT_STATE.md` and pins its parent. This page, the
baselines and every other file were committed before it.
## Open decisions (owner)

* **Which gate is next.** The execution-state file is stale; a real next gate has
  to be chosen and recorded by the owner. Until then work stays in
  hygiene/verification.
* **Promote the redesign?** The redesigned Command Center and the fixes above are
  on main but not in the running release.
* **Release retention (live install, ~131 GB).** `%LOCALAPPDATA%\JARVIS\releases` holds 133 immutable release folders of about 1 GB each (a venv per release). Only `CURRENT` and `PREVIOUS` (the rollback target) matter operationally; the rest is rebuildable history and the product has no retention policy. It was left alone because the live install is off-limits without an explicit owner goal; `python scripts/doctor.py` reports the figure.
* **Leftover scheduled tasks (clutter only).** Eight old tasks
  (`JARVIS-Codex-V9-Lane1/Lane3-Resume`, the four `…Proving-Window-Monitor*`
  tasks, `JARVIS-Foundry-Worker-Loop`, `JARVIS-Prompt4-M11-Trust-Monitor`) are
  all **Disabled**, so nothing runs from them; they can be deleted at leisure.
  The live tasks are `JARVIS-Foundry-Supervisor` (Running) and `Hermes_Gateway`
  (Ready).