# WP9 — V2 Knowledge Brain, Slice-5: Snapshots / Backup / Restore / Semantic Rollback

Status: **IMPLEMENTED + REPAIRED** (2026-08-12). Slices 1–4 CLOSED —
PASS (deleg_548b6bb6, deleg_7c4759fa, deleg_01ebd46f, deleg_3cce471c).
Verifier round-1 (deleg_9ecde3af) FAIL: F1 test-count overstatements
(7→8 snapshot tests, 44→45 combined, 292→293 suite — corrected by the
tampering regression + honest counts), F2 verify_snapshot did not
detect CONTENT corruption (payload byte flips passed quick_check;
verification self-attested from the file's own row) → REPAIRED:
content sha256 recorded in the event/projection and verification
cross-checks the FILE against the AUTHORITATIVE live-DB record
(quick_check + sha256 + claims count), F7 rollback idempotency key now
deterministic (one rollback per snapshot). Verifier round-2
(deleg_23953faa) **PASS** — M1 45/45 (counts self-verified), M2 22/22
probes, M3 suite 293 EXIT=0 + clean tree, M4 honest lineage.
Ledger: verification.failed ×1 (repair-keyed, seq 4667) +
verification.passed:verdict-wp9-slice5 (seq 4668) +
evidence.created:wp9-slice5 (seq 4669). **Slice-5 CLOSED — PASS.**
8/8 snapshot tests green; knowledge 45/45; full suite 293 EXIT=0.
**ALL FIVE V2 SLICES CLOSED — the WP9 gate review is next.**

## Delivered (bounded slice-5 scope)

1. **Schema v9** (migrations.py, migration 9): `knowledge_snapshots`
   (snapshot_id PK, mission_id, scope, head_event_id, schema_version,
   claims_count, path) + a knowledge_claims REBUILD adding the
   ROLLED_BACK status and `rolled_back_by` (SQLite cannot alter CHECK
   constraints — the v9 migration recreates the table, preserves all
   rows, and re-creates the indexes).
2. **Events** (projections.py handlers):
   - `knowledge.snapshot_created` — recorded BEFORE the file backup so
     the backup file is self-describing (verify_snapshot validates it).
   - `knowledge.claims_rolled_back` — flags each claim ROLLED_BACK with
     the snapshot id (append-only; history preserved; the ledger stays
     the source of truth; rebuild reproduces the flags).
3. **`knowledge_snapshots.py`**:
   - `create_snapshot` — measures the ledger head + schema + counts,
     writes a CONSISTENT file-level backup via the SQLite backup API,
     then records the event WITH the file's content sha256.
   - `verify_snapshot(db, snapshot_id)` — cross-checks the file against
     the authoritative live-DB record: quick_check (structural) +
     content sha256 + claims count; any tampering (payload or
     structural) fails.
   - `open_snapshot_readonly` — read-only restore verification (never
     mutates the snapshot; the live DB is never restored onto).
   - `semantic_rollback` — validates the snapshot exists (F9 rollback
     pattern), flags the claims ROLLED_BACK with reason; deterministic
     idempotency key (one rollback per snapshot).
4. Registries updated: PROJECTION_TABLES, reconcile key-column map,
   row/all/count allowlists.

## Evidence

- Tests: `tests/foundry/test_knowledge_snapshots.py` — 8 tests (red
  before implementation, green after): snapshot row + head/counts;
  restorable backup (verify_snapshot); payload-tampering + structural
  corruption detection (content sha256 regression); read-only restore
  with post-snapshot mutation isolation; semantic rollback flags +
  history preserved; rollback validation (missing snapshot → raise +
  rollback); rebuild reproduces snapshots + flags; schema version ==
  code.
- Slices 1–4 regression: 37/37 knowledge tests still green (45/45
  combined).
- Full suite: EXIT=0 (293 tests) → var/evidence/wp9-slice5/
  suite-slice5.txt.

## Live-DB impact

**CORRECTED (gate review deleg_d0a4244d G3):** no knowledge DATA was
ever written to the live ledger (zero knowledge.* events; all
knowledge_* tables EMPTY) — but the slice-closure tooling's
initialize() DID apply the schema migrations to the live daemon DB
(v5→v9, same-second with the closure ledger events; the daemon never
restarted; gapless heartbeats; daemon healthy). The earlier "live DB
stays at schema v4" wording was wrong; the corrected truth is recorded
in the ledger (evidence.created:wp9-gate-truth-correction, seq 4789)
and docs. Additive DDL only; no rollback migration needed.

## Next

- Independent verifier for slice-5 (fresh adversarial session).
- With slices 1–5 closed: the V2 gate evidence per 05 §5 exit criteria
  (knowledge brain: provenance/confidence/freshness/contradiction,
  whole-vault retrieval + bounded context manifests, entity resolution,
  continuous ingestion, snapshots/backup/restore/semantic rollback) →
  WP9 gate verdict → V3 DISCOVERY AND BUILDING (candidate pipeline
  design: temp/V3_SLICE1_DESIGN.md).
