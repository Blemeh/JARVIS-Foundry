# WP9 — V2 Knowledge Brain, Slice-2: Retrieval + Bounded Context Manifests

Status: **IMPLEMENTED** (2026-08-12; 8/8 tests green; independent
verifier <PENDING>). Slice-1 CLOSED — PASS (deleg_548b6bb6; ledger
3731–3735).

## Delivered (bounded slice-2 scope)

1. **Schema v6** (migrations.py, migration 6): `knowledge_manifests`
   (manifest_id PK, mission_id, step_id, objective_hash, active_count,
   stale_count, conflict_count, truncated, manifest_hash, last_event_id,
   updated_at).
2. **`knowledge_retrieval.py`**:
   - `retrieve(connection, mission_id, claim_type?, status=ACCEPTED,
     fresh_only=True, limit<=500)` — deterministic ordering (confidence
     DESC, recency), size-capped; fresh-only excludes expired claims.
   - `build_context_manifest(connection, mission_id, objective,
     max_chars=8000)` — objective hints select claim_type sections from
     the registry (causal-analysis/fact/constraint/preference/decision);
     ACTIVE = ACCEPTED+fresh (hint-filtered, deduped); STALE = expired
     ACCEPTED claims (never dropped, listed explicitly); CONFLICTS =
     live contradiction rows for the mission's claim types; serialized
     cap with EXPLICIT truncation (flag + counts, never silent);
     `manifest_hash` = sha256 over the stable payload (excludes the
     wall-clock generated_at — determinism contract).
3. **Event**: `knowledge.context_manifest_generated` — audit record
   (hash + counts, NOT the full text — bounded ledger growth; the full
   manifest is reproducible from the projection). Projection +
   PROJECTION_TABLES + reconcile key-column map + row() allowlist
   updated.

## Evidence

- Tests: `tests/foundry/test_knowledge_retrieval.py` — 8 tests (red
  before implementation, green after): fresh-ACCEPTED retrieval,
  type filter + confidence ordering + limit, objective-hint section
  selection, conflict listing (both ids), STALE section never drops,
  explicit truncation, manifest-event projection + deterministic
  rebuild, hash/content determinism.
- Staged smoke (NEVER the live DB): var/evidence/wp9-slice2/
  (staging.db + slice2-smoke-summary.json) — retrieval ordering,
  hint selection, stale listing, manifest-event round-trip with
  matching hash.
- Slice-1 regression: 10/10 knowledge tests still green (18/18 combined).
- Full suite: <PENDING> (expect EXIT=0, 266 tests).

## Live-DB impact

NONE — all slice-2 events ran on the throwaway staging DB. The live
daemon DB stays at schema v4 until its next natural initialize
(deployment path: v5 then v6 apply together at that point).

## Next

- Independent verifier for slice-2 (fresh adversarial session).
- Slice-3+ per the WP9 spec: entity resolution, continuous ingestion,
  snapshots/backup/restore — then the V2 gate evidence (05 §5 exit
  criteria).
