# JARVIS FOUNDRY — CURRENT STATE

This is the authoritative current-truth surface for the isolated canonical
convergence candidate. Source, candidate verification, and live state are
separate truths. This lane does not describe the candidate as live merely
because it builds or passes disposable tests.

DOCUMENTATION STATE BASE SHA: `c6bfe6ca0a03b148f30e56fb8391b10b2f2136e8`

The SHA above is the source parent of this final documentation-only
reconciliation commit. The freshness gate requires that relationship and
rejects source or test drift after the pin.

## Identity and boundaries

```text
CANDIDATE BRANCH:         candidate/canonical-convergence-20260828
CANDIDATE BASE:           58baca5e7feccf2507614cde75d641bddc15ed98
CANDIDATE SOURCE SHA:     c6bfe6ca0a03b148f30e56fb8391b10b2f2136e8
CANONICAL MAIN SHA:       8dfb981876985525621cf10b8b9b67e34468b450
FETCHED ORIGIN/MAIN SHA:  fe0592991e98de3f5de27c53d40339f4281156f1
CANONICAL CHECKOUT:       DIRTY / PRESERVED / NOT MODIFIED
PRODUCTION RUNTIME:       NOT TOUCHED BY THIS LANE
LIVE DATABASE:            NOT TOUCHED BY THIS LANE
SCHEDULER / REMOTE:       NOT TOUCHED BY THIS LANE
```

The candidate is a new isolated worktree created from the recovery candidate.
No historical branch was merged or replayed when its tip was already an
ancestor of the base. The complete ref-by-ref classification and exclusions
are recorded in
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
the existing canonical authorities. The Command Center is a read projection
and owner-control client; it does not introduce a second mission engine,
ledger, STOP plane, scoring system, registry, or owner path. `UNKNOWN`,
`NOT_AVAILABLE`, `NOT_TESTED`, `BLOCKED`, and `UNCONFIGURED` remain truthful
outcomes when evidence or providers are unavailable.

## Reconciled capability state

| Accepted capability | State in candidate |
| --- | --- |
| Recovery production-spine fixes | `ALREADY_PRESENT` from the recovery base |
| V4 owner control and briefings | `ALREADY_PRESENT`; durable GOAL, constraint, veto, redirect, pause/resume, STOP, and briefing semantics remain on the canonical path |
| V5 multi-system intelligence | `ALREADY_PRESENT`; systems, connectors, effects, and broker remain integrated |
| V6 self-evolution | `ALREADY_PRESENT`; builder/lab/verifier, shadow, canary, promote, restore, discovery, and cycles remain integrated |
| V7A broad operations | `ALREADY_PRESENT` |
| V7B executive portfolio | `ALREADY_PRESENT` |
| V7C resilience and continuity | `ALREADY_PRESENT` |
| Cinematic Command Center | `ALREADY_PRESENT`; one NavRail/flyout, cinematic core, owner surface, active operations, intelligence stream, telemetry, and Obsidian graph implementation |

No accepted lane was `PARTIALLY_MISSING` or `MISSING` after the actual Git
graph/worktree comparison. The old non-ancestor Command Center UI, an unused
Obsidian orbit draft, and planning-only artifacts were intentionally excluded
as `SUPERSEDED` or `REJECTED`; no duplicate competing runtime implementation
was carried forward.

## Candidate implementation

- `src/jarvis_foundry/command_center.py` preserves the recovery health/status
  endpoints and adds a bounded ledger-backed vault projection, safe Obsidian
  wikilink graph and note reads, cache invalidation after authenticated owner
  commands, and truthful `NOT_AVAILABLE` responses for missing legacy vault
  tables/indexes.
- `ui/command-center` uses the cinematic god-mode surface with one accessible
  `NavRail`, desktop flyouts, a mobile drawer, source/trust/STOP labels,
  owner-command controls, active operations, intelligence, telemetry, and the
  Obsidian graph. Simulated, live, degraded, disconnected, and unknown states
  are labelled rather than fabricated.
- The former `Sidebar` component and unused `ObsidianOrbitCore` draft are not
  part of the candidate. Existing canonical service, ledger, Supervisor,
  release, and owner-control authorities were extended rather than forked.

## Verification

All verification below ran in the isolated candidate worktree.

| Surface | Result |
| --- | --- |
| Python Foundry + convergence + acceptance | Exit 0; 2,304 tests collected, 2,302 runnable tests passed, 0 failed; 2 expected skips: host symlink creation and bounded staging Scheduler probe. |
| Frontend Vitest | 25 files, 117 tests passed. |
| Frontend typecheck | `tsc -b --noEmit` passed. |
| Frontend lint | `eslint src e2e --max-warnings 0` passed. |
| Frontend production build | Vite build passed; only the existing dynamic/static vault chunking advisory was emitted. |
| Browser/product sweep | 169 Playwright tests passed, including accessibility, route matrix, owner workflow, STOP/resume, recovery/event journeys, responsive layouts, cinematic states, and truth-lane checks. |
| Impeccable detector | Ran once on the final UI targets and returned `[]`. |

The acceptance README remains explicit that accelerated time is
`ACCELERATED_EQUIVALENT_ONLY`, and that full 12-hour, 24-hour, and reboot-
assisted real-time profiles are `NOT_RUN`. This candidate therefore does not
claim live provider proving, production promotion, or a long-duration soak.

## Promotion boundary and verdict

The candidate has not been merged to `main`, pushed, installed, promoted, or
used to change production/runtime/DB state. Promotion is a separate operation
requiring explicit authorization and fresh release/runtime evidence.

`READY_FOR_PROMOTION`

This verdict means the isolated candidate is source-complete for the accepted
lanes, coherent, documented, and test-green. It does not mean that live
promotion or provider-backed proving has occurred.

## Documentation freshness contract

`scripts/doc_staleness_check.py` is the single freshness authority. It pins
this file to the immediate parent source commit, validates schema v37 plus the
historical v34/v35/v36 markers, rejects forbidden stale claims in current
status surfaces, and checks referenced evidence paths. The final reconciliation
commit may change only `docs/CURRENT_STATE.md`,
`docs/ARCHITECTURE_DOCUMENTATION_CONTRACT.md`, and
`tests/acceptance/README.md`.
