# WP8 F3 Fault Injection — Builder Kill After Mutation, Before Result

Status: **PASS** (reproduced; independent verifier round-2 PASS, 0 findings)
Date: 2026-08-12 (day 1 of the seven-day gate)
Plan mapping: `docs/evidence/WP8_GATE_EXECUTION_PLAN.md` §3 — F3 | Builder kill
after mutation, before result | partial diff captured; useful work preserved or
rolled back deliberately; lease reclaimed once; fresh worker.

## 1. Scenario (plan F3)

A builder session is hard-killed AFTER mutating the mission worktree and
BEFORE returning any result. Durable aftermath in the live ledger:

- mission advanced `READY -> EXECUTING` (run-scoped state_changed events);
- `agent_run.started` row RUNNING for the killed run;
- role lease `role:builder:<mission>:<step>` held (expires after
  `duration_seconds` without renewal);
- partial work left uncommitted in the worktree.

A fresh instance must be able to re-claim the step: no duplicate action, no
duplicate commit, partial work preserved, lease reclaimed exactly once.

## 2. Defect Reproduced (inject -> FAIL)

Before this step, the machinery had NO recovery path for this scenario:

1. `propose_next_action()` refuses non-dispatchable mission states
   (`DISPATCHABLE_STATES = {ACTION_SELECTION, REPAIRING, READY}`); a mission
   stuck in `EXECUTING` (or `VERIFYING`) raises `ProposalError` forever.
2. The state machine has no `EXECUTING -> READY/ACTION_SELECTION` transition
   (only VERIFYING/REPAIRING/BLOCKED/CANCELLED) — nothing could un-stick it.
3. `reap_stale_role_runs()` existed but was NEVER wired into the supervisor:
   stale RUNNING runs and expired role leases were never reaped in production
   (only in unit tests).

Reproduction (baseline suite, this run): the pre-fix regression test
`test_f3_builder_killed_after_mutation_step_reclaimed_without_duplicates`
failed at the recovery step (`ProposalError: mission mission-1 is not
dispatchable in state EXECUTING`) — the mission was permanently stuck.

## 3. Repair (smallest correct increment)

1. **`src/jarvis_foundry/roles/reaper.py`** — new deterministic recovery
   `recover_stale_mission_state()`: for missions in EXECUTING/VERIFYING with
   NO RUNNING run holding a live role lease, append atomically
   `mission.state_changed` `BLOCKED` (reason
   `session_lost:heartbeat_timeout:step_reclaimable`) then
   `ACTION_SELECTION` (preserving `next_action`), so a fresh loop instance
   re-claims the step. Safety: missions with a genuinely live session are
   untouched; deliberate blocks are never auto-resumed; idempotency keys are
   INCIDENT-scoped (mission `last_event_id`), so a SECOND kill of the same
   mission recovers again.
2. **`src/jarvis_foundry/database.py`** — `append_events()`: atomic
   multi-event append in ONE transaction (per-event dedupe/conflict semantics
   identical to `append()`; `append()` is now a thin wrapper). The two
   recovery transitions are all-or-nothing — the intermediate BLOCKED state
   is never observable, so no partial-state convergence logic is needed.
3. **`src/jarvis_foundry/supervisor/service.py`** — `_reap_and_recover()`
   wired into `Supervisor.start()` and `Supervisor.heartbeat()`: stale role
   runs are reaped (lease reclaimed once) and stuck missions recovered inside
   the daemon. A maintenance failure is recorded as ONE throttled
   `supervisor.degraded` (reason `role_reaper_failure`) and never takes the
   heartbeat down.

## 4. Evidence (fresh, reproduced)

- Full Foundry suite: `pytest tests/foundry -q` → **226 passed**, exit 0
  (`var/evidence/wp8-gate/step-8/foundry-suite-226.txt`).
- Focused: reaper + F3 end-to-end + installer → 33 passed
  (`var/evidence/wp8-gate/step-8/f3-focused-tests.txt`).
