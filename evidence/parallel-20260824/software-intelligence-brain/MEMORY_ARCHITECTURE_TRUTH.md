# MEMORY ARCHITECTURE TRUTH — verified inventory (Phase A)

Method: direct AST/source inspection of the worktree at frozen base 079245a;
schema read from migrations.py; no reliance on historical prose.

## Subsystem classification

| Subsystem | Source | Status | Notes |
|---|---|---|---|
| Tier A ledger/projections | src/jarvis_foundry/{database,events,projections}.py | PROVEN_CURRENT | append-only; idempotency-keyed; v36 |
| STOP/directives/veto | control/, owner_controls.py, supervisor/ | PROVEN_CURRENT | consulted by compiler governance section first |
| Resource registry | connectors/resources.py + projections project.registered | PROVEN_CURRENT | deterministic resource-{sha24}; kinds local_repository/git_remote/http_service/browser_endpoint/... ; upsert projection keyed by resource_id |
| Tier B claims | knowledge_claims (+v35 refresh tables) | PROVEN_CURRENT | confidence 0..1, fresh_until, status ACCEPTED/CONTRADICTED/SUPERSEDED, superseded_by |
| Tier B record taxonomy | knowledge_records.py six kinds | PROVEN_CURRENT | Observation/Evidence/Claim/Belief/Decision/Unknown + entity types; import-safe leaf |
| Contradictions | knowledge_contradictions | PROVEN_CURRENT | pair records |
| Retrieval manifests | knowledge_retrieval.py build_context_manifest | PROVEN_CURRENT | active/stale/conflicts; worker_context wraps it |
| Research queue | research_queue.py | PROVEN_CURRENT | STALE/CONTRADICTED/UNSUPPORTED/WEAK detection -> research.queue_item_created; candidate generation hook exists |
| Tier C lifecycle | hybrid_memory/lifecycle.py + tier_c_* tables | PROVEN_CURRENT | OBSERVED->...->ACCEPTED_EXPERIENCE/CORE/PROCEDURAL->STALE/SUPERSEDED/ARCHIVED; validation stamps required for accept |
| Tier C blocks | blocks.py | PROVEN_CURRENT | char budgets, read-only, revisions |
| Tier C recall | recall.py | PROVEN_CURRENT | LEXICAL ONLY (known limitation, retained) |
| Tier C procedures | procedures.py | PROVEN_CURRENT | add/search/set_state; experiential only |
| Curation | curation.py | PROVEN_CURRENT | provenance/dup/contradiction(Tier B+C)/value/defense gates |
| Defense | defense.py | PROVEN_CURRENT | poison/secret patterns; PARTIAL coverage acknowledged in-source (config-driven) |
| Versioning | versioning.py | PROVEN_CURRENT | git-backed projection, divergence detection, restore/revert; secret scan pre-git-write |
| Context compiler | compiler.py | PROVEN_CURRENT | roles executive/builder/verifier/curator; budget law: Tier A never dropped, Tier B >=400 or budget//3, ALL Tier C droppable w/ notes; verifier excludes recall+procedures |
| Verifier isolation | compiler role gate + test_hybrid_memory_verifier_repairs.py | PROVEN_CURRENT | builder claims labeled UNVERIFIED_BUILDER_CLAIM; verifier gets no recall/procedures |
| Obsidian writer | obsidian/writer.py + policy.py | PROVEN_CURRENT | expected-hash conflict preservation, parse check, audit events, rollback refuses newer-edit clobber |
| Snapshots | obsidian/snapshots.py | PROVEN_CURRENT | atomic writes, hashes |
| Vault retrieval/indexing | vault_indexer.py, vault_retrieval.py | PROVEN_CURRENT | budgeted note retrieval |
| Worker manifests | worker_context.py | PROVEN_CURRENT | reconstruct-from-sources + hash equality; NO chat history |
| Semantic recall | (reserved hook) | MISSING (by design) | out of lane scope; anti-drift forbids new vector DB |
| Project identity RESOLUTION across aliases | — | MISSING | resources exist but nothing maps local path / git root / remote URL aliases / worktrees onto one resource_id deterministically; Phase B delivers as an adapter OVER the resource registry |
| Software context profile (project-aware selection) | — | PARTIAL | compiler is mission-aware but not project-aware; Phase D/E extend via composition, no authority change |
| Writeback contracts (research/outcome/lifecycle) | — | PARTIAL | building blocks exist (knowledge_records, tier_c lifecycle, obsidian writer); no single governed software-memory writeback path; Phases F-J compose them |
| Cross-project scoped transfer | — | MISSING | Phase N |
| Fresh-agent benchmark harness | — | MISSING | Phase L/M |

## Law restated for this lane

1. No second memory store, mission system, procedure runtime, or vector DB.
2. Memory never outranks live evidence, STOP/veto, capability state, effect
   reconciliation, or independent verifier truth.
3. Chat history is never canonical.
4. Failed builder claims stay labeled lessons, never accepted truth.
5. External/web content is DATA_ONLY until it becomes sourced evidence.
