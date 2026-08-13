# V6 MASTER PLAN — CONTINUOUS SELF-EVOLUTION

**Status:** CANONICAL PRE-EXECUTION ROADMAP

**Phase boundary:** V6 only; a final PASS produces a V7 handoff but does not
implement V7

**Requirement:** `R-EVO-01`

**Primary risks:** `RK-12`, `RK-20`, `RK-21`

## 1. Outcome

JARVIS continuously identifies its own measured weaknesses and opportunities,
builds isolated candidate improvements, independently verifies them, runs
shadow and canary evaluations, promotes successful releases, measures actual
outcomes, and automatically rolls back degradation without creating an owner
approval queue.

Success is a controlled empirical learning system, not merely a release
manager. Every accepted change must have a measured cause, frozen hypothesis,
isolated implementation, external verification, risk-appropriate canary,
automatic rollback contract, and durable before/after evidence.

## 2. Architecture

```text
Independent JARVIS Supervisor
│
├── STOP / veto / directive enforcement
├── release registry and immutable manifests
├── live / candidate / previous pointers
├── candidate process and traffic controller
├── health, budget and rollback evaluator
└── append-only release decisions
     │
     ├── LIVE release
     ├── CANDIDATE release
     ├── PREVIOUS recoverable release
     └── QUARANTINED / REJECTED history

External verifier
└── frozen source + artifacts + metrics; never candidate-controlled
```

The Supervisor stays outside both live and candidate installations. Candidate
failure cannot prevent detection, termination, pointer restoration, previous
release restart, restored-health validation, or rollback recording.

## 3. Release and experiment records

Every improvement record contains:

- immutable hypothesis and candidate IDs;
- measured problem/opportunity and provenance;
- parent/live release identity;
- proposed change and actual diff;
- deterministic risk class and reasons;
- baseline, success, guardrail, cost and observation metrics;
- frozen workloads and input hashes;
- builder/tester/verifier identities;
- source, artifact, dependency and runtime hashes;
- migration/compatibility/restore contracts where stateful;
- shadow and canary allocation;
- promotion and rollback thresholds;
- actual outcome and learning;
- append-only decisions and state transitions.

Recommended candidate states:

```text
DISCOVERED → BUILDING → BUILT → VERIFIED → SHADOW → CANARY → PROMOTED
                                                        └→ PREVIOUS

Any non-terminal state may fail closed to:
FAILED | REJECTED | QUARANTINED | ROLLED_BACK
```

Transitions are monotonic and idempotent. Historical candidates remain
auditable after rejection or rollback.

## 4. Evidence gates by risk

### R0

- targeted tests or validation;
- one bounded representative cycle;
- independent review of the frozen change;
- measured outcome and rollback path.

### R1

- all affected tests;
- several representative cycles;
- adversarial behavior checks;
- bounded canary;
- before/after outcome and cost measurement.

### R2

- full suite;
- fault and restart tests;
- migration rehearsal when stateful;
- shadow comparison;
- extended canary;
- reconciliation and restore evidence.

### R3

- multiple independent reviews;
- forward and backward compatibility proof;
- executable and data restore rehearsal;
- STOP/veto independence proof;
- crash and fault injection;
- extended canary;
- automatic rollback without candidate cooperation;
- no unresolved severity-one finding.

Higher risk changes evidence strength and observation duration, never routine
owner permission.

## 5. Slice roadmap

### V6-S0 — Entry, baseline, isolation, and truth reconciliation

Deliver:

- verified V5 acceptance baseline and live health snapshot;
- exact Git status/diff/worktree/branch inventory;
- protected user-owned untracked files;
- discovered parallel V4 branches/worktrees;
- isolated V6 construction worktree, environment, database copy and paths;
- additive reconciliation of stale canonical status;
- V6 active work package, requirements map, risk map and slice manifest.

No self-modifying behavior is enabled in S0. A stale `PROJECT_STATE.md` cannot
override newer verified V5 acceptance evidence, but it is corrected only
additively.

Gate: the accepted V5 checkout, runtime, live DB, `.h`, Hermes home, and
parallel V4 work are byte/state preserved.

