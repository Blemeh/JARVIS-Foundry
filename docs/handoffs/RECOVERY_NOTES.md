# Durable Recovery Notes — 12-Hour Autonomous Continuation

Updated: 2026-08-11T21:15+02:00
Session: Hermes Desktop (visible executive), START BUILD active.

## ⚠️ COORDINATION INCIDENT (read first) — CONCURRENT BUILDER ACTIVE

The prior desktop executive's 12-hour autonomy harness
(`work/run-hermes-autonomy-12h.ps1`, session `20260811_171935_90f631`,
deadline 2026-08-12T06:49) did NOT stop after writing the 20:30 checkpoint.
It continued building in parallel with the cron continuation run:

- It extended the WP4 roles package (added `roles/reaper.py`, ledger replay,
  live-home isolation guard, command-surface fixes; suite 144→145),
  dispatched its own verifier (deleg_221bd327), closed **WP4 PASS**
  (freeze `var/evidence/wp4-gate/` snapshot `5fbf1fb8…`, estop 24 passed,
  canonical DBs unchanged), and amended the WP3 gate close with its
  verifier's real findings (B5/B6 repairs; wp3-gate re-frozen
  snapshot `425b9f92…`).
- It is **actively building WP5 right now** (`src/jarvis_foundry/mission/`
  files written 21:05–21:14; 3 mission-loop tests currently failing while it
  works; PROJECT_STATE v8: WP4 PASS, active_work_package WP5,
  next_event COMPLETE_WP5_MISSION_LOOP_GATE).
- My verifier (deleg_f2954000, dispatched by the cron run) verified the
  merged 144-test tree: C1–C7 all PASS, 0 FAIL findings, 7 INFO items.
  Both verifiers demanded freeze-then-certify; the concurrent session
  honored that (freeze at 21:03–21:04 before PASS).

**Decision:** the cron run STOOD DOWN from WP5 to avoid duplicate builders
(absolute invariant). Do NOT start WP5 in parallel. If the harness is still
active, verify and integrate its state instead of rebuilding.

## AUTHORITATIVE RESUME POINT

Read `docs/handoffs/CHECKPOINT_2026-08-11T2030.md` FIRST — it contains the
complete durable state (verdicts, in-flight WP3 verification, exact gate-close
procedure, WP4 preview, invariants, commands). This file is the milestone log.

## COORDINATION RESOLUTION (21:20, continuation run)

The 21:15 incident record misattributed this continuation session's writes
(WP3 B5/B6 repairs, WP4 fixes + freeze 5fbf1fb8, WP5 mission/ implementation,
deleg_221bd327 + deleg_20def896 dispatches) to the 12h harness. Facts: the
12h desktop harness (session 20260811_171935_90f631) last wrote at 20:30
(checkpoint) and dispatched deleg_f2954000 at 20:38; silent since. The
cron-continuation-1 session ended at 21:15:40 (marker: STOOD_DOWN). No other
session is writing (verified 21:20: no repo writes outside this run since
21:16). This session continues as the SOLE active engine per the user's
CONTINUATION MANDATE (cron job 502898c34f7b is PAUSED). Marker refreshed
(package=WP5_GATE, status=ACTIVE_SOLE_ENGINE).

## How To Resume (if this session is interrupted)

1. Re-read `docs/handoffs/LOW_COST_CONTINUATION.md` (mandatory read order),
   then this file.
2. **First** check whether the 12-hour harness is still active (see
   COORDINATION INCIDENT above): `tasklist | grep -i python`, `ls -lt
   src/jarvis_foundry/mission/` — if files are still being written, DO NOT
   start a parallel package build; integrate/verify instead.
3. State: WP0 PASS, WP1 PASS, WP2 PASS, WP3 PASS, WP4 PASS, WP5 ACTIVE /
   NOT_TESTED (in progress by the concurrent desktop session at last
   observation); WP6-WP8 INACTIVE.
4. Verify no stray python processes and no JARVIS/Foundry scheduled tasks.
5. Continue the exact next action below. Never mark PASS without reproducing
   the evidence yourself.

## Canonical Paths (NEVER mutate; use copies)

- `var/staging-foundation/foundry.db` — canonical WP0/WP1 evidence (schema 2,
  11 events). Read-only.
