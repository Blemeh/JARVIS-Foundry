# Prompt 1 recovery baseline (append-only)

Recorded: 2026-08-19T15:05+00:00 (local 17:05+02:00).
Recovery attempt: Prompt 1 of 4 — Recovery Core.
Observer: isolated read-only M0 then deployment-free M1. No live Task Scheduler, live DB, or live Supervisor mutation.

Historical claims from `docs/CURRENT_STATE.md` / `PROJECT_STATE.md` are labeled **HISTORICAL** below. Everything else in this file is **OBSERVED** on this machine at the timestamp above.

---

## OBSERVED — Git truth map

| Item | Value |
|---|---|
| Repository root | `C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry` |
| Current branch | `main` |
| Current HEAD (canonical checkout) | `0b45d9ee799d1c5de38f8c307fc0455783aca80d` |
| Local `main` | `0b45d9ee799d1c5de38f8c307fc0455783aca80d` |
| `origin/main` | `b8f7642e504d27bb3459f3a68c4e20e315e32293` |
| Merge-base HEAD vs origin/main | `b8f7642e504d27bb3459f3a68c4e20e315e32293` |
| Local main vs origin/main | **NOT equal.** Local main is **ahead 3** |
| Working tree | clean (no staged/modified tracked files in the canonical checkout) |
| Worktrees | 72 registered (none deleted) |

Commits on local `main` not on `origin/main`:

1. `c090c57` — Goal 1 STOP identity and owned process trees
2. `c874e76` — Goal 2 Supervisor-owned worker_loop and P1 mission correctness
3. `0b45d9e` — Goal 3 structural P2/P3: singular config, FAST/FULL truth, one production executor

This recovery **does not continue Goal 4** and **does not reset main**.

### HISTORICAL (docs, not observed HEAD)

`docs/CURRENT_STATE.md` last reconciled 2026-08-19 still pins:

- CURRENT CANONICAL HEAD `e9da9ba`
- TESTED SOURCE HEAD `84a22b4`
- semantic runtime source `93d23f5`
- deployed release `c60db92`

Those SHAs are **ancestors of current local main** (they appear in `git log`). They are **not** the current checkout HEAD.

---

## OBSERVED — Live runtime identity map

| Item | Value |
|---|---|
| Foundry home | `C:\Users\b\AppData\Local\hermes\foundry` |
| Live DB | `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` (plus `-wal`/`-shm`; WAL active) |
| Schema | **v33** (`schema_migrations.max=33`; table `mission_step_contracts` **absent**) |
| Source schema in checkout | **v34** (`SCHEMA_VERSION = 34`) |
| DB health | `PRAGMA integrity_check = ok` |
| STOP | **OFF** (no `EMERGENCY_STOP` file) |
| Trust | **LAB** (`supervisor_state.trust_state`) |
| Supervisor instance | `supervisor-401cd2da-ae13-44f6-bd6d-abc7186ff981` status RUNNING, lease `supervisor:singleton` |
| Supervisor executable | `...\JARVIS-Foundry\.venv\Scripts\python.exe` |
| Supervisor args | `-m jarvis_foundry.supervisor.run --home "C:\Users\b\AppData\Local\hermes\foundry"` |
| Supervisor cwd (task) | **empty** |
| Supervisor PIDs | 25528 (parent) and child 26852 |
| Code actually imported | **this developer checkout** (`source_kind=git_checkout`, HEAD `0b45d9e`) |
| spawn_worker_loop | **OFF** (scheduled task has no `--spawn-worker-loop`) |
| Worker-loop task | `JARVIS-Foundry-Worker-Loop` **Disabled**; installer refuses to enable the production task name |
| Worker config | task points at repo `config\worker_routing.yaml` (`enabled: true` in YAML, but the task is disabled and Supervisor does not spawn) |
| Hermes python | present (`...\hermes-agent\venv\Scripts\python.exe`) |
| OpenHands exe | present |
| Active missions | 7 ACTION_SELECTION, 8 BLOCKED, 8 COMPLETE, 1 PLANNED |
| Live `worker_loop.*` events | 1 historical `worker_loop.frozen` only |
| Release identity table | `release_control` empty |
| Directives (active) | redirect, waive, extend_duty_window, align (2026-08-12 set) |

