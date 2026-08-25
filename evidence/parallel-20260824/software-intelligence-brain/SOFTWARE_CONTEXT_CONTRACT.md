# SOFTWARE CONTEXT CONTRACT + TAXONOMY (Phases C/D/E)

## Taxonomy mapping (existing epistemic classes only)

| Software fact | Canonical class | Store | Event |
|---|---|---|---|
| CI run failed at test_x | OBSERVATION | tier_c_memories (OBSERVED) | memory.observed |
| CI log / diff / upstream release | EVIDENCE | knowledge_claim_evidence link | knowledge.evidence_linked |
| "Library v4 breaks adapter (method Y removed)" | CLAIM (research) | knowledge_claims | knowledge.claim_recorded |
| "Migrating to Z is lower-risk" | BELIEF-style claim w/ confidence | knowledge_claims (confidence) | knowledge.claim_recorded |
| "Use Z; rejected X due to upstream removal" | DECISION | knowledge_claims (claim_type=decision) | knowledge.claim_recorded |
| "Does upstream v5 restore Y?" | UNKNOWN | knowledge_claims (research/unknown phrasing) + research queue cues | knowledge.claim_recorded / research.queue_item_created |
| "When changing provider routing, replay persisted identity" | LESSON | tier_c_memories via governed curation (recallable) | memory.observed -> validation -> accepted |
| Dependency-migration procedure experience | PROCEDURE | tier_c_procedures + confidence/state | procedure.created / procedure.updated |
| Predicted 90m vs actual 38m, verifier PASS | OUTCOME | candidate_outcomes + outcome claims | discovery.candidate_outcome_recorded |

Nothing is collapsed into a single "memory" bucket: operational truth stays in
Tier A, epistemic truth in Tier B, experience in Tier C, human narrative in
Obsidian.

## Context manifest contract

Compiled by SoftwareContextCompiler over the canonical ContextCompiler.

Ordering/budget law:
1. mandatory_governance + owner_directives + mission_contract + evidence
   (canonical compiler sections; never dropped);
2. tier_b_knowledge (canonical, budget//3 floor 400);
3. project_identity (resources registry pointer + worktree truth);
4. typed slices per role from remaining budget (per-slice cap, omissions recorded):
   decisions, failures, repair_lineage, lessons, procedures, research,
   contradictions, stale, unknowns, criteria;
5. Obsidian pointers last (dropped first). Not yet wired live: mirror-only.

Every inclusion carries provenance (pointer: claim:<id> / resource:<id> /
verdict:<id>). Every omission lands in `dropped[]`. Degraded surfaces land in
`notes[]` (tier_b_unavailable, project_unbound, contradiction_scan_degraded).

## Role policy (Phase E)

ROLE_SECTIONS gates typed slices per role:
executive(project_state, unknowns), researcher(research, contradictions,
stale, unknowns), architect(decisions, constraints), builder(decisions,
lessons, procedures, failures, worktree), tester/critic(criteria, failures),
verifier(criteria, failure_modes ONLY), repairer(failures, repair_lineage,
lessons), knowledge_curator(contradictions, stale, provenance).

VERIFIER_EXCLUDED = lessons, procedures, builder_claims, recalled_experience.
The compiler additionally withholds caller-supplied builder claims from the
verifier path entirely (recorded note), and the canonical compiler already
excludes Tier C recall/procedures for verifiers. Proven by
test_software_context.py::test_verifier_independence_preserved.
