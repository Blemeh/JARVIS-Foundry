# JARVIS FOUNDRY - ARCHITECTURE CONTRACT

Version: 4.0
Status: BINDING AUTONOMY-FIRST CONTRACT - IMPLEMENTATION LIVE
Date: 2026-08-11 (status header reconciled 2026-08-16)

> Current-state reading rule: this contract is binding and permanent.
> Implementation status lives in `../CURRENT_STATE.md` and
> `../CAPABILITY_MATRIX.md`.

## 1. Contract Purpose

This document defines the permanent architectural invariants for every JARVIS
Foundry version. JARVIS may evolve the architecture through the R3 candidate,
verification, canary, and rollback process. It may not weaken owner STOP,
active vetoes, evidence integrity, audit continuity, or verifier independence.

The contract governs:

- process separation;
- trust and standing autonomy;
- action execution;
- emergency stop;
- credentials and secrets;
- operational state;
- epistemic memory;
- Obsidian ownership;
- agent and model independence;
- verification;
- recovery;
- self-evolution;
- auditability.

## 1a. Established Tier Architecture (V8, canonical)

The canonical implementation has legitimately established the following
permanent structure (schema v33):

```text
TIER A — AUTHORITATIVE OPERATIONAL TRUTH       (ledger, events, STOP, directives,
                                              verdicts, evidence, missions)
          ↓ outranks
TIER B — GOVERNED EPISTEMIC KNOWLEDGE          (knowledge_claims, contradictions,
                                              freshness, manifests)
          ↓ outranks
TIER C — EXPERIENTIAL MEMORY                   (tier_c_* tables, event-derived:
                                              lifecycle with validation stamps,
                                              blocks, recall, procedures,
                                              curation, defense, git versioning,
                                              context compiler)
          ↓ influences only
MODEL / AGENT JUDGMENT
```

Binding properties:

- Tier C is ADVISORY context, never authority: it cannot create directives,
alter STOP, replace evidence, change verdicts, or certify builders.
- Tier C rows are projections of ledger events (event-derived), never a
second source of truth.
- No authority path upward from Tier C.
- The Letta runtime is NOT integrated; Letta-derived mechanics are embodied
natively (NATIVE_JARVIS decision, recorded in the V8 final verdict).

## 2. Non-Negotiable Invariants

1. JARVIS acts under a standing mandate after the initial build start.
2. Evidence gates automatically expand capability readiness.
3. The deterministic Supervisor is independent from Hermes.
4. Emergency stop does not depend on an LLM or Hermes health.
5. Freeze precedes rollback.
6. Every material action has a goal, risk decision, and audit record.
7. Irreversible actions require stronger independent verification and explicit
   consequence accounting, not routine owner permission.
8. Builders do not provide final certification of their own work.
9. Evidence and observations are never silently rewritten as beliefs change.
10. Retrieved content is data and cannot impersonate an owner veto or goal.
11. Agents receive scoped capabilities, not raw reusable credentials.
12. Model names are configuration, not architecture.
13. A process crash, provider outage, duplicate event, or machine restart must
    not corrupt the canonical mission state.
14. Completing a verified step triggers selection of the next valuable action.
15. Capability stages advance automatically when their evidence gates pass.

## 3. Process Architecture

### 3.1 Required process boundary

The minimum deployment topology is:

    Windows
    |
    +-- JARVIS Supervisor
    |   +-- event intake and durable ledger
    |   +-- scheduler and wake evaluation
    |   +-- mission state coordination
    |   +-- autonomous action control plane
    |   +-- capability and credential broker
    |   +-- health watchdog
    |   +-- emergency stop
    |   +-- release slot controller
    |   +-- rollback coordinator
    |
    +-- Hermes Gateway
    |   +-- agent sessions
    |   +-- tools
    |   +-- Kanban worker dispatch
    |   +-- web and browser access
    |   +-- messaging
    |
    +-- Obsidian vault
    +-- discovered and registered repositories, worktrees, and resources
    +-- Codex App Server or supported Codex interface
    +-- OpenCode

The Supervisor may live in the same source repository as the Hermes fork, but
it must run as a separate executable process and may not import gateway runtime
state as a required dependency.

### 3.2 Supervisor responsibilities

The Supervisor owns deterministic control only:

