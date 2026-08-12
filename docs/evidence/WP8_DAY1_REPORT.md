# JARVIS Foundry — WP8 Day-1 Gate Report (2026-08-12)

Status: **DAY 1 COMPLETE — 12 verified cycles closed; gate continues on schedule**
Gate: `docs/architecture/04_V1_ACCEPTANCE_GATE.md` (seven-day window; clock
started 2026-08-12; day 1 of 7)
Execution: one 24h duty window (recovery + clock start + cycles), a
scheduled cron continuation run (05:05–06:45 local), this session's
reconciliation + F2 execution, and a second parallel cron session that
closed its F2-live/step-10 cycle and this report's first edition (see
§5 concurrency note; the two sessions used non-colliding IDs throughout).
Owner input: NONE (authenticated START BUILD mandate).

## 1. Verified Cycles Closed (12)

| # | Cycle | Commit(s) | Verifier(s) | Outcome |
|---|---|---|---|---|
| 1 | step-1 foundation (roster/rules/CLI/fixtures) | mission `893eec8` | deleg_0bda1ed9 + deleg_6ad0fed6 (repair re-verify) | PASS (exit-3 + traceback findings repaired) |
| 2 | step-2 scheduler core (AC1/AC2/AC3/AC4) | mission `508ca5f` | deleg_ace03e13 | PASS (49/49 adversarial; AC3 73.9 ms) |
| 3 | step-3 G6 failure injection | mission `5b01665` | deleg_20994c80 (FAIL) + deleg_041a903e (PASS) | PASS (inject→FAIL→repair→PASS) |
| 4 | F1 live supervisor kill | ledger event | probe + events | PASS (auto-restart 35 s, lease reclaim, one owner) |
| 5 | step-5 daemon loop resilience (FIND-01) | Foundry `751ef83` | deleg_23b93f66 | PASS (OBS-1/OBS-3 applied; 215 tests) |
| 6 | step-6 F14/F15 CLI hardening | mission `a5d3f4c` | deleg_59821c9a | PASS (leak vector flagged → step-7) |
| 7 | step-7 leak-vector sanitization | mission `5eaa6ee` + `290265d` | deleg_b6f54995 (FAIL) + deleg_698003c6 (PASS) | PASS (second FAIL→repair→PASS lineage) |
| 8 | step-8 = plan F3 builder-kill recovery | Foundry `9ff56e8` | deleg_2b5a84e5 round-1 FAIL (4 findings) → round-2 PASS | PASS (reaper wiring, atomic resume, incident-scoped keys) |
| 9 | FIND-2026-08-12-02 installer LogonTrigger UserId | Foundry `9ff56e8` | (in step-8 cycle) | PASS (226-test suite) |
| 10 | step-10 F2-live adapter kill probe | Foundry `54d9520` (adapter docstring) | deleg_f49b07cb | PASS (5/5; real kill, FAILED, no orphans; FIND-03) |
| 11 | F2 gateway termination (idle/independence) | ledger event | live probe | PASS (staged gateway killed; dispatch independent; live gateway untouched) |
| 12 | step-9 F2-active (this session): staged gateway killed MID-SESSION + resume | Foundry `d063511` (3 regression tests + F2 doc) | deleg_40968b66 | PASS (0 findings; FAILED hermes_oneshot_failed, lease released, resume; suite 229) |

Mission repo: 40 tests green (`290265d`). Foundry repo: 229 tests green
(`var/evidence/wp8-gate/step-9/foundry-suite.txt`, 229 passed in 201.38 s),
plus earlier 226-test run (`var/evidence/wp8-gate/step-8/foundry-suite-226.txt`).

## 2. Fault-Injection Matrix Status (F1–F15)

| F | Item | Status |
|---|---|---|
| F1 | Supervisor kill (idle+active) | **PASS** (day 1; auto-restart ≤60 s, lease reclaimed once, no duplicates) |
| F2 | Gateway termination | **PASS** (day 1, early; three complementary records: staged gateway killed while idle → dispatch independent [sibling, test_id F2]; real oneshot killed mid-run → FAILED, no orphans [sibling, F2-live]; staged gateway killed MID-SESSION → FAILED hermes_oneshot_failed, lease released, fresh-worker resume, 3 regression tests [this session, F2-active, step-9]) |
| F3 | Builder kill after mutation | **PASS** (day 1, early; machinery + 9 tests) |
| F4 | Windows restart | **PASS** (2026-08-12 ~09:49 owner reboot; supervisor auto-started from the task trigger in 1s, single lease, heartbeats resumed, ledger/WAL intact, reconcile consistent — evidence var/evidence/wp8-gate/f4-restart/F4_AFTER_STATE.json + fault_injection.completed:F4) |
| F5–F13 | Network outage, provider unavail., auth, disk, corrupted event, duplicate delivery, Obsidian conflict, two agents, prompt injection | F5 **PASS** (network outage: real CLI exit-0 failure report classified — FIND-2026-08-12-04; honest BLOCKED, bounded retry, local work continued, resume once) · F6 **PASS** (model outage: HTTP 401 model-not-supported classified — FIND-2026-08-12-05; fallback used+recorded OR honest waiting; no invented model) · F7 **PASS** (expired auth: real 401 denied, no secrets in logs, reauth resumes) · F8 **PASS** (disk full: bounded quota simulation — builds stop, state commits, no cleanup, condition reported) · F9 **PASS** (corrupted event: interrupted txn atomic, hash tamper/forge rejected with rollback, deterministic rebuild) · F10 **PASS** (duplicate delivery: dedup → one transition/commit/wake; changed payload → EventConflict) · F11 **PASS** (concurrent Obsidian edit: conflict, both versions preserved) · F12 **PASS** (day 2, early: second daemon → LeaseUnavailable, exit 1, zero events, lease untouched) · F13 **PASS** (day 3, early: hostile doc inert, illegal transitions rejected, fabricated events recorded-but-inert + 3 regressions) |
| F14 | Path escape | **PASS** (mission step-6) |
| F15 | False success report | **PASS** (mission step-6; G6/step-7 lineages also exercise planted-claim rejection) |