### Task Scheduler (observed)

| Task | State |
|---|---|
| JARVIS-Foundry-Supervisor | Running |
| JARVIS-Foundry-Worker-Loop | Disabled |
| JARVIS-Foundry-Proving-Window-Monitor-Canonical-R3 | Ready (script `run-monitor-canonical-c60db92-r3.cmd`) |
| older proving-window monitors | Disabled |

**Question 9:** production **is** serving from a candidate/developer checkout (the canonical repo `.venv` + live `src`, not a frozen release tree).

**HISTORICAL:** CURRENT_STATE says deployed release `c60db92`. The R3 monitor script name still references `c60db92`. The Python module loaded by Supervisor is **current checkout `0b45d9e`**, not a separate `c60db92` install prefix.

---

## OBSERVED — What currently prevents autonomous operation

1. Supervisor heartbeat is watchdog-only: STOP, lease, reap. `spawn_worker_loop` default **false**.
2. Scheduled worker-loop task is **Disabled** and **cannot be enabled** for the production task name.
3. Live DB is **v33**; executable contracts (`mission_step_contracts`, schema v34) are **not live**.
4. Live missions therefore have **no dispatchable executable contracts**.
5. After a verified PASS, `MissionLoop` writes `next_action=None`. The next `worker_loop` cycle **WAITs** (`action list is exhausted`). There is no unattended Action-B writer on the production worker_loop path.
6. `worker_loop` does not pass a commit envelope, so PASS does not make a local git commit; leftover uncommitted files poison the next step's evidence delta.
7. `latest_contract(mission_id)` **fail-closes** if two active `step_id`s exist, so a two-step plan cannot be represented as two contracts without supersession on one step_id.
8. Unresolved **writer collision** (see below).

---

## OBSERVED — Collision state

**COLLISION = YES (unresolved).** Do not overwrite, kill, reset, or retarget live Supervisor.

1. **Live Supervisor imports this checkout.** Any `src/` edit here becomes live on the next scheduled Supervisor restart (~1 minute). M2 source mutation would mutate the live system without a deployment action.
2. **Sibling worktree** `C:\temp\JARVIS-Runtime-Live-Activation` (`candidate/runtime-live-activation`) is **dirty on the same SHA `0b45d9e`**, modifying overlapping files (`cli/main.py`, `installer.py`, `runtime/authority.py`, `supervisor/run.py`, plus Goal-4 `LIVE_ACTIVATION.md` / `test_goal4_live_activation.py`). That session can commit/mutate the Goal-4 program. This recovery **must not** continue Goal 4 or overwrite that worktree.

Safe mutations this attempt: documentation under the canonical repo + disposable `C:\temp\jarvis-prompt1-m1\*` only.

---

## OBSERVED — Worktree disposition

72 worktrees. **None deleted.**

Classification rule used:

- HEAD is ancestor of local `main` → `CONTAINED_IN_MAIN` (keep; history/evidence)
- dirty files → add `OWNER_OR_UNTRACKED_DATA_PRESENT`
- extra commits not in `main` on candidate/experiment branches → `EXPERIMENTAL_UNACCEPTED`
- extra commits on promote/integration branches not in `main` → `UNIQUE_ACCEPTED_WORK` (unmerged; do not delete)
- otherwise → `UNKNOWN`

Counts (approximate from the 2026-08-19 scan): most trees are `CONTAINED_IN_MAIN`; **12 dirty**.

Dirty trees (**must not delete**):

