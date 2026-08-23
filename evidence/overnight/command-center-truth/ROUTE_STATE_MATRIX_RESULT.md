# All-Route Automation Matrix Result

Work package **WP-E** — Command Center truth lane.

## Harness

`ui/command-center/e2e/route-state-matrix.spec.ts`

- **Routes:** 15 non-Discovery surfaces (list, detail, not-found)
- **States:** `NORMAL_OPERATION`, `STOP_ACTIVE`, `MISSION_BLOCKED`, `WORKER_CRASH`, `EMPTY_STATE` via `?scenario=`
- **Invariants per cell:**
  - `main#main-content` visible
  - expected route heading present
  - no `console.error` / `pageerror`
  - no fabricated telemetry (`99.8`, `$0.38`, `Integrity node online`)
  - simulated data-source pill on operational routes

## Additional flows

| Flow | Coverage |
| --- | --- |
| Rapid route changes | no stale heading leak |
| Refresh on `/systems` | projection label survives reload |

## Excluded (boundary)

`/discovery` and V3-owned surfaces are out of scope for this matrix.

## Gate

```text
npx playwright test e2e/route-state-matrix.spec.ts
```
