# V6-S6 — ZERO-EFFECT SHADOW MODE

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S6 — zero-effect shadow mode
OBJECTIVE: shadow candidates observe, reason and propose against
  COPIED inputs with ZERO live effects; capability use is refused
  (live kinds and blocked connectors fail the run, recorded); STOP
  freezes shadow runs before any proposal; divergence vs baseline
  decisions recorded; live surface proven unchanged before/after.
BASE COMMIT: 48c7834 (S5 close)
CANDIDATE RELEASE: none (machinery slice)
RISK CLASS: R3-class machinery (effect control) built and verified
  inside the isolated V6 construction; live surfaces untouched
HYPOTHESIS: n/a (machinery; exercised by the S11 ladder)
BASELINE METRIC: full suite 811 EXIT=0 at S5 close
SUCCESS THRESHOLD: propose-only runs COMPLETE with zero_live_effects
  proven (DB + live marker byte-identical); live capability attempts
  and blocked-connector attempts FAIL the run with recorded attempts;
  STOP (runtime-home sentinel) freezes runs with stop_observed; STOP
  cleared -> runs proceed; divergence (decision/expected_effect/cost)
  recorded per case; no divergence -> empty; unknown improvement/
  release refused; secret proposals refused; all five projection touch
  points wired; suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v26); live DB stays v20; no live writes; shadow scratch under the
  runtime home; capabilities refused by construction (ShadowSink)
ACTIONS TAKEN: TDD red (10 tests) -> implemented evolution/shadow.py
  (ShadowSink with proposal recording + capability refusal,
  shadow_run with STOP checks, live-surface hashing, divergence
  recording) -> migration v26 + PROJECTION_TABLES + reconcile key map +
  database row/all/count allowlists -> focused green (106 total across
  S1-S6) -> runtime DB migrated to v26 (reconcile consistent) -> full
  suite
FILES CHANGED: src/jarvis_foundry/evolution/shadow.py (new);
  tests/foundry/test_evolution_shadow.py (new, 10 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 26 + migration 26);
  src/jarvis_foundry/projections.py (shadow.run_started/completed/
  failed + live_effect/blocked_connector attempt handlers,
  PROJECTION_TABLES); src/jarvis_foundry/reconcile.py (key map);
  src/jarvis_foundry/database.py (row/all/count allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 10 new S6 tests + S1-S5 regression (96) + FULL SUITE 821 passed
  EXIT=0 (325.03s; var/evidence/v6-s6/suite-v6-s6-full.txt,
  sha256 e9c2f99a9e326b807c80f49e3f01d26a9c7c79052f4c938d7235432739ee3386)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s6/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v26, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); shadow
  isolation is harness-level (decision functions are in-process with an
  injected effect sink) — a shadow candidate that bypasses the sink
  entirely (direct filesystem/DB writes from inside decision_fn) is
  out of scope for S6 and is the S7 canary controller's fault-injection
  surface to bound; divergence compares the FIRST proposal per case
  (documented)
