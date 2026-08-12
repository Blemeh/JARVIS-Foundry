# JARVIS Foundry Project State

This file is the current project truth.

```yaml
project: JARVIS Foundry
owner: Blendi Mehmeti
state_version: 16
updated_at: 2026-08-12

phase: V1_AUTONOMY_KERNEL
phase_status: WP8_ACCEPTED
day1_closed: 2026-08-12 (12 verified cycles; F1-F15 all PASS; independent
  reviews: round-1 FAIL -> round-2 FAIL -> round-3 PASS (deleg_8ee78c80);
  seven-day duration WAIVED by the owner (DIR-2026-08-12-seven-day-waiver,
  authenticated message 16242; ledger 1763+2143); gate report
  docs/evidence/WP8_GATE_REPORT.md)
implementation_started: true
start_command_received: true
start_command_received_at: 2026-08-11T17:33:05+02:00
active_version: V1 (accepted)
active_work_package: WP9_V2_KNOWLEDGE_BRAIN (auto-queued per
  DIR-2026-08-12-gate-progression; WP8 archived as
  docs/planning/WP8_WORK_PACKAGE.closed.yaml)

trust_state: TRUSTED_AUTONOMOUS
permission_model_after_start: STANDING_AUTONOMY
owner_control_model: VETO_AND_STOP
per_task_approval: false
per_work_package_approval: false
per_version_approval: false
per_resource_approval: false

runtime_exists: true
runtime_scope: PROJECT_LOCAL_STAGING_ONLY
background_runtime_active: true
supervisor_installed: true
database_created: true
startup_task_created: true
emergency_stop_installed: true

capability_registry:
  - local_event_store: READY
  - independent_supervisor: ACTIVE
  - sentinel_stop: ACTIVE
  - registered_file_read: READY
  - worktree_write: READY
  - obsidian_write_back: READY
resource_registry:
  - id: hermes-development
    path: .h
    role: HERMES_DEVELOPMENT_CHECKOUT
    acceptance_role: NOT_V1_PROVING_RESOURCE
    baseline: 244d296646909aca1dd16c9759491da0ef4cd163
    state: READY_IN_STAGING
v1_proving_resource: SELECTED (wfm-optimizer; scoring in
  docs/evidence/WP8_RESOURCE_SCORING.md; ledger registration at clock start)
v1_proving_mission: DRAFTED (mission-wfm-optimizer; contract in
  docs/evidence/WP8_MISSION_CONTRACT.md; ledger registration at clock start)
active_owner_vetoes: []
active_owner_constraints: []

work_package_verdicts:
  WP0: PASS
  WP1: PASS
  WP2: PASS
  WP3: PASS
  WP4: PASS
  WP5: PASS
  WP6: PASS
  WP7: PASS (activated; recovery repairs 2026-08-12 verified — see
        docs/evidence/WP7_RECOVERY_2026-08-12.md)
  WP8: IN_PROGRESS (day 1 of the seven-day clock CLOSED; 12 verified cycles:
        steps 1,2,3/G6, F1, 5, 6/F14/F15, 7, 8/F3, installer fix, F2-live,
        F2 gateway, step-9 F2-active)

autonomous_local_actions_after_start: true
autonomous_obsidian_actions_after_capability_ready: true
autonomous_online_research_after_start: true
autonomous_external_actions_after_capability_ready: true
autonomous_git_and_deployment_after_capability_ready: true
autonomous_financial_actions_after_capability_ready: true
autonomous_self_evolution_after_capability_ready: true

next_event: WP8 day 2 — F5/F6/F7 on schedule (network/provider/auth fault
  injection); F4 remains owner-resumable (physical reboot; scripted
  verification ready). Day-1 closed: docs/evidence/WP8_DAY1_REPORT.md.
```

## Completed

- WP0–WP6 PASS: as recorded in v11 (see git history for the full records;
  evidence in `docs/evidence/WP*_GATE_REPORT.md`, frozen snapshots under
  `var/evidence/wp*-gate/`).
