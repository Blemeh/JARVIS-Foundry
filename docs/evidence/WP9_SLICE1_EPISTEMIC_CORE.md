# WP9 — V2 Knowledge Brain, Slice-1: Epistemic Core

Status: **IMPLEMENTED + REPAIRED** (2026-08-12). Verifier round-1 —
TWO independent verifiers, both FAIL on the same K1 defect:
deleg_642b84b2 (contraction false-negatives: "X isn't Y" did not
contradict "X is Y" — punctuation stripping destroyed `n't` tokens
before token matching; apostrophe-free spellings removed the copula) and
deleg_0d8aeb07 (same defect on the pinned commit, everything else PASS;
explicitly recognized the fix at 97322a3). K2–K7 PASS in both. →
REPAIRED (97322a3: contraction expansion before canonicalization,
word-boundary regex, copula-preserving `isnt -> is not` mappings) +
regression `test_negation_variant_family_no_false_negatives` (9
negation variants + 3 non-contradiction siblings). Verifier round-2
(deleg_22b8c3a9) <PENDING>.
WP9 package ACTIVE (docs/planning/CURRENT_WORK_PACKAGE.yaml).

## Delivered (bounded slice-1 scope)

1. **Schema v5** (migrations.py, migration 5): `knowledge_claims`
   (claim_id PK, mission_id, statement, claim_type, confidence [0,1],
   fresh_until, source_run_id, source_event_id, status
   ACCEPTED/CONTRADICTED/SUPERSEDED, superseded_by, last_event_id,
   updated_at) + `knowledge_contradictions` (contradiction_id PK,
   claim_a_id, claim_b_id, claim_type, reason, last_event_id,
   updated_at). Indexes on mission/type + status + pair.
2. **Event types**: `knowledge.claim_recorded`
   (idempotency_key `knowledge.claim_recorded:{claim_id}` — identical
   re-delivery deduped, changed payload EventConflict, F10 machinery) and
   `knowledge.claim_superseded` (validates BOTH claims exist inside the
   transaction; missing → ValueError + rollback, F9 pattern).
3. **Deterministic contradiction detection** (src/jarvis_foundry/knowledge.py):
   contraction expansion (word-boundary; `isn't/isnt -> is not`, ...
   copula-preserving) + token-level negation canonicalizer
   (`not/no/never`), core equality + exactly-one-negated ⇒ contradiction.
   String-level, deterministic, test-driven — NO LLM inference (recorded
   future work). Detection runs at record time (derived projection; no
   separate event — deterministic under rebuild; both claims stay
   visible, statuses flagged).
4. **Queries**: `FoundryDatabase.row/all/count` bounded helpers
   (allowlisted tables; exact-match filters).
5. **Reconcile integration**: key-column map + PROJECTION_TABLES include
   both tables (rebuild + reconcile deterministic).

## Evidence

- Tests: `tests/foundry/test_knowledge_claims.py` — 8 tests (red before
  implementation, green after): provenance row, dedup, conflict,
  negation-pair at record time, ordering case, supersede validation +
  atomic rollback, deterministic rebuild, bounded queries.
- Staged smoke (NEVER the live DB): var/evidence/wp9-slice1/
  (staging.db + slice1-smoke-summary.json) — claim + negation →
  CONTRADICTED pair detected; third claim superseded by the first;
  schema v5.
- Full suite: <PENDING> (EXIT expected 0, 256 tests).

## Live-DB impact

NONE — all smoke/knowledge events ran on throwaway/staging DBs. The live
daemon DB is untouched by slice-1 (schema v5 will apply to the live DB on
the next natural initialize; reconcile compares at SCHEMA_VERSION — the
live daemon continues at v4 until its next restart, which is the normal
deployment path).

## Next

- Independent verifier for slice-1 (fresh adversarial session).
- Slice-2: retrieval + bounded context manifests.