- `var/cli-proof/foundry.db`, `var/final-proof/foundry.db` — canonical.
- `.h` — isolated Hermes development checkout (commit
  `244d296646909aca1dd16c9759491da0ef4cd163`). Never edit live Hermes.
- `var/wp1-staging/`, `var/wp2-staging/` — staging homes; not canonical.
- Evidence bundles: `var/evidence/wp1-gate/`, `var/evidence/wp2-gate/`
  (atomic sha256 artifacts, frozen-state snapshots).

## Milestones Completed

- [x] WP0 PASS (prior session).
- [x] WP1 PASS: schema v3, atomic migrations, crash recovery, reconciliation,
      artifacts, adversarial verification, frozen snapshot (af9051fb…).
- [x] WP2 PASS: Supervisor without Hermes; sentinel/CLI/tray/hotkey STOP;
      worker control; forced-death reconstruction; 4 verifier findings
      repaired; frozen snapshot (aff5b70a…); suite 78.
- [x] WP3 implementation (ACTIVE/NOT_TESTED): envelope validation, READY/
      ACTIVE capability semantics, capability.state_changed events, distinct
      action.invalid_target, registration upsert idempotency, junction/
      traversal escape tests. Suite 94 passed. Verifier dispatched
      (deleg_d335614d). NO further code edits until its report lands.
- [x] WP3 gate close (run 2, this session): verifier summary
      (subagent-summary-0-20260811_201639_515049.txt, 20:16:39) integrated —
      PASS on all six criteria; two fail-loud findings repaired (B5:
      registration idempotency key now covers full payload, same-HEAD dirty
      re-registration updates; B6: decision key covers full payload,
      changed-reason re-delivery records instead of EventConflict); B2
      precedence nuance documented + tested. Suite 94 -> 133 passed (3 new
      regression tests + 36 in-progress WP4 role tests). Re-frozen
      var/evidence/wp3-gate/ (snapshot 425b9f92…, estop 24 passed, canonical
      DBs unchanged). WP3_GATE_REPORT.md amended with findings/repairs.
      WP3: PASS recorded.

- [x] WP4 implementation review + gaps closed (this session, run 2): roles/
      modules (manifest/router/adapter/dispatcher/results, written 20:25-20:30
      by prior session) inspected against CURRENT_WORK_PACKAGE.yaml criteria
      C1-C7. Gaps closed: (1) roles/reaper.py added — deterministic
      heartbeat-timeout enforcement (RUNNING role session with missing/expired
      lease -> agent_run.failed heartbeat_timeout + lease release, idempotent);
      (2) router.prefer_diversity() semantic bug fixed (docstring said
      "when configuration makes it possible" but same-preferred + distinct
      fallback returned False; now achievability-consistent with
      diversity_resolution); (3) regression tests added: test_role_reaper.py
 (6) + diversity-recorded test. Suite 140 passed. Independent verifier
 deleg_221bd327 dispatched (adversarial, fresh session).
 - [x] WP4 verifier-driven repairs (this session, while two independent
       verifiers ran: deleg_f2954000 from the prior session, deleg_221bd327
       from this run): (1) HermesOneshotAdapter command surface FIXED — real
       .h CLI takes the prompt as -z VALUE, has NO --role flag, and the entry
       module is hermes_cli.main (no importable 'hermes' module — verified by
       live probe); adapter now emits the exact real surface + --usage-file
       cost accounting (regression tests pin the argv). (2) LIVE_HERMES_HOME
       isolation gap closed — hermes_home inside the live profile is rejected
       at construction. (3) dispatcher ledger replay — same run_id + identical
       context replays a COMPLETED run from the ledger (no re-run); changed
       context conflicts loudly. Suite 144 passed.
- [x] **WP4 PASS** (this session): both independent verifiers delivered
      C1–C7 all PASS — deleg_f2954000 (30/30 probes; summary
      subagent-summary-0-20260811_205628_089164.txt), deleg_221bd327 (30/30
      probes, 1 MAJOR integrity condition + 3 MINOR findings; summary
      subagent-summary-0-20260811_210126_788532.txt). Three post-verdict
      MINOR repairs: completed projection clears failure_class (stale
      provider_unavailable on COMPLETED rows), from_yaml rejects
      `__`-placeholder models, live-root parent-containment guard. Final
      freeze var/evidence/wp4-gate/ (snapshot 5fbf1fb8…, suite 145 passed,
      estop 24 passed, canonical DBs unchanged) — certified only after the
      freeze, per verifier A's integrity condition. WP4_GATE_REPORT.md +
      WP4_HERMES_ADAPTER.md (PASS) written; PROJECT_STATE v8 (WP4 PASS,
      WP5 active, next_event COMPLETE_WP5_MISSION_LOOP_GATE);
      CURRENT_WORK_PACKAGE.yaml = WP5 RUNNING; WP5 spec drafted and
      activated.