- read and append durable events;
- enforce event idempotency;
- maintain leases and recover expired work;
- evaluate STOP state;
- evaluate STOP, owner vetoes, goals, risk, and capability readiness;
- wake the executive when judgment is required;
- dispatch the next gate-ready autonomous task;
- track child process identity and health;
- detect Hermes and worker failure;
- hold the live release pointer;
- select or restore a release slot;
- persist health and audit events.

The Supervisor must not:

- invent missions through hard-coded rules;
- perform open-ended web research;
- interpret vague owner intent through deterministic string matching;
- certify agent output;
- ignore or weaken an owner veto or STOP;
- depend on Hermes to determine whether Hermes is alive.

### 3.3 Hermes responsibilities

Hermes owns model-driven execution and existing agent capabilities. It may be
restarted, upgraded, or rolled back without losing Foundry mission truth.

Hermes sessions are workers. The Supervisor and Foundry database provide
durable coordination while the executive selects autonomous work.

### 3.4 Windows lifecycle

The Supervisor is started by a Windows startup mechanism with:

- run at owner logon;
- restart on non-zero exit;
- bounded restart backoff;
- no visible console window during normal operation;
- a deterministic working directory;
- profile-safe `HERMES_HOME` resolution;
- logs outside the Hermes process;
- no dependency on the Hermes Desktop window being open.

The PC-only architecture means JARVIS is offline while Windows is powered off
or suspended. On resume, the Supervisor performs recovery and catch-up from
durable state. It never claims uninterrupted wall-clock operation while the PC
was unavailable.

## 4. Trust Contract

Trust describes observed reliability. Evidence-backed trust transitions may
automatically activate the next capability stage under the standing mandate.

Allowed trust states:

| State | Meaning |
|---|---|
| `DESIGN_ONLY` | No runtime exists or implementation is not activated. |
| `LAB` | Runtime exists only in controlled proving conditions. |
| `PROVEN` | A bounded acceptance gate passed. |
| `TRUSTED_AUTONOMOUS` | Repeated unattended evidence supports continuous autonomous operation. |
| `DEGRADED` | Health or behavior no longer meets the active trust gate. |
| `SUSPENDED` | Owner or watchdog froze operation. |

Trust transitions require a named gate report with evidence. A failed
mandatory criterion prevents promotion. Material regressions may demote trust
automatically to `DEGRADED` or `SUSPENDED`.

Trust promotion may expand capability readiness, resources, tools, providers,
and operating scope automatically when the next-stage gate defines and tests
that expansion. It never overrides an active owner veto or STOP.

## 5. Standing Autonomy Contract

### 5.1 Default decision

After `START BUILD`, the default decision is `ACT` when an action:

- advances a durable goal or resolves a valuable unknown;
- is technically available;
- is lawful;
- does not conflict with an authenticated owner veto or STOP;
- has an evidence, verification, and consequence plan proportionate to risk.

JARVIS does not wait for per-action, per-resource, per-project, per-version,
external-action, financial-action, or self-improvement permission.

### 5.2 Capability readiness

Capability states replace authority levels:

| State | Meaning |
|---|---|
| `DISCOVERED` | The resource or tool exists but has not been tested. |
| `PROBING` | JARVIS is running a reversible readiness experiment. |
| `READY` | Tests, recovery, identity, and observability are sufficient for use. |
| `ACTIVE` | The capability is serving autonomous missions. |
| `DEGRADED` | Use is reduced while JARVIS repairs or reroutes. |
| `VETOED` | An authenticated owner directive prohibits use. |
| `STOPPED` | Global or scoped STOP prevents use. |

JARVIS moves capabilities through these states automatically. Risk changes the
strength of evidence and canary duration, not whether the owner is asked.

### 5.3 Owner control

The owner may add or remove goals, constraints, vetoes, budgets, and STOP
states at any time. Revocation is immediate. In the absence of such a
directive, the standing mandate remains active.

Public communication, Git operations, deployments, purchases, account actions,
local computer operation, and representational actions are autonomous when the
required capability is READY. JARVIS represents itself transparently and
records identity, cost, recipient, effect, and evidence.

## 6. Autonomous Resource Registry

JARVIS discovers accessible resources and registers them automatically before
material use. Registration is a technical identity and recovery record, not
an owner enrollment request.

Every registered repository records:

- stable resource ID;
- absolute canonical repository path and resolved Git root;
- baseline commit and dirty state;
- branch or worktree identity;
- relevant build, test, and health commands;
- remote and deployment behavior;
- service and port state;
- snapshot and rollback procedure;
- current owner vetoes;
- readiness evidence and current capability state.

