# Source → Display Matrix

| Source field | Route / surface | When absent | When zero | When measured | Simulated mode |
|--------------|-----------------|-------------|-----------|---------------|----------------|
| `costs.measured_actual_total_usd` | Overview telemetry | `UNKNOWN` (no `$`) | `$0.00` | `$X.XX` | suffix `· SIMULATED` |
| `costs.measured_actual_total_usd` (string `"UNKNOWN"`) | snapshotMapper → Overview | `NOT_REPORTED_NUMERIC` → `UNKNOWN` | n/a | n/a | suffix `· SIMULATED` |
| `resources[].utilization_pct` | Systems resource meters | `NOT REPORTED` | `0%` if authoritative | `N%` | fixture labels in toolbar |
| `health.trust_state` | Trust dialog / Overview goal | `DEGRADED` | n/a | `PROVEN` only when token is `PROVEN` | `SIMULATED` state from fixtures |
| `health.trust_state = LAB` | Trust summary | `DEGRADED` (not PROVEN) | n/a | n/a | n/a |
| `verification[]` | Trust evidence coverage | `NOT REPORTED` | `0%` if empty list with explicit count | computed pass ratio | fixture `96%` with fixture note |
| `trust.score` | Trust dialog | `SCORE: NOT AVAILABLE` + aria-label | n/a | numeric only when non-null | withheld |
| `controls.constraints[]` | Overview constraints + trust factor | `0` | n/a | count from array length | from fixture trust factors if present |
| `health.stale_leases[]` | Overview stale lease hint + trust factor | `0` / heartbeat current | n/a | count from array length | from fixture trust factors if present |
| `mission.state` completion | Missions list (live mapper) | `NOT_REPORTED_NUMERIC` (-1) | `0` blocked/failed; `100` complete | n/a | fixture completion retained |
| Timeline `evidenceIds` | Overview decision signal | `linked records NOT REPORTED` | `0 linked records` | exact count | exact count from fixture |
| `connection.source` | All routes | `ErrorState` / unavailable copy | n/a | `LIVE` pill | `SIMULATED` pill |

## Minimum test coverage

| Case | Test location |
|------|---------------|
| cost absent/UNKNOWN → no currency | `truthFormat.test.ts`, `overview.test.tsx` |
| cost zero → `$0.00` only if authoritative | `truthFormat.test.ts`, `snapshotMapper.test.ts` |
| trust/resource absent → no fabricated % | `snapshotMapper.test.ts` |
| stale timestamp | `truthFormat.test.ts` |
| simulated label | `overview.test.tsx`, `truth-lane.spec.ts` |
| live → no fixture fallback | `realClient.test.ts` (unchanged) |
| partial snapshot | `overview.test.tsx` (partial render path) |