- End-to-end assertions (real FoundryDatabase/reaper/MissionLoop/git repo):
  exactly 2 commits (baseline + one verified commit), killed run FAILED
  (heartbeat_timeout), exactly ONE fresh COMPLETED builder run, exactly ONE
  session-loss resume event, `result.txt` partial work preserved and
  committed, `PRAGMA quick_check` ok.
- Recurring-kill regression (verifier F3-1): kill the same mission twice →
  BOTH incidents recover to ACTION_SELECTION (incident-scoped keys).
- Changed-next_action regression (verifier F3-2): second incident with a
  different requirement recovers with NO EventConflict; a raising reaper
  records exactly one `supervisor.degraded(role_reaper_failure)` and the
  heartbeat keeps returning RUNNING.
- Deliberate-block safety (verifier F3-4): a mission BLOCKED by
  `repair_budget_exhausted` stays BLOCKED; recovery returns [].
- Live-session safety: EXECUTING + RUNNING run with UNEXPIRED lease →
  untouched (state stays EXECUTING, run stays RUNNING).
- Mission suite (wfm-optimizer, untouched): 40 passed. Hermes estop
  baseline: 24 passed.

## 5. Independent Verification

- Round 1 (deleg_2b5a84e5): **FAIL** — 4 legitimate findings (F3-1..F3-4:
  cross-incident key dedupe stranded recurring kills; EventConflict crash
  path on changed next_action; unscoped BLOCKED-branch query shadowing;
  no provenance for the current block). Transcript:
  `C:\Users\b\AppData\Local\hermes\cache\delegation\live\deleg_2b5a84e5\task-0.log`.
- Repair implemented (incident-scoped keys + atomic append + BLOCKED-branch
  deletion + degraded guard).
- Round 2 (deleg_806f5664): **PASS — 0 findings**; all four probes
  re-executed adversarially on the fixed code; full suite 226 green
  reproduced; scope clean; live runtime healthy (read-only). Transcript:
  `C:\Users\b\AppData\Local\hermes\cache\delegation\live\deleg_806f5664\task-0.log`.
- Limitation (recorded): same-provider verification (builder and verifier
  both ran under the configured router provider; provider diversity
  attempted, fallback recorded per contract §14.3).

## 6. Installer Defect Found Along The Way (FIND-2026-08-12-02)

The baseline suite exposed 9 installer test failures (`schtasks create
failed: ERROR: Access is denied`) in THIS automated (cron) session, while the
same suite was green interactively. Bisection (diag scripts in the step-8
bundle) proved the trigger: a `<LogonTrigger>` WITHOUT `<UserId>` is rejected
by schtasks from non-interactive tokens; with an explicit `<UserId>` the same
XML registers from both contexts. Repair: `installer.py` now emits
`<UserId>{user_id}</UserId>` (`_current_user_id()`, DOMAIN\user) in the
LogonTrigger + regression test `test_task_xml_logon_trigger_carries_user_id`
(real schtasks round-trip). 17/17 installer tests pass in this context. The
live task (installed interactively) is unaffected; autonomous reinstalls
from scheduled sessions now work. Independent verifier round-2: PASS.

## 7. Ledger / Obsidian

- Events appended (source wp8-executive): `step.created`, `step.ready`,
  `evidence.created` (bundle wp8-step-8), `step.completed`,
  `verification.started`, `verification.passed`, `git.local_commit_created`
  (see event export in the step-8 bundle).
- V1 Obsidian projection updated after the accepted step (governed writes,
  snapshot before/after in the ledger).

## 8. Rollback

- Code: revert the four source files (reaper.py, database.py, service.py,
  installer.py) + three test files; the recovery is additive and only fires
  on provably dead sessions.
- Data: the recovery appends only standard `mission.state_changed` events;
  no schema change; the live DB needs no migration.
- Freeze before any rollback (WP2 machinery); no pushes during V1.