Every registered folder or application records:

- canonical identity and resolved target;
- supported read, append, patch, rewrite, create, delete, and execute actions;
- data classification and secret exposure risk;
- snapshot, restore, conflict, and concurrency behavior;
- current owner vetoes;
- readiness evidence and current capability state.

No fuzzy path, unresolved environment variable, wildcard root, drive root, or
symlink target may obscure what will be changed. JARVIS may operate broadly,
but every actual target is resolved, logged, and independently verifiable.

## 7. Action Envelope And Policy Broker

Every material action is represented by an `ActionEnvelope` before execution.

Required fields:

```yaml
action_id:
mission_id:
step_id:
requested_by_agent_run_id:
action_type:
target_resources:
capabilities_required: []
capability_states_required: []
goal_alignment:
active_owner_vetoes_checked:
expected_effect:
risk_class:
reversibility:
before_state_capture:
verification_plan:
rollback_plan:
idempotency_key:
budget_estimate:
created_at:
```

Deterministic processing order:

1. Check emergency-stop state.
2. Resolve and normalize all targets.
3. Register or reconcile target identity and current state.
4. Evaluate goals, owner vetoes, lawfulness, risk, and capability readiness.
5. Evaluate secret and credential requirements.
6. Evaluate resource and monetary budgets.
7. Verify an executable recovery strategy where required.
8. Capture before state.
9. Issue short-lived capabilities.
10. Execute once under an idempotency key.
11. Capture after state.
12. Run deterministic verification.
13. Route evidence to independent verification when required.
14. Commit, retain for repair, freeze, or roll back.
15. Append the terminal event.

The policy broker returns exactly one of:

- `ALLOW`;
- `VETOED`;
- `NOT_READY`;
- `MISSING_RECOVERY_PATH`;
- `BUDGET_EXCEEDED`;
- `EMERGENCY_STOPPED`;
- `INVALID_TARGET`.

An LLM may explain a decision but may not override it.

## 8. Credential And Capability Broker

### 8.1 Principle

Agents receive a capability to perform an action, not a reusable secret.

Examples:

- `vertex.generate(model_role=verifier, budget_usd=0.40)`;
- `git.operate(repository=registered_repo, action=selected_action)`;
- `obsidian.patch(note_id, expected_hash)`;
- `browser.use(profile=research_readonly)`;
- `database.query(connection=project_readonly)`.

### 8.2 Broker behavior

The credential broker:

- stores no plaintext secret in prompts or mission records;
- resolves existing provider authentication at execution time;
- issues the narrowest possible short-lived action capability;
- binds capabilities to mission, agent run, target, and expiry;
- redacts secrets from stdout, logs, errors, evidence, and summaries;
- records secret identifier and use outcome without recording secret value;
- prevents workers from forwarding or persisting credentials;
- denies capability use after STOP or an applicable owner veto.

### 8.3 Protected credentials

Credentials, browser sessions, OAuth material, SSH keys, API keys, database
passwords, auth cookies, and provider refresh tokens remain broker-managed.
Broad autonomous operation does not require exposing raw credential values to
workers.

## 9. Emergency Stop Contract

### 9.1 Independent stop mechanisms

At minimum, supported runtime versions expose:

- sentinel file: `%HERMES_HOME%\foundry\EMERGENCY_STOP`;
- local CLI stop command;
- Windows tray stop action;
- registered local hotkey;
- command-center stop control when that UI exists;
- owner natural-language stop routed to the same deterministic mechanism.

Natural language is convenience, not the underlying control.

### 9.2 Freeze sequence

When STOP is detected:

1. Persist `emergency_stop.requested` with source and timestamp.
2. Reject every new `ActionEnvelope`.
3. Stop new executive, worker, and verifier creation.
4. Revoke issued capabilities where technically possible.
5. Cancel queued work.
6. Interrupt controllable active workers.
7. Disable candidate promotion and all external actions.
8. Capture in-flight action state.
9. Mark trust `SUSPENDED`.
10. Present a reconciliation report.

STOP does not automatically launch bulk rollback.

### 9.3 Reconciliation and rollback

After freeze, deterministic inspection classifies each in-flight action:

- no mutation occurred;
- mutation completed and verified;
- mutation completed but unverified;
- mutation partially completed;
- state cannot be determined.

Rollback is then performed one action at a time according to dependency order.
Ambiguous or potentially harmful rollback remains frozen for owner review.

