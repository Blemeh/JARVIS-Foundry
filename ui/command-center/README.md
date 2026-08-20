# JARVIS Foundry Command Center - UI Candidate

Isolated frontend candidate for the JARVIS Foundry Command Center, built from the
`stitch_jarvis_foundry_command_center(1).zip` design package under the design
authority of `jarvis_foundry/DESIGN.md`.

## Status

Production mode connects to the canonical JARVIS runtime through
`RealJarvisRuntimeAdapter`, which reads `GET /api/command-center` and writes
owner commands through `POST /api/owner-command` (localhost-only, server-side
HMAC signing). Simulated/demo mode keeps `MockJarvisClient` and labels data
`SIMULATED`.

```text
VITE_JARVIS_MODE=live     # production adapter (default for integration)
VITE_JARVIS_MODE=simulated  # fixture universe for tests/demo
```

## Candidate baseline

```text
SOURCE HEAD:        a1e0e8b713d54b0e08c347e828752e690ef3fea7
SOURCE BRANCH:      promote/hybrid-worker-activation
UI BRANCH:          candidate/command-center-ui
UI WORKTREE:        C:\temp\JARVIS-CommandCenter-UI
SOURCE WORKTREE:    dirty (5 untracked docs files from the active session; untouched)
STITCH PACKAGE:     C:\Users\b\Downloads\stitch_jarvis_foundry_command_center.zip (inspected)
```

Canonical `main` was not modified, merged, rebased, or force-updated by this
candidate. A later reconciliation mission will compare latest main against this
candidate and build the real runtime adapter.

## Stack

- React 19 + TypeScript + Vite
- React Router (hash-free BrowserRouter; all routes render in one SPA shell)
- CSS design tokens (`src/styles/tokens.css`), no CSS framework
- Vitest + Testing Library for unit tests, Playwright for browser smoke/visual

## Commands

```text
npm install
npm run dev              # dev server on http://localhost:4173
npm run typecheck
npm run lint
npm run build            # production build
npm run test             # vitest unit tests
npm run e2e              # playwright smoke tests + screenshots
```

## Data boundary

```
Command Center
      ↓
JarvisClient   (src/lib/contracts.ts - typed frontend-facing interface)
      ↓
MockJarvisClient  (src/lib/mockClient.ts - tests/demo only)

production:
RealJarvisRuntimeAdapter  (src/lib/realClient.ts - maps canonical snapshot)
```

The UI imports no JARVIS database internals. A future runtime adapter maps the
Foundry ledger and Supervisor APIs onto the same contracts.

## Routes

| Route | Section |
| --- | --- |
| `/` | Overview |
| `/missions` | Missions |
| `/missions/:missionId` | Mission Detail |
| `/agents` | Agents |
| `/agents/:agentId` | Agent Detail |
| `/memory` | Memory graph |
| `/memory/:memoryId` | Memory node detail |
| `/evidence` | Evidence ledger |
| `/evidence/:evidenceId` | Evidence provenance detail |
| `/timeline` | Timeline |
| `/systems` | Systems |
| `/systems/:systemId` | System Detail |
| `/notifications` | Notification / Exception Center |

## Cohesion layer

The candidate is one coherent simulated JARVIS universe behind `JarvisClient`
(`src/lib/contracts.ts`). `MockJarvisClient` (`src/lib/mockClient.ts`) owns a
single mutable universe (`src/lib/cohesion.ts`): missions, steps, agent runs,
workers, evidence, timeline, systems, memory, trust, and notifications all
reference stable IDs and stay synchronized when an event or scenario mutates
state.

- **Cross-linking:** missions, workers, evidence, systems, memory nodes,
  verdicts, and timeline events link to their related objects everywhere they
  appear.
- **Deep links + filters:** entity detail routes and query filters such as
  `/timeline?mission=M-HYB-014`, `/evidence?verdict=FAILED`,
  `/agents?mission=M-HYB-014`, `/systems?health=degraded`, and
  `/memory?mission=M-HYB-014` survive refresh and back/forward.
- **Global search:** the bottom command bar resolves deterministic intents
  (`show mission M-HYB-014`, `open agent A-82`, `show degraded systems`,
  `show evidence for mission M-HYB-014`, ...) and fuzzy-searches every entity
  type. `/` focuses it.
- **Keyboard:** `/` focus search, `g` then `m/a/e/t/s` section navigation,
  `?` shortcut help, `Esc` closes surfaces.
- **Notifications:** the bell and `/notifications` list only meaningful JARVIS
  exceptions; every item links to the object that caused it and supports
  acknowledge/resolve.
- **Event stream:** `MockEventStream` behind the client emits local events
  (`verification.failed`, `repair.created`, `worker.recovered`,
  `verification.passed`, crash, stop, resume...). The notifications surface
  exposes a deterministic "Simulate next event" demo control.
- **Scenarios:** dev-only `?scenario=...` bootstraps deterministic states:
  `NORMAL_OPERATION`, `VERIFICATION_FAILURE`, `WORKER_CRASH`,
  `PROVIDER_DEGRADED`, `MISSION_BLOCKED`, `MEMORY_CONFLICT`, `STOP_ACTIVE`,
  `RECOVERY`, `ROLLBACK`, `MISSION_COMPLETE`, plus `EMPTY_STATE` and
  `STREAM_DISCONNECTED`.
- **STOP:** simulated only. Activating STOP freezes the UI candidate globally
  (top bar SUSPENDED banner, frozen controls, STOP subsystem ACTIVE, timeline
  `emergency_stop.*` events, critical notification) and resume writes
  `resume.reorientation` / `resume.reconciliation` events. No production side
  effect; labeled `SIMULATED STOP - UI CANDIDATE`.
- **Integrity:** `validateUniverseIntegrity` (`src/lib/integrity.ts`) checks
  every entity cross-reference and runs in the unit suite; the mock universe
  stays defect-free after scenarios and events.

## Verification

```text
TYPECHECK:      PASS
LINT:           PASS
UNIT TESTS:     27/27 (incl. fixture integrity + event consistency)
PRODUCTION BUILD: PASS
PLAYWRIGHT:     38/38 (smoke, responsive, six E2E journeys, deep links,
                event consistency, unknown-entity recovery, captures)
```

Visual evidence is captured in `evidence/` at 1600x1280 plus tablet/mobile
states for the cohesion surfaces.

Independent cohesion review (fresh context): PASS on all 21 claims; three
reproduced defects (repeat-cycle repair-run duplication, misleading memory
mission filter fallback, unfrozen bell simulate control during STOP) were
repaired and re-verified.
