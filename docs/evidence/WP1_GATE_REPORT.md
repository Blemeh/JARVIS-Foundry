# WP1 Gate Report — Durable Domain And Event Store

Verdict: **PASS**
Gate start (this run): 2026-08-11T19:13+02:00 (independent verifier dispatch)
Gate close: 2026-08-11T19:35+02:00
Gate document: `docs/architecture/03_V1_AUTONOMY_KERNEL_SPEC.md` §16 WP1 and
`docs/planning/V1_BUILD_SEQUENCE.md` WP1
Gate contract: `docs/planning/CURRENT_WORK_PACKAGE.yaml` (WP1)
Evidence file: `docs/evidence/WP1_FOUNDATION.md`
Incident: `docs/evidence/INCIDENT_2026-08-11_WP1_STAGING.md`

## Entry State

- WP0 PASS (`docs/evidence/WP0_BASELINE.md`); WP1 the only ACTIVE package.
- START BUILD durable (config/start_record.yaml; system_state start_active=1).
- Frozen headless-run work reconciled and treated as unverified until
  independently reproduced (see incident record).

## Mandatory Criteria — Final Verdicts

| ID | Criterion | Verdict | Evidence |
|---|---|---|---|
| C1 | Duplicate and concurrent event delivery produce one effect | PASS | Dedup on idempotency_key inside BEGIN IMMEDIATE compares event_type+payload_hash; 24-thread race = 1 event; verifier's 8-thread different-payload race = 1 winner + 7 EventConflict; retry-with-new-id dedup = 1 row |
| C2 | Invalid hashes and invalid transitions cannot commit | PASS | Forged hash rejected pre-write (0 events); PLANNED->COMPLETE rolls back event+projection; verifier full-row snapshot equality before/after failed append. Soft spots (unknown event types; lifecycle UPDATE on missing row) recorded as residual risks RK-WP1-01/02 — silent-skip is deliberate for mixed-version ledger replay/rollback tolerance |
| C3 | Projections rebuild deterministically from verified events | PASS | rebuild_projections twice = byte-identical rows across all 8 projection tables incl. budget accumulation; reconcile consistent=true on seeded DB and on the migrated real staging copy (11 events, 0 mismatches); verifier built real v2 DB -> v3, consistent |
| C4 | Verified backup restores ledger and projections | PASS | backup_to (sqlite online backup + destination quick_check) -> mutate -> restore_from (mode=ro source, quick_check, re-hash of every event) -> exact prior state; verifier 9-event roundtrip consistent |
| C5 | Schema migration and recovery evidence are durable | PASS | v1->v3 and v2->v3 with seeded data preserved; failure-injection mid-version rollback (DDL + marker) with heal; real subprocess TerminateProcess kills in both crash modes (event-between-statements, migration-mid-script) independently reproduced by the verifier; splitter correct on semicolons-in-literals; migrate(target=) guards unknown versions; idempotent re-init |
| C6 | Reconciliation diagnostics: exact mismatch output, read-only | PASS | Exact table/key_column/key/column/expected/actual; missing/unexpected/integrity-failure reporting; double-reconcile byte-identical (no mutation); version-mismatch guard on pre-v3 DBs. Adversarial hole found by verifier (TypeError on NULL REAL column) REPAIRED + regression test `test_reconcile_reports_null_float_mismatch_without_crashing` |
| C7 | Atomic artifact hashing/storage below project-local var | PASS | temp+fsync+os.replace atomicity; sha256 sidecars; tamper rejection; name validation. Adversarial hole found by verifier (bundle_id='..' escapes base) REPAIRED via `_validate_bundle_id` + regression tests `test_unsafe_bundle_ids_are_rejected`; real bundle artifacts verify |
| C8 | Combined baseline: Foundry + selected Hermes tests | PASS | Foundry 55 passed; Hermes test_estop.py 24 passed; state/WAL/health suite 317 passed, 8 skipped (isolated HERMES_HOME) — all re-run fresh on the frozen state |

## Fault Injection (WP1-scope)

All WP1 fault injections PASS (crash modes F-crash-1..3, failure injection
F-fail-1, tamper F-tamper-1, drift F-drift-1) — details in
`docs/evidence/WP1_FOUNDATION.md`; independently reproduced by the verifier
(manual subprocess kills outside pytest).

## State Reconciliation

- Migrated real staging copy: consistent=true, 11 events, 0 mismatches.
- Canonical databases: hashes re-checked at gate close — all unchanged
  (staging-foundation schema 2/11 events, cli-proof schema 1, final-proof
  schema 2, wp1-staging schema 3/0 events; quick_check ok on all).
- Evidence bundle `var/evidence/wp1-gate/` regenerated on the final state;
  all artifacts verify against sha256 sidecars.

## Frozen-State Re-Verification (closing the moving-target concern)

Git commits are not authorized by the WP1 package contract, so the verified
state was frozen cryptographically instead:

- `verified-state-snapshot.json`: sha256 of all 55 source/test files +
  pyproject.toml (snapshot digest `af9051fb…`, stored with atomic sidecar).
- Fresh runs against the frozen snapshot: Foundry suite exit 0 (55 passed);
  Hermes estop 24 passed; canonical DB recheck all unchanged.
- `verification-summary.json` records the complete battery.
- Any future audit can re-hash the working tree and compare against the
  snapshot digest to prove the verified state is byte-identical.

## Independent Verification

- Verifier: fresh isolated subagent session (deleg_8b2e316c), adversarial
  mandate, 43 API calls, 780s; wrote and ran its own 22-test scratch suite;
  independently reproduced both crash modes and both adversarial holes.
- Verdict: PASS on all 8 criteria.
- Independence limitation (recorded per architecture contract §14.3):
  same provider family as builder (deepseek-v4-flash/opencode-go), fresh
  session with no builder reasoning. The two adversarial holes it found were
  repaired by the executive within ~1 minute (visible in its live transcript)
  and are covered by regression tests in the frozen snapshot.

## Residual Risks (accepted, non-blocking)

- RK-WP1-01: unknown event types commit to the ledger with no projection.
  Intentional: mixed-version ledger replay during rollback must not crash the
  rebuild. A strict event-type registry belongs with the WP4 adapter (single
  writer) and is tracked there.
- RK-WP1-02: lifecycle UPDATE events on nonexistent rows no-op silently.
  Same rollback-tolerance rationale. Deterministic raise on 0-row updates
  would break replay of truncated/mixed-version ledgers; deferred with RK-WP1-01.
- V1 seven-day proving mission/resource remains NOT_SELECTED (WP8 scope).

## Next Autonomous Action

WP1 PASS recorded. Update `PROJECT_STATE.md` (WP1: PASS, active package WP2),
create the WP2 work-package record, and begin the WP2 independent-Supervisor
and STOP gate. WP2 remains INACTIVE until this gate verdict is durable.