## 3. Findings Recorded This Gate (all resolved or scoped)

- FIND-2026-08-12-01: 21-min heartbeat-event stall (unexplained transient) →
  repaired (resilient loop, step-5) + observability.
- FIND-2026-08-12-02: installer LogonTrigger UserId (automated installs) →
  repaired (step-8).
- FIND-2026-08-12-03: adapter python_exe default resolution-fragility →
  documented (deployment contract; docstring; renumbered from a collision
  with -02).
- Residual: F4 physical reboot (owner action); same-provider verifier
  limitation (recorded in every verdict); 21-min wedge root cause remains
  unexplained but observable+bounded.

## 4. Runtime State (verified fresh this session)

- Daemon: `supervisor-54a43fb2` RUNNING, 30 s heartbeats, exactly one
  singleton lease, quick_check ok, reconcile consistent (ledger 1090 events
  after the step-9 append; rows 1083–1090 = step-9 chain).
- Task: `JARVIS-Foundry-Supervisor` enabled, repeating trigger PT1M/P30D +
  IgnoreNew + LogonTrigger(UserId fix), one instance.
- Repos: Foundry HEAD `d063511` (this session's step-9 commit; clean);
  mission HEAD `290265d` (clean).
- Obsidian V1 root: 9 governed notes (00–07 + Audit); 02/04/07 maintained
  after every close (02 refreshed to step-9/day-1 state this session).
- Isolated dispatch home `var/wp8-hermes-home` (scoped credentials, secrets
  never printed); dispatch path proven end-to-end (probes + F2-live + F2 +
  F2-active).

## 5. Day-1 Verification Discipline Notes

- Every cycle: builder (executive or sibling session) → fresh independent
  verifier → commit only after PASS. Two FAIL→repair→PASS lineages (G6,
  step-7) + one FAIL→repair→PASS in step-8 — all genuine, unscripted.
- Same-provider limitation recorded per verdict (opencode-go family).
- **Concurrency incident (2026-08-12, documented honestly):** two cron
  sessions ran the F2 scenario in parallel (~08:25–08:50 local). Collision
  management was non-colliding IDs (step-9 vs step-10; test_id F2-active vs
  F2/F2-live; FIND-03 supersede for an ID collision), preserved artifacts
  (sibling's f3-live-probe.json left in the step-9 dir, excluded from its
  manifest), one final day-1 report (this file, merged), and full git
  reconciliation (HEAD 147ea5c → d063511; no conflicting commits). No
  duplicate ledger event was created (idempotency keys); the ledger is
  consistent at 1090 events. Lesson: concurrent slices must check for
  sibling activity before choosing step/verdict/finding IDs (recommended
  for RECOVERY_NOTES).
- No push; local commits only; no paid/public/external actions; no AI
  generation in the mission repo (human-written code, per contract).
- Ledger is machine truth; docs reconciled after every transition
  (PROJECT_STATE v14→v15, CURRENT_WORK_PACKAGE, RECOVERY_NOTES,
  LOW_COST_CONTINUATION, marker).

## 6. Next (day 2+)

- F-tests on schedule (F5/F6/F7 day 4; F8/F9/F10/F11 day 5; F12/F13 day
  2–3), via scheduled continuation runs.
- F4: executed the moment a human reboots (scripted verification).
- Gate reconciliation + freeze + independent final review at day 7.

## 7. Evidence Index (day 1)

- `var/evidence/wp8-gate/`: CLOCK_START.json, step-1..7 bundles, step-8
  (F3 doc + suite), step-9 (F2-active: experiment summary, staged gateway,
  regression tests, tasklist diffs, manifest), step-10 (F2-live probe),
  f4-restart/ (before-state + task export), step-2-design.md,
  FREEZE_DAY1_MANIFEST.json (50 files, sha f82b577b; frozen before the
  step-9 bundle, which carries its own manifest).
- `docs/evidence/`: WP7_RECOVERY_2026-08-12.md, WP8_ENTRY_PRECONDITIONS.md,
  WP8_G6_FAILURE_INJECTION.md, WP8_F3_FAULT_INJECTION.md,
  WP8_F2_FAULT_INJECTION.md (step-9, F2-active), this report.
- `docs/handoffs/`: CHECKPOINT_2026-08-12T0645.md (05:05–06:45 run),
  CHECKPOINT_2026-08-12T0850.md (day-1 close), CHECKPOINT_2026-08-12T0905.md
  (step-9 close, this session), CONTINUATION_2026-08-12T0350.md,
  F4_RESTART_HANDOFF_2026-08-12.md.
- Verifier transcripts: `C:\Users\b\AppData\Local\hermes\cache\delegation\live\deleg_*`.
