# JARVIS V4 Owner Briefings — Serial Job Queue

Branch: `candidate/v4-owner-briefings-20260823`
Frozen base: `01db32cbcaa409dfe26386ce66110429c2c0b0e0`
Worktree: `C:\\temp\\JARVIS-V4-Owner-Briefings`

Green tests close a job, not the lane. Each completed job must improve a real
owner/JARVIS behavior, add focused and adversarial proof, update this queue,
and leave a verified local commit.

## V4-1 — Owner directive truth inventory + consolidation

Status: DONE @ `e3ee481`

Target behavior: owner intent is reconstructible from authenticated ledger
events with stable identity, provenance, command/scope, lifecycle, and honest
UNKNOWN rather than projection-only state. Reuse `owner_directive.*`; do not
create a second directive authority.

Required proof: duplicate admission, replay, supersession, stale/unknown
target, revocation, restart reconstruction, and malformed optional fields.

Evidence: `V4-1_DIRECTIVE_TRUTH.md`

## V4-2 — Goals / priorities / constraints / vetoes as operating input

Status: DONE @ `e57811e`

Target behavior: durable owner inputs are visible to candidate selection and
owner-facing traceability, with history, versions, active/inactive state,
provenance, conflicts, and veto precedence.

Evidence: `V4-2_OWNER_OPERATING_INPUT.md`

## V4-3 — Pause / resume product semantics

Status: DONE @ `1954290`

Target behavior: PAUSED, STOPPED, BLOCKED, DEGRADED, WAITING, UNKNOWN, and
RUNNING are durable and truthful; duplicate/stale/restart requests do not
erase unrelated constraints/vetoes or weaken deterministic STOP.

Evidence: `V4-3_CONTROL_STATE.md`

## V4-4 — Briefing event model

Status: DONE @ `c8b10ce`

Target behavior: deterministic first-pass briefing items derive from
authoritative events/evidence, with source refs, importance, freshness,
dedupe identity, entity/mission links, and owner-action-required classification.

## V4-5 — Deduplicated twice-daily + exception briefing engine

Status: DONE @ `c8b10ce`

Target behavior: restart-safe morning/evening windows and exception path use a
bounded cursor/checkpoint, late-event handling, cross-window dedupe, and zero-
change suppression without fabricated activity. Production scheduler remains
untouched; use a fake clock/local harness.

## V4-6 — Owner-decision queue, not approval queue

Status: DONE @ `c8b10ce`

Target behavior: only genuinely consequential owner attention is classified as
INFORM, DECISION_REQUIRED, BLOCKED_EXTERNAL_DEPENDENCY, OWNER_VETO_ACTIVE,
or UNKNOWN, with evidence, consequence, options when evidenced, dedupe, and
resolved/superseded lifecycle.

## V4-7 — Command Center management-by-exception surface

Status: DONE @ `4cd6e20`

Target behavior: owner can inspect goals, constraints/vetoes, pause/STOP truth,
briefing history, exception and decision queues, and why/what-changed/what-next
traceability without adding a second authority. Preserve current design system.

## V4-8 — Result traceability

Status: DONE @ `6280ae6`

Target behavior: representative outcomes link decision, evidence, candidate,
mission, worker/verifier, failure/repair, actual outcome, external change, next
action, and any owner decision without fabrication.

## V4-9 — Structured briefing packet + export contract

Status: DONE @ `6280ae6`

Target behavior: stable structured packet for Command Center/future delivery,
including window, items, decision-required subset, source/evidence refs,
next-action summary, uncertainty, and delivery eligibility. No connectors,
voice, or localization implementation.

## V4-10 — Failure / restart / honesty hardening

Status: DONE @ `6280ae6`

Target behavior: duplicate replay, interrupted checkpoint, malformed/old
events, missing nested fields, future timestamps, hostile text, partial
directives, UI reconnect, and selection races fail honestly and remain
restart-safe.

## V4-11+ — Continue only real V4-owned gaps

Status: QUEUED

Reject decorative churn, generic notification frameworks, duplicate STOP
engines, V5/V6 internals, voice, localization, and architecture rewrites.
