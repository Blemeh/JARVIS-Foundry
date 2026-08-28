# JARVIS FOUNDRY — CURRENT STATE

This is the authoritative current-truth surface for the final isolated
convergence candidate. Source, immutable installation, database authority,
and live runtime are separate truths until the guarded promotion procedure
proves their parity.

DOCUMENTATION STATE BASE SHA: `93c86c472926264a1d4f087aac0fbe6764e15076`

The SHA above is the source parent of this final documentation-only
reconciliation commit. The freshness gate requires that relationship and
rejects source or test drift after the pin.

## Identity and boundaries

```text
FINAL CANDIDATE BRANCH:      candidate/final-production-convergence-20260828
LANE 1 SOURCE SHA:           a319017231de3361a11e819e7fff965004e138c9
LANE 2 IMPLEMENTATION SHA:   dc8bfbc136b70d85fecfe16d8a07ab3b94880209
LANE 2 EVIDENCE SHA:         c8b97b842072b851b4e7b9f0731c006bf1b53aa6
LANE 3 SOURCE SHA:           186aeda568bede4fa4bd410f317327893349e9a6
CONVERGED SOURCE BASE:       93c86c472926264a1d4f087aac0fbe6764e15076
CONVERGED SOURCE TREE:       d10d1143e42c91c1c25ce4e36c81408e4871f8aa
CANONICAL CHECKOUT:          DIRTY / PRESERVED / NOT MODIFIED
PRODUCTION RUNTIME:          NOT TOUCHED BEFORE GUARDED PROMOTION
LIVE DATABASE:               NOT TOUCHED BEFORE GUARDED PROMOTION
SCHEDULER / REMOTE:          NOT TOUCHED BEFORE GUARDED PROMOTION
```

The candidate was created in a new isolated worktree from Lane 1's exact
final SHA. Lane 2 and Lane 3 were reconciled by actual diff and semantic
comparison. The reconciliation preserved Lane 1's canonical authorities,
carried Lane 2's release/runtime/state fixes, and selectively forward-ported
Lane 3's truth-aware Command Center and autonomy proof. The complete Lane 1
graph classification remains in
`docs/evidence/integration/canonical-convergence-20260828/RECONCILIATION.md`.

## Schema, authority, and trust

```text
CURRENT/CANDIDATE SCHEMA:     v37
CURRENT SCHEMA:               v37 (v34 live baseline migrated through v35/v36)
V2 KNOWLEDGE SCHEMA:          v35
V3 DISCOVERY/BUILDING SCHEMA: v36
```

The append-only event ledger, Supervisor authority, owner-control service,
effect reconciliation, evidence/verifier contracts, and release authority are
the canonical authorities. The Command Center is a read projection and
owner-control client; it does not introduce a second mission engine, ledger,
STOP plane, scoring system, registry, or owner path. `UNKNOWN`, `NOT_AVAILABLE`,
`NOT_TESTED`, `BLOCKED`, and `UNCONFIGURED` remain truthful outcomes when
evidence or providers are unavailable. Process identity/census is centralized
in the deployment runtime-identity authority.

## Reconciled capability state

| Accepted capability | State in final candidate |
| --- | --- |
| Recovery production-spine fixes | `PRESERVED` from the recovery base |
| V4 owner control and briefings | `PRESERVED`; durable GOAL, constraint, veto, redirect, pause/resume, STOP, and briefing semantics remain on the canonical path |
| V5 multi-system intelligence | `PRESERVED`; systems, connectors, effects, and broker remain integrated |
| V6 self-evolution | `PRESERVED`; builder/lab/verifier, shadow, canary, promote, restore, discovery, and cycles remain integrated |
| V7A broad operations | `PRESERVED` |
| V7B executive portfolio | `PRESERVED` with truthful lifecycle/idle state |
| V7C resilience and continuity | `PRESERVED` with release, rollback, recovery, and reconciliation gates |
| Lane 2 runtime/release truth | `INTEGRATED`; immutable staging/publication, DB live-slot authority, identity parity, process census, scheduler contract, idle truth, and recovery proof |
| Lane 3 Command Center/product truth | `INTEGRATED`; one cinematic surface, source/trust labels, owner-goal state, live fixture proof, and autonomy evidence |

No accepted lane is `PARTIALLY_MISSING` or `MISSING`. Superseded UI drafts,
unused orbit variants, and planning-only artifacts were excluded; no duplicate
runtime, mission engine, ledger, STOP plane, or owner-control path was added.

## Verification

All candidate verification ran in the isolated final worktree. The counts
below are the completed gates available before production promotion; a final
promotion evidence packet records the live results separately.

| Surface | Result |
| --- | --- |
| Python Foundry + convergence + acceptance | 2,315 collected; 2,314 passed, 1 expected host/environment skip, 0 failed |
| Lane 2/Lane 3 focused Python suites | PASS; covered again by the complete run |
| Frontend Vitest | 25 files, 118 tests passed |
| Frontend typecheck | Direct TypeScript `tsc -b --noEmit` passed (npm shim is malformed on this host) |
| Frontend lint | Direct ESLint `src e2e --max-warnings 0` passed |
| Frontend production build | Direct TypeScript build plus Vite build passed; only the existing dynamic/static vault chunking advisory was emitted |
| Product verifier | PASS; 212 checks, 0 failures (15 routes × 7 scenarios × 2 viewports plus navigation) |
| Static design/accessibility/security checks | PASS; no detector findings, 12-route console check clean, and no frontend security findings |
| Live autonomy fixture proof | PASS; 2 Playwright tests; disposable fixture DB and API, not production |

The acceptance README remains explicit that accelerated time is
`ACCELERATED_EQUIVALENT_ONLY`, and that full 12-hour, 24-hour, and reboot-
assisted real-time profiles are `NOT_RUN`. No provider-backed or production
claim is inferred from disposable evidence.

## Promotion boundary

Promotion is a separate guarded operation. It must follow the Lane 2 runbook:
read-only preflight, live DB backup, disposable migration rehearsal, exact
owner STOP, immutable build, database authority before atomic pointers,
single Supervisor launch, source/install/DB/runtime identity parity, health and
trust/reconciliation/idle checks, signed STOP/PAUSE/RESUME/REVOKE, hard-kill
restart, and rollback/recovery. Any failed gate requires safe rollback and
diagnosis; no failure may be hidden or patched around.

Before that operation, the live installation remains an older, intentionally
degraded state and this candidate does not claim production promotion. The
final session handoff must record the resulting live release SHA, Supervisor
state, schema/reconciliation, health/status, owner-control proofs, rollback
result, and any blocker.

## Documentation freshness contract

`scripts/doc_staleness_check.py` is the single freshness authority. It pins
this file to the immediate parent source commit, validates schema v37 plus the
historical v34/v35/v36 markers, rejects forbidden stale claims in current
status surfaces, and checks referenced evidence paths. The final
reconciliation commit may change only `docs/CURRENT_STATE.md`,
`docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`, and
`tests/acceptance/README.md`.
