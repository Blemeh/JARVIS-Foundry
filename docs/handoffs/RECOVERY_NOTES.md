# Recovery Notes — JARVIS Foundry

Authoritative resume point. Updated 2026-08-12 ~23:55+02:00 — **V4 duty
window in progress** (DIR-2026-08-12, ~12h; owner away; silence = continue).

## Current state

- Branch `main`, HEAD `e456c22` (V4-S2 verifier truth) — clean tree
  (docs/evidence/V4_S2_VERIFIER_TRUTH.md uncommitted).
- **MR-1 CLOSED**: mandatory model routing fix (`bab5830`) — every role →
  opencode_go + deepseek-v4-flash, no fallbacks; enforcement in
  run_one_loop + live drivers; verifier deleg_debc2612 **PASS** (P1–P10);
  ledger `verification.passed:verdict-v4-mr1-routing:round1` +
  `evidence.created:v4-mr1-routing-close` (seq 14050–14051, schema 16).
  Evidence: docs/evidence/V4_MR1_MODEL_ROUTING.md + var/evidence/v4-mr1-routing/.
- **V4-S2 implemented** (`e456c22`, suite 429 EXIT=0): structured-verdict
  parsing in HermesOneshotAdapter (exit-0 never PASS; missing verdict →
  NOT_TESTED); loop verifier instructions demand `VERDICT:` line;
  evidence bundles bind artifact sha256 hashes. Verifier deleg_81eea974
  RUNNING (freeze: no src/tests edits until verdict).
- Live system: daemon healthy (5s heartbeats, 1 lease, STOP false, schema
  v16, ~14,050 events, 954 claims); candidates 3 EXECUTED / 233 SCORED /
  696 NEW; monitors registry EMPTY (0 rows — R6 fix pending).
- Live audit findings (all confirmed read-only; design in
  docs/planning/V4_TRUTH_SLICES.md):
  - R2a: heartbeats never renew leases → live sessions get reaped at
    lease expiry (dispatcher + reaper).
  - R2b: agent_runs projection not terminal-monotonic (late completed
    flips FAILED→COMPLETED; started resets terminal rows).
  - R3: interrupted verifier + same-run_id resume crashes (EventConflict
    on agent_run.started) — no generic exactly-once resume.
  - R4: completion truth verified OK live (3 COMPLETE ↔ 3 events ↔ 3
    EXECUTED).
  - R5: 766 live claims carry `handoff:handoff:` run_id duplication;
    note_support only knows vault sources (894/920 UNSUPPORTED claims).
  - R6: monitors registry unused; register+poll vault/handoffs/repo/system.
  - R7: scoring lacks freshness/cooldown/source-trust terms.
  - R8: docs to reconcile at close.

## If interrupted, resume with

1. `git status` — expect clean at the S2-verifier commit.
2. `./.venv/Scripts/python.exe -m pytest tests -q` (429 tests, EXIT=0).
3. Read docs/planning/V4_TRUTH_SLICES.md (design) +
   CURRENT_WORK_PACKAGE.yaml (V4_CONTINUOUS_AUTONOMY).
4. Verify the deleg_81eea974 verdict landed (PASS expected); if FAIL,
   repair exact findings → regression → re-verify. Then append the
   ledger close (`var/evidence/close_v4_mr1.py` is the pattern).
5. Implement V4-S3 (lease renewal + terminal monotonicity) → S4
   (exactly-once resume) → S1 (support semantics — the package's named
   FIRST MISSION) → S5 (monitors) → S6 (scoring + docs). Each: TDD red →
   fix → focused → full suite → freeze → verifier → ledger close.
6. After S1 lands: run a real live loop (var/evidence/v3_loops/
   live_loop_driver.py — enforcement already wired) to (a) confirm the
   ledger records deepseek-v4-flash for builder+verifier+recovery
   (directive routing confirmation), (b) re-scan support on live claims,
   (c) supersede false-UNSUPPORTED candidates additively.

## Standing invariants (never violate)

- Append-only ledger; no reset/clean/restore of existing work; no
  fabricated PASS/evidence/consent; no push (no remotes); no cron
  (owner paused all 2026-08-12); note content is UNTRUSTED data;
  governed writes only through ObsidianService; live DB never replaced
  while the daemon is active; builders never certify their own work;
  independent adversarial verification with provider limitation
  recorded; freeze discipline before every verifier dispatch; SCHEMA
  check before any live-ledger append (currently 16 == code 16).
- Owner control: STOP / pause / veto / redirect — silence means
  continue. Owner-only blockers: mark OWNER_PENDING, continue
  independent work, ask once.

## Evidence indexes

- MR-1 routing: docs/evidence/V4_MR1_MODEL_ROUTING.md,
  var/evidence/v4-mr1-routing/ (verdict deleg_debc2612.json sha256
  5ff10149…, suite-v4-mr1.txt, close_v4_mr1.py).
- S2 verifier truth: docs/evidence/V4_S2_VERIFIER_TRUTH.md (status
  <PENDING> until verdict), var/evidence/v4-s2-verifier-truth/.
- Design: docs/planning/V4_TRUTH_SLICES.md (R1–R8 + slice plans).
- Checkpoints: var/evidence/checkpoints/CHECKPOINT-2026-08-12T2338.md.
- Prior gates: docs/evidence/WP11_V2_OPERATIONAL_GATE.md (PASS 16/16),
  docs/evidence/WP12_V3_OPERATIONAL_GATE.md (PASS 6/6).
