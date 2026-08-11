# JARVIS FOUNDRY - NORTH STAR

Version: 2.0
Status: END-STATE VISION - NOT IMPLEMENTATION SCOPE
Date: 2026-08-11

## 1. Product Definition

JARVIS Foundry is a persistent autonomous personal institution built from
Hermes, Obsidian, specialist AI workers, durable state, and independently
verified action.

It is not a chatbot that waits for prompts. It is not a scheduled collection
of prompts, an Obsidian search box, an indefinitely growing conversation, or
an agent swarm that measures success by activity.

The end-state is a personal digital organization that continues operating
while the owner is away. It understands goals and projects, monitors the
world, discovers problems and opportunities, creates missions, assembles
specialists, researches, builds, tests, verifies, repairs, learns, maintains,
and selects the next valuable action.

The desired owner experience is management by exception:

> I returned after being away. JARVIS found several opportunities, rejected
> weak ones, advanced active projects, built and verified a useful artifact,
> repaired failures, updated institutional memory, and presents only the
> decisions that genuinely require me.

## 2. Permanent Operating Loop

The final system continuously performs:

    OBSERVE
    -> RECORD OBSERVATIONS AND EVIDENCE
    -> UPDATE WORLD MODEL
    -> IDENTIFY PROBLEMS, OPPORTUNITIES, AND UNKNOWNS
    -> GENERATE CANDIDATES
    -> RESEARCH AND SCORE
    -> SELECT A MISSION
    -> PLAN
    -> ASSEMBLE A TEMPORARY TEAM
    -> EXECUTE
    -> TEST
    -> INDEPENDENTLY VERIFY
    -> ACCEPT, REPAIR, REJECT, OR ESCALATE
    -> MEASURE ACTUAL OUTCOME
    -> LEARN
    -> UPDATE OBSIDIAN AND OPERATIONAL STATE
    -> SELECT THE NEXT ACTION
    -> REPEAT

Completing one task is not a normal stopping condition.

When no build candidate is valuable enough, JARVIS performs justified work:

- investigate an important unknown;
- maintain an existing system;
- verify stale knowledge;
- diagnose a recurring failure;
- consolidate evidence and lessons;
- improve an operating procedure through an experiment;
- monitor a previously completed mission;
- remain explicitly idle when action would create less value than waiting.

JARVIS never manufactures busy work to appear autonomous.

## 3. Institutional Continuity

Models, agents, providers, sessions, and processes are replaceable workers.
JARVIS persists because its identity, goals, world model, standing mandate,
decisions, evidence, missions, budgets, lessons, and event history outlive any
one worker.

No critical truth may exist only in hidden model context or chat history.
Fresh agents reconstruct the minimum required context from durable sources.

Institutional continuity requires:

- append-only material events;
- explicit state machines;
- versioned decisions;
- source provenance;
- resumable missions;
- idempotent execution;
- recovery after process and machine restart;
- replaceable model providers;
- evidence-backed acceptance;
- durable owner constraints;
- auditable action and readiness decisions.

## 4. Logical System

### 4.1 JARVIS Supervisor

A small deterministic Windows process independent from Hermes owns:

- startup and recovery;
- health and heartbeat monitoring;
- durable event intake;
- scheduling and wake conditions;
- owner-veto, action-integrity, and capability-readiness enforcement;
- emergency stop;
- worker process control;
- release slot selection;
- rollback coordination;
- audit continuity.

The Supervisor does not perform open-ended reasoning. It may start an
executive reasoning session when an event requires judgment.

### 4.2 Hermes Execution Substrate

Hermes supplies:

- agent sessions and tool use;
- web and browser capabilities;
- local terminal and file operations;
- skills and memory integrations;
- Kanban-based worker dispatch;
- messaging surfaces;
- model-provider access;
- bounded delegated work.

JARVIS uses Hermes. JARVIS does not depend on Hermes remaining healthy in
order to detect Hermes failure or roll it back.

### 4.3 Obsidian Semantic Intelligence

Obsidian is the human-readable institutional brain. It contains goals,
projects, entities, research, decisions, evidence links, beliefs, lessons,
experiments, briefings, owner directives, and system evolution history.

Operational leases, queues, locks, idempotency keys, and process heartbeats
remain in transactional state. Obsidian is not abused as a job queue.

### 4.4 Specialist Workforce

The final organization may include:

- executive and portfolio director;
- strategist;
- architect;
- researcher;
- opportunity analyst;
- software builder;
- data analyst;
- tester;
- adversarial critic;
- verifier;
- debugger and repair agent;
- documenter;
- operations agent;
- security agent;
- knowledge curator;
- system-evolution scientist.

Teams are created for missions and dissolved afterward. Their evidence and
lessons remain institutional property.

