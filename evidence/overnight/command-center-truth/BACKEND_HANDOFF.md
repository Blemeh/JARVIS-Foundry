# Backend Handoff — Command Center Truth Lane

## Missing or partial snapshot fields (live mode)

### `resources[]` utilization

- **UI need:** Percent utilization bars on `/systems`.
- **Current backend:** `build_snapshot()` includes `resources` with rows from `resources` table, but utilization fields are often absent.
- **UI behavior:** Renders `NOT REPORTED` per resource row; empty list shows explicit message.
- **Backend ask:** Populate `utilization_pct` or `usage_pct` on each resource row when measured.

### `health.stale_leases`

- **UI need:** Overview telemetry stale lease count and trust factor.
- **Current backend:** Present when leases expire before `generated_at`.
- **UI behavior:** Count surfaced via `mapTrustSummary` factor `stale-leases`.

### `controls.constraints`

- **UI need:** Overview constraint count and trust factor.
- **Current backend:** Populated from durable directives.
- **UI behavior:** Count surfaced via `mapTrustSummary` factor `constraints`.

### Mission completion percentage

- **UI need:** Optional progress indicator on mission cards (live).
- **Current backend:** No authoritative completion field in snapshot missions.
- **UI behavior:** Mapper sets `completion = NOT_REPORTED_NUMERIC (-1)` for in-progress states; UI shows "progress UNKNOWN".
- **Backend ask:** Add measured completion or step-derived completion with source metadata if product requires numeric progress in live mode.

### Timeline event linked evidence count

- **UI need:** Linked record count on Overview decision signal.
- **Current backend:** Timeline events expose `evidence_refs`; UI type lacks normalized `evidenceIds` on all fixtures.
- **UI behavior:** Shows `linked records NOT REPORTED` when count unavailable.
- **Backend ask:** Normalize `evidence_refs.length` into timeline projection or document fixture field name.

## Verified backend contracts (no UI change required)

- `costs.measured_actual_total_usd === "UNKNOWN"` when no measured rows (`test_v4_command_center.py`).
- Live adapter reads `/api/command-center` without fixture fallback (`realClient.test.ts`).

## Owner actions none required

All gaps render honest UNKNOWN/NOT REPORTED labels and do not block lane completion.
