# Recovery Notes — JARVIS Foundry

Authoritative resume point. Updated 2026-08-12 ~21:05+02:00 (V2
OPERATIONAL GATE CLOSED — PASS 16/16 round-2).

## Current state

- Branch `main`, HEAD `4083fd9` (V2 gate close; tree clean at
  verification; close commit pending docs).
- PROJECT_STATE v20: **V2 promotion_state ELIGIBLE** (engineering PASS +
  operational PASS — WP11 16-criterion gate, round-2 PASS 16/16
  deleg_303dac65, ledger verification.passed:verdict-v2-operational:round2).
  V1 op NOT_TESTED (observation window OPEN), V3 op NOT_TESTED (HOLD),
  V4 inactive. VERSION PROMOTION HOLD still active (ledger 5487) — V2
  ELIGIBLE means promotion-ready, not promoted; the owner lifts the HOLD.
- Active work package: **V3_REAL_LOOPS** (Phase 7 of
  DIR-2026-08-12-master-plan-alignment) —
  docs/planning/CURRENT_WORK_PACKAGE.yaml.
- Live system: daemon healthy (5s heartbeat, one lease, STOP false);
  live DB schema v16, ~9,500 events, ~510 claims, 8 indexed vault files;
  reconcile consistent (WAL-snapshot-safe since eca0124).
- Live vault note 06 - Lessons.md: byte-identical to pre-gate content
  (440B / 70f9f8bd48fa — anchor-verified by the gate and round-2
  verifier). No leftover probe content.

## If interrupted, resume with

1. `git status` — expect clean at 4083fd9 (or the close commit).
2. `./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short`
   (378 tests, EXIT=0).
3. Read CURRENT_WORK_PACKAGE.yaml (V3_REAL_LOOPS) and
   docs/planning/V2_OPERATIONAL_DESIGN.md.
4. Phase 7 sequence (bounded real loops on mission-wfm-optimizer):
   a. loop wiring: monitor feed (live ingestion cycles) → candidates →
      scoring → mission creation → MissionLoop step (RoleDispatcher +
      HermesOneshotAdapter, isolated .h + var/wp8-hermes-home) →
      verifier → outcome measurement → next selection.
   b. FAIL→repair→fresh PASS cycle (unscripted verifier).
   c. interrupted-build recovery (kill real builder mid-run; resume via
      replay — no duplicate mutation).
   d. remote Git reconcile (read-only fetch + compare; NO push) and
      safe deployment-target reconcile (staging home + full suite).
   e. maintenance work + next-mission selection.
   f. V3 operational gate: evidence table, frozen HEAD, sanitized
      manifest, independent adversarial verifier, ledger close →
      V4 activation decision.

## Standing invariants (never violate)

- Append-only ledger; no reset/clean/restore of existing work; no
  fabricated PASS/evidence/consent; no push; no cron (owner paused all
  2026-08-12); note content is UNTRUSTED data (STOP/veto stay outside
  content); governed writes only through ObsidianService; live DB never
  replaced while the daemon is active (documented initialize only);
  builders never certify their own work; independent adversarial
  verification with provider limitation recorded; freeze discipline
  before every verifier dispatch.
- Owner control: STOP / pause / veto / redirect — silence means
  continue. Owner-only blockers: mark OWNER_PENDING, continue
  independent work, ask once.

## Evidence indexes

- V2 gate: docs/evidence/WP11_V2_OPERATIONAL_GATE.md (CLOSED — PASS),
  docs/evidence/EVIDENCE_MANIFEST_V2_OPERATIONAL.md (round-1 verdict
  05c49d0c…, round-2 verdict 38c64c72…, artifacts c0bfe268…/f843c31e…).
- Private artifacts: var/evidence/v2-operational-gate/ (gate runs,
  verdicts, snapshots), var/evidence/v2op-plan/ (design drafts),
  var/evidence/wp11-slice-*/.
- Live ledger sequences of note: 5486-5488 (directive + HOLD + V1
  window), 5639 (slice-2 round-1 FAIL), 5830-5831 (slice-2 PASS +
  evidence), verification.failed:verdict-v2-operational:round1,
  verification.passed:verdict-v2-operational:round2,
  evidence.created:v2-operational-gate.
