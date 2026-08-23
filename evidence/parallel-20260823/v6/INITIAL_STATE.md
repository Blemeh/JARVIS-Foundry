# V6 Parallel Lane — Initial State (2026-08-23)

Lane: candidate/v6-self-evolution-20260823
Worktree: C:/temp/JARVIS-V6-Self-Evolution
Frozen base: 01db32cbcaa409dfe26386ce66110429c2c0b0e0 (main == base at lane start, clean)
Live production: UNTOUCHED. This lane owns isolated source + disposable releases only.

## Baseline verification (fresh, reproduced this session)

- Frozen base HEAD: `01db32cbcaa409dfe26386ce66110429c2c0b0e0`
- Worktree status: clean (0 modified files) at creation.
- Evolution focused suite: 12 files
  (`test_evolution_{acceptance,builder,canary,cycle,discover,lab,promote,records,registry,restore,shadow,verify}.py`)
  → **195 passed** under Python 3.11.15, pytest tests/foundry (FAST default).
- Schema version on base: SCHEMA_VERSION = 36.

## Existing machinery inventory (src/jarvis_foundry/evolution/, 4,114 LOC)

| Module | Slice | Role |
|---|---|---|
| records.py | V6-S1 | fail-closed improvement records, deterministic risk classifier (unknown surface → R3), monotonic risk raises, content-derived dedupe ids |
| registry.py | V6-S2 | immutable release manifests, slot pointers, idempotent transitions; verify_manifest re-hashes artifacts |
| builder.py | V6-S3 | isolated candidate roots, sanitized env, stale-marker reaping, outside-write detection, Windows process liveness via OpenProcess |
| lab.py | V6-S4 | frozen workloads (input hashes), hash-verified copies per run, per-case execution, comparison fails closed on incomparable windows |
| verify.py | V6-S5 | verifier boundary: request→verdict binding, forgery guard, one verdict per request, frozen criteria, builder≠verifier session |
| shadow.py | V6-S6 | zero-effect shadow runs, ShadowSink refuses capability use, live-surface hash before/after |
| canary.py | V6-S7 | supervisor-owned bounded canaries, raw-metric health recompute, auto-terminate, stale reaping |
| promote.py | V6-S8 | atomic promotion gate chain (trusted PASS + measured improvement + canary R2/R3), crash recovery, freeze-first rollback with reconcile check |
| restore.py | V6-S9 | verified sqlite backup points, data-loss analysis before restore, disposable rehearsals |
| discover.py | V6-S10 | observation→candidate derivation, evidence_ref mandatory, template hypotheses, all-or-nothing recording |
| cycle.py | V6-S11 | end-to-end ladder: build → release → lab → verify → canary → promote, stage failures recorded as rejected |
| acceptance.py | V6-S12 | engineering + operational acceptance minimums from ledger, fails closed |
| scheduler.py | V7-S7 | event-first scheduler, disabled by default, budgeted ticks |
| portfolio.py | V7-S7 | project/goal scoring and selection |

Control plane: src/jarvis_foundry/supervisor/evolution_control.py (V7-S3) gates all
effectful evolution ops behind controller=='supervisor', observe-by-default mode,
and EMERGENCY_STOP sentinel. Consumers: reporting/briefing.py reads slot_pointers;
procedural_learning/shadow.py reuses evolution.shadow_run.

## Truth-trace findings (JOB V6-1, verified against source)

F1. **Self-certifying verdict in the cycle ladder (HIGH).**
    cycle.py run_cycle step 5 (lines ~196–214) calls
    record_verification_verdict(..., verdict="PASS", criteria_results=[...PASS...])
    with a fabricated per-criterion PASS list inside the same process that built
    the candidate. The S5 forgery guards are real but the ladder bypasses their
    purpose: no independent verifier ever runs. is_trusted_verdict then sees a
    "different" session ('supervisor-verifier' vs 'cycle-builder') and promotion
    proceeds. The machinery cannot self-certify through verify.py directly, but
    the canonical cycle path certifies itself today.

F2. **Discovery has no producer (HIGH).**
    discover.py consumes four observation types (metric_exceedances,
    guardrail_breaches, verifier_findings, task_failures). Grep across src/:
    only discover.py itself mentions them; nothing derives observations from the
    durable ledger (agent_runs failures, evaluation_runs error rates, cost
    measurements). Discovery exists but never fires from real outcomes —
    PROVEN_BUT_NOT_CONNECTED.

F3. **Scheduler does not drive evolution (MEDIUM).**
    scheduler.py tick_scheduler fires registered ScheduleItems but contains zero
    references to discovery, cycles, or improvements. No continuous
    improvement loop exists end-to-end.

F4. **No outcome-feedback loop (MEDIUM).**
    After promotion, nothing measures post-change outcome against the hypothesis'
    predicted delta, updates calibration, or records lessons. cycles table ends
    at outcome promoted/rejected; candidate_outcomes table (v9/v17) belongs to
    the older mission-candidate system, not release promotions.

F5. **No weakness detector over durable outcomes (MEDIUM).**
    agent_runs carries status/failure_class/cost_json; evaluation_runs carry
    error_rate/duration_s/cost_usd; verifications carry findings — but nothing
    aggregates them into evidence-backed weakness observations with
    window/baseline/threshold/dedupe identity.

F6. **Superseded path kept alive (LOW).**
    registry.promote_candidate / rollback_live / quarantine_release (S2) are
    referenced only by their own tests; the governed path is promote.py (S8).
    Not removed (history/tests remain auditable); classified SUPERSEDED.

F7. **Baseline metrics recorded but unused beyond compare_evaluations (LOW).**
    improvement_records.baseline_metric_json feeds _metric_key mapping to
    exactly two keys (error_rate, duration_s); latency/cost comparisons exist as
    guardrails only. Acceptable for now; benchmark engine (V6-8) should widen.

## What this lane will build (queue summary)

Reuse everything above. Add: outcome-derived weakness detector (feeds F2/F5),
hypothesis linkage is already durable (records.py), measurement layer with
explicit UNKNOWN/INSUFFICIENT_SAMPLE, experiment plan compiler, hardened builder
attacks, first-class shadow experiments, typed benchmark engine, independent
verifier orchestration replacing F1's fabricated verdict (the critical repair),
disposable canary execution, eligibility decision function, bad-candidate
factory, disposable promotion+rollback loop, outcome feedback, next-improvement
selector, multi-cycle harness.

## Boundary compliance at start

- Live DB (var/v7-runtime/v7.db): not opened, not migrated, not read beyond path existence.
- No Task Scheduler changes. No provider calls. No pushes yet.
