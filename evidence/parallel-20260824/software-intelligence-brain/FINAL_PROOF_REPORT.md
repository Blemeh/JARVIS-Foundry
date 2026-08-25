# FINAL_PROOF_REPORT — Lane 4: Software-Intelligence Brain Activation

FINAL VERDICT: PASS (candidate-level; live activation is a later owner decision)
FROZEN BASE: 079245aec0eed9e1026df87548817a1baf3be036 (origin/main at lane start)
CANDIDATE BRANCH: candidate/software-intelligence-brain-activation-20260824
FINAL HEAD: dc265fc8a33e... (see FINAL_VERDICT.json for full sha)
REMOTE HEAD: pushed to origin under the same branch name (see CANDIDATE PUSHED below)
WORKTREE CLEAN: yes, at closure

## CURRENT BRAIN TRUTH (verified by source inspection, not prose)
TIER A: append-only ledger + event-derived projections incl. resources registry; v36 base schema.
TIER B: knowledge_claims (confidence/freshness/status), immutable evidence links, contradictions,
six record kinds, research queue with STALE/CONTRADICTED/UNSUPPORTED/WEAK cues.
OBSIDIAN: transactional governed writer (expected-hash conflicts preserved byte-exact, rollback
refusing newer-edit clobber), snapshots, policy boundary.
TIER C / HYBRID MEMORY: lifecycle with validation stamps, bounded blocks, lexical recall,
procedures, curation gates, defense scanner, git-backed versioning with divergence protection.
LETTA STATUS: architectural influence inside native Hybrid Memory only; never installed/run.
SCHEMA OBSERVED: 36 -> 37 (additive: derived tier_c_project_aliases from memory.alias.bound).
KNOWN HISTORICAL LIMITATIONS STILL PRESENT: lexical-only recall; defense partly configuration-driven;
restore-oriented rollback. None worsened; defense patterns extended.

## PROJECT IDENTITY
PROJECTS TESTED: disposable fixture repos (identity suite, factory s14) + alias forms
(https/scp/slug/nested-group). ALIAS/RESOURCE RESOLUTION: deterministic over the existing
resource registry; origin-first hosted slug becomes canonical endpoint; secondary remotes
(fork upstream/mirrors) NEVER auto-merge — explicit bind_alias governance decision.
AMBIGUITY HANDLING: fail closed everywhere (ProjectNotBoundError / ProjectIdentityError):
unregistered paths/remotes, alias hijack, silent repo swap (remote-slug check + baseline-commit
ancestry check that also covers remote-less local repos).

## SOFTWARE CONTEXT
Role profiles implemented for all nine roles (ROLE_SECTIONS); verifier receives criteria and
failure modes ONLY — lessons/procedures/builder_claims/recalled_experience excluded at both the
wrapper layer and (pre-existing) canonical compiler layer. CONTEXT BUDGET law proven:
governance survives a 500-char budget; omissions recorded in dropped[]; degraded surfaces in notes[].

## RESEARCH MEMORY
Real-shape fixtures through canonical ingestion: claims start UNSUPPORTED, flip SUPPORTED only via
immutable evidence links; official docs supersede stale secondary sources WITHOUT deleting history;
contradictions coexist as projected pairs and raise revalidation cues through the EXISTING research
queue (STALE on expired fresh_until); no external source can become owner authority.

## ENGINEERING MEMORY
ACCEPTED OUTCOMES: require verifier PASS; verdict pair + V3 outcome (predicted vs actual, honest
UNKNOWN when unmeasured) + decision/outcome claims + recallable accepted lessons.
FAILED ATTEMPTS PRESERVED: labeled "NOT accepted truth" FAILURE ATTEMPT lessons (recallable),
never decision/outcome claims. PROCEDURE EXPERIENCE: bounded maturity (+0.10 PASS cap 0.95,
-0.15 FAIL, -0.20 + STALE on reality change). PREDICTED VS ACTUAL recorded when measured.

## OBSIDIAN PROOF
DISPOSABLE VAULT/MIRROR: temp-dir mirror only; LIVE OWNER VAULT MUTATED: NO.
PROJECT NOTES: two richly populated wikilinked brains (QueueTwin <-> LeadGen) plus adapter note.
SNAPSHOT/CONFLICT/ROLLBACK: update via expected-hash; stale concurrent edit -> conflict with
incoming bytes preserved under Audit/conflicts/; byte-exact rollback refusing newer-edit clobber;
malformed frontmatter refused before ANY mutation; post-write semantic verification answers
what/why-trusted/what-failed/lesson/unknown from content alone.

