# V6-S7 — SUPERVISOR-OWNED CANARY CONTROLLER

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S7 — Supervisor-owned canary controller
OBJECTIVE: live canaries bounded in traffic share, duration, budget,
  health thresholds and promotion eligibility; the SUPERVISOR
  (controller) owns start, tick allocation and termination; candidates
  can never start/extend/expand/terminate their own canary; STOP
  terminates immediately; termination works WITHOUT candidate
  cooperation (hung candidates reaped); health recomputed from raw
  metrics (reported flags never trusted); cost spikes, regressions and
  invariant breaches terminate automatically; traffic stops in the
  ledger without candidate consent.
BASE COMMIT: c67a603 (S6 close)
CANDIDATE RELEASE: none (machinery slice)
RISK CLASS: R3-class machinery (canary/termination control) built and
  verified inside the isolated V6 construction; live surfaces untouched
HYPOTHESIS: n/a (machinery; exercised by the S11 ladder)
BASELINE METRIC: full suite 825 EXIT=0 at S6 close
SUCCESS THRESHOLD: controller-only start/tick/terminate (candidate
  identities refused); start bounds validated; STOP blocks starts and
  terminates running canaries; health recomputed from raw metrics
  (false-health report terminates); latency/cost/regression/invariant
  breaches terminate; duration reached COMPLETES + promotion gate;
  hung candidates terminated + stale reaping; ticks refused after
  termination (traffic stops); all five projection touch points wired;
  suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v27); live DB stays v20; no live writes; controller identity checks
  ("candidate" in controller refused); sentinel checks on the runtime
  home
ACTIONS TAKEN: TDD red (18 tests) -> implemented evolution/canary.py
  (start_canary, canary_tick, terminate_canary, reap_stale_canaries,
  canary_status, canary_completed_successfully, CanaryError) ->
  migration v27 + PROJECTION_TABLES + reconcile key map + database
  row/all/count allowlists -> focused green (124 total across S1-S7) ->
  runtime DB migrated to v27 (reconcile consistent) -> full suite
FILES CHANGED: src/jarvis_foundry/evolution/canary.py (new);
  tests/foundry/test_evolution_canary.py (new, 18 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 27 + migration 27);
  src/jarvis_foundry/projections.py (canary.started/tick/terminated/
  completed/reaped handlers, PROJECTION_TABLES); src/jarvis_foundry/
  reconcile.py (key map); src/jarvis_foundry/database.py (row/all/
  count allowlists); src/jarvis_foundry/evolution/shadow.py +
  tests/foundry/test_evolution_shadow.py (zero_live_effects proof
  hardened to a WAL-safe logical event-ownership check + external
  marker hash — the S6-verifier-disclosed WAL contingency made the raw
  DB-file hash unreliable; see S6 doc section 9)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 18 new S7 tests + S1-S6 regression (106) + FULL SUITE 843 passed
  EXIT=0 (365.22s; var/evidence/v6-s7/suite-v6-s7-full.txt,
  sha256 8ce45183c1d5d171dfee81778831c4c0feac9430b8fa81acb5f1f20908b7fe83)
VERIFIER: <fill after dispatch>
EVIDENCE: var/evidence/v6-s7/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v27, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); the
  controller-identity check ("candidate" in controller string) is a
  recorded convention — the live deployment binds the real Supervisor
  process identity; Hermes-loss/network-loss fault classes are
  machinery-level NOT_TESTED (no live network in the isolated lab) —
  the state-machine paths that handle them (stale reaping, controller
  override) are covered; the canary gate does not route real traffic
  in the isolated lab (the controller's routing decision is outside
  the state machine — the ledger records termination authoritatively)
