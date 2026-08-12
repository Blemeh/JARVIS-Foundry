# JARVIS Foundry Project State

This file is the current project truth.

```yaml
project: JARVIS Foundry
owner: Blendi Mehmeti
state_version: 20
updated_at: 2026-08-12

phase: V2_KNOWLEDGE_BRAIN engineering ACCEPTED + operational ACCEPTED
  (WP11 V2-OPERATIONAL gate 16/16 PASS) -> V3_DISCOVERY operational
  integration in progress (real loops)
phase_status: ALIGNMENT_ACTIVE — VERSION PROMOTION HOLD (DIR-2026-08-12-master-plan-alignment,
  ledger seq 5486-5488; canonical record docs/evidence/ALIGNMENT_2026-08-12_RECLASSIFICATION.md)
day1_closed: 2026-08-12 (12 verified cycles; F1-F15 all PASS; independent
  reviews: round-1 FAIL -> round-2 FAIL -> round-3 PASS (deleg_8ee78c80);
  seven-day duration WAIVED by the owner (DIR-2026-08-12-seven-day-waiver,
  authenticated message 16242; ledger 1763+2143); gate report
  docs/evidence/WP8_GATE_REPORT.md)
v2_closed: 2026-08-12 (V2 KNOWLEDGE BRAIN ENGINEERING GATE PASS: five
  slices, each independently verified — slice-1 epistemic core (548b6bb6),
  slice-2 retrieval+manifests (7c4759fa), slice-3 entities (01ebd46f),
  slice-4 ingestion (3cce471c), slice-5 snapshots (23953faa); 8 recorded
  verifier FAIL rounds all repaired with regressions; gate review round-1
  FAIL (G3 truth correction) -> round-2 PASS (deleg_39ce6249); ledger
  verification.passed:verdict-wp9-gate seq 4846.
  OPERATIONAL GATE PASS 2026-08-12 (WP11 V2-OPERATIONAL, directive Phase
  4): slices A-G implemented and committed (80d3c38..07a36b7) with live
  evidence — 8 real vault notes indexed, 506 live claims, real Hermes
  worker dispatch with ledger trace, governed consolidation + integrated
  rollback on a live note (restored byte-identical, anchor-verified).
  16-criterion gate round-1 FAIL (C8 agent_run trace absent, C9 pristine
  bytes) -> repairs 41eb9c6 -> round-2 PASS 16/16 (deleg_303dac65,
  verdict sha256 38c64c72…; ledger verification.passed:
  verdict-v2-operational:round2). V2 promotion_state ELIGIBLE)
implementation_started: true
start_command_received: true
start_command_received_at: 2026-08-11T17:33:05+02:00
active_version: V2 (engineering gate PASS; operational gate PASS;
  promotion_state ELIGIBLE — promotion still under the HOLD's
  promotion-eligibility rules until the owner lifts it; see version_gates)
active_work_package: V3_REAL_LOOPS (Phase 7 of DIR-2026-08-12-master-plan-alignment —
  real monitored sources, multiple complete OBSERVE..SELECT-NEXT loops,
  FAIL->repair cycle, interruption recovery, Git + deploy reconciliation;
  design and close records in docs/planning/)

version_gates:
  V1: {engineering_gate: PASS, operational_gate: NOT_TESTED,
       promotion_state: HOLD, history: "promoted under authenticated
       seven-day waiver (ledger 1763+2143); accepted-under-waiver,
       NOT seven-day-reliability-proven", observation_window: OPEN}
  V2: {engineering_gate: PASS, operational_gate: PASS (WP11 gate
       16/16, round-2 PASS deleg_303dac65; ledger
       verification.passed:verdict-v2-operational:round2),
       promotion_state: ELIGIBLE, next: "owner promotion decision —
       V2 remains HOLD-executed until the owner lifts the promotion
       hold; operational acceptance proven"}
  V3: {engineering_gate: PASS (slice-1 CLOSED PASS ledger 5100-5102;
       slice-2 CLOSED PASS ledger 5830-5831 with FAIL->repair lineage),
       operational_gate: NOT_TESTED, promotion_state: HOLD,
       next: "V3_REAL_LOOPS (Phase 7): multiple complete real loops,
       FAIL->repair, interruption recovery, Git + deploy
       reconciliation, then the V3 operational gate"}
  V4: {engineering_gate: NOT_TESTED, operational_gate: NOT_TESTED,
       promotion_state: HOLD, activation: "requires V2 eng+op PASS (DONE),
       V3 eng+op PASS, reconciled state, durable evidence, no severity-one
       defect, healthy Supervisor and STOP"}

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
  - knowledge_brain_engineering: READY (operational integration pending)
  - discovery_candidate_pipeline: READY (slice-1)
  - monitored_sources: READY (slice-2, engineering)
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
active_directives:
  - DIR-2026-08-12-gate-progression (redirect; active)
  - DIR-2026-08-12-seven-day-waiver (waive; active)
  - DIR-2026-08-12-window-extension (extend_duty_window; active)
  - DIR-2026-08-12-master-plan-alignment (align; active; ledger 5486)

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
  WP8: ACCEPTED-under-waiver (2026-08-12; independent round-3 PASS;
        trust TRUSTED_AUTONOMOUS; waiver recorded; OPERATIONAL GATE
        NOT_TESTED — V1 reliability observation window OPEN, ledger 5488)
  WP9: ENGINEERING ACCEPTED (2026-08-12; five slices independently
        verified; gate review round-2 PASS; ledger 4846; OPERATIONAL
        GATE NOT_TESTED — promotion HOLD per DIR-2026-08-12)
  WP10: ENGINEERING CLOSED — slice-1 CLOSED PASS (deleg_d70f796a; ledger
        5100-5102); slice-2 CLOSED PASS (round-1 FAIL deleg_verifier_slice2_adv
        seq 5639 → repair 89b7ca4 → round-2 PASS deleg_verifier_slice2_adv_r2
        seq 5830-5831); V3 operational gate NOT_TESTED

autonomous_local_actions_after_start: true
autonomous_obsidian_actions_after_capability_ready: true
autonomous_online_research_after_start: true
autonomous_external_actions_after_capability_ready: true
autonomous_git_and_deployment_after_capability_ready: true
autonomous_financial_actions_after_capability_ready: true
autonomous_self_evolution_after_capability_ready: true

next_event: WP11 V2-OPERATIONAL INTEGRATION (DIR-2026-08-12 Phase 4):
  slices A-G per docs/planning/V2_OPERATIONAL_DESIGN.md (A vault
  indexing → B whole-vault retrieval → C continuous ingestion → D
  research queues → E real worker context → F governed consolidation →
  G integrated rollback), each with TDD + full suite + ledger events +
  slice doc; then the 16-criterion V2 operational gate + independent
  verifier → V2 ELIGIBLE; then Phase 5 evidence manifests; then V3 real
  loops (Phase 7); V4 activation only after both V2 and V3 gates PASS.
  VERSION PROMOTION HOLD active until then.
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
- WP9 (V2 Knowledge Brain) 2026-08-12: five slices implemented with TDD
  and independently verified (see v2_closed above); gate review round-1
  FAIL (G3: live-DB schema drift truth correction — closure scripts had
  applied v5→v9 to the live daemon DB) → additive truth correction →
  round-2 PASS (deleg_39ce6249; ledger 4846–4847). Live knowledge_*
  tables exist but are EMPTY (no operational data yet).
- WP10 (V3 Discovery) 2026-08-12: slice-1 candidate pipeline (schema v10)
  CLOSED — PASS (deleg_d70f796a; ledger 5100–5102; round-1 P3 timing
  finding repaired); slice-2 monitored sources + mission-execution wiring
  (schema v11, commit b9d279a + pre-v11 fix ac65025) — round-1 verifier
  FAIL (deleg_verifier_slice2_adv: C4 pre-v11 mission.completed crash +
  C7d raw KeyError; ledger 5639) → repair commit 89b7ca4 + 2 regressions
  (suite 312 EXIT=0) → round-2 PASS (deleg_verifier_slice2_adv_r2,
  A/B-discriminated; ledger 5830–5831). ENGINEERING CLOSED; V3
  operational gate NOT_TESTED.
- WP11 (V2-OPERATIONAL) activated 2026-08-12 per DIR-2026-08-12 Phase 4:
  slices A–G + 16-criterion V2 operational gate (design
  docs/planning/V2_OPERATIONAL_DESIGN.md).

## Current Reality

The 2026-08-12 master-plan alignment directive (ledger 5486–5488) put a
VERSION PROMOTION HOLD on V1–V4 and reclassified the versions under a
two-gate model (engineering vs operational). V1 was accepted under the
owner's authenticated seven-day waiver — that remains valid, and V1's
multi-day reliability proof is now an explicit OPEN observation window,
not a completed claim. V2's engineering core is accepted; its operational
gate is NOT_TESTED because the live knowledge tables hold no data and no
real vault has been indexed, retrieved, ingested, consolidated or rolled
back. V3 has an engineering-passed slice-1 and an implemented slice-2
awaiting its verifier; its operational gate requires real monitored
sources and multiple complete autonomous loops. The alignment program
(PHASES 1–7 of the directive) is the current work; nothing is promoted
and V4 stays inactive until both gates PASS per version. WP10 slice-2
closed ENGINEERING PASS (round-1 FAIL → repair → round-2 PASS, ledger
5639/5830–5831); WP11 V2-OPERATIONAL integration is ACTIVE (slices A–G).
The daemon is healthy (5s cadence, one lease, live schema v10, 5800+
events, STOP false). Canonical reclassification record:
`docs/evidence/ALIGNMENT_2026-08-12_RECLASSIFICATION.md`; verbatim
directive: `docs/evidence/OWNER_DIRECTIVE_2026-08-12_ALIGNMENT.md`.

## What Happens After START BUILD

The program advances through the alignment phases and later V2-V7 without
asking for phase-by-phase permission. It reconciles truth, integrates the
missing V2 operational surfaces, runs real V3 loops, verifies
independently, promotes only on both-gate PASS, and self-improves
automatically.

The owner receives reports and may veto or STOP at any time.