| Path | Class |
|---|---|
| `C:\temp\JARVIS-Runtime-Live-Activation` | OWNER_OR_UNTRACKED_DATA_PRESENT + CONTAINED_IN_MAIN (Goal-4 dirty; collision) |
| `C:\temp\JARVIS-Canonical-Convergence-Verifier-20260819` | OWNER_OR_UNTRACKED_DATA_PRESENT + CONTAINED_IN_MAIN |
| `C:\temp\JARVIS-Foundry-Canonical-Convergence` | OWNER_OR_UNTRACKED_DATA_PRESENT + CONTAINED_IN_MAIN |
| `C:\temp\JARVIS-Foundry-Canonical-Convergence-R2` | OWNER_OR_UNTRACKED_DATA_PRESENT + CONTAINED_IN_MAIN |
| `C:\temp\JARVIS-Foundry-R8-PROMOTE` | OWNER_OR_UNTRACKED_DATA_PRESENT + CONTAINED_IN_MAIN |
| `C:\temp\JARVIS-R10-ReplayRepair-20260819` | OWNER_OR_UNTRACKED_DATA_PRESENT + CONTAINED_IN_MAIN |
| `C:\Users\b\AppData\Local\Temp\JARVIS-Canonical-V8-Integration` | OWNER_OR_UNTRACKED_DATA_PRESENT + CONTAINED_IN_MAIN |
| `C:\Users\b\Documents\JARVIS-External-Candidate` | OWNER_OR_UNTRACKED_DATA_PRESENT + EXPERIMENTAL_UNACCEPTED |
| `C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry-Remaster` | OWNER_OR_UNTRACKED_DATA_PRESENT + UNKNOWN |
| plus remaining dirty repair/canary trees from the scan | do not delete |

Experimental / unique-unmerged examples (**do not delete**):

- `C:\temp\JARVIS-CommandCenter-UI` EXPERIMENTAL
- `C:\temp\JARVIS-Learned-Procedure-Phase5` (HEAD contained; Phase 5 frozen)
- `C:\Users\b\Documents\JARVIS-Foundry-HybridWorkers` UNIQUE_ACCEPTED_WORK (5 commits not in main)
- `C:\tmp\v5-merge-test` UNKNOWN detached
- `.h/` Hermes development checkout (not a git worktree of this repo; not a JARVIS runtime)

### Answers required by M0

1. **Canonical source SHA:** local `main` `0b45d9ee799d1c5de38f8c307fc0455783aca80d` (this checkout). `origin/main` is behind at `b8f7642`.
2. **Live source:** same checkout `0b45d9e` via `.venv` `-m jarvis_foundry.supervisor.run`. Monitor naming still says `c60db92`.
3. **Live DB:** `C:\Users\b\AppData\Local\hermes\foundry\foundry.db`
4. **Live schema:** **v33**
5. **Unique accepted work outside main:** HybridWorkers integration worktree and some promote/integration branches with extra commits; treat as **keep**. Goal 1–3 **are already on local main**.
6. **Experimental only:** Command Center UI candidate, audit-remediation candidate, Graphiti/OpenHands external candidate, Phase 5 learned-procedure trees, Goal-4 live-activation dirty tree, etc.
7. **Must not be deleted:** live Foundry home + DB; all 72 worktrees; dirty trees; `.h/`; owner Documents trees; this canonical checkout.
8. **local main equal origin/main?** **NO**
9. **Production serving from developer checkout?** **YES**
10. **Autonomy blockers:** spawn OFF, worker task disabled, live schema v33 vs contracts v34, PASS clears next_action, no commit after PASS, writer collision.

M0 VERDICT: **PASS** (answers unambiguous).

---

## OBSERVED — M1 isolated experiment (deployment-free)

Home: `C:\temp\jarvis-prompt1-m1\foundry-home-2` (schema **v34**).
Repo: `C:\temp\jarvis-prompt1-m1\mission-repo-2`.
Evidence JSON: `docs/evidence/PROMPT1_RECOVERY/m1_isolated_experiment.json`.

Commands:

