# WP-24H-V2KB — V2 Knowledge Brain Typed-Records Vertical Slice

## 1. Objective
Close the remaining gap between the proven WP9/WP11 knowledge brain and the
canonical V2 exit criteria (docs/architecture/05_POST_V1_ROADMAP.md §5 +
MASTER GOAL 3 REQUIRED CAPABILITIES): first-class typed epistemic records
(Observation, Evidence, Claim, Belief, Decision, Unknown), immutable
evidence links with explicit unsupported marking, typed entity resolution,
wider bounded retrieval dimensions, and hard proof of the mandatory
durability/security battery. One source of truth: everything rides the
existing event ledger, FoundryDatabase, projection applier, verifier,
repair and Obsidian integration. No second mission engine.

## 2. Requirement Sources (authoritative)
- docs/architecture/05_POST_V1_ROADMAP.md §5 (V2 outcome/capabilities/exit evidence)
- MASTER GOAL 3 (this program brief) REQUIRED CAPABILITIES + MANDATORY TESTS
- docs/architecture/02_ARCHITECTURE_CONTRACT.md (invariants)
- AGENTS.md engineering standard

## 3. Base and Isolation
- Branch candidate/24h-v2-knowledge-brain @ exactly 7ac0b2373c8d4c74924fcea15c028c4f6e9cdc3c
  (resolves the reviewed commit 7ac0b23; origin/main contained it at
  session start — verified a743fde ancestry BEFORE any edit).
- Worktree C:\temp\JARVIS-24H-V2-Knowledge-Brain (isolated; main checkout
  dirty WIP preserved untouched).
- Local commits only; NO push; NO release pointers; live DB/vault untouched.

## 4. In Scope
1. Schema v35 (versioned migration, additive):
   - knowledge_claims += record_kind ('CLAIM' default), evidence_status,
     unsupported_reason, content_sha256;
   - knowledge_entities += entity_type ('system' default);
   - NEW table knowledge_claim_evidence(claim_id, evidence_id, ...,
     PRIMARY KEY(claim_id, evidence_id)) + index;
   - SCHEMA_VERSION 34 -> 35, MIGRATIONS[35] registered in the SAME patch.
2. Typed records: knowledge_records.py (kind registry, evidence-status
   rules, content-hash helper, validation). Projection support:
   knowledge.claim_recorded persists kind/status/hash; new event
   knowledge.evidence_linked (immutable link; duplicate identical
   delivery dedupes; flip UNSUPPORTED -> SUPPORTED).
3. Entity typing: knowledge.entity_registered accepts entity_type in
   {goal, project, person, organization, system}; resolve_entity stays
   alias-deterministic; new typed lookup helpers.
4. Bounded retrieval: retrieve() gains record_kind, confidence band
   (min/max), time window (from/to), source_run filter, entity filter;
   limits capped; deterministic ordering; manifests stay hash-bound and
   size-capped with explicit truncation; every item provenance-aware.
5. Mandatory-test battery (TDD, watched RED first):
   - conflicting claims remain visible (both rows + contradiction row);
   - every claim has evidence or explicit UNSUPPORTED state;
   - concurrent owner/JARVIS edits preserve both meanings (WAL threads);
   - duplicate ingestion idempotent (no duplicate claims);
   - interrupted write recovery (subprocess kill mid-transaction);
   - restore reproduces bytes AND semantics;
   - retrieval bounded/relevant/reproducible/provenance-aware;
   - malicious note/web content cannot create directives, widen scope
     or disable STOP (knowledge-path injection battery).
6. Disposable migration/rollback rehearsal on COPIES (v34 fixture ->
   v35; fresh-init idempotency; interrupted-migration recovery; restore;
   data-loss proof vs per-copy baseline).

## 5. Out of Scope (forbidden drift)
- V3 discovery/building work; LLM/embedding retrieval; vector stores.
- Any change to STOP/veto/directive machinery semantics (we only PROVE
  isolation from the knowledge path).
- Live DB/vault mutation, deployment, pushes, release promotion.
- Rewriting WP9 history or weakening existing tests.

## 6. Allowed paths
src/jarvis_foundry/{migrations.py,projections.py,knowledge_records.py(new),
knowledge_retrieval.py,knowledge_entities.py,knowledge_ingestion.py,reconcile.py}
tests/foundry/{test_knowledge_records.py(new),test_knowledge_v35_durability.py(new)}
plus minimal additive test touches ONLY where a shared fixture requires it.
evidence/24h/v2-knowledge-brain/** (spec, runs, verdicts, FINAL.json)

## 7. Forbidden paths
Main checkout dirty files; ui/**; .h/**; var/**; live Hermes home;
release/deployment modules; provider configs; secrets.

## 8. Security
- Untrusted document/note text is DATA: it can only ever become
  knowledge.* events/rows; the slice proves directives/STOP/scope are
  unreachable from the ingestion boundary (ledger event-type diff +
  control-state comparisons).
- Secret scan on the final diff before commit.

## 9. Deterministic boundary
All new behavior is deterministic string/row logic. No model calls.

## 10. Failure and recovery
Migration failures roll back whole-statement (existing runner); crash
fixture proves interrupted-write healing; restore path verified
bytes-first then semantically; semantic rollback remains append-only
flagging (history never deleted).

## 11. Binary acceptance criteria
A. Green baseline before first edit (full tests/foundry, EXIT=0).
B. Every new test observed RED before implementation.
C. Focused new suites green; full tests/foundry green; convergence suite
   green; compileall clean; secret scan clean; final diff reviewed.
D. Migration rehearsal artifacts on disposable copies: idempotency,
   legacy-v34 compatibility (all prior rows preserved), interrupted
   recovery, restore equality, zero data loss vs per-copy baseline.
E. Independent adversarial verifier PASS (model-locked x-preview /
   x-preview-f-free; no fallback) against the frozen commit.
F. FINAL.json committed with exact SHA/commands/counts/verdict/rollback.

## 12. Exit condition
All of §11 satisfied, accepted-lane changes committed locally, evidence
bundle complete. Then STOP (V3 explicitly out of scope this program).
