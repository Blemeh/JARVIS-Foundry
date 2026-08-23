# WP-B — Automatic Outcome & Continuation Closure Proof

## The gap at base ceb90d7 (source-verified)

- `runtime/worker_loop.py` terminal branch recorded ONLY:
  - `_advisory_accepted_outcome_writeback` (Tier-C memory observation), and
  - `_record_executive_outcome` (executive calibration).
- Nothing anywhere in `src/` called `v3_outcomes.record_outcome`.
  (`grep -rn "record_outcome|discovery.candidate_outcome_recorded" src/`
  shows only definitions, the legacy `discovery_candidates.record_outcome`
  variant, and the projections handler.)
- Therefore every outcome at base required a manual/lab invocation of
  OutcomeService. The prior lab report's claim was CORRECT.

## Failing-first proof

`tests/foundry/test_v3_operational_closure.py::test_terminal_pass_automatically_records_outcome_exactly_once`
drove the REAL `run_cycle` through the REAL hybrid worker stack
(`lab_scripted_hermes=True`, the runtime's own provider-free lab mode) to a
PASSED terminal and asserted exactly one
`discovery.candidate_outcome_recorded`. At base it failed with
`0 == 1` ("terminal PASSED must auto-record exactly one outcome").
RED observed before implementation; GREEN after.

## Implementation (single authoritative seam)

- `v3_outcomes.reconcile_mission_outcome(database, mission_id=...)`:
  - candidate-lineage check (`created_from = candidate:{id}`);
  - idempotency pre-check on `candidate_outcomes.outcome_id =
    {candidate}:immediate`;
  - terminal truth gate: mission row state COMPLETE **and** a durable
    `mission.completed` event with `outcome=PASSED`;
  - predicted from the recorded `executive.prediction_recorded` event;
    actual from `executive.calibration.outcome_from_mission` (durable rows);
  - delegates to the canonical `record_outcome` (PASSED-only rule,
    UNKNOWN honesty, calibration hook all preserved).
- `v3_outcomes.reconcile_pending_outcomes(...)`: bounded catch-up scan
  (≤100 newest) for candidate-lineage missions in state COMPLETE lacking an
  outcomes row — closes crash windows left by a dead process.
- Wiring: ONE call site — the terminal branch of `run_cycle`, gated
  `config.discovery_cycle_enabled and terminal == "PASSED"`.

## Exactly-once across crash windows (tests prove each)

| Window | Test |
|---|---|
| Terminal result written, process died before closure | test_crash_window_...: canonical events simulate the crash; first scan reconciles ≥1; repeat scans reconcile 0 |
| Process restart / DB reopen | test_restart_reopen_still_exactly_once: fresh FoundryDatabase handle over same file; reconcile_mission_outcome → idempotent=True; pending scan → 0 |
| Repeated cycle after completion | test_terminal_pass_...: second run_cycle records nothing new; executed candidate not re-selected into duplicate work |
| Non-PASSED terminals | test_failed_or_blocked_terminal_never_records: sabotaged artifacts drive FAIL→repair→budget exhaustion; zero outcome events |

Structural guarantee: the ledger append dedupes on idempotency key
`outcome:{candidate_id}:{window}` AND the projection PK is
`candidate_outcomes.outcome_id`; any replay path converges on one row.

## Continuation / next action

Derived from ledger state only (projection `_discovery_projection`):
COMPILED + non-terminal mission → EXECUTE_MISSION:{id}; PASSED + outcome →
RUN_NEXT_CYCLE; PASSED without outcome → RECONCILE_OUTCOME:{id} (debt made
visible); WAITING/BLOCKED/STOPPED → WAIT (+reason); veto/STOP → HOLD.
The e2e harness proves cycle 2 honestly waits with
NO_ELIGIBLE_CANDIDATES and creates no duplicate mission/outcome.

## Flag-off regression

test_flag_off_records_no_new_outcome_events: flag OFF + PASSED dispatch ⇒
zero new outcome events (base behavior byte-for-byte). Additionally, the
flag-off unified-cycle absence is covered by existing
test_worker_loop_flag_off_unchanged_no_cycle_event.
