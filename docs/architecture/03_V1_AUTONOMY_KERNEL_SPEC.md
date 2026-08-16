# JARVIS FOUNDRY V1 - AUTONOMY KERNEL SPECIFICATION

Version: 1.2
Status: HISTORICAL SPECIFICATION - IMPLEMENTATION COMPLETE (conformance: docs/CAPABILITY_MATRIX.md)
Parent architecture: JARVIS Foundry V3
Date: 2026-08-11 (status header reconciled 2026-08-16)

> **Reading rule:** this document is the original V1 build specification,
> preserved for auditability. The autonomy kernel it specifies is
> IMPLEMENTED and gate-proven (WP0–WP8, V7 adoption, live runtime).
> Current truth: `../CURRENT_STATE.md`. Old status lines below are
> HISTORICAL.

## 1. V1 Objective

Prove the smallest reliable autonomous loop:

    PERSIST STATE
    -> OBSERVE MISSION STATE
    -> SELECT NEXT USEFUL ACTION
    -> EXECUTE INSIDE A REGISTERED WORKTREE
    -> INDEPENDENTLY VERIFY
    -> ACCEPT, REPAIR, REJECT, OR BLOCK
    -> WRITE OPERATIONAL AND OBSIDIAN STATE
    -> SELECT THE NEXT USEFUL ACTION
    -> REPEAT WITHOUT OWNER PROMPTING

The V1 success statement is:

> JARVIS can select and continue useful work on one mission without Blendi
> sitting beside it, while preserving state, checking itself, recovering from
> failure, respecting vetoes, and knowing what to do next.

## 2. V1 Is Not The North Star

V1 does not yet implement:

- broad online opportunity discovery;
- multi-project autonomous discovery;
- whole-vault reading or rewriting;
- whole-computer operation;
- direct edits to an owner's normal working tree;
- public actions;
- Git push, pull requests, merges, and deployments;
- WhatsApp;
- voice and wake word;
- command-center UI;
- multi-project portfolios;
- large or nested agent swarms;
- autonomous model-threshold tuning;
- self-modification or self-promotion;
- live/candidate release switching;
- later capability stages;
- purchases or new paid services.

These are temporary technical staging limits, not permission boundaries. After
V1 passes, JARVIS automatically begins the next capability stage and continues
expanding toward the full North Star.

## 3. V1 Runtime Boundary

### 3.1 Trust and capability stage

Initial state:

```yaml
trust_state: LAB
capability_stage: WORKTREE_READY
active_version: V1
active_missions_max: 1
active_builders_max: 1
active_verifiers_max: 1
```

During kernel acceptance, V1 initially operates through:

- `%HERMES_HOME%\foundry\` V1 state;
- the registered isolated Git worktree;
- `C:\Users\b\Blendi\JARVIS\V1\`;
- bounded temporary and build-output directories created for V1.

The executive autonomously selects the first suitable proving repository from
accessible projects after inspecting goals, repository state, reversibility,
testability, and expected value. It creates a resource record and before-state
snapshot. It does not ask the owner to select the project.

### 3.2 Source repository

When START BUILD is received, create a separate development
checkout from the clean installed Hermes source. Do not edit the live Hermes
installation in place.

The development repository must:

- retain Nous Hermes as fetch-only upstream;
- record remote and push behavior before use;
- preserve the verified base commit in history;
- follow Hermes repository development instructions;
- use profile-safe paths;
- keep Foundry capability away from normal Hermes tool schemas when inactive;
- build and test without using the owner's live `HERMES_HOME`.

### 3.3 Process layout

V1 runs these processes:

1. `jarvis-supervisor`: independent deterministic controller.
2. Hermes Gateway: agent execution substrate.
3. At most one active executive, builder, or verifier session as the mission
   state requires.

The Supervisor is started independently through Windows Task Scheduler with:

- trigger at owner logon;
- restart after failure at 1, 5, and 15 minute backoff;
- a hidden window;
- an absolute executable path;
- an explicit `HERMES_HOME`;
- maximum one running instance;
- a shutdown grace period;
- no requirement that Hermes Desktop be open.

The task is created automatically after WP7 passes. The single `START BUILD`
event is the only launch instruction; V1 does not request a second activation.

## 4. V1 Package Boundaries

Use focused modules rather than growing Hermes god-files:

```text
foundry/
  __init__.py
  config.py
  paths.py
  clock.py
  domain.py
  events.py
  database.py
  migrations.py
  projections.py
  supervisor/
    service.py
    lifecycle.py
    health.py
    recovery.py
    estop.py
    scheduler.py
  control/
    registration.py
    policy.py
    action_envelope.py
    capabilities.py
  missions/
    state_machine.py
    selector.py
    dispatcher.py
    results.py
  agents/
    roles.py
    prompts.py
    hermes_adapter.py
    model_router.py
  verification/
    evidence.py
    verdicts.py
    gate.py
  obsidian/
    policy.py
    writer.py
    snapshots.py
  cli/
    main.py