```text
python C:\temp\jarvis-prompt1-m1\run_m1_experiment.py
```

(using `.venv\Scripts\python.exe`; `build_worker_stack` replaced only inside that process with a Hermes-only **scripted mutating** adapter so owner Hermes profile and live providers were not used)

Results:

| Probe | Outcome |
|---|---|
| `run_cycle` enabled=false | DISABLED |
| Cycle A (contract + next_action) | **PASSED**; `step_a.md` written; `verification.passed`; `evidence.created` |
| Cycle immediately after PASS | **WAITING**: `no next action scheduled; the mission's action list is exhausted` |
| Cycle B after manual BLOCKED→ACTION_SELECTION + contract v2 | **BLOCKED**: evidence mismatch `claimed=['step_b.md']` vs observed `['step_a.md','step_b.md']` (uncommitted prior step); repair budget exhausted |
| Isolated Supervisor start+heartbeat | RUNNING then stop; live DB schema remained v33 |
| Live Task Scheduler | untouched |

CASE: **A** — keep `runtime.worker_loop → MissionLoop → HybridWorkerRoleAdapter`. Narrow wiring defects only. Do not invent OperatingRuntime.

Exact minimum M2 (NOT executed this run, collision):

1. After PASS, write the next durable `next_action` from remaining acceptance/plan without owner chat.
2. Bind `worker_loop` to one executable contract per action (step_id + supersession **or** select-by-next-action without two live step_ids).
3. Commit or isolate evidence baseline after PASS so the next step's delta is only that step.
4. Keep Supervisor deterministic; optional `--spawn-worker-loop` remains fail-closed and **must not** be turned on for the live task in Prompt 1.
5. Do not migrate live v33 in Prompt 1.

Code **not** changed: all `src/`, installer, Task Scheduler, live DB, Hybrid Memory, Eyes, Discovery, OpenAdapt, Command Center, V6 self-evolution.

M1 VERDICT: **PASS** (graph + experiment + A/B/C evidence).
M2 VERDICT: **NOT_TESTED** (collision + finite-scope gate: live import path).

---

## OBSERVED — M2 CASE A kernel (disposable worktree; live src untouched)

Recorded: 2026-08-19T15:50+00:00.

**Collision handling used:** did not edit live checkout `src/`. Implemented in registered worktree:

- path: `C:\temp\jarvis-prompt1-m2`
- branch: `repair/prompt1-m2-case-a`
- base SHA: `0b45d9ee799d1c5de38f8c307fc0455783aca80d`
- PYTHONPATH: `C:\temp\jarvis-prompt1-m2\src` with the existing Foundry `.venv`

Live Supervisor still imports the canonical checkout. Goal-4 dirty tree `C:\temp\JARVIS-Runtime-Live-Activation` was not touched.

### Wiring (CASE A, no second engine)

1. `next_executable_contract` — one remaining un-passed step contract is the dispatch owner. Multiple step_ids are the plan, not an error.
2. `MissionLoop._continue_after_pass` — after PASS, persist `next_action` from the next contract; COMPLETE + `mission.completed` when none remain. Legacy missions without contracts still WAIT.
3. Evidence isolation — every worker role is reconciled against the **content hash delta** since capture. Accepted Step-A files do not poison Step-B; repair overwrites still count.
4. Optional local commit after PASS when `git_local_commit` is READY (does not block if not).
5. Hermes pin in `worker_loop` (OpenHands marked NOT_READY, not deleted).
6. Supervisor heartbeat unchanged.

### Proving

| Item | Result |
|---|---|
| 15-point isolated mission | PASS (`tests/foundry/test_prompt1_m2_kernel.py`) |
| Action B unblocked | YES — next_action persisted automatically; evidence isolation held |
| Repair | YES — verifier FAIL with `repair_requirements`, automatic repair, fresh verifier PASS |
| Fresh process reconstruct | YES — subprocess reopened the disposable DB and read COMPLETE |
| Full suite | `pytest tests/foundry tests/convergence` collected **1404**, EXIT=0 |
| Independent verifier | PASS — `docs/evidence/PROMPT1_RECOVERY/M2_INDEPENDENT_VERIFIER.json` |
| Live src | unchanged |
| Live DB | still v33 |
| STOP | still OFF |

