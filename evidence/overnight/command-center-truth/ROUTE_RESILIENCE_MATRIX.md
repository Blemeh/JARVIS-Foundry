# Route Resilience Matrix

Work package **WP-B** — Command Center truth lane (`candidate/overnight-command-center-truth`).

Recorded: 2026-08-23T00:08:05+02:00 (local) after `npm run lint && npm run typecheck && npm run test` in `ui/command-center`.

## Pattern

| Layer | Responsibility |
| --- | --- |
| `useRouteQuery` | Async fetch with `.catch` → `status: error`, no infinite loading |
| `RouteQueryGate` | List / aggregate routes: loading → error (incl. disconnected) → children |
| `DetailRouteQueryGate` | Detail routes: loading → **notFound** (null data) vs **fetch error** (rejected promise) |
| `CommandCenterContext` | Notifications load status for context-backed `/notifications` |

## Route coverage (non-Discovery)

| Route | Gate | Loading label | Error / not-found |
| --- | --- | --- | --- |
| `/` Overview | `RouteQueryGate` | Loading command deck | Command deck unavailable |
| `/missions` | `RouteQueryGate` | Loading missions | Missions unavailable |
| `/missions/:missionId` | `DetailRouteQueryGate` | Loading mission detail | Mission not found / Mission detail unavailable |
| `/agents` | `RouteQueryGate` | Loading agent fleet | Agent fleet unavailable |
| `/agents/:agentId` | `DetailRouteQueryGate` | Loading agent detail | Agent not found / Agent detail unavailable |
| `/memory` | `RouteQueryGate` | Loading knowledge graph | Knowledge graph unavailable |
| `/memory/:memoryId` | inline not-found after graph ready | — | Memory node not found |
| `/executive` | `RouteQueryGate` | Loading executive decisions | Executive decisions unavailable |
| `/evidence` | `RouteQueryGate` | Loading evidence ledger | Evidence ledger unavailable |
| `/evidence/:evidenceId` | `DetailRouteQueryGate` | Loading evidence record | Evidence not found / Evidence detail unavailable |
| `/timeline` | `RouteQueryGate` | Loading timeline | Timeline unavailable |
| `/systems` | `RouteQueryGate` | Loading systems | Systems unavailable |
| `/systems/:systemId` | `DetailRouteQueryGate` | Loading system detail | System not found / System detail unavailable |
| `/notifications` | context `notificationsStatus` | Loading notifications | Notifications unavailable (disconnected / fetch error) |
| `/*` | shell not-found | — | unchanged |

**Excluded (boundary):** `Discovery.tsx`, `realClient.ts`, `contracts.ts`, `v3Models.ts`, `v3Fixtures.ts`, backend.

## Fixture trust factors (Overview constraints / stale leases)

`fixtures.ts` `trustSummary.factors` now includes:

- `constraints` — `2 active constraints in durable controls.`
- `stale-leases` — `0 stale leases reported by supervisor health.`

Overview reads both via `trustFactorNumericDetail` for the constraints counter and stale-lease telemetry line.

## Automated evidence

### Gate commands (PASS)

```text
npm run lint       → exit 0
npm run typecheck  → exit 0
npm run test       → 15 files, 69 tests passed
```

### New tests

| File | Cases |
| --- | --- |
| `src/lib/__tests__/routeResilience.test.ts` | `routeFetchErrorDetail`, `shouldShowRouteFetchError`, `formatArchivedMissionLabel`, `isDetailNotFound` |
| `src/routes/__tests__/routeResilience.test.tsx` | gate loading/error/not-found, Timeline load, MissionDetail unknown id |

### Representative assertions

- Disconnected surface copy: `Mission vectors unavailable: runtime source is DISCONNECTED; no fixture fallback is shown.`
- Detail not-found vs error: `Mission not found` vs `Mission detail unavailable` + `ledger read failed`
- Integration: `/missions/MISSING-999` resolves to **Mission not found** (not infinite spinner)
- Integration: Timeline renders **Evidence Spine** heading after simulated client fetch

## Manual spot-check (simulated client)

1. Open `/missions/MISSING-999` → not-found error state with simulated copy.
2. Open `/` → command deck loads; constraints show `02` from fixture `constraints` factor.
3. Disconnect runtime (live mode) → list routes show unavailable error, not fixture fallback.

## Risks / follow-ups

- `useRouteQuery` resets loading via `queueMicrotask` to satisfy `react-hooks/set-state-in-effect`; acceptable for this shift.
- Notifications error path depends on `listNotifications()` rejection; simulated client always succeeds today — covered by gate unit tests and context wiring.
