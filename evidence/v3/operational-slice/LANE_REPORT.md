# Operational Slice — Final Lane Report

Lane: candidate/overnight-v3-operational-slice
Base: ceb90d7d201b4775757cae0f7e03ae1e2d6f22e2 (origin/main, verified exact)
Worktree: C:\temp\JARVIS-Overnight-V3-Operational-Slice

## Delivered

### WP-A — snapshot.discovery projection + real adapter mapping
- `_discovery_projection` in `src/jarvis_foundry/command_center.py`:
  additive, read-only, bounded, parameterized SQL over canonical v36
  rows/events. States: FRESH / PARTIAL / EMPTY_LEDGER / UNSUPPORTED_SCHEMA.
  Honest degradation (null + candidate["partial"]) on malformed JSON; no
  cost_window (unfabricated); maintenance_due always null (no durable
  surface at this schema). Flag carried as explicit parameter:
  ON / OFF / UNREGISTERED.
- Contract first: `PAYLOAD_CONTRACT.md` written BEFORE implementation.
- UI: new `discoveryMapper.ts`; `realClient.ts getDiscovery()` maps the
  backend section with NO fixture fallback; absence stays
  `{available:false, reason:"PROJECTION_NOT_AVAILABLE"}`. Unknown additive
  enum values render verbatim without crashing. `snapshotTypes.discovery?`
  optional. Minimal sanctioned edits to `v3Models.ts` (nullable cycle
  counts for STOPPED cycles; V3CycleOutcomeOrUnknown) and `Discovery.tsx`
  (typed badge lookup fallback only).
- TDD throughout: 17 backend projection tests RED→GREEN; 3 adapter tests
  RED→GREEN.

### WP-B — automatic outcome/continuation closure
- Gap PROVEN at base: nothing called `v3_outcomes.record_outcome`; worker
  loop recorded memory observations and executive calibration only.
- Implemented single authoritative seam: `v3_outcomes.reconcile_mission_
  outcome()` + `reconcile_pending_outcomes()` catch-up scan; wired at ONE
  call site (run_cycle terminal branch), gated on discovery_cycle_enabled,
  PASSED==terminal only. Exactly-once via outcome_id PK + ledger
  idempotency key. Predicted (executive prediction event) vs actual
  (`outcome_from_mission` durable truth) stay DISTINCT; UNKNOWN honest.
- 5 integration tests through the REAL run_cycle + REAL lab-scripted
  Hermes stack; RED observed before implementation.

### E2E harness (WP-C part 1)
- `scripts/v3_operational_e2e.py`: fully disposable (fresh DB + fresh git
  repo each run under var/overnight/v3-operational-slice/), drives empty →
  flag-off → generate+score → flag-on unified cycle+dispatch (PASSED) →
  AUTOMATIC closure → honest continuation cycle (duplicate prevention
  asserted). Regenerates the UI contract fixture from live output.
- Automated ledger reconciliation: 10/10 field checks pass on every run
  (`E2E_LEDGER_RECONCILIATION.json`).

### WP-C — browser proof
- Real backend served on port 4611; production UI build
  (VITE_JARVIS_MODE=live, same-origin /api proxy via vite preview :4612).
- Discovery route rendered LIVE (DATA SOURCE: LIVE): flag banner OFF,
  portfolio KPIs, policy version, current cycle e2e-cycle-2 WAITING
  (NO_ELIGIBLE_CANDIDATES), recent cycles incl. e2e-cycle-1 COMPILED,
  executive selection with mission lineage, team & verifier PASS,
  predicted-vs-actual distinct, candidate card with score/dimensions/
  lineage. Field-for-field match vs direct SQL documented in
  `WP_C_BROWSER_PROOF.md`.
- Generated contract fixture + mapper-drift detection both directions
  (backend test re-parses fixture; UI test maps fixture payload).
- Bounds proven: 300 candidates × 200 cycles × 150 outcomes → 49.6 ms
  build, 214 KiB snapshot; asserted limits (≤25/≤10/≤50, <2 s, <1.5 MiB).

### WP-D — reliability pass
- Concurrent snapshot readers during reconciliation bursts: consistent,
  exactly-once holds (test_concurrent_snapshot_reads_during_outcome_
  reconciliation).
- Mixed-offset timestamps: pinned deterministic ordering behavior;
  cycle selection follows ledger sequence, NOT timestamp strings
  (discriminating assertions).
- Hostile text (SQLi/script/control chars) treated as data; JSON-safe;
  tables survive. Legacy-schema snapshot degrades honestly. Repeated DB
  reopen proves no handle leaks. Windows read-only git files handled by
  harness cleanup.

## Verification results (exact)

| Gate | Result |
|---|---|
| Backend focused (discovery/closure/bounds/reliability suites) | 33 passed |
| Full backend suite tests/foundry | ~1653 collected, 1652 passed, 1 failed |
| └ failure analysis | test_installer.py::test_task_restarts_after_hard_kill_via_repeating_trigger — Windows scheduled-task timing flake; PASSED when re-run on BASE checkout ceb90d7; installer domain untouched by lane |
| UI unit suite (vitest run) | 13 files, 57 passed, 0 failed |
| UI typecheck tsc -b --noEmit | clean |
| UI lint eslint --max-warnings 0 | clean |
| UI production build vite build | success (chunk-size warning pre-exists) |

## Safety

PRODUCTION_TOUCHED: NO. LIVE_DB_TOUCHED: NO. FLAG_CHANGED_IN_PROD_CONFIG: NO
(flag exercised only in disposable lane configs/tests). PUSHED: NO.
Only lane-owned processes used; orphaned vite preview PIDs from this lane
were identified by command line before termination. No other worktrees,
branches, or sessions touched.