NEXT AUTONOMOUS ACTION: S7 — Supervisor-owned canary controller
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/shadow.py`:

- `ShadowSink` — the ONLY effect boundary a shadow candidate touches:
  `propose(decision, expected_effect, cost_usd, kind)` records;
  `use_capability(...)` ALWAYS refuses: live kinds raise
  _ShadowLiveEffect (run FAILED, shadow.live_effect_attempted
  recorded), blocked-connector kinds (browser./email./sms./voice./
  search./calendar.) raise _ShadowBlockedConnector (run FAILED,
  shadow.blocked_connector_attempted recorded). Zero live effects by
  construction.
- `shadow_run(db, *, improvement_id, candidate_release_id,
  decision_fn, inputs_dir, controller, baseline_decisions=None,
  live_marker=None, stop_check=None)` — validates improvement +
  release manifest; copies inputs into shadow-scratch (per-run,
  hashed and bound as input_hash); STOP check (injected callable or
  the runtime-home EMERGENCY_STOP sentinel) freezes the run BEFORE any
  proposal (shadow.run_failed "stop active"); per-case decision_fn
  with a fresh sink; proposals secret-scanned and recorded as
  shadow.decision_proposed; divergence vs baseline (first proposal per
  case: decision/expected_effect/cost) recorded in
  shadow.run_completed; live surface (runtime DB + optional live
  marker) hashed before/after and proven unchanged (zero_live_effects).

Projection `shadow_runs` (migration v26, status CHECK RUNNING/
COMPLETED/FAILED, zero_live_effects/stop_observed/live_attempts/
blocked_attempts/input_hash/divergences_json) wired into
PROJECTION_TABLES, the reconcile key map, and database row/all/count
allowlists. Handlers validate mandatory keys and refuse completed/
failed transitions on unknown runs inside the append transaction.

## Tests (10)

propose-only shadow COMPLETES with zero live effects (DB + marker
byte-identical, 3 decisions); live capability attempt fails the run
with the attempt recorded; blocked connector attempt fails with its
own class; STOP sentinel freezes the run (stop_observed, zero
decisions) and cleared STOP lets the run proceed; divergence recorded
(decision + expected_effect kinds); no divergence -> empty list;
unknown improvement / unknown release refused; tampered inputs bound
honestly (input_hash of the actual bytes used); secret proposal
refused with zero decision events; projection/schema integrity (pinned
to the imported SCHEMA_VERSION constant >= 26).

## 8. Verifier findings — repair record (deleg_1b787321, PASS)

Verdict JSON: var/evidence/v6-s6/verdict-deleg_1b787321.json,
sha256 e3f51971fb66d15671b9bdd38efb7b09b0b7ae9a829fa9f7f10f207eff4537ee.
13 probes; Z1-Z7 PASS.

| Severity | Finding | Resolution |
|---|---|---|
| MEDIUM | Pre-attempt proposals were DROPPED on a capability attempt (the proposal flush loop was skipped on the exception path): decisions_count=0 despite 3 proposals before the attempt. | REPAIRED: _flush_proposals runs on EVERY path (success + both exception paths) — pre-attempt proposals are recorded (history preserved), then the attempt event, then run_failed. Regression: test_pre_attempt_proposals_preserved. |
| LOW | Cases beyond the baseline length were recorded as FULLY divergent (kinds decision/expected_effect/cost from comparing against {}), asserting differences where no baseline exists. | REPAIRED: missing baseline entries are recorded as kind "uncomparable" (no asserted difference). Regression: test_missing_baseline_uncomparable. |
| LOW | Empty-inputs refusal fired AFTER shadow.run_started — orphaned RUNNING row with no terminal event. | REPAIRED: the file-count validation now runs BEFORE run_started (no row is created on refusal); the check counts FILES (a dir with only a subdir now refuses). Regressions: test_empty_inputs_no_orphan_row, test_nested_inputs_are_cases. |
| LOW | Z1's DB-file byte-identity is WAL-mediated: appends sit in the uncheckpointed WAL, so the main DB file can be byte-identical even with new events; a wal_checkpoint between the hashes would flip zero_live_effects to 0. | ACCEPTED documented boundary: the sink refusal IS the zero-effect guarantee; the DB-file hash is belt-and-braces (WAL-contingent); the live MARKER file hash is the strong external proof. Documented in the S6 doc. |
| INFO | (b) Direct filesystem writes from inside decision_fn bypass the sink: a write to the live marker is detected via hash (zero_live_effects=0, run still COMPLETES); a write to an unhased path is undetected. | ACCEPTED documented boundary (harness-level isolation; the sink is the contract; S7 canary bounds candidate processes). |
| INFO | (e) Nested subdir files were copied+hashed but NOT executed as cases; a dir containing only a subdir passed the zero-files check. | REPAIRED with the file-count validation: every FILE under the inputs tree is a case (nested included). |
| INFO | (c) Distinct run ids/rows per run. (Z5-note) A secret in a LATER case keeps earlier clean proposals (fail-closed on the violating proposal, earlier decisions recorded). | ACCEPTED: sequential fail-closed history. |

Round-2: fresh verifier with A/B discrimination dispatched after this
commit.
