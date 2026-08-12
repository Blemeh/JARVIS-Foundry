# WP9 — V2 Knowledge Brain, Slice-3: Entity Resolution

Status: **CLOSED — PASS** (2026-08-12; verifier deleg_01ebd46f, FIRST
round — 27/27 tests, all six adversarial probes green, suite 275 EXIT=0,
clean tree; ledger verification.passed:verdict-wp9-slice3 seq 4202 +
evidence.created:wp9-slice3 seq 4203). Slices 1–2 CLOSED — PASS
(deleg_548b6bb6, deleg_7c4759fa). Next: slice-4 (continuous ingestion).

## Delivered (bounded slice-3 scope)

1. **Schema v7** (migrations.py, migration 7): `knowledge_entities`
   (entity_id PK, mission_id, canonical_name, aliases_json, last_event_id,
   updated_at) + `knowledge_claim_entities` (claim_id + entity_id PK,
   role) with an entity-side index.
2. **Events** (projections.py handlers):
   - `knowledge.entity_registered` — canonical name + initial aliases
     (idempotency key per entity_id; changed payload → EventConflict).
   - `knowledge.entity_alias_added` — append-only alias accumulation
     (deduped per alias; missing entity → ValueError + rollback).
   - `knowledge.claim_entity_linked` — validates BOTH the claim and the
     entity exist inside the transaction (F9 rollback pattern);
     INSERT OR IGNORE on (claim_id, entity_id).
3. **`knowledge_entities.py`** (deterministic, no NLP):
   - `resolve_entity(connection, alias)` — exact or canonicalized
     matching (canonicalize_statement reuse) against canonical_name and
     every registered alias.
   - `claims_for_entity(entity_id)`, `entities_for_claim(claim_id)` —
     bounded read-only queries.
4. Registries updated: PROJECTION_TABLES, reconcile key-column map,
   row/all/count allowlists.

## Evidence

- Tests: `tests/foundry/test_knowledge_entities.py` — 8 tests (red
  before implementation, green after): register → row; redelivery dedup
  + changed-payload conflict; alias-add dedup; link validation (both
  exist, both rollback arms); canonicalized resolution; both query
  directions; rebuild determinism; schema v7.
- Slices 1–2 regression: 19/19 knowledge tests still green (27/27
  combined).
- Full suite: <PENDING> (expect EXIT=0, 275 tests) →
  var/evidence/wp9-slice3/suite-slice3.txt.

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

- Independent verifier for slice-3 (fresh adversarial session).
- Slice-4: continuous ingestion; Slice-5: snapshots/backup/restore/
  semantic rollback — then the V2 gate evidence (05 §5 exit criteria)
  and V3 start.
