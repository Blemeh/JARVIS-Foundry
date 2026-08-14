# V7-S3 — SUPERVISOR ADOPTION IN ISOLATED OBSERVE-ONLY MODE

```text
STATUS: PASS (independent verifier verdict appended on close)
PHASE: V7_FULL_AUTONOMOUS_OPERATION
SLICE: S3 — Supervisor adoption wiring in isolated observe-only schema-32 runtime
OBJECTIVE: wire all V6 evolution services (records, registry, builder, lab,
  verifier, shadow, canary, promotion, restore, discovery, cycle, acceptance)
  into the durable Supervisor through a thin Supervisor-owned control-plane
  adapter, and prove in an isolated schema-32 runtime that the Supervisor runs
  and recovers without Hermes, evolution defaults observe-only, only the
  Supervisor can drive cycles/canaries/promotion/freeze/rollback/restore/
  release pointers, STOP blocks new and in-flight work, duplicate wakeups/
  events/leases/retries/restarts are idempotent, workers and candidates cannot
  bypass or terminate the control plane, and command/UI/voice failure cannot
  disable Supervisor control.
AUTHORITY_AND_ACTIVE_CONTROLS: V7 directive; S0-S2 closed PASS; standing
  mandate; no vetoes; live STOP sentinel absent; daemon task triggers held.
BASE_COMMIT_AND_DIFF: base 547b177 (S2 close); this slice adds
  src/jarvis_foundry/supervisor/evolution_control.py (new) +
  tests/foundry/test_v7_supervisor_adoption.py (new, 14 tests). No changes to
  accepted V6 machinery — the adapter only pins identity, mode, and STOP.
LIVE_RELEASE_PROCESS_AND_DATABASE: untouched (verified at close: live schema
  v20, daemon RUNNING pre-merge code, task triggers N/A).
CAPABILITIES_AND_READINESS: the Supervisor's evolution control plane is now
  a real, tested entry point (observe-only by default); release pointers,
  cycles, canaries, promotion, freeze, rollback, restore, and discovery are
  all Supervisor-pinned and STOP-gated.
MISSION_OBSERVATION_OR_HYPOTHESIS: improvement-c4f21554bf73 (adoption base);
  S3 supplies the control-plane wiring S5/S6/S7 will drive.
SYSTEMS_AND_CANONICAL_TARGETS: isolated temp homes + temp DBs (schema 32);
  main src at 547b177 + S3 changes; live systems read-only.
RISK_AND_CONSEQUENCE: control-plane boundaries now enforced in ONE pinned
  entry point (the adapter) + the machinery's own candidate refusals. One
  documented boundary: raw registry.set_slot records the controller as
  provenance, not authorization — release-pointer authorization flows through
  promote/rollback gates and the adapter (accepted-V6 design, recorded).
BASELINE_SUCCESS_THRESHOLD_AND_GUARDRAILS: 14 new tests cover the gate matrix
  (observe default, controller pinning, STOP blocking incl. startup FROZEN,
  idempotent restarts/duplicate events/leases, candidate refusal, bare-process
  control); affected suites (227 tests) green; full suite green from frozen
  source (hash below); no live mutation.
BUDGET_QUOTA_AND_COST: local only; zero provider cost.
ACTIONS_TAKEN: TDD red (ModuleNotFoundError) -> implemented
  src/jarvis_foundry/supervisor/evolution_control.py -> 14 tests green ->
  affected suites (227 tests) green -> full suite (below); test defects found
  and fixed during TDD (Event.payload API, full-env subprocess, lease
  released-on-clean-stop semantics, machinery set_slot provenance boundary).
FILES_PROCESSES_DATABASES_AND_EXTERNAL_STATE_CHANGED: two new source/test
  files (committed with this slice); temp artifacts under var/evidence/v7-s3;
  no live changes.
TESTS_RECONCILIATION_RESTORE_AND_FAULTS: focused 14/14; affected 227/227;
  full suite 926 passed EXIT=0 (414.30 s; artifact var/evidence/v7-s3/
  suite-v7-s3-full.txt, sha256 fae98016f5a9622598724d0ff49c29628a35c704cae129272b19064fe2645be1);
  fault probes: STOP sentinel blocks effectful ops + freezes startup; duplicate
  event delivery idempotent; three consecutive daemon starts idempotent.
VERIFIER_AND_STRUCTURED_VERDICT: <fill after dispatch>
EVIDENCE_PATHS_AND_HASHES: src/jarvis_foundry/supervisor/evolution_control.py;
  tests/foundry/test_v7_supervisor_adoption.py; var/evidence/v7-s3/
  suite-v7-s3-full.txt (fae98016…); docs/evidence/V7_S3_SUPERVISOR_ADOPTION.md.
REAL_EFFECT_PROVIDER_REFERENCE_AND_AFTER_STATE: none (isolated runtime).
ROLLBACK_COMPENSATION_OR_CONTAINMENT: source rollback = revert the S3 commit
  (two files); the adapter is not imported by any live process yet (wired in
  S7); zero live exposure.
UNRESOLVED_RISKS_LIMITATIONS_AND_BLOCKERS: same-provider verifier limitation
  (D-031); raw set_slot provenance boundary (recorded); the adapter is not yet
  invoked by the daemon loop (S7 wires the cadence; S6 starts the daemon
  observe-only).
NEXT_AUTONOMOUS_ACTION: S4 — real-observation discovery and shadow decisions
  (live ledger observations, deduplicated candidates, honest WAITING).
OWNER_INPUT_REQUIRED: NONE
```