tests/foundry/
```

Gateway integration is a narrow adapter used to request and observe agent
sessions. The Supervisor must remain testable with a fake Hermes adapter.

## 5. V1 Configuration

Behavioral configuration belongs in `config.yaml`, not `.env`.

Minimum configuration:

```yaml
foundry:
  enabled: false
  version: v1
  identity: JARVIS
  timezone: Europe/Budapest
  database_path: foundry/foundry.db
  emergency_stop_path: foundry/EMERGENCY_STOP
  heartbeat_seconds: 60
  stall_after_seconds: 300
  worker_lease_seconds: 900
  maximum_active_missions: 1
  maximum_active_builders: 1
  maximum_active_verifiers: 1
  trust_state: LAB
  capability_stage: WORKTREE_READY
  obsidian:
    vault_path: C:\Users\b\Blendi
    v1_write_root: JARVIS\V1
  external_actions: not_ready_in_v1
  purchases: not_ready_in_v1
  git:
    local_commits: true
    push: not_ready_in_v1
  models:
    executive_role: executive
    builder_role: builder
    verifier_role: verifier
    require_provider_diversity_when_available: true
```

Secrets remain in existing authenticated providers. V1 does not add new raw
credential configuration.

## 6. Minimum Domain Model

### 6.1 Event

Fields:

- ID and schema version;
- event type;
- occurred and recorded timestamps;
- source and correlation IDs;
- causation ID;
- idempotency key;
- payload and hash.

### 6.2 OwnerDirective

Fields:

- directive ID;
- command type;
- exact owner text;
- authenticated source;
- target mission or global target;
- durable constraint when applicable;
- created, applied, revoked timestamps;
- causation event.

V1 commands:

- `STOP_EVERYTHING`;
- `STOP_MISSION`;
- `VETO_ACTION`;
- `ADD_CONSTRAINT`;
- `RESUME`;
- `STATUS`.

### 6.3 ResourceRecord

Fields:

- registration ID;
- repository path and resolved Git root;
- baseline commit;
- isolated worktree path;
- observed branch and remote behavior;
- current target and non-target paths;
- build and test commands;
- discovered local services;
- rollback procedure;
- autonomously selected Obsidian context notes;
- autonomous readiness event;
- readiness state;
- applicable owner vetoes.

### 6.4 Mission

Fields:

- mission ID;
- goal-aligned objective;
- resource record ID;
- current state;
- acceptance criteria;
- active owner constraints and vetoes;
- current hypothesis;
- next-action requirement;
- remaining budget;
- created and updated timestamps;
- terminal outcome.

V1 mission states:

```text
PLANNED
-> ORIENTING
-> ACTION_SELECTION
-> READY
-> EXECUTING
-> VERIFYING
-> ACCEPTING | REPAIRING | REJECTING | BLOCKED
-> ACTION_SELECTION
-> COMPLETE | CANCELLED
```

`COMPLETE` is allowed only when the mission-level acceptance criteria and
post-completion state contract say no further action is required. Completing a
step returns to `ACTION_SELECTION`.

### 6.5 MissionStep

Fields:

- step ID and mission ID;
- role and objective;
- allowed and forbidden scope;
- expected result;
- acceptance criteria;
- dependencies;
- status;
- attempt number;
- lease owner and expiry;
- action IDs;
- evidence bundle ID;
- verifier verdict;
- repair lineage.

### 6.6 ActionEnvelope

Use the architecture-contract fields. V1 supports only:

- read registered files;
- write registered worktree files;
- run registered local commands;
- create bounded temporary artifacts;
- create a local Git commit after PASS;
- write governed V1 Obsidian notes;
- restore V1-owned mutations.

### 6.7 AgentRun

Fields:

- run ID;
- role;
- provider and model actually used;
- mission and step IDs;
- context manifest hash;
- tool policy;
- start, heartbeat, and finish times;
- process or session handle;
- result envelope;
- cost or quota usage when available;
- failure class.

### 6.8 EvidenceBundle

Fields:

- bundle ID;
- objective and criteria;
- before-state references;
- repository baseline and final diff;
- files changed;
- commands and tests run;
- raw result artifact hashes;
- customer or user-facing artifact references when applicable;
- after-state references;
- known uncertainty;
- rollback proof;
- builder claim;
- verifier verdict.

### 6.9 VerificationVerdict

Fields:

- verdict ID;
- independent agent run ID;
- `PASS`, `FAIL`, `BLOCKED`, or `NOT_TESTED`;
- criterion-by-criterion findings;
- reproduced evidence;
- missing proof;
- scope violations;
- repair requirements;
- timestamp.

## 7. Event Types

V1 implements these events:

```text
supervisor.started
supervisor.stopped
supervisor.heartbeat
supervisor.degraded
emergency_stop.requested
emergency_stop.frozen
emergency_stop.reconciled
owner_directive.recorded
owner_directive.applied
project.registered
project.revoked
mission.created
mission.state_changed
step.created
step.ready
step.claimed
step.heartbeat
step.completed
step.failed
step.lease_expired
action.requested
action.allowed
action.vetoed
action.not_ready
action.started
action.completed
action.verification_required
action.rolled_back
evidence.created
verification.started
verification.passed
verification.failed
verification.blocked
obsidian.write_started
obsidian.write_completed
obsidian.conflict
obsidian.rolled_back
git.local_commit_created
provider.unavailable
provider.recovered
```

## 8. Agent Roles

### 8.1 Executive

The executive is a fresh bounded session invoked only when mission state
requires judgment.

It receives:

- mission contract;
- resource record;
- current project state;
- prior accepted and failed steps;
- unresolved verifier findings;
- active owner directives;
- available role capabilities;
- remaining mission budget.

It must return a structured `NextActionProposal`:

```yaml
proposal_id:
mission_id:
action_kind: inspect | research | implement | test | repair | document | measure | complete | block
objective:
why_now:
expected_value:
scope:
dependencies:
acceptance_criteria:
verification_required:
rollback_expectation:
```

The Supervisor validates goal alignment, active owner vetoes, target identity,
capability readiness, risk, and recovery before creating a step.

The executive keeps at most one mission active in V1. It may select the next
mission, register another resource, and propose the next capability stage after
the current gate passes. It does not activate untested functionality directly.

### 8.2 Builder

The builder receives one step and one worktree. It may inspect broader
registered context but limits mutation to the step target so evidence,
rollback, and attribution remain clear.

It must:

- inspect before editing;
- preserve user work;
- record before state;
- implement the smallest correct change;
- run required verification;
- produce a result envelope;
- avoid local commit until independent PASS;
- stop after the bounded step.

### 8.3 Verifier

The verifier is a fresh session and is read-only except for its own evidence
report in Foundry state or the V1 audit area.

It receives actual diffs and artifacts, not hidden builder reasoning. It maps
every acceptance criterion to reproduced evidence and attempts to disprove the
builder's claim.

Use a provider different from the builder when a configured eligible provider
is available. Record when this independence target cannot be met.

## 9. Context Manifest

Every agent run receives a persisted manifest listing:

- architecture-contract version and hash;
- V1-spec version and hash;
- mission and step versions;
- resource record ID and hash;
- repository baseline and working-tree status;
- exact files supplied;
- exact Obsidian notes supplied;
- owner directives supplied;
- tool policy;
- model role and fallback decision;
- prompt template version.

The context manifest is evidence. It prevents later claims that an agent saw a
rule or note that was not actually supplied.

V1 agent context is limited to:

- `00_READ_FIRST.md` and applicable contract excerpts;
- V1 task contract;
- registered repository instruction files;
- `Agent Shared Context.md` when available;
- mission-relevant project notes selected by retrieval;
- relevant `JARVIS/V1` notes.

V1 does not index or inject the entire vault.

## 10. Supervisor Loop

The Supervisor is event-driven. Its deterministic loop is:

```text
START
-> OPEN DATABASE AND VERIFY INTEGRITY
-> READ EMERGENCY STOP
-> RECOVER INCOMPLETE EVENTS AND EXPIRED LEASES
-> VERIFY RESOURCE AND MISSION PROJECTIONS
-> START EVENT WATCHERS
-> WAIT FOR EVENT OR HEARTBEAT

