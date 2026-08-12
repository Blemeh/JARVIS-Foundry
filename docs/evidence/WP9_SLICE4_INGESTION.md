# WP9 — V2 Knowledge Brain, Slice-4: Continuous Ingestion

Status: **IMPLEMENTED + REPAIRED** (2026-08-12). Slices 1–3 CLOSED —
PASS (deleg_548b6bb6, deleg_7c4759fa, deleg_01ebd46f). Verifier
round-1 (deleg_7eacf4e9) FAIL: G1 schema-version test pinned to v7
(broke at the v8 migration), G2-b overlapping delta batches crashed
with EventConflict (per-run ingestion uuid leaked into claim payloads →
unchanged docs re-appended with different payload hashes; retries
poisoned), G3 suite artifact EXIT=1, G4 wording inaccuracies. →
REPAIRED: a2e25e1 (de-pinned schema assertions to
version == SCHEMA_VERSION), 8152ac6 (fully deterministic claim payloads
— per-doc content hashes in source.run_id/event_id; unchanged docs
dedup, delta-only claims, retries safe) + regressions
test_overlapping_delta_batch_no_conflict + test_retry_after_failure_is_safe.
Verifier round-2 (deleg_a18133f6) **PASS** on H1–H3, FAIL on H4 (doc
lineage missing) → docs repairs 9fbfc14 + b21f4ab (lineage + count
285 collect-verified) → round-2b (deleg_47a4d674) J1 count FAIL →
b21f4ab fix → round-2c (deleg_3cce471c) **PASS**. Ledger:
verification.failed ×1 (repair-keyed, seq 4476) +
verification.passed:verdict-wp9-slice4 (seq 4477) +
evidence.created:wp9-slice4 (seq 4478). **Slice-4 CLOSED — PASS.**
10/10 ingestion tests green; full suite 285 EXIT=0.
Next: slice-5 (snapshots/backup/restore/semantic rollback).

## Delivered (bounded slice-4 scope)

1. **Schema v8** (migrations.py, migration 8): `knowledge_ingestions`
   (ingestion_id PK, mission_id, source_type, source_ref, source_hash,
   status QUEUED/RUNNING/COMPLETED/FAILED, claims_count, errors_json)
   with source-hash + mission indexes.
2. **Events** (projections.py handlers): `knowledge.ingestion_queued`
   (INSERT QUEUED), `knowledge.ingestion_completed` (UPDATE COMPLETED +
   counts), `knowledge.ingestion_failed` (UPDATE FAILED + errors).
3. **`knowledge_ingestion.py`** — `ingest_documents(db, mission_id,
   source_type, source_ref, docs)`:
   - deterministic per-document claim ids (content hash) — identical
     re-ingestion creates identical ids (deduped by event keys);
   - whole-batch source_hash dedup: a completed ingestion with the same
     hash is a duplicate (no new claims, recorded as such);
   - CHANGED content → NEW claims; history is never rewritten;
   - claim payloads are fully deterministic (per-doc content hashes in
     source.run_id/event_id) — overlapping delta batches dedup the
     unchanged docs instead of conflicting, and a failed batch is
     retryable (already-applied claims dedup on retry);
   - batch recorded as queued → completed with counts (or failed with
     errors; a failed batch records NO completion and stays QUEUED —
     partial claim events from a failed run are deduped on retry).
4. Registries updated: PROJECTION_TABLES, reconcile key-column map,
   row/all/count allowlists.

## Evidence

- Tests: `tests/foundry/test_knowledge_ingestion.py` — 10 tests (red
  before implementation, green after): queue row; completed-hash
  idempotency; batch → 3 claims + counts; idempotent re-ingestion;
  changed doc → new claim (old preserved); failure → FAILED + errors;
  rebuild determinism; schema version == code; overlapping delta batch
  (no conflict); retry after failure (safe dedup).
- Slices 1–3 regression: 27/27 knowledge tests still green (37/37
  combined).
- Full suite: EXIT=0 (285 tests) → var/evidence/wp9-slice4/
  suite-slice4.txt.

## Live-DB impact

NONE — all slice-4 events ran on throwaway temp DBs in the tests; the
live daemon DB stays at schema v4 until its next natural initialize.

## Next

- Independent verifier for slice-4 (fresh adversarial session).
- Slice-5: snapshots/backup/restore/semantic rollback — then the V2
  gate evidence (05 §5 exit criteria) and V3 start.