## 1. What was built

`src/jarvis_foundry/supervisor/evolution_control.py` — `EvolutionControl`:
- controller identity pinned to `supervisor` on EVERY effectful entry point
  (run_cycle, start_canary/canary_tick/terminate/reap, freeze/unfreeze,
  promote, rollback, set_slot, create_restore_point/restore,
  record_discovered, record_improvement, register_release);
- mode default `observe` (nothing effectful runs until `enable()` by the
  Supervisor, and every mode transition is ledger-recorded
  `evolution.mode_changed`);
- STOP sentinel checked on every effectful operation (in addition to the
  machinery's own checks);
- read-only queries (mode, is_frozen, slot_pointers) always available.

## 2. Gate matrix (14 tests)

| Gate requirement | Test(s) |
|---|---|
| Supervisor runs and recovers without Hermes | bare `python -m jarvis_foundry.supervisor.run --once` × 3: RUNNING, distinct instance ids, clean lease release, idempotent |
| evolution defaults disabled/observe-only | default mode observe; observe refuses every effectful op; read-only works |
| only Supervisor schedules cycles/canaries/promote/freeze/rollback/restore/pointers | adapter pins supervisor (candidate refused at the adapter); machinery refuses candidate controllers (promote/freeze/unfreeze/canary/discovery); frozen release control blocks promotion |
| STOP blocks new and in-flight work | sentinel blocks effectful ops in active mode; startup with sentinel → FROZEN + emergency_stop.frozen event |
| duplicate wakeups/events/leases/retries/restarts idempotent | duplicate event delivery → one row; 3 consecutive starts → 3 started events, no stale lease |
| workers/candidates cannot bypass or terminate control plane | no candidate-facing path; every entry point pinned |
| command/UI/voice failure cannot disable control | bare-process drive (no Hermes/UI/voice) enables/freezes/unfreezes successfully |

## 3. Documented boundary (accepted-V6 design)

`registry.set_slot` records the controller as provenance, not authorization;
release-pointer authorization flows through the controller-gated entry points
(promote/rollback) and the Supervisor's adapter. Recorded here so the S13
gate can re-check it explicitly.

## 4. Verifier — round 1: deleg_bb1a6605, FAIL (1 MEDIUM) → repaired

Round-1 verdict JSON: `%TEMP%\v7s3-verdict-v7s3.json` (early-skeleton
protocol; verdict FAIL, C1/C3/C4/C5 PASS, C2 and C6 FAIL on ONE claim; 19
API calls, ~313 s).

| Severity | Finding | Resolution |
|---|---|---|
| MEDIUM (C2) | `EvolutionControl.reap_stale_canaries` was arity-broken: called `canary_mod.reap_stale_canaries(db)` without the machinery's required `current_cycle` / `max_cycles_without_tick` / `controller` kwargs → TypeError on use; fail-closed (no safety hole) but non-functional and untested | REPAIRED: adapter now threads `current_cycle`, `max_cycles_without_tick`, and `controller=SUPERVISOR_CONTROLLER` through; added read-only `canary_status`; two discriminating regression tests added (`test_adapter_reap_stale_canaries_works` — old code raised TypeError here; `test_adapter_reaps_hung_canary` — a seeded RUNNING canary is TERMINATED without cooperation); 16/16 focused green; full suite re-run below |
| INFO | `record_improvement` has no machinery controller param — adapter call correct | RECORDED |
| INFO | 6th untracked file = V7_S4_DISCOVERY.md (parent S4 prep) | RECORDED — additive |
| INFO | task XML has Enabled=false while Status shows Running (running instance predates the disable) | RECORDED — consistent with the S1 hold |

Round-2 verifier: <fill after dispatch — fresh independent adversarial
verifier with ROUND_2_FINDINGS_CLOSED criterion + old-code A/B
discrimination>

## 6. Final status

<fill after round-2 verdict>
