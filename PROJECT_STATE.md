# JARVIS Foundry Project State

This file is the current project truth.

```yaml
project: JARVIS Foundry
owner: Blendi Mehmeti
state_version: 12
updated_at: 2026-08-12

phase: V1_AUTONOMY_KERNEL
phase_status: WP8_IN_PROGRESS
implementation_started: true
start_command_received: true
start_command_received_at: 2026-08-11T17:33:05+02:00
active_version: V1
active_work_package: WP8

trust_state: LAB
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
  - local_event_store: READY_IN_STAGING
  - independent_supervisor: ACTIVE
  - sentinel_stop: ACTIVE
  - registered_file_read: READY_IN_STAGING
  - worktree_write: PROBING
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
  WP8: IN_PROGRESS (entry preconditions; clock not yet started)

autonomous_local_actions_after_start: true
autonomous_obsidian_actions_after_capability_ready: true
autonomous_online_research_after_start: true
autonomous_external_actions_after_capability_ready: true
autonomous_git_and_deployment_after_capability_ready: true
autonomous_financial_actions_after_capability_ready: true
autonomous_self_evolution_after_capability_ready: true

next_event: WP8_CLOCK_START (after all entry preconditions are recorded)
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

## Current Reality

WP8 (seven-day acceptance) is the active package: entry preconditions are
being recorded (see `docs/evidence/WP8_ENTRY_PRECONDITIONS.md`); the seven-
day clock has NOT started. Proving resource scored and selected (wfm-
optimizer — `WP8_RESOURCE_SCORING.md`), mission contract drafted
(`WP8_MISSION_CONTRACT.md`), gate execution plan written
(`WP8_GATE_EXECUTION_PLAN.md`). Clock start appends the START BUILD record
and the resource/mission registrations to the live ledger atomically, then
the G1–G9 / F1–F15 battery and ≥10 independently verified action cycles run
over seven PC-available days; the final acceptance verdict is an owner-gated
hold point (PASS changes trust to TRUSTED_AUTONOMOUS, activates continuous
operation, and queues V2 automatically).

## What Happens After START BUILD

The program advances through WP0-WP8 and later V2-V7 without asking for
phase-by-phase permission. It discovers resources, verifies capabilities,
builds, tests, repairs, promotes, expands, and self-improves automatically.

The owner receives reports and may veto or STOP at any time.