ON EVENT:
-> APPEND OR DEDUPLICATE EVENT
-> APPLY DETERMINISTIC PROJECTION
-> EVALUATE STOP AND HEALTH
-> IF JUDGMENT REQUIRED, START EXECUTIVE
-> VALIDATE NEXT ACTION PROPOSAL
-> CREATE READY STEP
-> DISPATCH APPROPRIATE ROLE
-> TRACK LEASE AND HEARTBEAT
-> RECORD RESULT
-> ROUTE TO VERIFICATION
-> APPLY VERDICT
-> RETURN MISSION TO ACTION_SELECTION

ON HEARTBEAT:
-> CHECK PROCESS HEALTH, LEASES, STALLS, DISK, DB, AND STOP
-> DO NOT CALL AN LLM WHEN NO JUDGMENT IS NEEDED
```

At most one state transition for a given idempotency key is accepted.

## 11. Next-Action Policy

V1 must prove autonomy rather than a fixed pipeline.

The executive selects the next action within durable goals, the standing
mandate, current evidence, and active owner vetoes. Eligible action classes
are:

- inspect current implementation or evidence;
- reduce an identified uncertainty;
- implement a required behavior;
- add or improve relevant tests;
- reproduce a failure;
- repair a failed verification finding;
- validate the actual user-facing result;
- reconcile durable project state;
- measure whether an accepted change met its goal;
- declare a genuine blocker;
- propose mission completion.

Selection order:

1. unresolved FAIL findings;
2. incomplete mandatory acceptance criteria;
3. blockers that can be resolved with available capabilities;
4. highest-value dependency for remaining criteria;
5. realistic user-facing validation;
6. state and evidence reconciliation;
7. mission completion evaluation.

The executive may not select cosmetic cleanup, generalized infrastructure,
unrequested refactors, future-phase capability, or a new product idea.

If no useful action exists, it records one of:

- `WAITING_FOR_EVENT`;
- `VETOED_BY_OWNER`;
- `BLOCKED_EXTERNAL_DEPENDENCY`;
- `MISSION_COMPLETE`.

It does not invent activity.

## 12. Failure And Repair

### 12.1 Worker failure

On worker crash or lease expiry:

1. inspect process state;
2. capture partial worktree diff and logs;
3. determine whether mutation occurred;
4. preserve useful bounded work;
5. create a failure event;
6. ask the executive for repair, retry, or rollback;
7. increment attempt count;
8. use a fresh worker for retry.

After two failed repair cycles for the same cause, require a fresh diagnosis
and independent critic. After four, replan with a different strategy or mark a
precise technical blocker without weakening acceptance criteria. Do not ask
for permission merely because repair is difficult.

### 12.2 Provider outage

Use configured role fallbacks. If none satisfy the role, persist
`provider.unavailable`, release the lease, and retry with backoff. Do not silently
substitute an unknown model.

### 12.3 Network outage

Local deterministic work may continue. Network-required work becomes waiting,
not failed, until retry budget is exhausted.

### 12.4 Disk pressure

At configured low-space threshold:

- stop new build dispatch;
- preserve state;
- finish only safe in-flight database commits;
- inventory exact high-volume paths;
- snapshot when necessary;
- delete or archive the lowest-value safe data autonomously;
- verify recovered capacity and report what changed.

### 12.5 Database inconsistency

Freeze dispatch. Preserve database and WAL files. Run integrity and event-
projection reconciliation. Rebuild projections from events when safe. Never
delete the database as a first recovery action.

## 13. V1 Obsidian Contract

V1 creates and owns only:

```text
C:\Users\b\Blendi\JARVIS\V1\
  00 - V1 State.md
  01 - Registered Project.md
  02 - Active Mission.md
  03 - Decisions.md
  04 - Evidence Index.md
  05 - Failure and Repair Log.md
  06 - Lessons.md
  07 - Daily State.md
  Audit\
