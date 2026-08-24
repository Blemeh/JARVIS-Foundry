# ORIENTATION — Lane 4: Obsidian + Hybrid Memory Software-Intelligence Activation

Lane: 4 of 4 (brain activation, NOT a memory rebuild)
Date: 2026-08-24
Frozen base (origin/main at lane start): `079245aec0eed9e1026df87548817a1baf3be036`
Worktree: `C:/temp/JARVIS-Software-Intelligence-Brain`
Branch: `candidate/software-intelligence-brain-20260824`

## Mission law

This lane ACTIVATES the existing JARVIS brain on software engineering. It does
not build a second brain. Letta stays an architectural influence inside native
Hybrid Memory. Obsidian remains the human-readable institutional surface.
Tier A (ledger) > Tier B (epistemic claims) > Tier C (advisory experience);
no authority path leads upward from memory to control.

## Sibling-lane isolation

Sibling lanes for this date already hold worktrees from the same frozen base:
`candidate/live-github-research-activation-20260824` (L1),
`candidate/autonomous-repo-evolution-20260824` (L2),
`candidate/autonomous-new-projects-20260824` (L3). This lane shares no files,
commits, or claims with them; their in-progress output is UNVERIFIED/NON-CANONICAL
here and is modeled only through disposable fixtures plus the cross-lane contract.

## Canonical reality observed at start (verified by inspection, not prose)

| Surface | Reality |
|---|---|
| Schema | v36 (`migrations.SCHEMA_VERSION = 36`), production opens at 36 |
| Tier A | append-only event ledger + event-derived projections incl. `resources`, missions, directives, verdicts |
| Tier B | `knowledge_claims` (confidence/fresh_until/status ACCEPTED/CONTRADICTED/SUPERSEDED), `knowledge_contradictions`, manifests, entities, evidence links; six record kinds in `knowledge_records.py` (Observation/Evidence/Claim/Belief/Decision/Unknown); `research_queue.py` detects STALE/CONTRADICTED/UNSUPPORTED/WEAK claims into `research.queue_item_created` |
| Tier C | `hybrid_memory/`: lifecycle state machine over `tier_c_memories` (+revisions/validations), bounded blocks, lexical recall, procedures registry, curation gates, defense scanner (poison/secret quarantine), git-backed versioning with divergence protection, role context compiler with verifier isolation |
| Resource identity | `connectors/resources.py` `discover_resource()` -> deterministic `resource-{sha24(kind\0canonical_endpoint)}`; kinds include `local_repository` and `git_remote`; canonical append-only `project.registered` event projects into `resources` |
| Obsidian | transactional governed writer (expected-hash concurrency -> conflict preserved byte-exact under Audit/conflicts/, before-snapshot rollback refusing clobber of newer edits), snapshots, policy |
| Worker context | `worker_context.py` reconstructable bounded manifests with recorded sources + manifest hash; `worker.manifest_delivered` events |

## Historical V8 reference vs current truth

The historical promotion SHA `2f7360e...` predates the current canonical head.
Current truth was re-derived from the live tree above; nothing was assumed from
the historical record. Known historical limitations re-checked against source:

- lexical-only recall: STILL PRESENT (`recall.py` is lexical; semantic hook reserved).
- defense scanner partly configuration-driven: STILL PRESENT (`defense.py` pattern table).
- rollback restore-oriented (no reverse migration): STILL PRESENT by design.

## Phase classification (Phase A deliverable)

See MEMORY_ARCHITECTURE_TRUTH.md for the full PROVEN_CURRENT /
IMPLEMENTED_UNPROVEN / PARTIAL / SUPERSEDED / MISSING table per subsystem.
Summary: all Phase-A-inspected memory surfaces are PROVEN_CURRENT at the
canonical level (promoted, tested, documented). The software-intelligence
ACTIVATION layer (project identity resolution onto resources, software context
profile, writeback contracts, fresh-agent proof) is MISSING/PARTIAL and is what
this lane builds WITHOUT touching proven machinery's authority semantics.
