# Owner Directive — 2026-08-12 — Master-Plan Alignment and Operational Acceptance

Status: **ACTIVE** — recorded in the live ledger as
`owner_directive.recorded:DIR-2026-08-12-master-plan-alignment` (seq 5486)
with the `control.changed:version-promotion-hold` event (seq 5487) and
`observation.started:v1-reliability-window` (seq 5488).

This file is the verbatim grounding record of the directive received in
the Hermes session on 2026-08-12. It is quoted in full below, exactly as
received. Nothing in this file is a paraphrase.

---

## JARVIS FOUNDRY — MASTER-PLAN ALIGNMENT AND OPERATIONAL ACCEPTANCE

MODE: AUTONOMOUS EXECUTION
AUTHORITY: AUTHENTICATED OWNER DIRECTIVE
REPOSITORY:
C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry

### MISSION

Correct the gap between JARVIS Foundry's version labels and the real operational outcomes required by the master plan.

Preserve all valid code, tests, commits, evidence, failures and architectural decisions.

Do not restart, simplify or redesign JARVIS.

The original destination remains unchanged:

JARVIS is a persistent autonomous digital organization that observes goals and changes, maintains institutional memory, discovers opportunities and problems, creates missions, researches, builds, tests, independently verifies, repairs failures, measures outcomes, learns, updates Obsidian and durable state, selects the next valuable action and continues without routine owner approval.

### STANDING AUTHORITY

The original START BUILD mandate remains active.

Silence means continue.

Do not ask for routine approval for tasks, repairs, tests, verification, work packages or internal implementation.

Owner control remains:

- STOP
- pause
- veto
- redirect
- constraints
- priority changes

This directive creates a temporary VERSION PROMOTION HOLD.

It does not stop useful engineering.

### IMMEDIATE RULE

Do not activate V4 or declare another version fully accepted until this alignment work passes.

An already-running bounded V3 Slice-2 builder or verifier may finish safely.

If it fails:

1. record FAIL;
2. repair the exact cause;
3. add regression coverage;
4. regenerate evidence;
5. dispatch a fresh verifier.

Do not run multiple builders against the same files.

Do not reset, clean, restore, stash or discard existing work.

### ESTABLISH LIVE TRUTH FIRST

Before changing anything, inspect:

1. Git branch, HEAD, status and uncommitted changes;
2. recent commits;
3. active Hermes builders and verifiers;
4. Supervisor process and scheduled task;
5. STOP, vetoes and constraints;
6. live Foundry database schema, counts and recent events;
7. PROJECT_STATE.md;
8. CURRENT_WORK_PACKAGE.yaml;
9. closed WP8, WP9 and WP10 records;
10. handoff, recovery and evidence files.

Live repository and runtime evidence outrank old summaries and this prompt's historical state.

### AUTHORITY ORDER

Follow:

1. latest owner STOP, veto or directive;
2. this alignment directive;
3. AUTONOMY_MANDATE.md;
4. 00_READ_FIRST.md;
5. 01_NORTH_STAR.md;
6. 02_ARCHITECTURE_CONTRACT.md;
7. current specifications and evidence gates;
8. durable project state;
9. repository instructions.

Do not weaken the North Star or roadmap to match incomplete implementation.

### PERMANENT INVARIANTS

Preserve:

- Supervisor independent from Hermes;
- deterministic STOP;
- freeze before rollback;
- owner veto precedence;
- append-only events and failure history;
- durable state over chat history;
- evidence separated from claims and beliefs;
- builders cannot certify their own work;
- independent adversarial verification;
- brokered secrets;
- idempotency and reconciliation;
- no overwrite of the only recoverable live copy;
- no fabricated PASS, evidence or owner consent;
- preservation of unrelated user work;
- no fake busy work.

### TWO-GATE VERSION MODEL

Every version must have:

version: Vn
engineering_gate: PASS | FAIL | BLOCKED | NOT_TESTED
operational_gate: PASS | FAIL | BLOCKED | NOT_TESTED
promotion_state: HOLD | ELIGIBLE | PROMOTED
accepted_scope:
remaining_scope:
evidence:
failures:
repairs:
residual_risks:
next_action:

ENGINEERING PASS means the components, tests, migrations, recovery and bounded adversarial verification pass.

OPERATIONAL PASS means the complete roadmap outcome worked through real integrated paths, produced live durable evidence, survived interruption and demonstrated useful results.

Unit tests alone cannot produce operational PASS.

Do not promote a version until both gates PASS, unless a later authenticated owner directive explicitly changes one named requirement.

### PHASE 1 — RECONCILE PROJECT TRUTH

Record this directive and the VERSION PROMOTION HOLD in the durable ledger.

Reconcile:

