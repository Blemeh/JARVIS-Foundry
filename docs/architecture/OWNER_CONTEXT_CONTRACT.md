# Owner Context And Standing Orders Contract

Status: V13 isolated build-ahead candidate.

## Purpose

`jarvis_foundry.owner_context` is the advisory personal-context foundation for
the existing Executive. It does not choose work, schedule work, create
missions, execute effects, certify outcomes, or replace owner controls.

The contract is:

```text
existing ledger + authenticated owner directives
  -> owner-context projection
  -> bounded task context / standing-order matches / ranked referents
  -> existing Executive or future Delegation Engine decides
```

STOP, PAUSE, veto, authenticated owner controls, evidence, and final outcomes
always outrank this context.

## Durable Records

The model covers:

- goals and priorities;
- programs, projects, and active objectives;
- standing orders;
- decisions, vetoes, constraints, and permissions;
- preferences with evidence;
- unresolved items and commitments;
- waiting-on-owner items;
- rejected approaches;
- relevant historical outcomes.

Every item has:

- a stable context ID, kind, lifecycle state, scope, priority, and confidence;
- observed, effective, recorded, updated, and optional freshness timestamps;
- source type, source ID, source reference, content hash, evidence references,
  and authority classification;
- optional fact keys, relationships, supersession, and contradiction links.

Corrections are additive. Items may be resolved, revoked, superseded, or marked
stale; prior records remain visible. Conflicting current facts coexist with
their provenance. A stale fact cannot become the current value when a fresh
replacement exists.

## Storage And Schema

The existing append-only `events` table is the source of truth:

- `owner_context.item_recorded`
- `owner_context.item_state_changed`

The projection also adapts authenticated `owner_directive.*` history for goals,
priorities, controls, and standing orders. There is no owner-context table and
no schema migration. Projection rebuild and process restart reconstruct the
same result from the ledger.

Tier-C V11 seed blocks remain a compatibility input to existing scoring. New
V13 records are merged into that advisory scoring shape at read time; they are
not copied into a second memory store.

## Standing Orders

`STANDING_ORDER` extends the existing authenticated owner-command vocabulary.
Its signed metadata contains:

- exact owner text;
- structured scope;
- trigger or condition;
- intended behavior;
- priority;
- active, revoked, superseded, or stale lifecycle truth;
- parser confidence and provenance.

Unknown text cannot be admitted as an active standing order. The initial
deterministic parser supports a deliberately small set of canonical patterns
and accepts explicit structured fields from a future trusted owner-facing
interpreter.

Standing-order evaluation is read-only. It returns matches and evidence, never
performs the intended behavior. Evaluation order is:

1. emergency STOP or PAUSE;
2. matching owner veto;
3. other authenticated owner controls;
4. advisory standing-order matches.

## Context Compiler

The compiler accepts project, program, objective, current situation, referenced
entity, requested task, and recent context IDs. It:

- scores deterministic scope, lexical, recency, freshness, and provenance
  evidence;
- excludes irrelevant project history;
- preserves both sides of a selected contradiction;
- treats stale and unknown records explicitly;
- enforces item, full serialized-package character, and per-kind limits;
- returns no authority to act.

Full context records appear once in `items`. Section lists contain stable
context IDs, avoiding duplicated payload while retaining deterministic
category indexes. `bounds.used_chars` is the exact canonical serialized size
of the returned package.

## Reference Resolution

The resolver returns ranked context referents for phrases such as `continue
this`, `fix it`, `that project`, and `the thing from yesterday`.

Ranking uses explicit recent-context IDs, project/objective/entity scope,
lexical evidence, kind cues, and timestamps. It returns `RESOLVED`,
`AMBIGUOUS`, or `UNKNOWN`. It never acts and never invents a referent when the
top evidence is weak or tied.

## Boundaries

This package does not modify MissionLoop, provider routing, campaigns, effects,
MCP, self-expansion, Scheduler, Supervisor, the UI, release state, or the live
database. It is source-only build-ahead and requires later semantic convergence
before any runtime or product claim.
