# V4-S3 — Heartbeat Lease Renewal + Terminal Monotonicity (DIR-2026-08-12 item 2)

**Slice:** V4_CONTINUOUS_AUTONOMY / truth-critical reliability — heartbeat,
lease and reaper safety
**Commit:** `7e02091` (fix(v4-s3): heartbeat lease renewal + terminal-state
monotonicity)
**Date:** 2026-08-12
**Status:** IMPLEMENTED — independent verifier <PENDING>

## Problem (observed, not assumed)

1. `RoleDispatcher` heartbeats appended `agent_run.heartbeat` events but
   NEVER renewed the session lease (leases.py had no renew API). Lease =
   duration_seconds (300 default) at acquire. A real session running
   past its lease bound was reaped MID-SESSION by the supervisor's
   reaper (documented V3 behavior: "lease = duration_seconds (300
   default), and the reaper reclaims expired leases MID-SESSION") —
   exactly what DIR-2026-08-12 forbids ("A live process must not be
   reaped").
2. `agent_runs` projection handlers had NO terminal-state guards: a late
   `agent_run.completed` event flipped a reaped FAILED row to COMPLETED;
   a late `agent_run.failed` regressed a COMPLETED row; a late
   `agent_run.started` (e.g. stale re-delivery) reset a terminal row to
   RUNNING. "A run marked FAILED must not later become COMPLETED.
   Terminal states must be monotonic and race-safe."

## Repair

1. **`LeaseManager.renew(lease_key, owner_id, duration_seconds)`**
   (supervisor/leases.py): extends the owner's lease to now+duration
   (heartbeat_at refreshed); raises `LeaseUnavailable` when the lease is
   not held by the owner or missing — a session whose lease cannot be
   renewed cannot complete durably and fails honestly.
2. **Dispatcher heartbeat renewal** (roles/dispatcher.py): the heartbeat
   callback passed to adapters appends the heartbeat event AND renews
   the session lease. With heartbeat interval 60s < duration 300s, a
   live session's lease never expires → the reaper can never reap it;
   only a dead dispatcher (heartbeats stopped) ages into the reaper.
3. **Terminal-monotonic projections** (projections.py):
   - `agent_run.completed`: `WHERE status != 'FAILED'` — a reaped run
     never becomes COMPLETED;
   - `agent_run.failed`: `WHERE status != 'COMPLETED'` — a completed run
     never regresses;
   - `agent_run.started` upsert: `WHERE status IN ('RUNNING','FAILED')`
     — COMPLETED never resets to RUNNING, while the provider-fallback
     chain (RUNNING → FAILED-attempt → RUNNING → COMPLETED) stays legal
     (the fallback re-start is a designed, documented transition;
     first-run implementation blocked it and the fallback test caught
     it — corrected with the IN-list).

## Tests (9 new)

- `tests/foundry/test_lease_renewal.py` (5): renew extends expiry for
  the owner; non-owner renew raises; missing lease renew raises;
  dispatcher heartbeat renews the session lease mid-session; the reaper
  never reaps a heartbeating session but reaps an expired one.
- `tests/foundry/test_projection_monotonic.py` (4): FAILED + late
  completed stays FAILED; COMPLETED + late failed stays COMPLETED;
  COMPLETED + late started stays COMPLETED with the ORIGINAL
  provider/hash; the fallback chain ends COMPLETED with provider B and
  failure_class NULL.

## Evidence

- Full suite: 438 passed EXIT=0 (was 429) — artifact regenerated at
  `var/evidence/v4-s3-lease-monotonic/suite-v4-s3.txt`.
- A/B: the first guard formulation (started `WHERE status = 'RUNNING'`)
  broke `test_provider_fallback_on_preferred_failure` — the re-start
  from a FAILED attempt row is legal; corrected to
  `IN ('RUNNING','FAILED')` and the fallback chain regression pinned in
  test_projection_monotonic.
- Next: independent adversarial verifier (freeze discipline), ledger
  close events after PASS.