```

All V1 notes use:

```yaml
origin: jarvis
current_steward: jarvis
system: jarvis-foundry
version: v1
```

V1 may search and read the entire available vault. During kernel acceptance it
writes only this V1 projection area so write recovery is proven before broader
mutation. The next capability stage automatically expands whole-vault write
operation.

Each write uses expected hash, before snapshot, atomic replace, parse check,
after hash, audit event, and rollback pointer.

## 14. Git Contract

V1 work occurs only in the registered isolated worktree.

Before each step:

- record repository root;
- record HEAD;
- record branch;
- record `git status --short`;
- distinguish pre-existing changes from JARVIS changes;
- preserve an unexpectedly dirty worktree and create or select an isolated
  worktree that does not overwrite existing changes.

After verifier PASS:

- create one coherent local commit for the accepted step;
- include mission and step IDs in commit metadata or audit record;
- verify the commit contains only accepted JARVIS changes;
- keep remote push inactive during the kernel proving stage because external
  Git recovery has not yet passed its capability gate.

This is a technical readiness limit. Later stages activate push, pull request,
merge, and deployment automatically after their tests pass.

On FAIL, do not commit. Preserve bounded work for repair or perform the tested
rollback.

## 15. V1 CLI And External Controls

Provide a separate local `jarvis` CLI:

```text
jarvis status
jarvis doctor
jarvis stop
jarvis stop --mission <id>
jarvis veto --action <id> --reason <text>
jarvis constrain --text <rule>
jarvis resume
jarvis events --since <timestamp>
jarvis mission show
jarvis resource show
```

The CLI writes authenticated local directives or the sentinel without requiring
Hermes.

V1 also supplies:

- sentinel-file STOP;
- a minimal Windows tray STOP control;
- a local emergency hotkey that creates the sentinel.

No command-center UI is built in V1.

## 16. V1 Implementation Work Packages

### WP0 - Specification and baseline lock

- Verify the live Hermes source commit, current repository state, Python and
  Node runtimes, Hermes tests, gateway behavior, and Windows paths.
- Record a baseline report. Planning-time facts are not accepted as current.
- Create the separate development checkout automatically after `START BUILD`.

Gate: unchanged fork builds and the required Hermes baseline tests pass.

### WP1 - Domain and event store

- Implement configuration, path resolution, schema migrations, events,
  projections, trust state, directives, registration, missions, steps, agent
  runs, evidence, and verdict persistence.
- Add concurrency, corruption, duplicate-event, and interrupted-write tests.

Gate: projections rebuild from events and survive forced interruption.

### WP2 - Independent Supervisor and STOP

- Implement standalone lifecycle, heartbeat, health, recovery, leases,
  sentinel, CLI, tray, hotkey, and freeze reconciliation.
- Use a fake Hermes adapter for deterministic tests.

Gate: STOP and recovery work while Hermes is absent or deliberately dead.

### WP3 - Autonomous action control and resource registry

- Implement standing-mandate evaluation, owner-veto checks, normalized paths,
  automatic resource records, action envelopes, before-state capture, and
  short-lived capabilities.
- Activate only capabilities whose readiness tests pass while automatically
  probing the next capability stage.

Gate: path traversal, symlink ambiguity, fuzzy targets, active vetoes, and
unready capabilities are handled deterministically without asking permission.

### WP4 - Hermes role adapter

- Implement abstract role routing, context manifests, structured executive,
  builder, and verifier results, leases, heartbeats, and provider fallback.
- Do not add Foundry tools to normal Hermes sessions.

Gate: a fake mission dispatches three isolated role sessions and records exact
context and provider identity.

### WP5 - Mission and verification loop

- Implement next-action proposal validation, step dispatch, result intake,
  independent verification, repair, rejection, blocking, and local commit after
  PASS.

Gate: the loop repairs a deliberately failed step and continues to the next
useful action without owner prompting.

### WP6 - Governed V1 Obsidian write-back

- Implement V1 folder creation, note metadata, transactional writes,
  snapshots, conflicts, audit links, and rollback.

Gate: concurrent edit and rollback tests preserve both meaning and bytes.

### WP7 - V1 integration and activation package

- Produce installer and uninstaller for only the V1 Supervisor task, CLI,
  tray control, configuration, and Foundry profile.
- After this gate passes, install and activate the V1 Supervisor automatically;
  the `START BUILD` event already authorized continuous progression.

Gate: clean install, disabled state, activation, restart, STOP, resume, and
uninstall are reproducible.

### WP8 - Seven-day acceptance

- Select and register the highest-value reversible proving project.
- Run `04_V1_ACCEPTANCE_GATE.md` and probe the next capability stage.
- Issue an independent gate verdict.

Gate: PASS changes trust, activates continuous operation, and queues V2
automatically.

## 17. V1 Completion State

On V1 gate PASS:

```yaml
trust_state: TRUSTED_AUTONOMOUS
capability_stage: WORKTREE_READY
active_version: V1
next_version: V2_KNOWLEDGE_BRAIN
next_version_state: QUEUED_AUTOMATICALLY
```

V1 continues operating while the executive starts V2 capability work. The
system does not stop at the gate or request owner permission.