## FRESH-AGENT BENCHMARK
CONTROL (no institutional context): 0/4 continuity facts. BRAIN (bounded manifest): 4/4 —
prior failure awareness, repair knowledge, decision recall, current/stale research signal —
with every fact line carrying a [claim:<id>] pointer and used_chars <= budget.
FALSE FACTS: none added (pointer assertion); CONTEXT SIZE bounded (asserted).
DUPLICATE INVESTIGATION AVOIDED / REPEATED FAILURE AVOIDED: generation-2 worker sees the
failure AND its repair instruction without chat or hidden transfer.

## GENERATION CONTINUITY
GENERATION 1 OUTCOME: verifier FAIL -> repair -> PASS -> lesson written back.
LESSON WRITTEN: durable tier_c_memories row labeled NOT accepted truth.
SESSION STATE DESTROYED: simulated by reconstructing from a NEW compiler reading only durable rows.
GENERATION 2 FRESH: retrieves failure + repair; BEHAVIOR IMPROVED: avoidance instruction present.
MANUAL CONTEXT TRANSFER: 0.

## VERIFIER INDEPENDENCE
BIAS ATTACK: pro-PASS memory + praising observation + stale older PASS all present.
CURRENT FAILURE REJECTION: planted defect still FAILs (verdict row FAIL asserted);
memory remains advisory context only. PASS.

## MEMORY FAILURE MATRIX
See MEMORY_FAILURE_MATRIX.md — 14 modes tested; control truth survives every outage;
degradation honest; rebuild/reconcile reproduces state from events.

CROSS-PROJECT TRANSFER: scoped by shared-stack signal; LEAKAGE: unrelated project C receives
neither the lesson text nor stack terms (asserted).

## CROSS-LANE CONTRACT
LANE 1 INPUT/OUTPUT, LANE 2 INPUT/OUTPUT, LANE 3 INPUT/OUTPUT: fully mapped in
CROSS_LANE_MEMORY_CONTRACT.md using existing event vocabulary; INTEGRATION BLOCKERS: none.

FACTORY: 15/15 scenarios pass (test_factory_scenarios.py), machine-readable expectations.
TESTS: focused lane suites 108 passed / 0 failed; post-repair regression batch EXIT=0
(lane suites + v3 schema pins + doc staleness + obsidian + hybrid memory + reconcile).
FULL SUITE: run once after stabilization; 5 failures found were ALL schema-pin/doc-staleness
assertions assuming SCHEMA_VERSION==36 — repaired per the repo's own anti-staleness law
(pins derive from constants; CURRENT_STATE.md reconciled); affected suites then green.
INDEPENDENT REVIEW: deleg_fa2bf14a PASS (fresh isolated session, disprove mandate, JSON verdict).
SECRET SCAN: diff-scanned credential shapes — NONE beyond clearly synthetic poison fixtures.
HYGIENE: git diff --check clean; no user-absolute paths in src; scope limited to expected files.

OPEN CRITICAL: 0    OPEN HIGH: 0
(F1 High repaired with discriminating regression dc265fc; F2 Low allowlist hygiene repaired same commit;
F3/F4/F5 Low/Info notes documented, no action required.)

MAIN MODIFIED: NO          PRODUCTION MODIFIED: NO      LIVE DB MODIFIED: NO
CURRENT/PREVIOUS MODIFIED: NO   SIBLING LANES MODIFIED: NO   TASK SCHEDULER MODIFIED: NO
CANDIDATE PUSHED: YES (normal push of this lane's own branch)

## FINAL PRODUCT STATEMENT
The existing Obsidian + Hybrid Memory brain now materially improves autonomous software
engineering WITHOUT becoming control authority: software work writes verified learning back as
claims-with-provenance, recallable lessons, procedure experience, and human-readable project
intelligence; a fresh worker compiles a bounded role-specific manifest whose facts outrank nothing
but beat empty context decisively — remembering what failed, why it failed, what fixed it, and
what remains unknown, entirely from durable state. Memory degrades honestly under outage, refuses
poison at every boundary, cannot self-promote trust, cannot bias an independent verifier, and
never outranks live evidence, STOP/veto, capability state, effect reconciliation, or verifier truth.

NEXT ACTION: POST-4-LANE SEMANTIC INTEGRATION