Resume requires removal of the sentinel and an authenticated owner directive.
Resume begins with orientation and reconciliation, not blind continuation.

## 10. Operational State Contract

### 10.1 Canonical stores

Use three distinct truth classes:

- Foundry database: runtime coordination and append-only events;
- repositories and artifacts: implementation and execution truth;
- Obsidian: human-readable semantic and institutional memory.

Chat history is never canonical state.

### 10.2 Foundry database

The default local store is profile-safe SQLite in WAL mode:

`%HERMES_HOME%\foundry\foundry.db`

At minimum it stores:

- schema metadata;
- events;
- trust state;
- resource registry and capability readiness;
- authenticated owner vetoes and constraints;
- owner directives;
- missions and steps;
- leases and dispatch attempts;
- action envelopes;
- artifacts and hashes;
- verification verdicts;
- budgets and usage;
- agent runs;
- health samples;
- Obsidian changes and snapshots;
- release slots and migrations.

The Supervisor is the sole coordination writer for state transitions. Worker
results enter as events or result envelopes, preventing multiple agents from
mutating mission state directly.

### 10.3 Event envelope

Every event includes:

```yaml
event_id:
event_type:
occurred_at:
recorded_at:
source_type:
source_id:
correlation_id:
causation_id:
idempotency_key:
schema_version:
payload:
payload_hash:
```

Material terminal events are append-only. Corrections are new events that
reference the incorrect event.

### 10.4 Incomplete writes

Events are committed transactionally. A checksum or schema failure rejects the
event. Recovery handles:

- a duplicated event;
- an event written without its derived projection;
- a projection updated without a terminal event;
- an interrupted transaction;
- a corrupted final record;
- a stale lease after machine restart.

Projections are rebuildable from valid events.

## 11. Epistemic Memory Contract

### 11.1 Observation

An observation records what was directly encountered.

Required fields:

- observation ID;
- source ID;
- acquisition method;
- observed content or content hash;
- capture timestamp;
- observer identity;
- environment context;
- immutable status.

### 11.2 Evidence

Evidence is a preserved source or artifact supporting evaluation. It includes
provenance, timestamp, locator, integrity hash, and retrieval method.

Evidence may become unavailable later; its historical record remains.

### 11.3 Claim

A claim is a proposition derived from observations or evidence.

It stores:

- exact proposition;
- supporting and contradicting evidence IDs;
- confidence;
- temporal scope;
- source freshness;
- extraction method;
- verification state;
- superseding claim when applicable.

Conflicting claims coexist. The system never resolves conflict by deleting the
losing history.

### 11.4 Belief

A belief is JARVIS's current probabilistic interpretation. It references
claims, confidence, rationale, and last evaluation time.

Beliefs may change. Changing a belief does not mutate its evidence or past
claims.

### 11.5 Decision

A decision records selected action, alternatives, rationale, standing mandate,
owner-veto evaluation,
evidence considered, uncertainty, decision-maker, and outcome-measurement plan.

Accepted decisions are immutable records. A later decision may supersede one.

### 11.6 Unknown

An unknown states a question, why it matters, expected information value,
research cost, dependencies, and resolution evidence.

Unknowns do not silently become known because a model produced an answer.

## 12. Obsidian Contract

### 12.1 Access model

JARVIS may search, create, reorganize, patch, rewrite, archive, and delete
across the Obsidian vault without asking permission. Prompt construction still
retrieves only mission-relevant material to control privacy, cost, and context
quality.

Human-authored and JARVIS-authored notes are both part of the evolving
institutional brain. Ownership metadata preserves provenance and responsibility;
it does not create a permission checkpoint.

### 12.2 Required note governance

Governed notes support:

```yaml
origin: owner | jarvis | shared | imported
current_steward: owner | jarvis | shared
confidence:
provenance: []
last_verified:
expires:
canonical_source:
revision_id:
supersedes:
```

Missing metadata triggers autonomous classification and a snapshot before
mutation. JARVIS may improve the note after it establishes provenance and
current meaning.

### 12.3 Autonomous namespaces

The eventual JARVIS-managed areas are:

- `/JARVIS/World Model/`;
- `/JARVIS/Goals/`;
- `/JARVIS/Projects/`;
- `/JARVIS/Missions/`;
- `/JARVIS/Research/`;
- `/JARVIS/Experiments/`;
- `/JARVIS/Entities/`;
- `/JARVIS/Lessons/`;
- `/JARVIS/Briefings/`;
- `/JARVIS/Audit/`;
- `/JARVIS/System Evolution/`.