- PROJECT_STATE.md
- CURRENT_WORK_PACKAGE.yaml
- WP8 closed record
- WP9 closed record
- WP10 records
- REQUIREMENTS_TRACEABILITY.md
- RISK_REGISTER.md
- DECISION_LOG.md
- current handoff
- recovery notes
- evidence indexes

Correct contradictions such as:

- stale active version;
- stale active package;
- stale next action;
- CLOSED and ACTIVE simultaneously;
- CLOSED_PASS with gate_verdict NOT_TESTED;
- current package pointing to an already-closed slice.

Preserve historical files. Mark them historical instead of rewriting history.

### PHASE 2 — RECLASSIFY V1 HONESTLY

Keep all valid V1 work.

Record approximately:

engineering_gate: PASS
operational_gate: NOT_TESTED
promotion_history: promoted under authenticated duration waiver
accepted_scope:
  - event store
  - Supervisor
  - STOP
  - leases and recovery
  - action control
  - mission and verification loops
  - governed Obsidian write-back
  - fault-injection foundation
remaining_scope:
  - real multi-day unattended reliability
  - incomplete physical reboot evidence
  - any previously NOT_TESTED reconciliation surface

Do not invalidate the owner's waiver.

Distinguish "accepted under waiver" from "seven-day reliability proven."

Create a durable V1 reliability observation window without stopping unrelated safe work.

### PHASE 3 — RECLASSIFY V2 HONESTLY

Preserve all WP9 implementation and evidence.

Record:

engineering_gate: PASS
operational_gate: NOT_TESTED
promotion_state: HOLD

Accepted engineering scope:

- epistemic claims and provenance;
- confidence, freshness and contradictions;
- SQLite retrieval;
- bounded context manifests;
- entity resolution;
- deterministic ingestion primitives;
- snapshots and backup verification;
- semantic rollback mechanics;
- migrations and projection reconciliation.

Operational scope still required:

- real Obsidian indexing;
- whole-vault retrieval;
- continuous live ingestion;
- autonomous memory consolidation;
- stale-knowledge research queues;
- context delivery to real workers;
- meaningful live knowledge data;
- integrated semantic rollback.

Describe V2 as:

"V2 engineering core accepted; operational acceptance pending."

### PHASE 4 — COMPLETE V2 OPERATIONAL INTEGRATION

Implement bounded, reversible slices using the existing architecture.

A. Real Obsidian indexing

- Start with a bounded vault subset.
- Preserve paths, hashes and provenance.
- Treat note content as untrusted data.
- Ingest changes incrementally and idempotently.
- Prove restart-safe indexing.

B. Whole-vault retrieval

- Retrieve real indexed vault knowledge plus SQLite knowledge.
- Support mission, entity, time, confidence, source and type filters.
- Return bounded relevant context.
- Show provenance, stale knowledge and contradictions.
- Measure false positives, false negatives and cross-mission leakage.

C. Continuous ingestion

Connect bounded sources:

- Obsidian changes;
- repositories;
- conversations and handoffs;
- research artifacts;
- mission and system events.

Prove retry safety, deduplication, restart recovery and bounded resource use.

D. Research queues

- Detect stale, contradicted, unsupported or weak claims.
- Generate evidence-backed research candidates.
- Deduplicate against candidates and missions.
- Reject low-value research activity.

E. Real worker context

- Deliver bounded manifests to actual workers.
- Record manifest hash, worker, mission, sources and truncation.
- Prove replacement workers can reconstruct context without chat history.

F. Governed consolidation

- Begin only in a reversible JARVIS-governed vault area.
- Preserve original notes and provenance.
- Use expected-hash conflict protection.
- Independently verify meaning before acceptance.
- Roll back semantic degradation.

G. Integrated rollback

Demonstrate:

snapshot → mutation → degradation detection → freeze → byte restoration → projection rebuild → semantic reconciliation.

Never replace the live database while the daemon is active.

### V2 OPERATIONAL GATE

V2 operational PASS requires:

1. real vault notes indexed with provenance;
2. meaningful live knowledge data;
3. every claim supported or marked unsupported;
4. contradictions remain visible;
5. bounded relevant whole-vault retrieval;
6. safe continuous ingestion;
7. stale knowledge creates research candidates;
8. real workers receive context manifests;
9. governed consolidation succeeds;
10. concurrent edits conflict instead of overwrite;
11. rollback restores bytes and meaning;
12. restart creates no duplicate mutation;
13. STOP and veto remain outside note content;
14. resource use is measured;
15. full regression suite passes;
16. independent verifier reproduces the important claims.

On FAIL, repair and re-verify. Never promote through documentation changes alone.

### PHASE 5 — STRENGTHEN EVIDENCE

Detailed evidence under var/evidence is local and ignored by Git.

