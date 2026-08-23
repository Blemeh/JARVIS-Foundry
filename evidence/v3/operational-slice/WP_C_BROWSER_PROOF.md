# WP-C — Real Command Center End-to-End Browser Proof

Date: 2026-08-23 (overnight lane)
Worktree: C:\temp\JARVIS-Overnight-V3-Operational-Slice @ candidate/overnight-v3-operational-slice

## Setup (all lane-local, disposable)

1. `python scripts/v3_operational_e2e.py` builds the scenario in
   `var/overnight/v3-operational-slice/` through REAL public APIs:
   empty ledger -> flag OFF cycle -> candidate generated+scored under the
   registered policy -> flag ON unified cycle (select+materialize) ->
   harmless dispatch via lab-scripted Hermes worker stack ->
   AUTOMATIC outcome closure -> honest continuation cycle.
2. Same script serves the real backend on **port 4611** (`--serve`).
3. UI production build with `VITE_JARVIS_MODE=live VITE_JARVIS_API_BASE=/api`
   served by `vite preview --port 4612` with `/api` proxied to 127.0.0.1:4611
   (`VITE_JARVIS_API_PROXY`) so the live adapter path is same-origin.

## Observed browser state (Discovery route)

Header: `DATA SOURCE: LIVE`, `TRUST: LIVE` — no fixture substitution.
The route rendered:

- Flag banner: `discovery.cycle_enabled = FALSE`, EXPERIMENTAL/OFF copy
  (the served DB snapshot was built without a registered flag param →
  UNREGISTERED maps to enabled:false; banner shows FALSE honestly).
- Portfolio KPI: 0 eligible · 0 deferred · 0 rejected (newest cycle is the
  continuation WAIT; portfolio summary reflects latest recorded counts).
- Policy: v3-policy-2026-08-21.
- Next selected action: `WAIT`.
- Current cycle: `e2e-cycle-2` STANDBY, observed=4, reason
  `NO_ELIGIBLE_CANDIDATES (NO_ELIGIBLE_CANDIDATES)`.
- Recent cycles: `e2e-cycle-1` COMPLETE (COMPILED).
- Executive selection: `exec-e2e-cycle-1`, selected cand-41eda7eb9557 at 0.46,
  Mission mission-eaf8eeeeeeb1 linked.
- Team & verifier: builder + verifier rows (opencode_go), verdict PASS,
  fresh session NO · independence INDEPENDENT · repairs 0/3.
- Predicted vs actual: cand-41eda7eb9557 COMPLETE immediate window,
  predicted expected_success 0.6828 / confidence 0.457 …,
  Actual duration_seconds 0.379128 · outcome PASSED · repair_count 0 ·
  verdict PASS — predicted and actual DISTINCT.
- Recent candidates: cand-41eda7eb9557 (EXECUTED badge "RUNNING"),
  score total 0.41 · pass ≥ 40%, all ten dimension bars, policy label,
  lineage link to mission-eaf8eeeeeeb1.

## Direct-ledger reconciliation (same instant)

| Field | Browser | Ledger query | Match |
|---|---|---|---|
| candidate id/status | cand-41eda7eb9557 / EXECUTED(badge RUNNING) | candidates row: cand-41eda7eb9557, EXECUTED | YES |
| policy version | v3-policy-2026-08-21 | candidates.scoring_policy_version | YES |
| score total | 0.41 | candidate_scores.score = 0.407 (display rounds) | YES |
| mission lineage | mission-eaf8eeeeeeb1 | missions.created_from = candidate:cand-41eda7eb9557 | YES |
| outcome | RECORDED / immediate / predicted≠actual | candidate_outcomes row identical | YES |
| cycles newest-first | e2e-cycle-2 WAITING, e2e-cycle-1 COMPILED | events ordered by sequence DESC | YES |
| next action | WAIT | derived from latest WAITING cycle | YES |

Automated field-level reconciliation additionally passes 10/10 checks in
`evidence/v3/operational-slice/E2E_LEDGER_RECONCILIATION.json`
(regenerated every harness run).

## Payload version skew (adapter tests, realClientDiscovery.test.ts)

- no discovery section → `{available:false, PROJECTION_NOT_AVAILABLE}` (legacy snapshot)
- partial section + unknown enum values → renders verbatim, no crash,
  no fabricated booleans
- current section → full typed view
- unknown additive fields → ignored by explicit mapping (no crash)
- malformed optional values → degraded nulls, never false-success

## Generated contract fixture + drift detection

- `ui/command-center/src/lib/__fixtures__/discoveryBackendFixture.gen.ts`
  is generated from real backend output by the harness (never hand-edited).
- `discoveryDrift.test.ts` maps that payload and asserts field-for-field
  equality against it (5 tests) — mapper drift fails CI.
- Backend-side `test_generated_fixture_matches_backend_projection_shape`
  re-parses the fixture and asserts shape compatibility both directions.

## Performance/payload bounds (test_v3_operational_bounds.py)

300 candidates × 200 cycles × 150 outcomes synthetic disposable ledger:
**49.6 ms build, 214 KiB full snapshot** (well inside a 5 s poll loop and
existing Command Center size conventions). Bounds asserted in test:
≤25 candidates, ≤10 cycles, ≤50 outcomes, <2.0 s, <1.5 MiB.

## Process hygiene

Only lane-owned PIDs used: python e2e server (4611), vite preview (4612).
Orphaned vite preview PIDs from earlier attempts were identified by command
line before termination; no other processes touched. Both servers stopped at
the end of the proof window.
