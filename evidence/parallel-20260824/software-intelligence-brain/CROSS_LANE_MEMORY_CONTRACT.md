# CROSS-LANE MEMORY CONTRACT (Phase T)

Integration law: sibling lanes wire into the EXISTING canonical event
vocabulary below. If an adapter needs a new field, it extends a payload — it
never creates a second memory authority, registry, or store. All event types
referenced here already exist in `src/jarvis_foundry/projections.py` unless
marked [LANE4] (the two lane additions: `memory.alias.bound` events and the
derived `tier_c_project_aliases` projection; schema v37 additive).

## LANE 1 — GitHub / Research / Discovery

INPUT TO BRAIN (Lane 1 calls, brain stores):
- repository/resource observations -> `project.registered` (resource registry)
- research evidence -> docs through `knowledge_ingestion.ingest_documents`
  (claims start UNSUPPORTED) + immutable `knowledge.evidence_linked`
- source contradictions -> automatic negation-pair detection at claim record
  time (knowledge_contradictions) + `research.queue_item_created` via
  `research_queue.detect_research_items`
- upstream changes / staleness -> claims carry `fresh_until`; expired claims
  surface STALE cues in the same research queue; supersession via
  `knowledge.claim_superseded` (history preserved)
- opportunity evidence / unknowns -> candidates via
  `discovery_candidates.generate_candidate`

BRAIN OUTPUT FOR LANE 1:
- prior research + staleness + known facts: knowledge_claims rows scoped by
  mission/project identity (`SoftwareContextCompiler` researcher profile)
- past decisions and rejected duplicate ideas: decision-type claims +
  REJECTED candidates with durable rejection reasons (evidence_json)
- research gaps: OPEN items in research_queue

## LANE 2 — Existing Project Evolution

INPUT TO BRAIN:
- selected project/task -> mission bound to resource_id (mission.created with
  resource_id); project identity resolved by
  `software_intelligence.project_identity.ProjectIdentityResolver`
  ([LANE4] alias spine over the resource registry)
- implementation attempts -> agent_run events (canonical worker system)
- critic/verifier verdicts -> verification.started/.passed/.failed/... via
  `mission.verification.start_verification` + `record_verification_outcome`
- repair lineage -> failed-attempt writebacks
  (`software_intelligence.writeback.record_failed_attempt`) storing labeled
  FAILURE ATTEMPT lessons (Tier C, recallable, never accepted truth) plus
  failure/constraint Tier B claims
- accepted commit + actual outcome -> `record_engineering_outcome`
  (requires verifier PASS): verdict row + V3 outcome (predicted vs actual,
  honest UNKNOWN when unmeasured) + decision/outcome claims + accepted
  Tier C experience
- maintenance/wake -> wake-condition lessons (Tier C) + portfolio state

BRAIN OUTPUT FOR LANE 2:
- project history, decisions, pitfalls, procedures (tier_c_procedures with
  confidence maturity), relevant research, unknowns — compiled per role by
  `SoftwareContextCompiler` under the budget law.

## LANE 3 — New Project Creation

INPUT TO BRAIN:
- opportunity candidates / rejected ideas -> discovery_candidates +
  discovery.candidate_rejected (rationale durably in evidence_json) and
  optionally a REJECTED IDEA lesson (Tier C, recallable)
- selected contract -> mission.created (+ created_from candidate link)
- new resource/repo -> discover_resource + registration_event; then bind
  path/slug aliases ([LANE4])
- architecture decisions -> decision claims; versioned via
  knowledge.claim_superseded chains
- MVP results -> verification verdicts + outcomes as Lane 2
- post-MVP improvements / maintenance -> outcome claims + wake lessons

BRAIN OUTPUT FOR LANE 3:
- existing similar projects: resources registry + knowledge_entities
  (entity_type=project, alias-linked to resource_id)
- prior rejected ideas with rationale (retrievable from durable state)
- reusable architecture lessons/procedures/research via context compile

## Integration blockers

None identified. Later integration should need only adapters that call the
functions named above; no schema redesign (v37 was additive), no second
authority, no dependency on sibling-lane in-progress code.
