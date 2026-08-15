# V8 Hybrid Memory — Production Component Mapping (R2 Integration, Deliverable 1)

Base: canonical `93a3ddc0965dfaf3e4cb9dc0952467bd5250520a` (main; verified identical to the audited reference — no advancement; 6 untracked docs; 6 pre-existing worktrees). Lab reference: `3aa18f2` (53/53 tests, 13P/0F/3INFO probes, final audit PASS). Source of truth for this mapping: `FINAL_AUDIT/07_PRODUCTION_COMPONENT_MAPPING.md` + `FINAL_AUDIT/08_PRODUCTION_SCHEMA_DELTA.md` (lab tree, committed).

## Mapping

```text
LAB COMPONENT                    → PRODUCTION JARVIS COMPONENT        → ACTION                → REASON                                    → MIGRATION IMPACT      → VERIFICATION REQUIREMENT
---------------------------------+-------------------------------------+-----------------------+-------------------------------------------+-----------------------+-----------------------------------------
hybrid_memory.storage.Storage    → FoundryDatabase (database.py) +     → DO NOT MERGE         → production DB exists, event-derived,       → none                  → hybrid tests run against FoundryDatabase
                                  migrations.py (schema v32, WAL)                             idempotency-keyed; lab Storage is a
                                                                                               simulation-only wrapper
hybrid_memory.truth.TruthStore   → events, directives,                  → DO NOT MERGE         → production control-plane truth is          → none                  → P1-equivalent: Tier C paths never write
                                  verification_verdicts, evidence,                            stronger (ledger + reconcile + STOP        →                       → events/directives/verdicts (test)
                                  supervisor_state/stop                                         machinery)
hybrid_memory.epistemic.         → knowledge_claims (fresh_until,      → DO NOT MERGE         → production Tier B is stronger              → none                  → hybrid recall reads production claims
EpistemicStore                     supersede statuses), knowledge_                             (event-derived, contradiction table);        →                       → (no duplicate epistemic store)
                                  contradictions, knowledge_manifests                          lab epistemic is simulation
hybrid_memory.context.compiler   → knowledge_retrieval.retrieve() +    → ADAPT INTO           → per-section budgets + Tier C sections      → none (additive        → role packages: priority order, budget,
                                  Manifest + worker_manifests +         PRODUCTION             + verifier isolation are new; must not       → module)               → verifier exclusion of unlabeled Tier C;
                                  deliver_worker_context()                                     replace the manifest hash/truncation          →                       → reconstruct_context hash still matches
                                                                                               contract — extend with a new entry point
hybrid_memory.memory.blocks      → no equivalent                        → ADAPT INTO           → bounded core blocks are new capability     → v33: tier_c_blocks,    → budget enforcement, read_only, occupancy
                                                                        PRODUCTION             (context real estate)                        → tier_c_block_revisions
hybrid_memory.memory.lifecycle   → no equivalent (consolidation_ops     → ADAPT INTO           → promotion state machine + validation      → v33: tier_c_memories,  → raw transitions refused (stamp gate);
(with validation stamps)           covers Obsidian only)                 PRODUCTION             → stamps (audit H1 repair) must survive      → tier_c_memory_        → governed path only; illegal attempts
                                                                                                                     → revisions,              → audited
                                                                                                                     → tier_c_validations
hybrid_memory.memory.recall      → no equivalent for experience          → ADAPT INTO           → deterministic lexical recall over Tier C  → v33: indexes on       → state/expiry/scope/role filtering;
                                  (knowledge_retrieval is Tier B)        PRODUCTION             → with production metadata contract          → tier_c_memories        → malformed timestamps degrade (L2)
hybrid_memory.memory.procedural  → Hermes skills exist (executor);      → ADAPT INTO           → governed procedure registry is new;       → v33: tier_c_          → trigger retrieval, expiry, provenance
                                  no JARVIS registry                     PRODUCTION             → reference, don't duplicate, Hermes         → procedures
                                                                                               → skills as executor
hybrid_memory.memory.curation    → consolidation.py (different          → ADAPT INTO           → reuse propose→verify→accept→rollback      → none (module)         → provenance/dup/contradiction/defense
                                  surface: Obsidian)                     PRODUCTION             → pattern; Tier C gates are new             →                       → gates; blocked promotions audited
hybrid_memory.memory.defense     → no equivalent (contract §18          → ADAPT INTO           → scanner + quarantine + full-projection    → none (module)         → novel poison strings; secrets in
                                  principle, no implementation)          PRODUCTION             → commit gate (H2/C9-2 repairs)             →                       → content/body/value all blocked
hybrid_memory.memory.versioning  → no equivalent                        → ADAPT INTO           → git-backed Tier C history + sync markers  → none (module +         → divergence detected; markerless fail-
                                                                        PRODUCTION             → + divergence gate (H3/C9-4 repairs);      → git dir under         → closed; rollback preserves history
                                                                                               → never source of truth for Tier A          → candidate home
hybrid_memory.obsidian_sim.vault → obsidian/ package                    → DO NOT MERGE         → production Obsidian governance exists     → none                  → projection tests use production obsidian
                                                                                               → and is stronger (policy, snapshots)
scripts/benchmark.py             → production benchmark (v8)            → REBUILD              → measures production semantics, not lab    → none (script)         → A/B/C results stored, hashed
tests/ (53 lab tests)            → tests/foundry/test_hybrid_memory_*   → PORT INTENT          → every invariant mapped; no blind copy     → none (tests)          → traceability matrix (lab → production)
audit/ probes                    → tests/foundry/ + scripts             → PORT INTENT          → adversarial battery against real code     → none                  → probes pass on production paths
```

## Porting rules applied

1. No directory copying. Each module is re-implemented against `FoundryDatabase`/real tables with production conventions (event-derived rows where the ledger is the source, `last_event_id`, CHECK constraints, idempotency keys).
2. No duplicate truth/epistemic stores: Tier C reads production `knowledge_claims`/`knowledge_contradictions` directly; governance sections of the context package are read from the real `events`/`directives`/`supervisor_state` tables.
3. All five audit repairs (H1 stamps, H2 commit gate, H3 divergence, C9-2 full projection, C9-4 fail-closed markerless) are carried over with their regression tests.
4. Context compiler extends `deliver_worker_context` via a new opt-in entry point; the existing manifest hash/truncation contract and `reconstruct_context` are untouched (no regression to worker manifests).
5. Migration is additive schema v33 only (six `tier_c_*` tables per FINAL_AUDIT/08); no Tier A/B table changes; rollback = drop six tables + remove v33 row (rehearsed).

## Explicit non-ports (anti-drift)

No dashboard, voice, WhatsApp, graph DB, Letta runtime/Postgres, provider infrastructure, Supervisor redesign, V3-V5 features, unrelated refactors.