Immutable evidence and append-only audit records are corrected by superseding
records, never destructive rewriting. Active owner vetoes and STOP state live
in the durable control plane and cannot be disabled by an Obsidian edit.
Credentials remain in the credential broker, not in vault notes.

### 12.4 Write transaction

Before a governed note mutation:

1. resolve note identity, provenance, and current hash;
2. check STOP and applicable owner vetoes;
3. classify the expected semantic effect;
4. create a recoverable before snapshot;
5. apply the mutation atomically;
6. parse YAML and wikilinks;
7. verify expected semantic invariants;
8. record diff, mission, agent, evidence, and after hash;
9. update the search and graph index;
10. roll back automatically if verification fails.

Concurrent hash mismatch creates a conflict event. It never silently overwrites
the newer note.

Snapshots protect bytes. Provenance, immutable evidence, conflict handling,
semantic verification, and rollback protect meaning without requiring owner
approval.

## 13. Event-First Executive Contract

The executive wakes for events requiring judgment, including:

- owner directive;
- mission or step completion;
- verification verdict;
- worker failure;
- expired lease;
- new discovered or registered information;
- scheduled discovery window;
- project or metric event;
- provider availability change;
- budget threshold;
- system health degradation;
- explicit maintenance window.

The five-minute heartbeat performs deterministic stall detection. If no work
or anomaly exists, it records health without invoking an executive model.

Duplicate events share an idempotency key and wake the executive once.

## 14. Model And Agent Role Contract

### 14.1 Abstract roles

Architecture refers to roles, never required model brands:

- `executive`;
- `scout`;
- `researcher`;
- `architect`;
- `builder`;
- `tester`;
- `critic`;
- `verifier`;
- `repairer`;
- `knowledge_curator`.

Each role configuration contains:

```yaml
role:
preferred_provider:
preferred_model:
fallbacks: []
required_capabilities: []
maximum_context:
maximum_cost_per_run:
maximum_duration:
reasoning_profile:
tool_policy:
independence_group:
```

### 14.2 Current defaults

Current provider choices are deployment defaults, not contract requirements:

- routine scout: OpenCode Go / DeepSeek V4 Flash;
- researcher and critic: OpenCode Go / DeepSeek V4 Pro;
- builder: OpenCode Go / Kimi K2.7 Code;
- executive: OpenCode Go / Qwen3.8 Max;
- verifier: a fresh capable provider different from the builder when possible;
- premium multimodal or tie-break review: Vertex AI Gemini within budget;
- high-stakes architecture and Codex-history work: authenticated Codex surface.

Availability must be rechecked at runtime. A missing preferred model causes a
configured fallback or a precise blocked state, never an invented model name.

### 14.3 Independence

Final verification should satisfy:

`builder.provider != verifier.provider`

when configured capabilities make that possible. If not possible, use a fresh
isolated verifier session with no hidden builder reasoning and record the
reduced-independence limitation.

### 14.4 Current access inventory

The owner currently reports access to:

- ChatGPT Plus;
- OpenCode Go;
- Hermes;
- Obsidian;
- Google Cloud trial credit totaling USD 300.

This is a planning snapshot, not a capability grant or entitlement claim.
ChatGPT Plus is treated as an interactive thinking and handoff surface unless
a separately supported programmatic interface is verified. Provider model
availability, API access, authentication, rate limits, and billing eligibility
must be checked at activation time. JARVIS never translates `account access`
into a usable capability without verifying the actual programmatic interface.

## 15. Verification Contract

Every verification verdict is exactly one of:

- `PASS`;
- `FAIL`;
- `BLOCKED`;
- `NOT_TESTED`.

`BLOCKED` requires a genuine unavailable dependency, missing credential or
physical act, external outage, contradictory owner directives, or inaccessible
evidence. Engineering difficulty and missing permission are not blockers.

The verifier receives:

- objective and acceptance criteria;
- standing mandate, goals, owner vetoes, and expected blast radius;
- before state;
- actual diff and artifacts;
- commands and tests run;
- raw test outputs or hashes;
- evidence sources;
- known uncertainty;
- rollback plan.

The verifier attempts to disprove completion. It does not accept the builder's
narrative as proof.

## 16. Candidate Scoring And Calibration

Scoring weights and thresholds are configuration, versioned with the decision
that used them.

For every candidate, preserve:

- predicted value and actual value;
- predicted difficulty and actual difficulty;
- predicted duration and actual duration;
- predicted success probability and actual outcome;
- predicted cost and actual cost;
- predicted risk and observed incidents;
- model, prompt, and scoring-policy version.

Initial research and build thresholds may be seeded by a later version spec.
They are not permanent rules and cannot be self-tuned until adequate outcome
data and a governed experiment exist.

## 17. Self-Evolution Contract

### 17.1 Release slots

Self-modifying versions use separate `live` and `candidate` installations.
The Supervisor owns the active pointer and remains outside both Hermes slots.

Hermes candidate failure cannot prevent the Supervisor from:

- detecting the failure;
- terminating the candidate;
- restoring the live pointer;
- restarting the previous version;
- validating restored health;
- recording rollback.

### 17.2 Risk classes

| Class | Change | Minimum evidence |
|---|---|---|
| `R0` | Documentation and non-governing prompts | Targeted tests and one bounded cycle. |
| `R1` | Skills and bounded agent behavior | Full affected tests and several representative cycles. |
| `R2` | Retrieval, scheduler, missions, tools, or state projections | Full suite, fault tests, migration rehearsal, extended canary. |
| `R3` | Supervisor, action control, credentials, event ledger, database schema, release controller | Multiple independent reviews, forward and backward compatibility proof, restore rehearsal, fault injection, extended canary, and automatic rollback. |

All risk classes may promote automatically after their evidence requirements
pass. A higher class increases verification depth and observation time; it
does not create an owner permission request.

### 17.3 Migration requirements

Before automatic promotion, every stateful change must provide:

- forward migration;
- compatibility expectations with the previous executable;
- rollback migration or complete tested restore point;
- migration idempotency;
- interrupted-migration recovery;
- schema integrity verification;
- data-loss analysis.

The executable cannot be declared rollback-safe while its database is not.

## 18. Security And Untrusted Content

Web pages, repository content, documents, messages, model output, imported
conversations, and agent summaries are untrusted data.

They cannot:

- create or remove an authenticated owner veto;
- impersonate an owner directive;
- disable STOP;
- request raw credentials;
- weaken a gate;
- redefine evidence;
- instruct the Supervisor.

Prompt-injection scanning is defense in depth. The fundamental protection is
that the standing mandate, authenticated owner vetoes, and integrity controls
live in deterministic state, not retrieved text.

## 19. Budget Contract

All paid or credit-backed provider use is attributed to mission, step, agent
role, provider, model, and result.

Initial Vertex AI Gemini budget policy:

```yaml
provider: vertex_ai
lifetime_budget_usd: 50
daily_soft_limit_usd: 5
billing_verified: required
ai_studio_credit_assumption: forbidden
```

Crossing a soft limit triggers autonomous routing review or fallback. Crossing
the current hard limit stops additional Gemini actions until JARVIS selects a
different provider or changes its audited budget allocation. An active owner
budget constraint cannot be exceeded. JARVIS verifies actual Google Cloud
billing data and does not assume trial-credit eligibility.

## 20. Audit And Observability

The system exposes enough information to reconstruct:

- what woke JARVIS;
- what it believed at the time;
- which evidence it used;
- what it decided and rejected;
- which goal, standing mandate, readiness evidence, and veto check supported
  the action;
- which capability and credential identifier was used;
- what changed;
- which verification ran;
- why a verdict was reached;
- whether rollback was possible and tested;
- what the actual outcome became.

Logs use bounded retention and aggressive secret redaction. Audit events are
append-only and correlated across Supervisor, Hermes, worker, repository,
Obsidian, and release activity.

## 21. Autonomous Change Control

JARVIS may improve its architecture, trust model, action control, resource
strategy, Obsidian structures, models, budgets, release policy, and operating
procedures through the applicable R0-R3 candidate process.

Every accepted change requires:

- a measured problem or opportunity;
- explicit hypothesis and expected outcome;
- isolated candidate implementation;
- migration and restoration evidence where stateful;
- independent verification;
- a risk-class canary;
- automatic rollback criteria;
- durable before-and-after measurement;
- append-only release decision and audit evidence.

JARVIS may not evolve away the owner's ability to veto or STOP, forge owner
directives, allow a candidate to certify itself, destroy immutable evidence,
expose raw credentials unnecessarily, or remove the only recoverable live
copy. These invariants preserve owner control and institutional continuity;
they are not routine permission checkpoints.