NEXT AUTONOMOUS ACTION: S8 — atomic promotion and automatic rollback
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/canary.py`:

- `start_canary(...)` — controller-only (identities containing
  "candidate" refused); bounds: traffic_share in (0, 1],
  duration_cycles > 0, max_cost_usd >= 0, health_thresholds with
  error_rate_max + latency_max_s; STOP active -> refused. Event
  canary.started -> RUNNING row.
- `canary_tick(...)` — controller-only; RUNNING-only; health is
  RECOMPUTED from raw metrics (reported_healthy is deliberately never
  trusted); the measurement is appended first, then termination
  conditions in order: health breach, cost budget, regression,
  invariant breach, STOP, duration reached -> COMPLETED. Returns the
  resulting status; terminal states refuse further ticks — traffic
  stops in the ledger without the candidate's consent.
- `terminate_canary(...)` — controller-only; works WITHOUT candidate
  cooperation (a hung candidate cannot prevent it).
- `reap_stale_canaries(db, *, current_cycle, max_cycles_without_tick,
  controller)` — RUNNING canaries whose last tick is older than the
  window are terminated (hung candidates).
- `canary_completed_successfully(...)` — the promotion gate: COMPLETED
  + informational reason only + within budget + all cycles run +
  last_health healthy. A forged completion (foreign reason) fails
  closed.

Projection `canaries` (migration v27, status CHECK RUNNING/COMPLETED/
TERMINATED, counters + thresholds JSON) wired into PROJECTION_TABLES,
the reconcile key map, and database row/all/count allowlists. Handlers
validate mandatory keys and refuse tick/terminal events on unknown or
non-RUNNING canaries inside the append transaction.

## Tests (18)

controller-owned start; candidate cannot start; start bounds (share
0/1.5/-0.1, duration 0/-3, cost -1, empty thresholds); STOP blocks
start; unknown improvement/release refused; healthy tick progresses
(cycles, health, cost); false-health report terminates (recomputed);
latency breach terminates; cost spike terminates; regression
terminates; invariant breach terminates; duration reached COMPLETES +
promotion gate True; termination without cooperation (hung candidate,
ticks refused after); candidate cannot terminate or extend; STOP
terminates running canary; stale reaping (and active not reaped);
projection/schema integrity (pinned to the imported SCHEMA_VERSION
constant >= 27).

## 8. Verifier findings — repair record (deleg_b8bf3e0b, PASS)

Verdict JSON: var/evidence/v6-s7/verdict-deleg_b8bf3e0b.json (recovered
from the harness-truncated summary; verdict PASS + C1-C7 PASS). 41
adversarial probes.

| Severity | Finding | Resolution |
|---|---|---|
| LOW | Out-of-order cycles ACCEPTED (cycle 5 then 2 -> RUNNING, last_tick_cycle rewinds): no monotonic guard; duration/reap math hazard for a misbehaving controller. | REPAIRED: ticks must be strictly after the last ticked cycle (monotonic guard); equal or earlier cycles refused. Regression: test_out_of_order_cycle_refused. |
| INFO | The verifier brief's C3 arithmetic slip: 5 errors / 0.05 traffic = error_rate 100.0, not 1.0 (outcome identical — both breach the 0.05 threshold). | RECORDED: brief arithmetic corrected in this doc; the recomputed-health behavior is unchanged. |
| INFO | The controller check is a substring blocklist ("candidate"), not an identity registry. | ACCEPTED documented boundary: the live deployment binds the real Supervisor process identity at the adapter boundary; the machinery enforces the convention. |
| INFO | Raw event writers can forge canary.completed (the projection trusts the stream; reconcile stays consistent) — the PROMOTION GATE fails closed (forged completion -> canary_completed_successfully False). | ACCEPTED: fail-closed gate is the contract (C5 verified). |
| INFO | reap_stale_canaries with max_cycles_without_tick=0 reaps anything not ticked in the current cycle (incl. freshly started canaries). | ACCEPTED: semantics match the docstring; callers use positive windows. |

Round-2: fresh verifier with A/B discrimination dispatched after this
commit.

## 9. Round-2 verdict (deleg_8c1e5871, PASS — A/B discriminated)

Fresh round-2 verifier: K1 monotonic cycle guard PASS (A/B both ways:
the OLD code accepts cycle 5->2 and rewinds last_tick_cycle with
cycles_run=2 — the round-1 flaw reproduced from a %TEMP% copy of the
old package; the NEW code refuses 5->5 and 5->2 with CanaryError
naming the guard, counters pinned at 5/1, exactly 1 canary.tick event,
and accepts 5->6); K2 documented boundaries PASS (all four recorded);
K3 no regression PASS (19 focused green, suite 844 EXIT=0 sha256
13f4942ce0f994c46202cb6e6702fe995c12786664a6ef27e8fd9b0fb87c33cb,
collect-only 844, runtime v27 consistent, live v20 zero canary events,
both checkouts unchanged). Verdict JSON:
var/evidence/v6-s7/verdict-deleg_8c1e5871-r2.json,
sha256 bbe9081530f2191178c6948d8e17fa5cb2211829657529c14cfb9247d1d1cabf.
INFO observations: live-DB growth is supervisor.heartbeat traffic
(content-level isolation holds); duplicate-cycle ticks on the OLD code
were silently deduped by the idempotency key (the substantive flaw was
the rewind); read-only sqlite opens touch only -wal/-shm sidecars.

**S7 CLOSED — PASS (rounds 1+2).** Next: S8 (atomic promotion and
automatic rollback).
