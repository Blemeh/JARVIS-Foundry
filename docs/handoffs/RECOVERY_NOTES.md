# Recovery Notes — JARVIS Foundry

Authoritative resume point. Updated 2026-08-13 ~01:50+02:00 — **V4 duty
window in progress** (DIR-2026-08-12, ~12h; owner away; silence =
continue).

## Current state

- Branch `main`, src HEAD `19fbb17` (docs HEAD `bb4a4b5`) — clean tree.
- **Six truth-critical slices CLOSED** (every close has an independent
  adversarial verifier PASS + ledger events + evidence docs under
  docs/evidence/ + artifacts under var/evidence/):
  - **MR-1 routing** (bab5830/2939415, deleg_debc2612 PASS): every
    internal role → opencode_go + deepseek-v4-flash, no fallbacks;
    `enforce_directive_routing()` runs before any loop mutation.
  - **S2 verifier truth** (e456c22/19ea656, deleg_81eea974 PASS):
    structured verdicts (exit-0 never PASS → NOT_TESTED); artifact
    hashes bound in evidence bundles.
  - **S3 lease/monotonic** (7e02091/2db5bdd, deleg_18d9f7b0 PASS):
    heartbeats renew session leases; agent_runs terminal-monotonic.
  - **S4 exactly-once** (f1dd903/1eeede5, deleg_a3e54630 PASS): resume
    never re-runs the builder; fresh verifier run id on interrupted/
    reaped rows; dangling verdicts corrected additively.
  - **S1 support semantics — the package's FIRST MISSION**
    (982f472/f2e3ea3, deleg_12682a02 PASS 17 probes): source-kind-aware
    support (vault/handoff/repo/system); projection-boundary run_id
    normalization; migration v17 (SUPERSEDED); LIVE CLOSE: v17 +
    atomic rebuild (14,945 events; 766 run_ids + 14 monotonicity rows
    converged), support re-scan **953/954 (was 894 unsupported)**,
    2 false-premise candidates superseded, reconcile 0 mismatches.
  - **S5 monitors** (fea9167 → **R1 verifier FAIL** deleg_71efdc9e —
    real findings (duplicate candidates/missions from repeated polls;
    REJECTED-item resurrection; unknown-monitor polls; empty
    objectives) → repaired at 19fbb17 → R2 verifier **PASS**
    deleg_9decc685, 16 probes): poll feed filtered (OPEN/non-empty/
    uncovered) before queue conversion; poll key = loop_id; duplicates
    land REJECTED in the projection; unknown-monitor polls fail
    atomically; migration v18 UNIQUE monitors index; LIVE CLOSE:
    v18 + rebuild (15,490 events; 45 inert duplicate rows converged),
    reconcile 0 mismatches.
- **S6 scoring in flight**: bounded context factors (freshness/
  cooldown/source-trust/evidence/similarity, all capped; threshold
  0.35 unchanged) + frozen recorded scores (b2b50e2; suite 485
  EXIT=0); verifier deleg_66e30870 RUNNING (freeze: no src/tests edits
  until the verdict; docs-only work allowed).
- Live system: daemon healthy (5s heartbeats, 1 lease, STOP false,
  schema **v18**, ~15,490+ events, 954 claims, 953 supported);
  candidates 3 EXECUTED / 2 SUPERSEDED / 233 SCORED / 696 NEW;
  reconcile **consistent**; monitors registry still empty (the live
  loop has not run since S5 — the m3 real-loop run registers them).

## If interrupted, resume with

1. `git status` — expect clean; `git log --oneline -12` for the chain.
2. Read docs/handoffs/RECOVERY_NOTES.md (this file) +
   docs/planning/V4_TRUTH_SLICES.md + CURRENT_WORK_PACKAGE.yaml.
3. Check the deleg_66e30870 verdict (S6): if PASS → archive +
   `var/evidence/close_v4_s6.py <id> <verdict.json> 19fbb17` (pattern:
   close_v4_s5.py) → update V4_S6_SCORING.md status → commit. If FAIL
   → repair exact findings → regression → re-verify (S5-R1 was the
   same loop).
4. R8 doc reconciliation (PROJECT_STATE v21 → v22, CURRENT_WORK_PACKAGE
   FIRST MISSION done, DECISION_LOG append D-0xx, RISK_REGISTER
   append) — the V4_TRUTH_SLICES R8 section lists the targets.
5. **m3 (mandatory routing proof)**: run ONE real live loop via
   var/evidence/v3_loops/live_loop_driver.py — the ledger must show
   builder+verifier+recovery workers ALL deepseek-v4-flash (directive
   routing item 8) AND the four monitors registered on the live DB.
6. Continuous cadence (loop every ~15–30 min on the live backlog);
   checkpoints every 60–90 min under var/evidence/checkpoints/;
   final report + GPT-5.6 Sol audit handoff at window end (~11:15).