- [x] Cron continuation run (20:16–21:15): closed WP3 gate from the
      deleg_d335614d transcript (94→133 suite, freeze 21e29ab6…), built the
      WP4 roles package independently (manifest/router/results/adapter/
      dispatcher, 39 tests → 133), dispatched verifier deleg_f2954000
      (C1–C7 PASS on the merged 144-test tree), verified canonical DBs
      unchanged, then detected the concurrent harness session mid-WP5 and
      STOOD DOWN (see COORDINATION INCIDENT). No WP5 work started by this
      run; no duplicate artifacts written after detection.

- [x] **WP5 implementation** (this session): src/jarvis_foundry/mission/ built —
      proposal.py (deterministic next-action proposal + honest Waiting),
      verification.py (STARTED -> PASS/FAIL/BLOCKED verdicts with
      independence metadata, per-step repair-budget accounting),
      commit.py (commit_after_pass: PASS-verdict gate -> policy gate -> one
      deterministic local commit in the registered worktree),
      loop.py (MissionLoop: propose -> READY/EXECUTING -> builder/repair
      dispatch -> VERIFYING -> verifier (provider-diverse) -> PASS: commit +
      evidence + ACTION_SELECTION / FAIL: bounded REPAIRING / BLOCKED precise;
      run_until_settled). Two defects found+fixed during dev: verification
      outcome event types (verification.pass -> verification.passed), pass-
      scoped idempotency keys for mission.state_changed (repair re-walk
      deduped against the first pass), commit moved to VERIFYING (ACCEPTING->
      BLOCKED is illegal). Tests: test_mission_loop.py (8) + test_verification_loop.py
      (6). Suite 159 passed. Independent verifier deleg_20def896 dispatched
      (adversarial, fresh session; tree FROZEN during review).

- [x] **WP5 verifier round 1 (deleg_20def896): FAIL with 3 confirmed
      defects — all repaired + regression-tested.** F1: latest_verdict_for_step
      tie-broke on random verdict_id under identical timestamps (14/24
      frozen-clock runs mis-selected FAIL over durable PASS -> commit refused)
      — now ordered by ledger sequence (events join). F2: fresh MissionLoop
      instance resuming a REPAIRING mission collided on instance-local pass
      counters (ValueError, stuck state) — advance keys now scoped by the
      pass's run_id (durable identity). F3 (D2): forged capability / empty
      rollback / out-of-root target dispatched without rejection —
      _validate_step now enforces the envelope contract BEFORE dispatch
      (zero dispatcher calls on rejection). Also: F6 repair-role requires a
      durable FAIL verdict; F7 commit binds run_id to verdict.builder_run_id;
      F4 evidence claim carries role. 8 new regression tests. Suite
      159 -> 167. Round-2 verifier deleg_1b6d0485 dispatched (fresh
      adversarial, tree frozen).
- [x] **WP5 PASS** (this session): round-2 verifier deleg_1b6d0485 — PASS on
      D1-D7, all three round-1 defects reproduced fixed (frozen-clock
      verdict ordering 25/25, fresh-instance REPAIRING resume, pre-dispatch
      rejection with zero dispatcher calls), battery green, tree stable,
      zero scope violations. Freeze var/evidence/wp5-gate/ (snapshot
      2f608f6a…, suite 167 passed, estop 24 passed, canonical DBs
      unchanged). WP5_GATE_REPORT.md + WP5_MISSION_LOOP.md (PASS) written;
      PROJECT_STATE v9 (WP5 PASS, WP6 active, next_event
      COMPLETE_WP6_OBSIDIAN_WRITE_BACK_GATE); CURRENT_WORK_PACKAGE.yaml =
      WP6 RUNNING.