Do not commit secrets, private vault content, databases or sensitive logs.

For every gate, commit a sanitized evidence manifest containing:

- candidate HEAD;
- test commands and exit codes;
- test counts;
- verifier identity and provider;
- artifact hashes;
- PASS/FAIL lineage;
- repairs;
- remaining uncertainty;
- references to private evidence.

Private evidence may remain local, but its loss or modification must be detectable through committed hashes.

No gate may depend only on a temporary subagent transcript.

### PHASE 6 — FREEZE VERIFICATION TARGETS

Before verifier dispatch:

1. finish the bounded candidate;
2. run focused tests;
3. run the full suite;
4. save evidence at unique stable paths;
5. record hashes;
6. pin HEAD and worktree status;
7. ensure no builder targets the same files;
8. dispatch a fresh adversarial verifier.

During verification:

- do not modify candidate files;
- do not regenerate the verifier's artifact;
- do not claim PASS.

If verification fails, end the freeze, repair, create new evidence and dispatch a fresh verifier.

Prefer a different model/provider family from the builder. If unavailable, record the limitation and use stronger deterministic probes or a second verifier.

### PHASE 7 — CONTINUE V3 WITHOUT EARLY ACCEPTANCE

Preserve the V3 candidate and monitor implementation.

V3 engineering may continue, but V3 operational PASS requires multiple real loops:

OBSERVE
→ RECORD EVIDENCE
→ GENERATE CANDIDATE
→ SCORE
→ SELECT OR REJECT
→ CREATE MISSION
→ PLAN
→ DISPATCH SPECIALISTS
→ RESEARCH OR BUILD
→ TEST
→ INDEPENDENTLY VERIFY
→ ACCEPT, REPAIR OR REJECT
→ RECONCILE SIDE EFFECTS
→ MEASURE OUTCOME
→ UPDATE KNOWLEDGE
→ SELECT NEXT ACTION

Requirements:

- use real monitored sources;
- complete multiple useful loops without owner prompting;
- operate on at least one real registered project with bounded scope;
- prove one FAIL → repair → fresh PASS cycle;
- prove interrupted-build recovery without duplicate mutation;
- reconcile actual remote Git state;
- reconcile an actual safe deployment target;
- create maintenance work when needed;
- select another valuable mission after completion.

Synthetic database events and unit tests do not satisfy this operational gate.

Do not push or deploy unrelated user projects merely to satisfy evidence.

### V4 ACTIVATION RULE

V4 remains inactive until:

- V2 engineering PASS;
- V2 operational PASS;
- V3 engineering PASS;
- V3 operational PASS;
- state files are reconciled;
- evidence is durable;
- no severity-one defect remains;
- Supervisor and STOP remain healthy.

### REPORTING

Use one stable owner-visible status task where possible.

At every checkpoint report:

- version and work package;
- engineering verdict;
- operational verdict;
- promotion hold;
- HEAD and worktree state;
- Supervisor health;
- live database schema and meaningful counts;
- tests and exact results;
- verifier identity;
- failures and repairs;
- remaining uncertainty;
- next action.

Use only:

PASS
FAIL
BLOCKED
NOT_TESTED

Never say "fully accepted" without stating whether it means engineering or operational acceptance.

### COMPLETION CONDITION

This directive completes only when:

1. project state is internally consistent;
2. engineering and operational gates are separate;
3. V1's shortened reliability proof is explicit;
4. missing V2 live integration is implemented;
5. V2 receives independent operational PASS;
6. evidence has durable privacy-safe manifests;
7. moving-target verification is eliminated;
8. V3 completes multiple real autonomous loops;
9. interruption, repair, Git and deployment reconciliation are proven;
10. V3 receives independent operational PASS;
11. only then V4 activates;
12. the original complete JARVIS vision remains unchanged.

### START NOW

1. Inspect live truth.
2. Preserve active work.
3. Finish any running bounded Slice-2 verification.
4. Record this directive and promotion hold.
5. Reconcile state files.
6. Reclassify V1, V2 and V3.
7. Complete V2 operational integration.
8. Verify it independently.
9. Complete real V3 operational loops.
10. Activate V4 only after both gates pass.

Do not ask for routine confirmation.

Preserve existing work.

Repair truth before promotion.

Continue building the full JARVIS we designed.

---

## Ledger Grounding

- `owner_directive.recorded` — seq 5486 — payload carries the operative
  clauses of this directive (idempotency key
  `owner_directive.recorded:DIR-2026-08-12-master-plan-alignment`).
- `control.changed` — seq 5487 — VERSION PROMOTION HOLD for V1–V4.
- `observation.started` — seq 5488 — V1 reliability observation window.
- Directives projection: `DIR-2026-08-12-master-plan-alignment` active=1.
