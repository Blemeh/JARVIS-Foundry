# WP9 — V2 Knowledge Brain, Slice-4: Continuous Ingestion

Status: **IMPLEMENTED** (2026-08-12; 8/8 tests green; independent
verifier <PENDING>). Slices 1–3 CLOSED — PASS (deleg_548b6bb6,
deleg_7c4759fa, deleg_01ebd46f).

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
   - batch recorded as queued → completed with counts (or failed with
     errors; a raised error leaves no partial claims).
4. Registries updated: PROJECTION_TABLES, reconcile key-column map,
   row/all/count allowlists.

## Evidence

- Tests: `tests/foundry/test_knowledge_ingestion.py` — 8 tests (red
  before implementation, green after): queue row; completed-hash
  idempotency; batch → 3 claims + counts; idempotent re-ingestion;
  changed doc → new claim (old preserved); failure → FAILED + errors;
  rebuild determinism; schema v8.
- Slices 1–3 regression: 27/27 knowledge tests still green (35/35
  combined).
- Full suite: <PENDING> (expect EXIT=0, 283 tests) →
  var/evidence/wp9-slice4/suite-slice4.txt.

## Live-DB impact

NONE — all slice-4 events ran on throwaway temp DBs in the tests; the
live daemon DB stays at schema v4 until its next natural initialize.

## Next

- Independent verifier for slice-4 (fresh adversarial session).
- Slice-5: snapshots/backup/restore/semantic rollback — then the V2
  gate evidence (05 §5 exit criteria) and V3 start.