### V6-S1 — Hypothesis, metric, and risk-class contract

Implement durable, fail-closed records for:

- measured problem/opportunity;
- evidence and provenance;
- hypothesis and expected mechanism;
- baseline and minimum meaningful improvement;
- correctness/safety/cost/latency guardrails;
- evaluation workload and observation requirement;
- risk class and actual changed surfaces;
- budget, capability scope, reversibility and rollback threshold.

The record freezes before implementation. Missing or mutable metrics make a
candidate promotion-ineligible. A builder may propose but cannot lower risk.

### V6-S2 — Immutable release registry and slots

Implement immutable release manifests and external live/candidate/previous/
quarantine slot control. Manifests bind source commit, artifact hashes,
dependencies, runtime identity, parent release, hypothesis, risk, migrations,
verdicts and rollback target.

Prove idempotent transitions, recovery from duplicate events, hash mismatch
refusal, unknown-parent refusal, and preservation of failed history.

### V6-S3 — Isolated candidate builder

Build candidates only inside registered, canonical candidate roots or
worktrees. Use separate virtual environments, runtime homes, databases, logs,
connector roots and capabilities.

Test canonical paths, symlinks/junctions, environment leakage, partial builds,
builder crash, concurrency, attempted live writes, attempted release-pointer
writes, credential overreach and cross-candidate collision.

### V6-S4 — Reproducible evaluation laboratory

Run live-baseline and candidate behavior against the same frozen representative
workload. Capture correctness, outcome quality, latency, cost, resource use,
error rates and invariant violations.

Requirements:

- input and configuration hashes;
- deterministic seeds where possible;
- explicit treatment of nondeterminism;
- no cherry-picked cases;
- no implementation-mirroring tests as sole proof;
- comparable output artifacts;
- fail closed on absent or incomparable measurements.

### V6-S5 — Independent verifier boundary

Run the verifier outside candidate control against frozen source, artifacts,
metrics and manifests. Exit code zero alone is NOT_TESTED. Require a structured
PASS/FAIL/BLOCKED/NOT_TESTED verdict bound to exact hashes.

Use adversarial and mutation-discriminating probes. Record builder and verifier
provider/model/session identities. A candidate can never invoke a trusted
promotion verdict for itself.

All actual JARVIS internal workers use `opencode_go` and
`deepseek-v4-flash`, with no silent fallback. DeepSeek unavailability is
BLOCKED. Same-provider verifier limitations remain explicit.

### V6-S6 — Shadow operation

Execute candidate decisions on copied, replayed or mirrored inputs with zero
live effects. Shadow candidates may observe, reason and propose, but cannot:

- consume live capabilities;
- mutate live projects, DB, Obsidian or release state;
- communicate externally;
- influence live mission selection;
- use blocked connectors.

Record decision divergence, expected-effect differences, correctness, cost,
latency, STOP/veto behavior and evidence completeness.

### V6-S7 — Supervisor-owned canary controller

The external Supervisor controls candidate start/kill, allocation, duration,
budget, capabilities, health thresholds, evidence collection, promotion
eligibility and rollback. The candidate cannot enlarge any of these.

Faults to inject include candidate hang, false health, crash, Hermes loss,
network loss, STOP, Supervisor restart, partial/duplicate events, cost spike,
outcome regression, invariant breach and refusal to terminate.

Gate: candidate traffic stops without candidate cooperation.

### V6-S8 — Atomic promotion and automatic rollback

Promotion occurs outside the candidate from a frozen PASS package. It must be
atomic or recoverable across every crash boundary and must retain the exact
previous release.

Test crashes before, during and after pointer change; before post-promotion
state validation; during rollback; and before restored-health confirmation.

Rollback triggers include STOP/veto, lost heartbeat, health failure, invariant
violation, error/outcome/latency/cost regression, evidence mismatch, schema
incompatibility, absent expected effect and verifier contradiction.

Freeze first, terminate candidate externally, restore executable and data,
validate health, reconcile state, then record rollback.

### V6-S9 — Stateful evolution and restoration

Every stateful R2/R3 candidate supplies:

- forward migration;
- migration idempotency;
- previous-executable compatibility expectations;
- rollback migration or complete restore point;
- interrupted-migration recovery;
- schema/data integrity verification;
- projection rebuild/reconciliation;
- explicit data-loss analysis.

All rehearsals run first on disposable copies. The live schema is untouched
until the complete R3 package passes.

### V6-S10 — Autonomous improvement discovery

Create improvement candidates only from measured sources such as incidents,
verifier failures, repeated repair patterns, outcome gaps, latency/cost,
knowledge staleness, tool/provider performance, scoring accuracy, maintenance
needs, owner veto history and registered risks.

Deduplicate equivalent hypotheses. A failed hypothesis cannot repeat without
new evidence. Selection includes expected value, information gain, risk, cost,
reversibility and opportunity cost. Honest WAITING is correct when none clears
the threshold.

### V6-S11 — R0–R3 proving ladder

Prove progressively:

1. one genuine R0 improvement, independently verified and measured;
2. one genuine R1 improvement with representative cycles and canary;
3. one genuine bounded R2 improvement with full suite, faults, shadow,
   extended canary and measured outcome;
4. realistic disposable R3 fault injection proving external STOP, independent
   Supervisor, candidate kill, compatibility, executable/data restore and
   automatic rollback.

Do not manufacture a dangerous live R3 change merely to close the gate. A real
R3 promotion requires a genuine measured opportunity and full proof.

### V6-S12 — Engineering and operational acceptance

Engineering PASS requires all machinery and invariants above, full affected
and complete tests, artifact hashes, reconciliation, independent final review,
no V1–V5 regression and no severity-one defect.

Operational PASS requires at minimum:

- three complete autonomous candidate cycles;
- two successful promotions with measured improvement;
- one real R1 promotion;
- one bounded R2 canary and promotion;
- one broken candidate automatically rejected or rolled back;
- durable before/after evidence;
- no live damage or candidate self-certification;
- independent final adversarial PASS.

Synthetic faults prove machinery but do not count as operational improvement.
If genuine opportunities or traffic are absent, report NOT_TESTED or WAITING.

### V6-S13 — Closure and V7 handoff

After engineering and operational PASS:

- close the V6 work package;
- reconcile canonical state;
- append final gate and release evidence;
- freeze the accepted baseline;
- retain failed/rolled-back history;
- record limitations and deferred work;
- create the V7 handoff.

Do not implement V7 in the V6 session.

## 6. Per-slice method

Every slice follows:

1. inspect current state, STOP/vetoes and diff;
2. freeze base commit, scope, requirements and risks;
3. write failing tests first;
4. implement the smallest coherent change;
5. run focused tests and complete suite;
6. run disposable migration/replay/reconcile/fault checks as applicable;
7. freeze source and artifacts;
8. use a fresh independent adversarial verifier;
9. require exactly one structured verdict;
10. on FAIL, preserve evidence, add a discriminating regression, prove an A/B
    flip where practical, repair, rerun and use a fresh verifier;
11. on PASS, verify hashes, append closure evidence, commit and activate the
    next slice automatically.

No test, threshold or invariant may be weakened to produce green status.

## 7. Parallel and carried boundaries

- Preserve all user-owned dirty/untracked work.
- Protect the original roadmap V4 Command Center/Voice worktrees and branches.
- Keep missing V4 product capability status separate and honest.
- Do not activate V5's blocked connectors without real interface/credential
  evidence and the established mini-gate.
- Do not claim provider reconciliation from local simulation.
- Do not push, deploy, communicate, purchase or perform an external effect
  merely because V6 is autonomous.
- A running live process is not updated by a source merge; candidate promotion
  is a separate verified release operation.

## 8. Final evidence package

The final report includes:

- complete release and candidate history;
- every rejection and rollback;
- risk-class coverage;
- frozen hypotheses and before/after metrics;
- shadow/canary allocations and outcomes;
- executable and schema/data restoration proof;
- R3 fault results;
- V1–V5 regression status;
- original roadmap V4 status;
- honest provider/connector limitations;
- V7 handoff status.
