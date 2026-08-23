# V6 Lane Final Report (2026-08-23)

FINAL VERDICT: PASS (lane scope — candidate branch only; no live promotion)
FROZEN BASE: 01db32cbcaa409dfe26386ce66110429c2c0b0e0
FINAL HEAD: see `git log -1` on candidate/v6-self-evolution-20260823 (post-6ba366f)
REMOTE CANDIDATE SHA: pushed at final validation (normal push, no force)

## Existing V6 assets reused (not rebuilt)

records.py (S1), registry.py (S2), builder.py (S3), lab.py (S4),
verify.py (S5), shadow.py (S6), canary.py (S7), promote.py (S8),
restore.py (S9), discover.py (S10), cycle.py (S11), acceptance.py
(S12), scheduler.py/portfolio.py (V7-S7), supervisor/evolution_control.py
(V7-S3). Baseline at lane start: 195 evolution tests PASS.

## What the lane built (all additive unless noted as repair)

- detector.py — deterministic weakness detection over agent_runs /
  evaluation_runs / verifications / missions; explicit window contract;
  min-sample suppression; confidence caps; dedupe identities.
- discovery lineage — detector observations flow into S10 discovery and
  freeze as hypotheses with rule/dedupe/window provenance; identical
  re-runs collapse to ONE record (V6-3).
- measurement.py — BEFORE/AFTER snapshots with distributions,
  confounders, INSUFFICIENT_SAMPLE honesty, typed UNKNOWN comparisons
  (never silently compares incompatible windows) (V6-4).
- plan.py — experiment plan compiler: frozen hypothesis -> bounded
  disposable plan; shadow/canary requirements by risk class; control
  plane always forbidden even to R3; execution-trace compliance (V6-5).
- builder hardening (REPAIRS): corrupt-marker reaping (was fatal),
  sibling-file content-change + removal outside-write detection
  (was new-entry-only), clean-root provenance boundary at registration
  (V6-6).
- shadow crash-cleanup (REPAIR): arbitrary decision_fn crash left an
  orphaned RUNNING row; now recorded + re-raised. Plus first-class
  experiment proofs: deterministic repeat, forbidden-effect fail-closed
  with zero non-shadow ledger writes, divergence comparison (V6-7).
- benchmark.py — typed per-metric benchmark table (no single scalar),
  honest NOT_AVAILABLE rows, guardrail failures listed explicitly (V6-8).
- verifier_orchestrator.py (REPAIR of F1 self-certification): probe
  suite executes OUT-OF-PROCESS against frozen criteria; unearned PASS,
  missing/extra criteria, wrong candidate all refuse; verdict through
  the S5 boundary with a dedicated verifier session; orchestration
  record binds tree hash + evidence hashes (V6-9).
- disposable_canary.py — deterministic bounded allocation; raw metrics
  into the REAL S7 tick machinery; auto-stop health/budget/STOP;
  terminated canaries refuse any post-mortem tick (V6-10).
- eligibility.py — ELIGIBLE/REJECTED/BLOCKED/UNKNOWN over the SAME gate
  predicates as promote (zero drift); read-only proof: slots unchanged
  by an ELIGIBLE verdict (V6-11).
- bad_candidate.py — 9 attack families, every one rejected by its
  corresponding machinery with no live damage (V6-12).
- disposable loop proof — good path promotes+retains; degraded successor
  gate-rejected; freeze-first rollback restores previous + quarantines;
  crash recovery idempotent; restore rehearsal clean (V6-13).
- feedback.py — predicted-vs-actual outcomes (CONFIRMED/PARTIAL/MISSED/
  ROLLED_BACK); retained success without measured delta refused;
  per-rule calibration; duplicate-window dedupe; 24h cooldown;
  calibration floor 0.4; bounded selection (V6-14).
- selector.py — deterministic next-improvement choice with reasons for
  EVERY rejected alternative; owner vetoes unselectable; R3 needs
  supervisor override; capacity saturation and negative expected value
  both WAIT honestly (V6-15).
- harness — multi-cycle end-to-end loop through real machinery:
  observe -> hypothesize -> accept good / reject degraded / rollback ->
  learn -> STOP blocks -> reconcile consistent; identical observations
  never spawn duplicate hypotheses (V6-16).

## Required proof points

GOOD IMPROVEMENTS PROVEN: 1 accepted path in harness + 4 in loop tests
(faster candidates promote; slower are refused).
BAD CANDIDATES REJECTED: 9 factory families + degraded successor in
harness + forged/self-certified verdicts in orchestrator tests.
ROLLBACKS PROVEN: outcome_regression trigger restores exact previous
release and quarantines failed one; restore rehearsal PASSes on copies.
STOP/VETO BLOCKS: sentinel blocks cycles/promotion/canary start/eligibility
(BLOCKED class); veto via selector unselectability; freeze blocks.

## Validation

TESTS (full FAST suite artifact var/evidence/v6-lane/full-fast-suite-
20260823.txt): 1812 collected — **1807 passed, 5 failed**.
The 5 failures are PRE-EXISTING on the pristine frozen base: all five
reproduce identically on a detached worktree at 01db32c
(test_console_events daemon-stop, test_installer daemon-exit +
headless-once-cycle, test_stop_controls latency-cap,
test_supervisor_loop_resilience slow-cycle) — supervisor-daemon/
wall-clock-timing tests sensitive to this host; zero caused by this
lane (lane files touch none of those surfaces).
Lane added 108 tests across 14 new files; evolution-focused subset:
253 passed EXIT=0 (var/evidence/v6-lane/evolution-suite-20260823.txt).

SECRET SCAN: clean — pattern scan over the full lane diff
(api_key=/secret=/password=/sk-…/PRIVATE KEY) returns nothing;
secret-like content is additionally REFUSED at records/discovery/lab/
plan boundaries by inherited V5-S7 guards.

HYGIENE: commits use explicit paths; `git diff --cached --check` before
each commit; only evidence .txt/.md artifacts committed under
var/evidence/v6-lane/ and evidence/parallel-20260823/v6/.

WORKTREE CLEAN: yes (after final evidence commit)
CANDIDATE PUSHED: yes (origin/candidate/v6-self-evolution-20260823)
MAIN MODIFIED: NO
ORIGIN/MAIN MODIFIED: NO
PRODUCTION MODIFIED: NO
LIVE PROMOTION PERFORMED: NO

## Cross-lane handoffs

None required: V6 consumed only canonical interfaces (S1–S12, provider
config, event store). No shared migrations were added — measurement and
feedback reuse the event-ledger payload pattern instead of new tables.

## Open items (non-blocking)

- cycle.py step 5 still fabricates a PASS internally; the governed path
  now has verifier_orchestrator.py, but a follow-up should rewire
  run_cycle to call it (kept out of this lane to avoid changing S11's
  published behavior without an owner-visible migration decision).
- registry.promote_candidate/rollback_live remain SUPERSEDED test-only.
- scheduler still does not drive discovery/cycles (integration point for
  a future lane; the building blocks now exist).

NEXT INTEGRATION ACTION: independent review of this branch, then owner
decision on rewiring cycle.run_cycle step 5 to verifier_orchestrator.
