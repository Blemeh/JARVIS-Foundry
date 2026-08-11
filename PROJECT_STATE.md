# JARVIS Foundry Project State

This file is the current project truth.

```yaml
project: JARVIS Foundry
owner: Blendi Mehmeti
state_version: 10
updated_at: 2026-08-11

phase: V1_AUTONOMY_KERNEL
phase_status: WP7_IN_PROGRESS
implementation_started: true
start_command_received: true
start_command_received_at: 2026-08-11T17:33:05+02:00
active_version: V1
active_work_package: WP7

trust_state: LAB
permission_model_after_start: STANDING_AUTONOMY
owner_control_model: VETO_AND_STOP
per_task_approval: false
per_work_package_approval: false
per_version_approval: false
per_resource_approval: false

runtime_exists: true
runtime_scope: PROJECT_LOCAL_STAGING_ONLY
background_runtime_active: false
supervisor_installed: false
database_created: true
startup_task_created: false
emergency_stop_installed: false

capability_registry:
  - local_event_store: READY_IN_STAGING
  - independent_supervisor: READY_IN_STAGING
  - sentinel_stop: READY_IN_STAGING
  - registered_file_read: READY_IN_STAGING
  - worktree_write: PROBING
resource_registry:
  - id: hermes-development
    path: .h
    role: HERMES_DEVELOPMENT_CHECKOUT
    acceptance_role: NOT_V1_PROVING_RESOURCE
    baseline: 244d296646909aca1dd16c9759491da0ef4cd163
    state: READY_IN_STAGING
v1_proving_resource: NOT_SELECTED
v1_proving_mission: NOT_SELECTED
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
  WP7: NOT_TESTED
  WP8: NOT_TESTED

autonomous_local_actions_after_start: true
autonomous_obsidian_actions_after_capability_ready: true
autonomous_online_research_after_start: true
autonomous_external_actions_after_capability_ready: true
autonomous_git_and_deployment_after_capability_ready: true
autonomous_financial_actions_after_capability_ready: true
autonomous_self_evolution_after_capability_ready: true

next_event: COMPLETE_WP7_INTEGRATION_ACTIVATION_GATE
```

## Completed

- WP0 PASS: host/Hermes/Obsidian/toolchain baseline observed; `.h` isolated
  development checkout established.
- WP1 PASS: durable domain and event store gate — schema v3 with
  agent_runs/evidence_bundles/verification_verdicts/budgets projections,
  all-or-nothing transactional migrations, subprocess crash recovery,
  deterministic reconciliation diagnostics, atomic artifact hashing,
  combined baseline, complete evidence bundle, independent adversarial
  verification (see `docs/evidence/WP1_GATE_REPORT.md`).
- WP2 PASS: independent Supervisor and STOP gate — Supervisor operates with
  no Hermes dependency; sentinel/CLI/tray/hotkey STOP all produce the same
  deterministic freeze; freeze-before-rollback; bounded worker control with
  lease expiry enforcement; forced-death reconstruction without duplicates;
  doctor health/reconciliation reporting; four verifier findings repaired
  with regression tests (see `docs/evidence/WP2_GATE_REPORT.md`).
- WP3 PASS: autonomous action control and resource registry gate — envelope
  contract validation before state lookup; deterministic path-boundary denial
  (traversal/symlink/junction/case escapes, INVALID_TARGET, no mutation);
  READY/ACTIVE capability semantics with NOT_READY denial; durable
  capability.state_changed events; active veto precedence over standing
  mandate; idempotent git resource registration; decision records with
  duplicate collapse. Independent verifier deleg_d335614d (65-test adversarial
  suite, real junction probes, no open()/resolve() divergence) PASS on all six
  criteria; two fail-loud findings repaired (B5: same-HEAD re-registration
  dirty-state update; B6: decision re-delivery dedupe on full payload) with
  regression tests; B2 precedence nuance documented (unready capability +
  matching veto => NOT_READY, denial preserved). Executive re-verification
  (133 passed fresh); frozen snapshot var/evidence/wp3-gate/ (digest
  `425b9f92…`, see `docs/evidence/WP3_GATE_REPORT.md`).

- WP4 PASS: Hermes role adapter gate — abstract role routing with provider
  fallback chains and diversity preference, content-addressed context
  manifests, structured executive/builder/verifier results, durable agent_run
  events with leases/heartbeats, deterministic reaper for stale sessions,
  disabled-by-default real-Hermes adapter through `.h` only with isolated
  HERMES_HOME (live install and live profile rejected), command surface
  verified against the real CLI. Two independent verifiers (deleg_f2954000,
  deleg_221bd327) C1–C7 all PASS; 8 findings repaired with regression tests.
  Executive re-verification (145 passed fresh); frozen snapshot
  var/evidence/wp4-gate/ (digest `5fbf1fb8…`, see
  `docs/evidence/WP4_GATE_REPORT.md`).

- WP5 PASS: mission and verification loop gate — deterministic next-action
  proposal with honest waiting, pre-dispatch envelope validation (step/
  capability/rollback/target-boundary), dispatch through the WP4 adapter with
  durable agent_run events, independent verification with recorded diversity
  metadata, bounded repair loop (FAIL -> REPAIRING -> re-verify, precise
  BLOCKED on budget exhaustion), local commit only after verified PASS (ledger-
  sequence verdict selection, run binding, policy-governed), evidence bundles
  per accepted step. Two verification rounds: round 1 (deleg_20def896) FAIL
  with three confirmed defects (F1 verdict tie-break, F2 fresh-instance
  resume collision, F3 pre-dispatch gap) + F4/F6/F7 — all repaired with
  regression tests; round 2 (deleg_1b6d0485) PASS on D1–D7 (all findings
  reproduced fixed). Executive re-verification (167 passed fresh); frozen
  snapshot var/evidence/wp5-gate/ (digest `2f608f6a…`, see
  `docs/evidence/WP5_GATE_REPORT.md`).

- WP6 PASS: governed V1 Obsidian write-back gate — V1 note set + Audit/
  with the exact frontmatter contract, transactional write protocol
  (expected hash → before snapshot → atomic replace → parse check → after
  hash → audit event → rollback pointer), concurrent-edit conflict detection
  preserving BOTH versions (fail-loud ObsidianConflictError), byte-exact
  rollback with clobber guard, write confinement to the V1 root with
  whole-vault read, schema v4 (obsidian_writes projection), durable
  obsidian.* events. Independent verifier deleg_d32753dd (156-check
  adversarial suite) E1–E7 all PASS, zero gate-failing findings; two
  defense-in-depth observations hardened (restore containment, atomic
  conflict preservation). Executive re-verification (180 passed fresh);
  frozen snapshot var/evidence/wp6-gate/ (digest `8227654a…`, see
  `docs/evidence/WP6_GATE_REPORT.md`).

## Current Reality

WP7 is the active package (V1 integration and activation). The kernel runs
in project-local staging and is not yet installed as a background service;
the WP7 gate must pass before real installation and activation (START BUILD
already authorized it).

## What Happens After START BUILD

The program advances through WP0-WP8 and later V2-V7 without asking for
phase-by-phase permission. It discovers resources, verifies capabilities,
builds, tests, repairs, promotes, expands, and self-improves automatically.

The owner receives reports and may veto or STOP at any time.