### Patch location

`docs/evidence/PROMPT1_RECOVERY/CASE_A_KERNEL.patch` plus `docs/evidence/PROMPT1_RECOVERY/test_prompt1_m2_kernel.py`.

Commands:

```text
git worktree add -b repair/prompt1-m2-case-a C:\temp\jarvis-prompt1-m2 0b45d9e
$env:PYTHONPATH = "C:\temp\jarvis-prompt1-m2\src;C:\temp\jarvis-prompt1-m2\tests\foundry"
pytest tests/foundry/test_prompt1_m2_kernel.py
pytest tests/foundry tests/convergence
```

M2 VERDICT: **PASS** (isolated). Canonical live `main` SHA unchanged. Do not begin M3.

---

## OBSERVED — Prompt 2 live runtime (2026-08-19T18:30Z)

Historical Prompt-1 collision (live import of developer checkout) was serialized: STOP + disable `JARVIS-Foundry-Supervisor` + `schtasks /End`. Production remained offline until M3/M4 activation.

| Item | Newly observed |
|---|---|
| local main / origin/main / HEAD | **equal** `adada7f31ac515cfac9782ace95f744b631f76f5` (normal push of Goals 1–3 + M2 CASE-A + M3 installer) |
| M2 promotion | `8576409` cherry-picked from verified worktree; `fc` vs `CASE_A_KERNEL.patch` identical |
| CURRENT release | `%LOCALAPPDATA%\JARVIS\releases\adada7f…` |
| PREVIOUS release | none |
| Task target | `%LOCALAPPDATA%\JARVIS\bin\jarvis-supervisor.cmd` (not developer checkout) |
| Live DB | still `C:\Users\b\AppData\Local\hermes\foundry\foundry.db` |
| Live schema | **v34** (v33 backup at `%LOCALAPPDATA%\JARVIS\backups\pre-m3-20260819T200510\foundry.db`) |
| Supervisor | release python `-m jarvis_foundry.deployment.launcher`; instance `supervisor-28195bfb` after F2 restart |
| Live mission | `mission-prompt2-live` COMPLETE in `C:\temp\jarvis-prompt2-live-mission` |
| Worker-Loop task | still **Disabled** |
| Goal-4 tree | still dirty, not merged |

M3 PASS. M4 PASS. M5 FAIL on rollback only (no PREVIOUS immutable release; v33 backup fail-closes in this executable). FULL suite 1411 EXIT=0.

Prompt 2 overall **PASS**. Prompt 3 ready. Do not begin M6.

---

## OBSERVED — Prompt 2 M5 repair (2026-08-19T18:55Z)

Historical (18:30Z): PREVIOUS missing; worker crash NOT_TESTED; M5 FAIL.

Newly observed:

| Item | Value |
|---|---|
| local main / origin/main | **equal** `b59836772d948bd7b2507949c42f3c7dfa75afaf` |
| CURRENT | `b598367…` immutable release (v34) |
| PREVIOUS | `adada7f…` exact prior immutable v34 release (not a checkout, not v33) |
| Rollback | PASS: launcher started PREVIOUS python; schema stayed 34; `mission-prompt2-live` COMPLETE; `mission.completed` count remained 1 |
| Return to CURRENT | PASS: launcher returned to `b598367` python |
| Worker crash | PASS: PARTIAL file then owned pid 5328 killed; repair wrote STEP-A; fresh verifier PASS |
| Windows restart | BLOCKED_OWNER_ENVIRONMENT |
| FULL suite | 1414 collected, EXIT=0 |

M5 PASS. L5 PASS. Prompt 3 ready. Do not begin M6.