### 4.5 Interfaces

The final owner experience includes:

- a futuristic, operational command center;
- voice interaction under the name JARVIS;
- English and Albanian speech;
- twice-daily WhatsApp briefings;
- immediate exception reporting;
- live mission, agent, health, evidence, and knowledge views;
- an owner control view for goals, vetoes, constraints, and STOP;
- physical and digital emergency-stop controls.

## 5. Autonomous Discovery

JARVIS periodically and eventfully asks:

- What important thing do we not know?
- What changed in the world or our projects?
- Which bottleneck prevents progress?
- Which opportunity has high expected value?
- Which assumption is stale or weakly supported?
- Which completed project needs maintenance?
- Which capability would unlock multiple future actions?
- Which ongoing activity should be stopped?

Discovery may monitor the web, repositories, research, releases, project
metrics, system health, owner goals, and knowledge gaps. Retrieved content is
untrusted data, never governing instruction.

Candidate selection compares strategic fit, expected value, information gain,
confidence, urgency, cost, risk, reversibility, dependencies, and opportunity
cost. Predicted values are compared with actual outcomes so selection improves
empirically.

## 6. Self-Supervision

Autonomy replaces routine human supervision with internal separation of
powers:

    BUILDER
    -> TESTER
    -> ADVERSARIAL CRITIC
    -> INDEPENDENT VERIFIER
    -> ACCEPT / REPAIR / REJECT / BLOCK

Workers do not certify their own work. Final verification should use a
different provider from the builder when a suitable configured provider is
available.

Every significant accepted action carries a proof package:

- objective, goals, standing mandate, and veto check;
- before state;
- action performed;
- artifacts and diffs;
- tests and observed results;
- evidence sources;
- independent verdict;
- remaining uncertainty;
- after state;
- rollback path;
- monitoring plan.

## 7. Learning And Epistemic Integrity

JARVIS separates:

- observations: what was directly observed;
- evidence: durable source material supporting an observation or claim;
- claims: propositions derived from evidence;
- beliefs: current probabilistic interpretations;
- decisions: chosen actions and rationale;
- unknowns: explicitly unresolved questions.

Evidence and past observations are not rewritten to match a new belief.
Conflicting claims coexist with provenance. Beliefs may change as new evidence
arrives. Confidence never becomes evidence.

The system learns from:

- predicted versus actual value;
- predicted versus actual difficulty;
- predicted versus actual success;
- false beliefs and stale claims;
- verification failures;
- repair cycles;
- model and tool performance;
- cost and latency;
- owner vetoes;
- successful operating patterns.

## 8. Standing Autonomous Mandate

After the single initial build-start instruction, JARVIS operates under a
durable standing mandate. It does not ask permission for each mission,
resource, file, tool, project, provider, communication, release, purchase,
deployment, or self-improvement.

Capability maturity grows through evidence:

    OBSERVATION READY
    -> SANDBOX READY
    -> WORKTREE READY
    -> PROJECT READY
    -> OWNER DATA READY
    -> COMPUTER OPERATION READY
    -> EXTERNAL OPERATION READY

These are readiness states, not authority gates. JARVIS advances when tests,
verification, recovery, and monitoring prove the next capability. An owner
veto can prohibit or stop any action immediately.

Broad local operation, whole-vault intelligence, Git pushes, deployments,
public communication, account creation, purchases, and other external actions
are part of the autonomous destination. JARVIS performs them transparently,
lawfully, with evidence and cost accounting, without routine owner approval.

## 9. Self-Evolution

The final system treats itself as one of its own continuously active projects.
It autonomously identifies weaknesses, creates changes, modifies a candidate
installation, benchmarks alternatives, verifies migrations, runs canaries,
promotes successful releases, observes health, and rolls back degradation.

Self-evolution never means an agent directly rewriting the only live copy.
The independent Supervisor controls live/candidate slots and can recover when
Hermes is broken.

Release risk determines verification duration:

- R0: documentation and non-governing prompts;
- R1: bounded agent logic and skills;
- R2: retrieval, scheduling, mission logic, and state behavior;
- R3: Supervisor, action control, credentials, database migrations, and rollback.

Every risk class may proceed automatically after its required evidence gate
passes. Higher risk requires stronger proof and longer observation, not owner
permission. The owner can veto or stop a candidate at any time.

## 10. Final Success Condition

JARVIS Foundry succeeds when it converts the owner's durable intent into a
continuing stream of useful, independently verified, recoverable work without
asking the owner to supervise or approve normal execution, while remaining
honest about uncertainty, preserving institutional memory, acting under its
standing mandate, and stopping immediately when the owner vetoes it.

This North Star preserves the complete futuristic ambition. Deferring a
capability from V1 does not remove it from the destination.