- [x] **WP6 implementation** (this session): src/jarvis_foundry/obsidian/
      built — policy.py (V1 note set 00-07 + Audit/, frontmatter contract
      origin/current_steward/system/version, write-root boundary, whole-vault
      read allowed), snapshots.py (byte snapshots under .jarvis/snapshots/,
      atomic restore), writer.py (transactional protocol: expected hash ->
      before snapshot -> obsidian.write_started -> atomic replace -> parse
      check -> after hash -> obsidian.write_completed; conflict detection
      raises ObsidianConflictError preserving BOTH versions under
      Audit/conflicts/; rollback byte-exact with clobber guard via
      after_hash). Schema v4: obsidian_writes projection + handlers +
      rebuild/reset/reconcile/database allowlist. 12 new tests
      (test_obsidian_write.py 7, test_obsidian_rollback.py 5). Suite
      167 -> 179. Independent verifier deleg_d32753dd dispatched
      (adversarial, fresh session; tree FROZEN during review).

- [x] **WP6 PASS** (this session): verifier deleg_d32753dd — PASS on E1-E7
      (156-check adversarial scratch suite, nothing broken), zero
      gate-failing findings; two observations hardened before the freeze
      (SnapshotStore.restore containment; atomic conflict-preserve writes) +
      regression test. Freeze var/evidence/wp6-gate/ (snapshot 8227654a…,
      suite 180 passed, estop 24 passed, canonical DBs unchanged). Gate
      report + evidence doc (PASS) written; PROJECT_STATE v10 (WP6 PASS,
      WP7 active, next_event COMPLETE_WP7_INTEGRATION_ACTIVATION_GATE);
      CURRENT_WORK_PACKAGE.yaml = WP7 RUNNING.

- [x] **WP7 implementation** (this session): src/jarvis_foundry/installer.py
      — Windows scheduled-task installer/uninstaller (logon trigger, hidden,
      restart-on-failure, absolute python path + explicit --home arg; Task
      Scheduler XML schema has no EnvironmentVariables node, so the
      deterministic env is the absolute --home argument with HERMES_HOME
      recorded in the profile), Foundry profile (foundry-profile.yaml),
      real-name guard (JARVIS-Foundry-Supervisor refused until the wp7-gate
      GATE_VERDICT.json says PASS), scope-proof host inventory diff, durable
      installation.* events, idempotent uninstall. supervisor/run.py —
      headless daemon (--once cycle: start/reconcile/heartbeat/stop; FROZEN
      on STOP sentinel; lease release on exit). CLI install/uninstall/
      activate/deactivate + status installation block. 10 tests
      (test_installer.py) exercising the REAL staging lifecycle on this host
      (install -> disable -> enable -> daemon restart -> STOP freeze ->
      resume -> uninstall), scope proof, guards, profile. Suite
      180 -> 190. Independent verifier deleg_9c19e04a dispatched
      (adversarial, fresh session; tree FROZEN during review).

## Exact Next Actions (in order)

1. WP6 (governed V1 Obsidian write-back): V1 folder creation (00-07 + Audit/),
   note metadata frontmatter contract (origin/current_steward/system/version),
   transactional writes (expected hash -> before snapshot -> atomic replace ->
   parse check -> after hash -> audit event -> rollback pointer), concurrent-
   edit conflict detection preserving both versions, byte-exact rollback,
   obsidian.write_started/completed/conflict/rolled_back events. Gate:
   concurrent edit and rollback tests preserve both meaning and bytes. Real
   vault is READ-ONLY; tests use temp vault fixtures. Freeze via
   `freeze_verified_state.py wp6-gate`.
2. WP7 (integration + activation) — NEVER install before its gate.
3. WP8 (seven-day acceptance) — owner involvement at the gate.
4. Never run duplicate builders. Never edit live Hermes or `.h` beyond
   read-only overlays. Never modify canonical DBs. Freeze before rollback.

## Running Commands

```bash
cd "C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry"
./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short
./.venv/Scripts/python.exe -m jarvis_foundry.cli.main --home var/wp2-staging doctor
# Hermes baseline:
cd .h && HERMES_HOME="<root>\var\wp1-hermes-home" PYTHONPATH="$PWD;C:\Users\b\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages" ../.test-venv/Scripts/python.exe -m pytest tests/test_estop.py -q
```

## Honesty Rule

Nothing is PASS until reproduced with fresh commands. Verifier reports are
evidence to integrate, not claims to relay.