- WP7 PASS + ACTIVATED (2026-08-11): V1 Supervisor task installed and
  activated. **2026-08-12 recovery incidents and repairs (all verified):**
  1. The live daemon died with 0xC000013A (console control event) leaving
     a stale RUNNING projection + expired lease → root cause fixed with a
     Windows console control handler (every control event performs a fast
     durable stop); startup recovery proven on a live-DB copy and then on
     the live DB itself.
  2. A fresh full-suite reproduction exposed one real test defect: the
     installer real-name tests' unconditional teardown deleted the LIVE
     task → tests are now host-state-agnostic and never remove
     pre-existing tasks; regression test added.
  3. Task Scheduler RestartOnFailure empirically does not fire on this
     host → bounded auto-restart delivered by a repeating time trigger
     (PT1M/30-day) + IgnoreNew, verified live (hard-kill → restart).
  4. Post-verifier hardening: `console.py` non-Windows import guard and
     adapter provider canonicalization (opencode_go/vertex_ai →
     opencode-go/vertex) so real role dispatches resolve on the CLI.
  - Independent adversarial verifier deleg_56e7719d: C1–C6 **PASS**
    (details in `docs/evidence/WP7_RECOVERY_2026-08-12.md` §6).
  - The real task was restored (00:55 local), daemon heartbeating every
    30 s, one singleton lease, reconcile consistent, live STOP freeze →
    resume proven.
- WP8 day 1 (2026-08-12): clock started (CLOCK_START.json + ledger);
  steps 1–8 + F1 + F2-live + F2 + step-9 closed (**12 verified cycles**).
  Step-8: plan-F3 fault injection (builder killed after mutation, before
  result) — reproduced the recovery gap (stuck EXECUTING mission, reaper
  never wired), repaired with `recover_stale_mission_state` (incident-scoped
  atomic mission resume) + `append_events` + supervisor wiring; independent
  verifier round-1 FAIL (F3-1..F3-4, all legitimate) → repaired → round-2
  PASS (0 findings). Also fixed FIND-2026-08-12-02: installer `<LogonTrigger>`
  now carries `<UserId>` so schtasks accepts the XML from automated
  (cron) contexts (was "Access is denied"); 9 baseline installer failures →
  17/17 green. Foundry suite 226 passed; mission suite 40; estop 24. Live
  daemon restarted onto the new code (supervisor-54a43fb2), reconcile
  consistent. Evidence: `docs/evidence/WP8_F3_FAULT_INJECTION.md` +
  `var/evidence/wp8-gate/step-8/`.
- WP8 day 1, late (2026-08-12, two parallel cron slices): F2 closed from
  three angles — sibling slice: F2-live (real oneshot kill, step-10,
  FIND-2026-08-12-03, adapter python_exe docstring, commit 54d9520) and F2
  idle/independence probe; this slice: **step-9 F2-active** — staged Hermes
  gateway terminated MID-SESSION by external taskkill on the real PID,
  honest FAILED `hermes_oneshot_failed`, role lease released, fresh-worker
  resume, live gateway untouched (tasklist diff), 3 regression tests
  (`tests/foundry/test_role_gateway_kill.py`), Foundry suite **229 passed**,
  verifier deleg_40968b66 PASS 0 findings, commit d063511, ledger rows
  1083–1090, Obsidian 02/04/07 governed writes. Concurrency incident
  documented (non-colliding IDs; sibling artifact preserved). Day-1 report:
  `docs/evidence/WP8_DAY1_REPORT.md` (12 cycles).

## Current Reality

WP8 (seven-day acceptance) is **ACCEPTED** (2026-08-12; the seven-day
duration was explicitly WAIVED by the owner — DIR-2026-08-12-seven-day-waiver,
authenticated direct message, ledger 1763 + 2143). 12 verified cycles
closed; F1–F15 all PASS; the F5–F11 fault-injection battery was executed
in the 2026-08-12 continuation run (FIND-2026-08-12-04/-05 repaired in
`adapter.py`); §9 reconciliation honest (10 PASS + 1 documented
NOT_TESTED); three independent gate reviews: round-1 FAIL
(deleg_acbc8ce5) → repairs → round-2 FAIL (deleg_1e6e9421) → repairs →
**round-3 PASS (deleg_8ee78c80)**. Verdict recorded in the ledger
(`verification.passed:verdict-wp8-gate`, seq 2910). Trust promoted to
TRUSTED_AUTONOMOUS; WORKTREE_READY; **V2_KNOWLEDGE_BRAIN (WP9) queued and
starting** (docs/planning/CURRENT_WORK_PACKAGE.yaml).

## What Happens After START BUILD

The program advances through WP0-WP8 and later V2-V7 without asking for
phase-by-phase permission. It discovers resources, verifies capabilities,
builds, tests, repairs, promotes, expands, and self-improves automatically.

The owner receives reports and may veto or STOP at any time.
