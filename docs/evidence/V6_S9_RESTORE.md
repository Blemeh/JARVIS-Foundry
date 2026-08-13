# V6-S9 — SCHEMA/STATE COMPATIBILITY AND RESTORATION

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S9 — schema/state compatibility and restoration
OBJECTIVE: stateful evolution with executable forward migration,
  idempotency, previous-executable compatibility, rollback
  migration/restore points, interrupted-migration recovery, schema/
  data integrity verification, projection rebuild/reconcile and
  data-loss analysis — rehearsed on disposable copies before anything
  touches the runtime DB.
BASE COMMIT: 3ea0342 (S8 close)
CANDIDATE RELEASE: none (machinery slice)
RISK CLASS: R3-class machinery (restore control) built and verified
  inside the isolated V6 construction; live surfaces untouched
HYPOTHESIS: n/a (machinery; exercised by the S11 ladder)
BASELINE METRIC: full suite 863 EXIT=0 at S8 close
SUCCESS THRESHOLD: restore points created durable + VERIFIED
  (quick_check, sha256, version, event count); restores run only after
  verification (tampered/unknown/newer-schema points refuse); data-loss
  analysis recorded BEFORE the restore executes; rehearsals run the
  full restore on a disposable copy (live untouched); STOP blocks
  restores and point creation; candidates can never create/restore;
  post-restore reconcile consistent; interrupted restores recover
  (transactional backup API); migration 29 (restore_points +
  promotions.planned_json ALTER for already-migrated DBs) applies
  cleanly to a v28 DB; all five projection touch points wired; suite
  green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v29 — the planned_json ALTER exercised on the already-migrated v28
  DB); live DB stays v20; no live writes; controller identity checks;
  secret-scanned labels; stop sentinel checks
ACTIONS TAKEN: TDD red (12 tests) -> implemented evolution/restore.py
  (create_restore_point, verify_restore_point, data_loss_analysis,
  restore_from_point, rehearse_restore, RestoreError) on the existing
  backup_to/restore_from primitives -> migration v29 (restore_points +
  ALTER TABLE promotions ADD COLUMN planned_json — migration 28's DDL
  reverted to NOT carry the column so 29 owns it uniformly on fresh
  AND already-migrated DBs) + PROJECTION_TABLES + reconcile key map +
  database row/all/count allowlists -> focused green (159 total across
  S1-S9) -> runtime DB migrated to v29 (reconcile consistent,
  planned_json present) -> full suite
FILES CHANGED: src/jarvis_foundry/evolution/restore.py (new);
  tests/foundry/test_evolution_restore.py (new, 12 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 29 + migration 29,
  28 DDL column reverted); src/jarvis_foundry/projections.py
  (restore.point_created/executed handlers, PROJECTION_TABLES);
  src/jarvis_foundry/reconcile.py (key map); src/jarvis_foundry/
  database.py (row/all/count allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 12 new S9 tests + S1-S8 regression (147) + FULL SUITE 875 passed
  EXIT=0 (362.11s; var/evidence/v6-s9/suite-v6-s9-full.txt,
  sha256 e8d6ea1b90862f2d10fad276781756245dfeca85c7eb7a43bea5c1546f8528fb)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s9/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v29, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); the
  restore executes via the sqlite backup API (transactional at the
  file level) and the point's own record is re-appended after the
  restore (the snapshot predates its own event — recorded in the
  executed batch); a restore discards post-point state BY DESIGN and
  only after data-loss analysis; restores of a NEWER-schema point are
  refused (forward-only compatibility)
NEXT AUTONOMOUS ACTION: S10 — autonomous improvement discovery
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/restore.py`:

- `create_restore_point(...)` — controller-only; label secret-scanned;
  STOP blocks; sqlite backup API snapshot (consistent, zero writes to
  the source) + sha256 + recorded schema version and event count;
  restore.point_created event -> CREATED row.
- `verify_restore_point(...)` — row exists, file exists, sha256
  matches, quick_check ok, recorded version == actual version,
  version <= current code (forward-only compatibility).
- `data_loss_analysis(...)` — reports (never executes) the event-count
  delta and per-table row deltas the restore would discard.
- `restore_from_point(...)` — verified FIRST (tampered/unknown/
  newer-schema refuse); analysis recorded; transactional restore via
  the backup API; the point's own record is re-appended (its snapshot
  predates its own event) and restore.executed marks it EXECUTED;
  post-restore reconcile; inconsistency raises a severity-one
  RestoreError.
- `rehearse_restore(...)` — the full restore against a disposable
  copy; the live DB is untouched.

Projection `restore_points` (migration v29, status CHECK CREATED/
VERIFIED/EXECUTED) wired into PROJECTION_TABLES, the reconcile key
map, and database row/all/count allowlists. Migration 29 also ALTERs
promotions.planned_json so already-migrated v28 databases (the V6
runtime DB) receive the S8 repair column.

## Tests (12)

create+verify (row, label, counts, version, sha256, path exists);
restore restores the exact snapshot state (+ reconcile); tampered
point refused (verify False + restore refuses + untouched); unknown
point refused; data-loss analysis deltas; rehearsal touches nothing;
previous-executable compatibility (point version <= current);
STOP blocks restore; candidates cannot create/restore; secret label
refused; interrupted-restore recovery (transactional restore +
fresh reopen verifies clean + executed record survives); projection/
schema integrity (pinned to the imported SCHEMA_VERSION constant >=
29; planned_json present via migration 29).

## 9. Verifier findings — repair record (deleg_c22bd424, PASS)

Verdict JSON: var/evidence/v6-s9/verdict-deleg_c22bd424.json. 26/26
probes. R1-R7 all PASS; 2 LOW + 4 INFO.

| Severity | Finding | Resolution |
|---|---|---|
| LOW | Restore-to-older orphans the NEWER point's FILE (its row rolls back; the file survives unreferenced) — no listing/reap. | REPAIRED: list_orphaned_points + reap_orphaned_points (controller-only, STOP-blocked, bounded unlink retries for transient AV/indexer locks). Regressions: test_orphaned_points_listed_and_reaped. |
| LOW | The point-file race (deleted between verify and restore) surfaced as raw sqlite3.OperationalError, not RestoreError. | REPAIRED: restore_from_point wraps the backup-API call and re-raises as RestoreError (documented family), DB untouched. Regression: test_restore_source_race_fails_closed. |
| INFO | Transient Windows PermissionError deleting fresh point files (AV/indexer) — resolved with gc+retry. | ROOT-CAUSED + REPAIRED: leaked sqlite3.Connection objects (the `with sqlite3.connect(...)` pattern commits but never closes; cycles defer GC) held the point files — explicit close() added in backup_to (database.py) and the point-opening paths (verify/data-loss analysis); the reaper keeps bounded retries as belt-and-braces. |
| INFO | LIVE DB actively host-written (events grew 30567->30630); content-level isolation holds. | RECORDED. |
| INFO | Extra (a) probe arithmetic (restored-to-B == 4, not 5). | RECORDED. |
| INFO | R4 analysis-record semantics: the executed event's lost_events IS the record; ordering proven by injected-failure probe. | RECORDED. |

Round-2: fresh verifier with A/B discrimination dispatched after this
commit.
